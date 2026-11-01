"""Porta `GatewayPagamento`: contrato da borda de pagamento (desenho em `docs/pagamento.md` §2 e §3).

Valores sempre em centavos `int` (nunca `float`), moeda BRL fixa, `tenant_id` em toda chamada e chave de
idempotência gerada pelo caso de uso. A máquina de estados (§3) fica aqui como dado imutável para que domínio,
casos de uso e adapters usem exatamente a mesma tabela.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, NewType, Protocol

TenantId = NewType("TenantId", str)
Centavos = NewType("Centavos", int)  # sempre > 0 nos valores cobrados; nunca float
ChaveIdempotencia = NewType("ChaveIdempotencia", str)
Moeda = Literal["BRL"]


class FormaPagamento(StrEnum):
    """Formas de pagamento aceitas pela loja (manual §5)."""

    PIX = "pix"
    CREDITO = "credito"
    DEBITO = "debito"
    BOLETO = "boleto"  # evolução: criar_boleto() com o mesmo padrão


class StatusPagamento(StrEnum):
    """Estados de um pagamento (`docs/pagamento.md` §3)."""

    CRIADO = "criado"  # registro local, antes de chamar o gateway
    PENDENTE = "pendente"  # cobrança emitida, aguardando o cliente
    AUTORIZADO = "autorizado"  # cartão autorizado, não capturado
    APROVADO = "aprovado"
    RECUSADO = "recusado"
    EXPIRADO = "expirado"
    CANCELADO = "cancelado"
    ESTORNADO_PARCIAL = "estornado_parcial"
    ESTORNADO = "estornado"
    CONTESTADO = "contestado"  # chargeback


TRANSICOES: Final[Mapping[StatusPagamento, frozenset[StatusPagamento]]] = {
    StatusPagamento.CRIADO: frozenset({StatusPagamento.PENDENTE, StatusPagamento.CANCELADO}),
    StatusPagamento.PENDENTE: frozenset(
        {
            StatusPagamento.APROVADO,
            StatusPagamento.AUTORIZADO,
            StatusPagamento.RECUSADO,
            StatusPagamento.EXPIRADO,
            StatusPagamento.CANCELADO,
        }
    ),
    StatusPagamento.AUTORIZADO: frozenset({StatusPagamento.APROVADO, StatusPagamento.CANCELADO}),
    StatusPagamento.APROVADO: frozenset(
        {StatusPagamento.ESTORNADO_PARCIAL, StatusPagamento.ESTORNADO, StatusPagamento.CONTESTADO}
    ),
    StatusPagamento.ESTORNADO_PARCIAL: frozenset({StatusPagamento.ESTORNADO_PARCIAL, StatusPagamento.ESTORNADO}),
    StatusPagamento.CONTESTADO: frozenset({StatusPagamento.APROVADO, StatusPagamento.ESTORNADO}),
    StatusPagamento.RECUSADO: frozenset(),
    StatusPagamento.EXPIRADO: frozenset(),
    StatusPagamento.CANCELADO: frozenset(),
    StatusPagamento.ESTORNADO: frozenset(),
}
"""Transições permitidas (§3). Estado ausente do conjunto de destino = transição proibida."""

ESTADOS_TERMINAIS: Final[frozenset[StatusPagamento]] = frozenset(
    estado for estado, destinos in TRANSICOES.items() if not destinos
)
"""`RECUSADO`, `EXPIRADO`, `CANCELADO` e `ESTORNADO`."""


def transicao_permitida(de: StatusPagamento, para: StatusPagamento) -> bool:
    """`True` se a máquina de estados (§3) permite ir de `de` para `para`."""
    return para in TRANSICOES[de]


@dataclass(frozen=True, slots=True)
class ReferenciaPagador:
    """Pagador com dados pessoais mínimos (LGPD)."""

    cliente_id: str  # id interno
    email: str | None = None  # só se o gateway exigir


@dataclass(frozen=True, slots=True)
class NovaCobrancaPix:
    """Comando para emitir uma cobrança PIX."""

    tenant_id: TenantId
    pedido_id: str
    valor: Centavos  # já com o desconto PIX aplicado pelo domínio
    descricao: str  # sem PII
    expira_em: timedelta  # ex.: 30 min (config por tenant)
    chave_idempotencia: ChaveIdempotencia
    pagador: ReferenciaPagador
    moeda: Moeda = "BRL"


@dataclass(frozen=True, slots=True)
class CobrancaPix:
    """Cobrança PIX emitida."""

    pagamento_id_externo: str
    status: StatusPagamento
    valor: Centavos
    copia_e_cola: str  # payload EMV do PIX
    qr_code_png_base64: str | None  # alguns gateways só devolvem o payload
    expira_em: datetime  # timezone-aware


@dataclass(frozen=True, slots=True)
class NovoCheckoutCartao:
    """Comando para abrir um checkout de cartão hospedado pelo gateway (PCI)."""

    tenant_id: TenantId
    pedido_id: str
    valor: Centavos
    tipo: Literal[FormaPagamento.CREDITO, FormaPagamento.DEBITO]
    parcelas_max: int  # calculado por Parcelamento (1..12); débito = 1
    url_retorno: str
    expira_em: timedelta
    chave_idempotencia: ChaveIdempotencia
    pagador: ReferenciaPagador
    moeda: Moeda = "BRL"


@dataclass(frozen=True, slots=True)
class CheckoutCartao:
    """Checkout de cartão aberto."""

    pagamento_id_externo: str
    status: StatusPagamento
    url_checkout: str  # página HOSPEDADA pelo gateway (PCI)
    expira_em: datetime


@dataclass(frozen=True, slots=True)
class SituacaoPagamento:
    """Fotografia do pagamento no gateway."""

    pagamento_id_externo: str
    status: StatusPagamento
    valor: Centavos
    valor_estornado: Centavos
    parcelas: int | None
    atualizado_em: datetime


@dataclass(frozen=True, slots=True)
class SolicitacaoEstorno:
    """Comando de estorno total (`valor=None`) ou parcial."""

    tenant_id: TenantId
    pagamento_id_externo: str
    valor: Centavos | None  # None = estorno total
    motivo: str
    solicitado_por: str  # usuario_id do JWT (auditoria)
    chave_idempotencia: ChaveIdempotencia


@dataclass(frozen=True, slots=True)
class ResultadoEstorno:
    """Resultado de um estorno."""

    estorno_id_externo: str
    status: StatusPagamento  # ESTORNADO_PARCIAL | ESTORNADO
    valor_estornado: Centavos


@dataclass(frozen=True, slots=True)
class EventoPagamento:
    """Evento do provedor já verificado e normalizado (sem PII)."""

    evento_id: str  # chave de deduplicação
    pagamento_id_externo: str
    tipo: str  # ex.: "pagamento.atualizado"
    status_informado: StatusPagamento | None  # pode faltar: o caso de uso reconsulta
    ocorrido_em: datetime


# Erros: a política de retry depende do tipo, não do texto da mensagem.
class ErroGateway(Exception):  # noqa: N818 - nomes do contrato em pt-BR (docs/pagamento.md §2)
    """Base dos erros do gateway de pagamento."""


class ErroTransitorio(ErroGateway):
    """Timeout, conexão, 429, 5xx: retry permitido (só em operação idempotente)."""


class ErroPermanente(ErroGateway):
    """400, 401/403, credencial inválida, transição proibida: sem retry."""


class AssinaturaInvalida(ErroGateway):
    """Webhook com assinatura ausente, inválida ou fora da janela de tempo."""


class PagamentoNaoEncontrado(ErroGateway):
    """Pagamento inexistente no tenant informado."""


class GatewayPagamento(Protocol):
    """Contrato de qualquer gateway de pagamento (fake, Mercado Pago, ...)."""

    nome: str  # "fake", "mercadopago", ... (métricas/circuit breaker)

    async def criar_cobranca_pix(self, cmd: NovaCobrancaPix) -> CobrancaPix:
        """Emite uma cobrança PIX. Mesma chave de idempotência → mesma cobrança."""
        ...

    async def criar_checkout_cartao(self, cmd: NovoCheckoutCartao) -> CheckoutCartao:
        """Abre um checkout hospedado. Mesma chave de idempotência → mesmo checkout."""
        ...

    async def consultar(self, tenant_id: TenantId, pagamento_id_externo: str) -> SituacaoPagamento:
        """Situação atual. `PagamentoNaoEncontrado` se não existe no tenant."""
        ...

    async def buscar_por_idempotencia(self, tenant_id: TenantId, chave: ChaveIdempotencia) -> SituacaoPagamento | None:
        """Reconciliação após timeout ambíguo: `None` se nada foi criado com a chave."""
        ...

    async def cancelar(
        self, tenant_id: TenantId, pagamento_id_externo: str, chave: ChaveIdempotencia
    ) -> SituacaoPagamento:
        """Cancela (void) uma cobrança `PENDENTE`/`AUTORIZADO`."""
        ...

    async def estornar(self, cmd: SolicitacaoEstorno) -> ResultadoEstorno:
        """Estorno total ou parcial de um pagamento `APROVADO`/`ESTORNADO_PARCIAL`."""
        ...

    async def processar_webhook(
        self, tenant_id: TenantId, cabecalhos: Mapping[str, str], corpo: bytes
    ) -> EventoPagamento:
        """Verifica a assinatura e normaliza o evento; não altera estado."""
        ...

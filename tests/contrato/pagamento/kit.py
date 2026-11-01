"""Kit da suíte de contrato de pagamento: o que cada gateway registrado precisa prover, e helpers comuns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from emporio.portas.pagamento import (
    Centavos,
    ChaveIdempotencia,
    ErroTransitorio,
    FormaPagamento,
    GatewayPagamento,
    NovaCobrancaPix,
    NovoCheckoutCartao,
    ReferenciaPagador,
    SolicitacaoEstorno,
    StatusPagamento,
    TenantId,
)

TENANT_A = TenantId("emporio")
TENANT_B = TenantId("loja-teste")
AGORA = datetime(2026, 3, 16, 14, 0, tzinfo=UTC)
VALOR = Centavos(129_900)  # centavos "neutros": nenhum cenário automático do fake
EXPIRACAO = timedelta(minutes=30)

TipoFalha = Literal["transitoria", "timeout_ambiguo", "permanente"]


class RelogioFixo:
    """Relógio parado em `AGORA`, que só anda quando o teste manda."""

    def __init__(self, instante: datetime = AGORA) -> None:
        self.instante = instante

    def agora(self) -> datetime:
        return self.instante

    def avancar(self, delta: timedelta) -> None:
        self.instante += delta


class ControleGateway(Protocol):
    """Alavancas de teste que todo gateway da suíte oferece (fake: métodos próprios; sandbox: API de testes)."""

    suporta_falhas: bool

    async def levar_a(self, tenant_id: TenantId, pagamento_id: str, status: StatusPagamento) -> None:
        """Faz o pagamento chegar a `status` como se o cliente/provedor agisse (pagar, recusar, capturar...)."""
        ...

    async def webhook(
        self, tenant_id: TenantId, pagamento_id: str, status: StatusPagamento | None
    ) -> tuple[dict[str, str], bytes]:
        """Webhook assinado com o segredo do tenant, **sem** mudar o estado."""
        ...

    async def cobrancas_emitidas(self, tenant_id: TenantId) -> int:
        """Quantas cobranças existem de fato no tenant (detecta cobrança dupla)."""
        ...

    def injetar_falha(self, tipo: TipoFalha, operacao: str, *, vezes: int = 1) -> None:
        """Faz as próximas `vezes` chamadas de `operacao` falharem (só se `suporta_falhas`)."""
        ...


@dataclass(frozen=True, slots=True)
class KitGateway:
    """Um gateway novo, sem estado compartilhado, e o controle correspondente."""

    gateway: GatewayPagamento
    controle: ControleGateway
    relogio: RelogioFixo


FabricaGateway = Callable[[], KitGateway]


def chave(sufixo: str, tenant: TenantId = TENANT_A) -> ChaveIdempotencia:
    """Chave no formato `{tenant}:{pedido}:{forma}:{tentativa}` (docs/pagamento.md §2)."""
    return ChaveIdempotencia(f"{tenant}:{sufixo}")


def nova_pix(
    sufixo: str = "1001:pix:1", *, tenant: TenantId = TENANT_A, valor: int = VALOR, pedido_id: str = "1001"
) -> NovaCobrancaPix:
    """Comando PIX válido."""
    return NovaCobrancaPix(
        tenant_id=tenant,
        pedido_id=pedido_id,
        valor=Centavos(valor),
        descricao="Pedido 1001 - Empório da Música",
        expira_em=EXPIRACAO,
        chave_idempotencia=chave(sufixo, tenant),
        pagador=ReferenciaPagador(cliente_id="c-42"),
    )


def novo_cartao(
    sufixo: str = "1001:credito:1",
    *,
    tenant: TenantId = TENANT_A,
    tipo: Literal[FormaPagamento.CREDITO, FormaPagamento.DEBITO] = FormaPagamento.CREDITO,
    parcelas_max: int = 10,
    valor: int = VALOR,
) -> NovoCheckoutCartao:
    """Comando de checkout de cartão válido."""
    return NovoCheckoutCartao(
        tenant_id=tenant,
        pedido_id="1001",
        valor=Centavos(valor),
        tipo=tipo,
        parcelas_max=parcelas_max,
        url_retorno="https://emporio.exemplo.com/pedido/1001",
        expira_em=EXPIRACAO,
        chave_idempotencia=chave(sufixo, tenant),
        pagador=ReferenciaPagador(cliente_id="c-42"),
    )


def estorno(pagamento_id: str, sufixo: str, valor: int | None = None) -> SolicitacaoEstorno:
    """Comando de estorno (total com `valor=None`)."""
    return SolicitacaoEstorno(
        tenant_id=TENANT_A,
        pagamento_id_externo=pagamento_id,
        valor=None if valor is None else Centavos(valor),
        motivo="devolução por arrependimento (manual §4)",
        solicitado_por="gerente-1",
        chave_idempotencia=chave(sufixo),
    )


async def com_retry[T](
    operacao: Callable[[], Awaitable[T]],
    *,
    esperas: list[float],
    tentativas: int = 3,
    base_s: float = 1.0,
    teto_s: float = 8.0,
) -> T:
    """Política da arquitetura §5.2 (3 tentativas, base 1 s, teto 8 s), só para `ErroTransitorio`.

    Em vez de dormir, registra a espera máxima (antes do jitter) em `esperas`, para o teste conferir.
    """
    for tentativa in range(tentativas):
        try:
            return await operacao()
        except ErroTransitorio:
            if tentativa == tentativas - 1:
                raise
            esperas.append(min(teto_s, base_s * 2**tentativa))
    raise AssertionError("inalcançável")  # pragma: no cover


async def verificar_idempotencia_criacao(kit: KitGateway) -> None:
    """Mesma chave duas vezes → mesma cobrança e uma só emissão. Usado também no teste meta."""
    cmd = nova_pix("idem:pix:1")
    primeira = await kit.gateway.criar_cobranca_pix(cmd)
    segunda = await kit.gateway.criar_cobranca_pix(cmd)
    assert segunda.pagamento_id_externo == primeira.pagamento_id_externo, "mesma chave gerou outra cobrança"
    assert segunda == primeira
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1, "cobrança dupla com a mesma chave"

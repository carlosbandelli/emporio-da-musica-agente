"""`FakeGatewayPagamento`: gateway simulado, em memória por instância e determinístico (`docs/pagamento.md` §8).

Sem rede nem credenciais. Tudo que ele devolve é marcado como simulação (copia-e-cola `SIMULADO-...` e link
`https://pagamento.simulado.local/...`). Serve para exercitar o contrato da porta, a máquina de estados (§3), a
idempotência e a política de retry (§5) sem dinheiro real.

Cenários (o que acontece com uma cobrança `PENDENTE` na primeira consulta):

- escolhidos pelos centavos do valor (`..91` aprovar, `..92` recusar, `..93` expirar, `..94` autorizar), ou
- programados por chave de idempotência (`programar`), ou
- `cenario_padrao` (padrão `AGUARDAR`: fica pendente até `simular_evento`, o caminho pendente → confirmado).

Falhas injetáveis (`injetar_falha`): transitória antes de executar (nada é criado), timeout ambíguo (executa e
"perde" a resposta) e permanente. `latencia_s` atrasa cada operação para testar o timeout do chamador.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Literal

from emporio.portas.estado import Relogio
from emporio.portas.pagamento import (
    AssinaturaInvalida,
    Centavos,
    ChaveIdempotencia,
    CheckoutCartao,
    CobrancaPix,
    ErroPermanente,
    ErroTransitorio,
    EventoPagamento,
    FormaPagamento,
    NovaCobrancaPix,
    NovoCheckoutCartao,
    PagamentoNaoEncontrado,
    ResultadoEstorno,
    SituacaoPagamento,
    SolicitacaoEstorno,
    StatusPagamento,
    TenantId,
    transicao_permitida,
)

CABECALHO_ASSINATURA: Final = "x-simulado-assinatura"
TOLERANCIA_ASSINATURA: Final = timedelta(minutes=5)
URL_BASE: Final = "https://pagamento.simulado.local"
PARCELAS_MAX: Final = 12
_TIPO_EVENTO: Final = "pagamento.atualizado"


class Cenario(StrEnum):
    """Destino de uma cobrança `PENDENTE` na primeira consulta."""

    AGUARDAR = "aguardar"  # continua PENDENTE até simular_evento (pendente → confirmado)
    APROVAR = "aprovar"
    RECUSAR = "recusar"
    EXPIRAR = "expirar"
    AUTORIZAR = "autorizar"  # cartão autorizado; a captura vem por simular_evento(APROVADO)


class Falha(StrEnum):
    """Falhas injetáveis nas próximas operações."""

    TRANSITORIA = "transitoria"  # 429/5xx/conexão antes de executar: nada muda no gateway
    TIMEOUT_AMBIGUO = "timeout_ambiguo"  # a operação acontece, mas a resposta se perde
    PERMANENTE = "permanente"  # 400/401/403


Operacao = Literal[
    "criar_cobranca_pix", "criar_checkout_cartao", "consultar", "buscar_por_idempotencia", "cancelar", "estornar"
]

CENARIO_POR_CENTAVOS: Final[Mapping[int, Cenario]] = {
    91: Cenario.APROVAR,
    92: Cenario.RECUSAR,
    93: Cenario.EXPIRAR,
    94: Cenario.AUTORIZAR,
}
"""Centavos finais do valor que escolhem o cenário (ex.: R$ 100,92 é recusado)."""

_DESTINO_CENARIO: Final[Mapping[Cenario, StatusPagamento]] = {
    Cenario.APROVAR: StatusPagamento.APROVADO,
    Cenario.RECUSAR: StatusPagamento.RECUSADO,
    Cenario.EXPIRAR: StatusPagamento.EXPIRADO,
    Cenario.AUTORIZAR: StatusPagamento.AUTORIZADO,
}


class _RelogioUTC:
    def agora(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class _FalhaProgramada:
    falha: Falha
    restantes: int
    operacao: Operacao | None


@dataclass(slots=True)
class _Pagamento:
    id_externo: str
    tenant_id: TenantId
    forma: FormaPagamento
    valor: Centavos
    parcelas_max: int
    cenario: Cenario
    expira_em: datetime
    atualizado_em: datetime
    status: StatusPagamento = StatusPagamento.CRIADO
    valor_estornado: int = 0
    parcelas: int | None = None
    resolvido: bool = False


@dataclass(frozen=True, slots=True)
class _RegistroIdempotente:
    operacao: Operacao
    conteudo: object
    resultado: object


@dataclass(slots=True)
class _Estado:
    pagamentos: dict[tuple[TenantId, str], _Pagamento] = field(default_factory=dict)
    por_chave_criacao: dict[tuple[TenantId, ChaveIdempotencia], str] = field(default_factory=dict)
    idempotencia: dict[tuple[TenantId, ChaveIdempotencia], _RegistroIdempotente] = field(default_factory=dict)
    cobrancas_emitidas: dict[TenantId, int] = field(default_factory=dict)
    falhas: list[_FalhaProgramada] = field(default_factory=list)
    programados: dict[tuple[TenantId, ChaveIdempotencia], Cenario] = field(default_factory=dict)
    sequencia: int = 0


class FakeGatewayPagamento:
    """Implementa `GatewayPagamento` em memória; cada instância tem seu próprio estado (sem global)."""

    def __init__(
        self,
        *,
        relogio: Relogio | None = None,
        cenario_padrao: Cenario = Cenario.AGUARDAR,
        segredos_webhook: Mapping[str, Sequence[str]] | None = None,
        latencia_s: float = 0.0,
    ) -> None:
        self.nome = "fake"
        self._relogio = relogio or _RelogioUTC()
        self._cenario_padrao = cenario_padrao
        self._segredos = {tenant: tuple(lista) for tenant, lista in (segredos_webhook or {}).items()}
        self._latencia_s = latencia_s
        self._estado = _Estado()

    # --- controle de teste ---------------------------------------------------
    def programar(self, tenant_id: TenantId, chave: ChaveIdempotencia, cenario: Cenario) -> None:
        """Define o cenário da cobrança que for criada com esta chave (vale mais que os centavos)."""
        self._estado.programados[(tenant_id, chave)] = cenario

    def injetar_falha(self, falha: Falha, *, vezes: int = 1, operacao: Operacao | None = None) -> None:
        """As próximas `vezes` chamadas (de `operacao`, ou de qualquer uma) falham com `falha`."""
        if vezes < 1:
            raise ValueError("vezes deve ser >= 1")
        self._estado.falhas.append(_FalhaProgramada(falha, vezes, operacao))

    def cobrancas_emitidas(self, tenant_id: TenantId) -> int:
        """Quantas cobranças/checkouts foram de fato criados no tenant (detecta cobrança dupla)."""
        return self._estado.cobrancas_emitidas.get(tenant_id, 0)

    def simular_evento(
        self,
        tenant_id: TenantId,
        pagamento_id_externo: str,
        status: StatusPagamento,
        *,
        parcelas: int | None = None,
    ) -> tuple[dict[str, str], bytes]:
        """Aplica a transição (validada pela §3) e devolve o webhook assinado correspondente.

        Raises:
            PagamentoNaoEncontrado: pagamento inexistente no tenant.
            ErroPermanente: transição proibida pela máquina de estados.
        """
        pagamento = self._obter(tenant_id, pagamento_id_externo)
        self._atualizar_por_tempo(pagamento)
        pagamento.resolvido = True  # evento explícito substitui o cenário automático
        self._transitar(pagamento, status)
        if status in {StatusPagamento.APROVADO, StatusPagamento.AUTORIZADO} and pagamento.parcelas is None:
            pagamento.parcelas = self._parcelas_aprovadas(pagamento, parcelas)
        return self.assinar_evento(tenant_id, pagamento_id_externo, status)

    def assinar_evento(
        self,
        tenant_id: TenantId,
        pagamento_id_externo: str,
        status_informado: StatusPagamento | None,
        *,
        evento_id: str | None = None,
        assinado_em: datetime | None = None,
    ) -> tuple[dict[str, str], bytes]:
        """Monta um webhook assinado (HMAC-SHA256) **sem** mudar estado (útil para eventos fora de ordem)."""
        agora = assinado_em or self._relogio.agora()
        corpo = json.dumps(
            {
                "id": evento_id or self._novo_id("evt"),
                "tipo": _TIPO_EVENTO,
                "pagamento_id": pagamento_id_externo,
                "status": None if status_informado is None else status_informado.value,
                "ocorrido_em": agora.isoformat(),
            },
            sort_keys=True,
        ).encode()
        carimbo = int(agora.timestamp())
        assinatura = self._assinar(self._segredos_de(tenant_id)[0], carimbo, corpo)
        return {CABECALHO_ASSINATURA: f"t={carimbo},v1={assinatura}"}, corpo

    # --- porta GatewayPagamento -----------------------------------------------
    async def criar_cobranca_pix(self, cmd: NovaCobrancaPix) -> CobrancaPix:
        """Emite uma cobrança PIX simulada (`PENDENTE`)."""
        falha = await self._antes("criar_cobranca_pix")
        anterior = self._repetida("criar_cobranca_pix", cmd.tenant_id, cmd.chave_idempotencia, cmd)
        if anterior is not None:
            return self._depois(falha, _como(anterior, CobrancaPix))
        self._validar_comum(cmd.tenant_id, cmd.valor, cmd.expira_em, cmd.moeda)
        pagamento = self._emitir(
            cmd.tenant_id, cmd.chave_idempotencia, FormaPagamento.PIX, cmd.valor, cmd.expira_em, parcelas_max=1
        )
        resultado = CobrancaPix(
            pagamento_id_externo=pagamento.id_externo,
            status=pagamento.status,
            valor=pagamento.valor,
            copia_e_cola=f"SIMULADO-PIX-{pagamento.id_externo}-NAO-PAGAR",
            qr_code_png_base64=None,
            expira_em=pagamento.expira_em,
        )
        self._lembrar("criar_cobranca_pix", cmd.tenant_id, cmd.chave_idempotencia, cmd, resultado)
        return self._depois(falha, resultado)

    async def criar_checkout_cartao(self, cmd: NovoCheckoutCartao) -> CheckoutCartao:
        """Abre um checkout hospedado simulado (`PENDENTE`)."""
        falha = await self._antes("criar_checkout_cartao")
        anterior = self._repetida("criar_checkout_cartao", cmd.tenant_id, cmd.chave_idempotencia, cmd)
        if anterior is not None:
            return self._depois(falha, _como(anterior, CheckoutCartao))
        self._validar_comum(cmd.tenant_id, cmd.valor, cmd.expira_em, cmd.moeda)
        if cmd.tipo not in {FormaPagamento.CREDITO, FormaPagamento.DEBITO}:
            raise ErroPermanente(f"tipo de cartão inválido: {cmd.tipo!r}")
        maximo = 1 if cmd.tipo is FormaPagamento.DEBITO else PARCELAS_MAX
        if not _e_inteiro(cmd.parcelas_max) or not 1 <= cmd.parcelas_max <= maximo:
            raise ErroPermanente(f"parcelas_max deve estar entre 1 e {maximo} para {cmd.tipo.value}")
        if not cmd.url_retorno.startswith("https://"):
            raise ErroPermanente("url_retorno deve ser https")
        pagamento = self._emitir(
            cmd.tenant_id, cmd.chave_idempotencia, cmd.tipo, cmd.valor, cmd.expira_em, parcelas_max=cmd.parcelas_max
        )
        resultado = CheckoutCartao(
            pagamento_id_externo=pagamento.id_externo,
            status=pagamento.status,
            url_checkout=f"{URL_BASE}/checkout/{pagamento.id_externo}",
            expira_em=pagamento.expira_em,
        )
        self._lembrar("criar_checkout_cartao", cmd.tenant_id, cmd.chave_idempotencia, cmd, resultado)
        return self._depois(falha, resultado)

    async def consultar(self, tenant_id: TenantId, pagamento_id_externo: str) -> SituacaoPagamento:
        """Situação atual; aplica o cenário pendente e a expiração pelo relógio."""
        falha = await self._antes("consultar")
        pagamento = self._obter(tenant_id, pagamento_id_externo)
        return self._depois(falha, self._situacao(pagamento))

    async def buscar_por_idempotencia(self, tenant_id: TenantId, chave: ChaveIdempotencia) -> SituacaoPagamento | None:
        """Situação do pagamento criado com a chave, ou `None` se nenhum foi criado."""
        falha = await self._antes("buscar_por_idempotencia")
        pagamento_id = self._estado.por_chave_criacao.get((tenant_id, chave))
        resultado = None if pagamento_id is None else self._situacao(self._obter(tenant_id, pagamento_id))
        return self._depois(falha, resultado)

    async def cancelar(
        self, tenant_id: TenantId, pagamento_id_externo: str, chave: ChaveIdempotencia
    ) -> SituacaoPagamento:
        """Cancela (void) uma cobrança `PENDENTE`/`AUTORIZADO`; outra situação → `ErroPermanente`."""
        falha = await self._antes("cancelar")
        anterior = self._repetida("cancelar", tenant_id, chave, pagamento_id_externo)
        if anterior is not None:
            return self._depois(falha, _como(anterior, SituacaoPagamento))
        pagamento = self._obter(tenant_id, pagamento_id_externo)
        self._atualizar(pagamento)
        self._transitar(pagamento, StatusPagamento.CANCELADO)
        resultado = self._situacao(pagamento)
        self._lembrar("cancelar", tenant_id, chave, pagamento_id_externo, resultado)
        return self._depois(falha, resultado)

    async def estornar(self, cmd: SolicitacaoEstorno) -> ResultadoEstorno:
        """Estorno total (`valor=None`) ou parcial de um pagamento aprovado."""
        falha = await self._antes("estornar")
        anterior = self._repetida("estornar", cmd.tenant_id, cmd.chave_idempotencia, cmd)
        if anterior is not None:
            return self._depois(falha, _como(anterior, ResultadoEstorno))
        if not cmd.motivo.strip() or not cmd.solicitado_por.strip():
            raise ErroPermanente("estorno exige motivo e solicitante (auditoria)")
        pagamento = self._obter(cmd.tenant_id, cmd.pagamento_id_externo)
        self._atualizar(pagamento)
        if pagamento.status not in {StatusPagamento.APROVADO, StatusPagamento.ESTORNADO_PARCIAL}:
            raise ErroPermanente(f"não é possível estornar um pagamento {pagamento.status.value}")
        restante = pagamento.valor - pagamento.valor_estornado
        valor = restante if cmd.valor is None else cmd.valor
        if not _e_inteiro(valor) or not 0 < valor <= restante:
            raise ErroPermanente(f"valor de estorno inválido ({valor}); restante: {restante} centavos")
        novo = StatusPagamento.ESTORNADO if valor == restante else StatusPagamento.ESTORNADO_PARCIAL
        self._transitar(pagamento, novo)
        pagamento.valor_estornado += valor
        resultado = ResultadoEstorno(
            estorno_id_externo=self._novo_id("est"),
            status=novo,
            valor_estornado=Centavos(valor),
        )
        self._lembrar("estornar", cmd.tenant_id, cmd.chave_idempotencia, cmd, resultado)
        return self._depois(falha, resultado)

    async def processar_webhook(
        self, tenant_id: TenantId, cabecalhos: Mapping[str, str], corpo: bytes
    ) -> EventoPagamento:
        """Verifica a assinatura (HMAC, janela de 5 min, segredos em rotação) e normaliza; não altera estado."""
        self._verificar_assinatura(tenant_id, cabecalhos, corpo)
        try:
            dados = json.loads(corpo)
            status_bruto = dados.get("status")
            status = StatusPagamento(status_bruto) if status_bruto in {s.value for s in StatusPagamento} else None
            return EventoPagamento(
                evento_id=str(dados["id"]),
                pagamento_id_externo=str(dados["pagamento_id"]),
                tipo=str(dados["tipo"]),
                status_informado=status,
                ocorrido_em=datetime.fromisoformat(str(dados["ocorrido_em"])),
            )
        except (ValueError, KeyError, TypeError, AttributeError) as erro:
            raise ErroPermanente("corpo do webhook malformado") from erro

    # --- internos ---------------------------------------------------------------
    async def _antes(self, operacao: Operacao) -> Falha | None:
        if self._latencia_s > 0:
            await asyncio.sleep(self._latencia_s)
        for programada in self._estado.falhas:
            if programada.operacao in (None, operacao):
                programada.restantes -= 1
                if programada.restantes == 0:
                    self._estado.falhas.remove(programada)
                if programada.falha is Falha.TRANSITORIA:
                    raise ErroTransitorio(f"falha transitória simulada em {operacao}")
                if programada.falha is Falha.PERMANENTE:
                    raise ErroPermanente(f"falha permanente simulada em {operacao}")
                return programada.falha
        return None

    @staticmethod
    def _depois[T](falha: Falha | None, resultado: T) -> T:
        if falha is Falha.TIMEOUT_AMBIGUO:
            raise ErroTransitorio("timeout simulado: a operação pode ter sido concluída")
        return resultado

    def _repetida(
        self, operacao: Operacao, tenant_id: TenantId, chave: ChaveIdempotencia, conteudo: object
    ) -> object | None:
        if not chave:
            raise ErroPermanente("chave de idempotência obrigatória")
        registro = self._estado.idempotencia.get((tenant_id, chave))
        if registro is None:
            return None
        if registro.operacao != operacao or registro.conteudo != conteudo:
            raise ErroPermanente("chave de idempotência reutilizada com outro conteúdo")
        return registro.resultado

    def _lembrar(
        self, operacao: Operacao, tenant_id: TenantId, chave: ChaveIdempotencia, conteudo: object, resultado: object
    ) -> None:
        self._estado.idempotencia[(tenant_id, chave)] = _RegistroIdempotente(operacao, conteudo, resultado)

    @staticmethod
    def _validar_comum(tenant_id: TenantId, valor: Centavos, expira_em: timedelta, moeda: str) -> None:
        if not tenant_id:
            raise ErroPermanente("tenant_id obrigatório")
        if not _e_inteiro(valor) or valor <= 0:
            raise ErroPermanente(f"valor deve ser centavos inteiros > 0, recebi {valor!r}")
        if expira_em <= timedelta(0):
            raise ErroPermanente("expira_em deve ser positivo")
        if moeda != "BRL":
            raise ErroPermanente(f"moeda não suportada: {moeda!r}")

    def _emitir(
        self,
        tenant_id: TenantId,
        chave: ChaveIdempotencia,
        forma: FormaPagamento,
        valor: Centavos,
        expira_em: timedelta,
        *,
        parcelas_max: int,
    ) -> _Pagamento:
        agora = self._relogio.agora()
        cenario = self._estado.programados.get(
            (tenant_id, chave), CENARIO_POR_CENTAVOS.get(valor % 100, self._cenario_padrao)
        )
        prefixo = "pix" if forma is FormaPagamento.PIX else "cartao"
        pagamento = _Pagamento(
            id_externo=self._novo_id(prefixo),
            tenant_id=tenant_id,
            forma=forma,
            valor=valor,
            parcelas_max=parcelas_max,
            cenario=cenario,
            expira_em=agora + expira_em,
            atualizado_em=agora,
        )
        self._transitar(pagamento, StatusPagamento.PENDENTE)
        self._estado.pagamentos[(tenant_id, pagamento.id_externo)] = pagamento
        self._estado.por_chave_criacao[(tenant_id, chave)] = pagamento.id_externo
        self._estado.cobrancas_emitidas[tenant_id] = self._estado.cobrancas_emitidas.get(tenant_id, 0) + 1
        return pagamento

    def _obter(self, tenant_id: TenantId, pagamento_id_externo: str) -> _Pagamento:
        pagamento = self._estado.pagamentos.get((tenant_id, pagamento_id_externo))
        if pagamento is None:
            raise PagamentoNaoEncontrado(f"pagamento {pagamento_id_externo!r} não encontrado no tenant")
        return pagamento

    def _atualizar_por_tempo(self, pagamento: _Pagamento) -> None:
        if pagamento.status is StatusPagamento.PENDENTE and self._relogio.agora() >= pagamento.expira_em:
            self._transitar(pagamento, StatusPagamento.EXPIRADO)

    def _atualizar(self, pagamento: _Pagamento) -> None:
        """Expiração pelo relógio e, na primeira leitura, o cenário programado."""
        self._atualizar_por_tempo(pagamento)
        if pagamento.resolvido:
            return
        pagamento.resolvido = True
        destino = _DESTINO_CENARIO.get(pagamento.cenario)
        if destino is not None and pagamento.status is StatusPagamento.PENDENTE:
            self._transitar(pagamento, destino)
            if destino in {StatusPagamento.APROVADO, StatusPagamento.AUTORIZADO}:
                pagamento.parcelas = self._parcelas_aprovadas(pagamento, None)

    def _situacao(self, pagamento: _Pagamento) -> SituacaoPagamento:
        self._atualizar(pagamento)
        return SituacaoPagamento(
            pagamento_id_externo=pagamento.id_externo,
            status=pagamento.status,
            valor=pagamento.valor,
            valor_estornado=Centavos(pagamento.valor_estornado),
            parcelas=pagamento.parcelas,
            atualizado_em=pagamento.atualizado_em,
        )

    def _transitar(self, pagamento: _Pagamento, novo: StatusPagamento) -> None:
        if not transicao_permitida(pagamento.status, novo):
            raise ErroPermanente(f"transição proibida: {pagamento.status.value} → {novo.value}")
        pagamento.status = novo
        pagamento.atualizado_em = self._relogio.agora()

    @staticmethod
    def _parcelas_aprovadas(pagamento: _Pagamento, parcelas: int | None) -> int:
        if parcelas is None:
            return pagamento.parcelas_max if pagamento.forma is FormaPagamento.CREDITO else 1
        if not 1 <= parcelas <= pagamento.parcelas_max:
            raise ErroPermanente(f"parcelas deve estar entre 1 e {pagamento.parcelas_max}")
        return parcelas

    def _novo_id(self, prefixo: str) -> str:
        self._estado.sequencia += 1
        return f"fake-{prefixo}-{self._estado.sequencia:06d}"

    def _segredos_de(self, tenant_id: str) -> tuple[str, ...]:
        return self._segredos.get(tenant_id) or (f"segredo-simulado-{tenant_id}",)

    @staticmethod
    def _assinar(segredo: str, carimbo: int, corpo: bytes) -> str:
        return hmac.new(segredo.encode(), f"{carimbo}.".encode() + corpo, hashlib.sha256).hexdigest()

    def _verificar_assinatura(self, tenant_id: TenantId, cabecalhos: Mapping[str, str], corpo: bytes) -> None:
        bruto = next((valor for nome, valor in cabecalhos.items() if nome.lower() == CABECALHO_ASSINATURA), None)
        if bruto is None:
            raise AssinaturaInvalida("cabeçalho de assinatura ausente")
        partes = dict(item.split("=", 1) for item in bruto.split(",") if "=" in item)
        try:
            carimbo = int(partes["t"])
            recebida = partes["v1"]
        except (KeyError, ValueError) as erro:
            raise AssinaturaInvalida("cabeçalho de assinatura malformado") from erro
        assinado_em = datetime.fromtimestamp(carimbo, UTC)
        if abs(self._relogio.agora() - assinado_em) > TOLERANCIA_ASSINATURA:
            raise AssinaturaInvalida("assinatura fora da janela de tempo (replay)")
        if not any(
            hmac.compare_digest(self._assinar(segredo, carimbo, corpo), recebida)
            for segredo in self._segredos_de(tenant_id)
        ):
            raise AssinaturaInvalida("assinatura não confere")


def _e_inteiro(valor: object) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _como[T](valor: object, tipo: type[T]) -> T:
    if not isinstance(valor, tipo):  # pragma: no cover - garantido pelo registro de idempotência
        raise TypeError(f"resultado idempotente inesperado: {type(valor).__name__}")
    return valor

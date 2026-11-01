"""Contrato da porta `GatewayPagamento` (`docs/pagamento.md` §2, §3, §5 e §8), rodado contra cada gateway registrado."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import replace

import pytest

from emporio.adaptadores.pagamento import FakeGatewayPagamento
from emporio.portas.pagamento import (
    AssinaturaInvalida,
    CobrancaPix,
    ErroPermanente,
    ErroTransitorio,
    FormaPagamento,
    NovaCobrancaPix,
    PagamentoNaoEncontrado,
    StatusPagamento,
    TenantId,
    transicao_permitida,
)
from tests.contrato.pagamento.fabricas import ControleFake
from tests.contrato.pagamento.kit import (
    AGORA,
    EXPIRACAO,
    TENANT_A,
    TENANT_B,
    VALOR,
    KitGateway,
    RelogioFixo,
    chave,
    com_retry,
    estorno,
    nova_pix,
    novo_cartao,
    verificar_idempotencia_criacao,
)

S = StatusPagamento


async def _pix_pendente(kit: KitGateway, sufixo: str = "1001:pix:1") -> CobrancaPix:
    return await kit.gateway.criar_cobranca_pix(nova_pix(sufixo))


async def _aprovado(kit: KitGateway, sufixo: str = "1001:pix:1") -> str:
    cobranca = await _pix_pendente(kit, sufixo)
    await kit.controle.levar_a(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)
    return cobranca.pagamento_id_externo


# --- emissão ----------------------------------------------------------------------
def test_gateway_tem_nome(kit: KitGateway) -> None:
    assert isinstance(kit.gateway.nome, str)
    assert kit.gateway.nome


async def test_pix_nasce_pendente_com_valor_e_expiracao(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)

    assert cobranca.status is S.PENDENTE
    assert cobranca.valor == VALOR
    assert cobranca.copia_e_cola
    assert cobranca.expira_em.tzinfo is not None
    assert cobranca.expira_em == AGORA + EXPIRACAO
    situacao = await kit.gateway.consultar(TENANT_A, cobranca.pagamento_id_externo)
    assert (situacao.status, situacao.valor, situacao.valor_estornado) == (S.PENDENTE, VALOR, 0)


@pytest.mark.parametrize(
    ("tipo", "parcelas_max"), [(FormaPagamento.CREDITO, 12), (FormaPagamento.CREDITO, 1), (FormaPagamento.DEBITO, 1)]
)
async def test_checkout_cartao_nasce_pendente_em_pagina_hospedada(
    kit: KitGateway, tipo: FormaPagamento, parcelas_max: int
) -> None:
    checkout = await kit.gateway.criar_checkout_cartao(
        novo_cartao(f"1001:{tipo.value}:{parcelas_max}", tipo=tipo, parcelas_max=parcelas_max)  # type: ignore[arg-type]
    )

    assert checkout.status is S.PENDENTE
    assert checkout.url_checkout.startswith("https://")
    assert checkout.expira_em.tzinfo is not None


@pytest.mark.parametrize("valor", [0, -100])
async def test_valor_nao_positivo_e_recusado_sem_cobrar(kit: KitGateway, valor: int) -> None:
    with pytest.raises(ErroPermanente):
        await kit.gateway.criar_cobranca_pix(nova_pix(valor=valor))
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 0


@pytest.mark.parametrize(
    ("tipo", "parcelas_max"), [(FormaPagamento.CREDITO, 0), (FormaPagamento.CREDITO, 13), (FormaPagamento.DEBITO, 2)]
)
async def test_parcelas_fora_da_faixa_sao_recusadas(kit: KitGateway, tipo: FormaPagamento, parcelas_max: int) -> None:
    with pytest.raises(ErroPermanente):
        await kit.gateway.criar_checkout_cartao(novo_cartao(tipo=tipo, parcelas_max=parcelas_max))  # type: ignore[arg-type]
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 0


# --- idempotência -----------------------------------------------------------------
async def test_mesma_chave_devolve_a_mesma_cobranca(kit: KitGateway) -> None:
    await verificar_idempotencia_criacao(kit)


async def test_mesma_chave_no_checkout_devolve_o_mesmo_checkout(kit: KitGateway) -> None:
    cmd = novo_cartao()
    assert await kit.gateway.criar_checkout_cartao(cmd) == await kit.gateway.criar_checkout_cartao(cmd)
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1


async def test_mesma_chave_com_outro_conteudo_e_erro_permanente(kit: KitGateway) -> None:
    cmd = nova_pix()
    await kit.gateway.criar_cobranca_pix(cmd)

    with pytest.raises(ErroPermanente):
        await kit.gateway.criar_cobranca_pix(replace(cmd, valor=cmd.valor + 100))
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1


async def test_chamadas_concorrentes_com_a_mesma_chave_cobram_uma_vez(kit: KitGateway) -> None:
    cmd = nova_pix()
    resultados = await asyncio.gather(*(kit.gateway.criar_cobranca_pix(cmd) for _ in range(5)))

    assert len({r.pagamento_id_externo for r in resultados}) == 1
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1


async def test_nova_tentativa_do_cliente_usa_outra_chave_e_gera_outra_cobranca(kit: KitGateway) -> None:
    primeira = await _pix_pendente(kit, "1001:pix:1")
    segunda = await _pix_pendente(kit, "1001:pix:2")

    assert primeira.pagamento_id_externo != segunda.pagamento_id_externo
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 2


async def test_buscar_por_idempotencia(kit: KitGateway) -> None:
    assert await kit.gateway.buscar_por_idempotencia(TENANT_A, chave("nada:pix:1")) is None
    cobranca = await _pix_pendente(kit)

    situacao = await kit.gateway.buscar_por_idempotencia(TENANT_A, chave("1001:pix:1"))
    assert situacao is not None
    assert situacao.pagamento_id_externo == cobranca.pagamento_id_externo
    assert situacao.status is S.PENDENTE


# --- resiliência (§5) -------------------------------------------------------------
async def test_falha_transitoria_com_retry_e_mesma_chave_cobra_uma_vez(kit_com_falhas: KitGateway) -> None:
    kit = kit_com_falhas
    kit.controle.injetar_falha("transitoria", "criar_cobranca_pix", vezes=2)
    esperas: list[float] = []

    cobranca = await com_retry(lambda: kit.gateway.criar_cobranca_pix(nova_pix()), esperas=esperas)

    assert cobranca.status is S.PENDENTE
    assert esperas == [1.0, 2.0]
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1


async def test_retry_desiste_apos_tres_tentativas_sem_cobrar(kit_com_falhas: KitGateway) -> None:
    kit = kit_com_falhas
    kit.controle.injetar_falha("transitoria", "criar_cobranca_pix", vezes=3)
    esperas: list[float] = []

    with pytest.raises(ErroTransitorio):
        await com_retry(lambda: kit.gateway.criar_cobranca_pix(nova_pix()), esperas=esperas)
    assert esperas == [1.0, 2.0]
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 0
    assert await kit.gateway.buscar_por_idempotencia(TENANT_A, chave("1001:pix:1")) is None


async def test_falha_permanente_nao_e_repetida(kit_com_falhas: KitGateway) -> None:
    kit = kit_com_falhas
    kit.controle.injetar_falha("permanente", "criar_cobranca_pix")
    esperas: list[float] = []

    with pytest.raises(ErroPermanente):
        await com_retry(lambda: kit.gateway.criar_cobranca_pix(nova_pix()), esperas=esperas)
    assert esperas == []


async def test_timeout_ambiguo_reconcilia_pela_chave_sem_cobranca_dupla(kit_com_falhas: KitGateway) -> None:
    kit = kit_com_falhas
    kit.controle.injetar_falha("timeout_ambiguo", "criar_cobranca_pix")

    with pytest.raises(ErroTransitorio):
        await kit.gateway.criar_cobranca_pix(nova_pix())
    reconciliada = await kit.gateway.buscar_por_idempotencia(TENANT_A, chave("1001:pix:1"))
    assert reconciliada is not None
    assert reconciliada.status is S.PENDENTE

    repetida = await kit.gateway.criar_cobranca_pix(nova_pix())
    assert repetida.pagamento_id_externo == reconciliada.pagamento_id_externo
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1


async def test_timeout_ambiguo_no_estorno_nao_estorna_duas_vezes(kit_com_falhas: KitGateway) -> None:
    kit = kit_com_falhas
    pagamento_id = await _aprovado(kit)
    kit.controle.injetar_falha("timeout_ambiguo", "estornar")
    cmd = estorno(pagamento_id, "1001:estorno:1", valor=10_000)

    with pytest.raises(ErroTransitorio):
        await kit.gateway.estornar(cmd)
    resultado = await kit.gateway.estornar(cmd)

    assert resultado.valor_estornado == 10_000
    assert (await kit.gateway.consultar(TENANT_A, pagamento_id)).valor_estornado == 10_000


async def test_leitura_com_falha_transitoria_pode_ser_repetida(kit_com_falhas: KitGateway) -> None:
    kit = kit_com_falhas
    cobranca = await _pix_pendente(kit)
    kit.controle.injetar_falha("transitoria", "consultar")
    esperas: list[float] = []

    situacao = await com_retry(lambda: kit.gateway.consultar(TENANT_A, cobranca.pagamento_id_externo), esperas=esperas)

    assert situacao.status is S.PENDENTE
    assert esperas == [1.0]


# --- multi-tenant (§4) ------------------------------------------------------------
async def test_pagamento_de_um_tenant_nao_existe_no_outro(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)

    with pytest.raises(PagamentoNaoEncontrado):
        await kit.gateway.consultar(TENANT_B, cobranca.pagamento_id_externo)
    assert await kit.gateway.buscar_por_idempotencia(TENANT_B, chave("1001:pix:1")) is None
    with pytest.raises(PagamentoNaoEncontrado):
        await kit.gateway.cancelar(TENANT_B, cobranca.pagamento_id_externo, chave("x:cancelar", TENANT_B))


async def test_mesma_chave_em_tenants_diferentes_sao_cobrancas_distintas(kit: KitGateway) -> None:
    sufixo = "1001:pix:1"
    a = await kit.gateway.criar_cobranca_pix(nova_pix(sufixo, tenant=TENANT_A))
    b = await kit.gateway.criar_cobranca_pix(
        replace(nova_pix(sufixo, tenant=TENANT_B), chave_idempotencia=chave(sufixo, TENANT_A))
    )

    assert a.pagamento_id_externo != b.pagamento_id_externo
    assert await kit.controle.cobrancas_emitidas(TENANT_A) == 1
    assert await kit.controle.cobrancas_emitidas(TENANT_B) == 1


async def test_consultar_inexistente(kit: KitGateway) -> None:
    with pytest.raises(PagamentoNaoEncontrado):
        await kit.gateway.consultar(TENANT_A, "nao-existe")


# --- máquina de estados (§3) ------------------------------------------------------
CAMINHOS = [
    pytest.param([S.APROVADO], id="pendente-aprovado"),
    pytest.param([S.AUTORIZADO, S.APROVADO], id="autorizado-captura"),
    pytest.param([S.AUTORIZADO, S.CANCELADO], id="autorizado-void"),
    pytest.param([S.RECUSADO], id="recusado"),
    pytest.param([S.EXPIRADO], id="expirado"),
    pytest.param([S.APROVADO, S.CONTESTADO, S.APROVADO], id="disputa-ganha"),
    pytest.param([S.APROVADO, S.CONTESTADO, S.ESTORNADO], id="disputa-perdida"),
]


@pytest.mark.parametrize("caminho", CAMINHOS)
async def test_caminhos_da_maquina_de_estados(kit: KitGateway, caminho: list[StatusPagamento]) -> None:
    checkout = await kit.gateway.criar_checkout_cartao(novo_cartao())
    observados = [checkout.status]

    for status in caminho:
        await kit.controle.levar_a(TENANT_A, checkout.pagamento_id_externo, status)
        observados.append((await kit.gateway.consultar(TENANT_A, checkout.pagamento_id_externo)).status)

    assert observados == [S.PENDENTE, *caminho]
    assert all(transicao_permitida(de, para) for de, para in itertools.pairwise(observados))


async def test_aprovado_informa_parcelas(kit: KitGateway) -> None:
    checkout = await kit.gateway.criar_checkout_cartao(novo_cartao(parcelas_max=10))
    await kit.controle.levar_a(TENANT_A, checkout.pagamento_id_externo, S.APROVADO)

    parcelas = (await kit.gateway.consultar(TENANT_A, checkout.pagamento_id_externo)).parcelas
    assert parcelas is not None
    assert 1 <= parcelas <= 10


@pytest.mark.parametrize("terminal", [S.RECUSADO, S.EXPIRADO])
async def test_estado_terminal_nao_volta(kit: KitGateway, terminal: StatusPagamento) -> None:
    cobranca = await _pix_pendente(kit)
    await kit.controle.levar_a(TENANT_A, cobranca.pagamento_id_externo, terminal)

    for _ in range(2):
        assert (await kit.gateway.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is terminal
    with pytest.raises(ErroPermanente):
        await kit.gateway.cancelar(TENANT_A, cobranca.pagamento_id_externo, chave("1001:cancelar:1"))


# --- cancelamento -----------------------------------------------------------------
async def test_cancelar_pendente_e_idempotente(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)
    chave_cancelamento = chave("1001:cancelar:1")

    primeira = await kit.gateway.cancelar(TENANT_A, cobranca.pagamento_id_externo, chave_cancelamento)
    segunda = await kit.gateway.cancelar(TENANT_A, cobranca.pagamento_id_externo, chave_cancelamento)

    assert primeira.status is S.CANCELADO
    assert segunda == primeira
    assert (await kit.gateway.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.CANCELADO


async def test_cancelar_aprovado_e_proibido(kit: KitGateway) -> None:
    pagamento_id = await _aprovado(kit)

    with pytest.raises(ErroPermanente):
        await kit.gateway.cancelar(TENANT_A, pagamento_id, chave("1001:cancelar:1"))
    assert (await kit.gateway.consultar(TENANT_A, pagamento_id)).status is S.APROVADO


# --- estorno ----------------------------------------------------------------------
async def test_estorno_parcial_e_depois_o_restante(kit: KitGateway) -> None:
    pagamento_id = await _aprovado(kit)

    parcial = await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:1", valor=30_000))
    assert (parcial.status, parcial.valor_estornado) == (S.ESTORNADO_PARCIAL, 30_000)
    outro = await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:2", valor=20_000))
    assert outro.status is S.ESTORNADO_PARCIAL
    situacao = await kit.gateway.consultar(TENANT_A, pagamento_id)
    assert (situacao.status, situacao.valor_estornado) == (S.ESTORNADO_PARCIAL, 50_000)

    total = await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:3"))
    assert (total.status, total.valor_estornado) == (S.ESTORNADO, VALOR - 50_000)
    situacao = await kit.gateway.consultar(TENANT_A, pagamento_id)
    assert (situacao.status, situacao.valor_estornado) == (S.ESTORNADO, VALOR)


async def test_estorno_total_direto(kit: KitGateway) -> None:
    pagamento_id = await _aprovado(kit)

    resultado = await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:1"))

    assert (resultado.status, resultado.valor_estornado) == (S.ESTORNADO, VALOR)


async def test_estorno_repetido_com_a_mesma_chave_nao_dobra(kit: KitGateway) -> None:
    pagamento_id = await _aprovado(kit)
    cmd = estorno(pagamento_id, "1001:estorno:1", valor=10_000)

    primeiro = await kit.gateway.estornar(cmd)
    segundo = await kit.gateway.estornar(cmd)

    assert segundo == primeiro
    assert (await kit.gateway.consultar(TENANT_A, pagamento_id)).valor_estornado == 10_000


@pytest.mark.parametrize("valor", [0, VALOR + 1])
async def test_estorno_com_valor_invalido_e_recusado(kit: KitGateway, valor: int) -> None:
    pagamento_id = await _aprovado(kit)

    with pytest.raises(ErroPermanente):
        await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:1", valor=valor))
    assert (await kit.gateway.consultar(TENANT_A, pagamento_id)).valor_estornado == 0


async def test_estorno_de_pagamento_nao_aprovado_e_recusado(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)

    with pytest.raises(ErroPermanente):
        await kit.gateway.estornar(estorno(cobranca.pagamento_id_externo, "1001:estorno:1"))


async def test_estorno_depois_de_estornado_e_recusado(kit: KitGateway) -> None:
    pagamento_id = await _aprovado(kit)
    await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:1"))

    with pytest.raises(ErroPermanente):
        await kit.gateway.estornar(estorno(pagamento_id, "1001:estorno:2", valor=1))


# --- webhook (§5) -----------------------------------------------------------------
async def test_webhook_valido_e_normalizado_sem_alterar_estado(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)
    cabecalhos, corpo = await kit.controle.webhook(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)

    evento = await kit.gateway.processar_webhook(TENANT_A, cabecalhos, corpo)

    assert evento.pagamento_id_externo == cobranca.pagamento_id_externo
    assert evento.status_informado is S.APROVADO
    assert evento.evento_id
    assert evento.ocorrido_em.tzinfo is not None
    assert (await kit.gateway.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.PENDENTE


async def test_webhook_fora_de_ordem_nao_regride_estado(kit: KitGateway) -> None:
    pagamento_id = await _aprovado(kit)
    cabecalhos, corpo = await kit.controle.webhook(TENANT_A, pagamento_id, S.PENDENTE)

    evento = await kit.gateway.processar_webhook(TENANT_A, cabecalhos, corpo)

    assert evento.status_informado is S.PENDENTE
    assert (await kit.gateway.consultar(TENANT_A, pagamento_id)).status is S.APROVADO


async def test_webhook_com_corpo_adulterado_e_rejeitado(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)
    cabecalhos, corpo = await kit.controle.webhook(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)

    with pytest.raises(AssinaturaInvalida):
        await kit.gateway.processar_webhook(TENANT_A, cabecalhos, corpo.replace(b"aprovado", b"estornado"))


async def test_webhook_sem_assinatura_e_rejeitado(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)
    _, corpo = await kit.controle.webhook(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)

    with pytest.raises(AssinaturaInvalida):
        await kit.gateway.processar_webhook(TENANT_A, {}, corpo)


async def test_webhook_assinado_por_outro_tenant_e_rejeitado(kit: KitGateway) -> None:
    cobranca = await _pix_pendente(kit)
    cabecalhos, corpo = await kit.controle.webhook(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)

    with pytest.raises(AssinaturaInvalida):
        await kit.gateway.processar_webhook(TenantId(TENANT_B), cabecalhos, corpo)


# --- prova de que a suíte detecta violações ----------------------------------------
class _GatewaySemIdempotencia(FakeGatewayPagamento):
    """Gateway quebrado: ignora a chave e emite uma cobrança nova a cada chamada (não está em GATEWAYS)."""

    contador = 0

    async def criar_cobranca_pix(self, cmd: NovaCobrancaPix) -> CobrancaPix:
        self.contador += 1
        return await super().criar_cobranca_pix(replace(cmd, chave_idempotencia=chave(f"quebrado:{self.contador}")))


async def test_suite_detecta_gateway_sem_idempotencia() -> None:
    relogio = RelogioFixo()
    quebrado = _GatewaySemIdempotencia(relogio=relogio)
    kit = KitGateway(gateway=quebrado, controle=ControleFake(quebrado), relogio=relogio)

    with pytest.raises(AssertionError):
        await verificar_idempotencia_criacao(kit)

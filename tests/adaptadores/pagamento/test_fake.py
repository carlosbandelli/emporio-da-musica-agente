"""Comportamento próprio do `FakeGatewayPagamento` (o contrato comum está em `tests/contrato/pagamento`)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from emporio.adaptadores.pagamento import CABECALHO_ASSINATURA, Cenario, FakeGatewayPagamento, Falha
from emporio.portas.pagamento import (
    AssinaturaInvalida,
    ChaveIdempotencia,
    ErroPermanente,
    ErroTransitorio,
    StatusPagamento,
)
from tests.contrato.pagamento.kit import TENANT_A, TENANT_B, RelogioFixo, chave, nova_pix, novo_cartao

S = StatusPagamento


@pytest.fixture
def relogio() -> RelogioFixo:
    return RelogioFixo()


@pytest.fixture
def fake(relogio: RelogioFixo) -> FakeGatewayPagamento:
    return FakeGatewayPagamento(relogio=relogio)


async def test_respostas_sao_marcadas_como_simulacao(fake: FakeGatewayPagamento) -> None:
    pix = await fake.criar_cobranca_pix(nova_pix())
    cartao = await fake.criar_checkout_cartao(novo_cartao())

    assert pix.copia_e_cola.startswith("SIMULADO-")
    assert cartao.url_checkout.startswith("https://pagamento.simulado.local/")
    assert fake.nome == "fake"


@pytest.mark.parametrize(
    ("centavos", "esperado"),
    [(91, S.APROVADO), (92, S.RECUSADO), (93, S.EXPIRADO), (94, S.AUTORIZADO), (0, S.PENDENTE), (50, S.PENDENTE)],
)
async def test_cenario_pelos_centavos_na_primeira_consulta(
    fake: FakeGatewayPagamento, centavos: int, esperado: StatusPagamento
) -> None:
    cobranca = await fake.criar_cobranca_pix(nova_pix(valor=100_000 + centavos))

    assert cobranca.status is S.PENDENTE
    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is esperado


async def test_programar_por_chave_vale_mais_que_os_centavos(fake: FakeGatewayPagamento) -> None:
    fake.programar(TENANT_A, chave("1001:pix:1"), Cenario.APROVAR)
    cobranca = await fake.criar_cobranca_pix(nova_pix(valor=100_092))

    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.APROVADO


async def test_cenario_padrao_configuravel(relogio: RelogioFixo) -> None:
    fake = FakeGatewayPagamento(relogio=relogio, cenario_padrao=Cenario.RECUSAR)
    cobranca = await fake.criar_cobranca_pix(nova_pix())

    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.RECUSADO


async def test_pendente_ate_confirmar(fake: FakeGatewayPagamento) -> None:
    cobranca = await fake.criar_cobranca_pix(nova_pix())
    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.PENDENTE

    fake.simular_evento(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)

    situacao = await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)
    assert (situacao.status, situacao.parcelas) == (S.APROVADO, 1)


async def test_expira_pelo_relogio(fake: FakeGatewayPagamento, relogio: RelogioFixo) -> None:
    cobranca = await fake.criar_cobranca_pix(nova_pix())
    relogio.avancar(timedelta(minutes=29))
    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.PENDENTE

    relogio.avancar(timedelta(minutes=1))

    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.EXPIRADO
    with pytest.raises(ErroPermanente):
        fake.simular_evento(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)


async def test_simular_evento_respeita_a_maquina_de_estados(fake: FakeGatewayPagamento) -> None:
    cobranca = await fake.criar_cobranca_pix(nova_pix())
    fake.simular_evento(TENANT_A, cobranca.pagamento_id_externo, S.APROVADO)

    with pytest.raises(ErroPermanente):
        fake.simular_evento(TENANT_A, cobranca.pagamento_id_externo, S.PENDENTE)


async def test_parcelas_do_evento_respeitam_o_maximo(fake: FakeGatewayPagamento) -> None:
    checkout = await fake.criar_checkout_cartao(novo_cartao(parcelas_max=6))
    with pytest.raises(ErroPermanente):
        fake.simular_evento(TENANT_A, checkout.pagamento_id_externo, S.APROVADO, parcelas=7)

    outro = await fake.criar_checkout_cartao(novo_cartao("1001:credito:2", parcelas_max=6))
    fake.simular_evento(TENANT_A, outro.pagamento_id_externo, S.APROVADO, parcelas=3)
    assert (await fake.consultar(TENANT_A, outro.pagamento_id_externo)).parcelas == 3


async def test_ids_deterministicos_e_instancias_independentes(relogio: RelogioFixo) -> None:
    um, outro = FakeGatewayPagamento(relogio=relogio), FakeGatewayPagamento(relogio=relogio)

    a = await um.criar_cobranca_pix(nova_pix())
    b = await outro.criar_cobranca_pix(nova_pix())

    assert a == b
    assert a.pagamento_id_externo == "fake-pix-000001"
    assert um.cobrancas_emitidas(TENANT_A) == outro.cobrancas_emitidas(TENANT_A) == 1


async def test_falha_programada_so_na_operacao_indicada(fake: FakeGatewayPagamento) -> None:
    fake.injetar_falha(Falha.TRANSITORIA, operacao="consultar")
    cobranca = await fake.criar_cobranca_pix(nova_pix())

    with pytest.raises(ErroTransitorio):
        await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)
    assert (await fake.consultar(TENANT_A, cobranca.pagamento_id_externo)).status is S.PENDENTE


def test_injetar_falha_exige_ao_menos_uma_vez(fake: FakeGatewayPagamento) -> None:
    with pytest.raises(ValueError, match="vezes"):
        fake.injetar_falha(Falha.TRANSITORIA, vezes=0)


async def test_latencia_permite_testar_o_timeout_do_chamador(relogio: RelogioFixo) -> None:
    lento = FakeGatewayPagamento(relogio=relogio, latencia_s=5.0)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(lento.criar_cobranca_pix(nova_pix()), timeout=0.01)
    assert lento.cobrancas_emitidas(TENANT_A) == 0


async def test_chave_vazia_e_erro_permanente(fake: FakeGatewayPagamento) -> None:
    with pytest.raises(ErroPermanente):
        await fake.criar_cobranca_pix(replace(nova_pix(), chave_idempotencia=ChaveIdempotencia("")))


async def test_url_de_retorno_precisa_ser_https(fake: FakeGatewayPagamento) -> None:
    with pytest.raises(ErroPermanente):
        await fake.criar_checkout_cartao(replace(novo_cartao(), url_retorno="http://x"))


# --- webhook ------------------------------------------------------------------------
async def test_webhook_aceita_segredo_antigo_durante_rotacao(relogio: RelogioFixo) -> None:
    antigo = FakeGatewayPagamento(relogio=relogio, segredos_webhook={TENANT_A: ("velho",)})
    novo = FakeGatewayPagamento(relogio=relogio, segredos_webhook={TENANT_A: ("novo", "velho")})
    cabecalhos, corpo = antigo.assinar_evento(TENANT_A, "fake-pix-000001", S.APROVADO)

    evento = await novo.processar_webhook(TENANT_A, cabecalhos, corpo)

    assert evento.status_informado is S.APROVADO


async def test_webhook_fora_da_janela_e_replay(fake: FakeGatewayPagamento, relogio: RelogioFixo) -> None:
    cabecalhos, corpo = fake.assinar_evento(TENANT_A, "fake-pix-000001", S.APROVADO)
    relogio.avancar(timedelta(minutes=6))

    with pytest.raises(AssinaturaInvalida, match="janela"):
        await fake.processar_webhook(TENANT_A, cabecalhos, corpo)


async def test_webhook_cabecalho_case_insensitive(fake: FakeGatewayPagamento) -> None:
    cabecalhos, corpo = fake.assinar_evento(TENANT_A, "fake-pix-000001", None)

    evento = await fake.processar_webhook(TENANT_A, {k.upper(): v for k, v in cabecalhos.items()}, corpo)

    assert evento.status_informado is None


async def test_webhook_cabecalho_malformado(fake: FakeGatewayPagamento) -> None:
    with pytest.raises(AssinaturaInvalida):
        await fake.processar_webhook(TENANT_A, {CABECALHO_ASSINATURA: "lixo"}, b"{}")


async def test_webhook_status_desconhecido_vira_none(fake: FakeGatewayPagamento) -> None:
    _, corpo = fake.assinar_evento(TENANT_A, "fake-pix-000001", S.APROVADO)
    dados = json.loads(corpo)
    dados["status"] = "processing"
    adulterado = json.dumps(dados, sort_keys=True).encode()
    cabecalhos = _assinar(fake, adulterado)

    evento = await fake.processar_webhook(TENANT_A, cabecalhos, adulterado)

    assert evento.status_informado is None


async def test_webhook_assinado_mas_malformado_e_erro_permanente(fake: FakeGatewayPagamento) -> None:
    corpo = b'{"id": "evt-1"}'
    with pytest.raises(ErroPermanente):
        await fake.processar_webhook(TENANT_A, _assinar(fake, corpo), corpo)


async def test_segredo_padrao_difere_por_tenant(fake: FakeGatewayPagamento) -> None:
    cabecalhos, corpo = fake.assinar_evento(TENANT_A, "fake-pix-000001", S.APROVADO)

    with pytest.raises(AssinaturaInvalida):
        await fake.processar_webhook(TENANT_B, cabecalhos, corpo)


def _assinar(fake: FakeGatewayPagamento, corpo: bytes) -> dict[str, str]:
    cabecalhos, _ = fake.assinar_evento(TENANT_A, "x", None)
    carimbo = int(cabecalhos[CABECALHO_ASSINATURA].split(",")[0].removeprefix("t="))
    assinatura = fake._assinar(f"segredo-simulado-{TENANT_A}", carimbo, corpo)
    return {CABECALHO_ASSINATURA: f"t={carimbo},v1={assinatura}"}

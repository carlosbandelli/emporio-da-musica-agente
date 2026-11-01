"""`ProvedorResiliente`: deadline, bulkhead, timeout, retry e circuit breaker (arquitetura §5.2)."""

import asyncio

import pytest

from emporio.adaptadores.llm import LLMFalso, PoliticaResiliencia, ProvedorResiliente, responder_texto
from emporio.adaptadores.memoria import CircuitoStoreMemoria
from emporio.portas import (
    ErroCircuitoAberto,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMRecusa,
    ErroLLMSemCredito,
    ErroLLMTransitorio,
    ErroPrazoEsgotado,
    EstadoCircuito,
    LLMProvider,
    RequisicaoLLM,
    RespostaLLM,
)

from .conftest import SonoFalso, Tempo, requisicao

POLITICA = PoliticaResiliencia(max_concorrentes=10)


def montar(
    falso: LLMFalso, tempo: Tempo, sono: SonoFalso, politica: PoliticaResiliencia = POLITICA
) -> tuple[ProvedorResiliente, CircuitoStoreMemoria]:
    circuito = CircuitoStoreMemoria(fonte_tempo=tempo)
    return ProvedorResiliente(falso, circuito, politica, sleep=sono, relogio_monotonic=tempo), circuito


def test_valores_padrao_da_politica() -> None:
    p = PoliticaResiliencia(max_concorrentes=5)
    assert (p.tentativas, p.espera_multiplicador, p.espera_max_s, p.retry_after_max_s) == (3, 0.5, 4.0, 5.0)


async def test_sucesso_registra_no_circuito_e_delega_nome(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([responder_texto("oi")], nome="gemini")
    provedor, circuito = montar(falso, tempo, sono)
    p: LLMProvider = provedor
    assert p.nome == "gemini"
    assert (await provedor.gerar(requisicao())).texto == "oi"
    assert await circuito.estado("gemini") is EstadoCircuito.FECHADO
    assert sono.esperas == []


async def test_401_abre_circuito_na_hora_e_proxima_chamada_nao_toca_a_api(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMAutenticacao("401 chave inválida"), responder_texto("nunca")], nome="claude")
    provedor, circuito = montar(falso, tempo, sono)
    with pytest.raises(ErroLLMAutenticacao):
        await provedor.gerar(requisicao())
    assert len(falso.requisicoes) == 1  # sem retry
    assert await circuito.estado("claude") is EstadoCircuito.ABERTO
    with pytest.raises(ErroCircuitoAberto):
        await provedor.gerar(requisicao())
    assert len(falso.requisicoes) == 1  # pulou o provedor sem chamar
    tempo.t += 200  # abaixo do menor tempo de abertura permanente (com ou sem jitter no store)
    with pytest.raises(ErroCircuitoAberto):
        await provedor.gerar(requisicao())
    tempo.t += 400  # 10 min depois: meio-aberto, a sonda passa e o sucesso fecha
    assert (await provedor.gerar(requisicao())).texto == "nunca"
    assert await circuito.estado("claude") is EstadoCircuito.FECHADO


async def test_transitorio_tenta_3_vezes_e_registra_falhas_transitorias(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMTransitorio("503")] * 5, nome="groq")
    provedor, circuito = montar(falso, tempo, sono)
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor.gerar(requisicao())
    assert not isinstance(info.value, ErroCircuitoAberto)
    assert len(falso.requisicoes) == 3
    assert await circuito.estado("groq") is EstadoCircuito.FECHADO  # 3 falhas seguidas < 5
    # backoff com full jitter: 1º retry U(0; 0,5), 2º retry U(0; 1)
    assert len(sono.esperas) == 2
    assert 0 <= sono.esperas[0] <= 0.5
    assert 0 <= sono.esperas[1] <= 1.0
    # 4ª e 5ª falhas seguidas abrem o circuito; a 3ª tentativa desta chamada é barrada sem tocar a API
    with pytest.raises(ErroCircuitoAberto):
        await provedor.gerar(requisicao())
    assert len(falso.requisicoes) == 5
    assert await circuito.estado("groq") is EstadoCircuito.ABERTO


async def test_transitorio_seguido_de_sucesso(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMTransitorio("timeout"), responder_texto("ok")])
    provedor, _ = montar(falso, tempo, sono)
    assert (await provedor.gerar(requisicao())).texto == "ok"
    assert len(falso.requisicoes) == 2


async def test_retry_after_curto_e_respeitado(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMTransitorio("429", retry_after_s=2.0), responder_texto("ok")])
    provedor, _ = montar(falso, tempo, sono)
    assert (await provedor.gerar(requisicao())).texto == "ok"
    assert sono.esperas == [2.0]


async def test_retry_after_maior_que_5s_desiste_na_hora(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMTransitorio("429", retry_after_s=6.0), responder_texto("nunca")])
    provedor, _ = montar(falso, tempo, sono)
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor.gerar(requisicao())
    assert info.value.retry_after_s == 6.0
    assert len(falso.requisicoes) == 1
    assert sono.esperas == []


async def test_retry_after_que_nao_cabe_no_deadline_desiste(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMTransitorio("429", retry_after_s=3.0), responder_texto("nunca")])
    provedor, _ = montar(falso, tempo, sono)
    req = requisicao(timeout_s=20.0, prazo_monotonic=tempo.t + 22.0)  # 22 - 3 = 19 < 20
    with pytest.raises(ErroLLMTransitorio):
        await provedor.gerar(req)
    assert len(falso.requisicoes) == 1


async def test_retry_after_que_cabe_no_deadline_tenta_de_novo(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMTransitorio("429", retry_after_s=3.0), responder_texto("ok")])
    provedor, _ = montar(falso, tempo, sono)
    req = requisicao(timeout_s=20.0, prazo_monotonic=tempo.t + 24.0)  # 24 - 3 = 21 >= 20
    assert (await provedor.gerar(req)).texto == "ok"


async def test_retry_nao_estoura_o_orcamento_do_deadline(tempo: Tempo, sono: SonoFalso) -> None:
    def falha_lenta(req: RequisicaoLLM) -> RespostaLLM:
        tempo.t += 6.0  # cada tentativa consome 6 s do deadline
        raise ErroLLMTransitorio("503")

    falso = LLMFalso([falha_lenta] * 3)
    provedor, _ = montar(falso, tempo, sono)
    req = requisicao(timeout_s=10.0, prazo_monotonic=tempo.t + 30.0)
    with pytest.raises(ErroLLMTransitorio):
        await provedor.gerar(req)
    # 1ª falha em t+6 (resta ~24 >= 10: tenta); 2ª em ~t+12,5 (resta ~17,5 >= 10: tenta); a 3ª é a última
    assert len(falso.requisicoes) == 3

    falso2 = LLMFalso([falha_lenta] * 3)
    provedor2, _ = montar(falso2, tempo, sono)
    req2 = requisicao(timeout_s=10.0, prazo_monotonic=tempo.t + 15.0)  # após a 1ª, resta 9 - espera < 10
    with pytest.raises(ErroLLMTransitorio):
        await provedor2.gerar(req2)
    assert len(falso2.requisicoes) == 1


async def test_prazo_ja_esgotado_nao_chama_nem_mexe_no_circuito(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([responder_texto("nunca")], nome="gemini")
    provedor, circuito = montar(falso, tempo, sono)
    for _ in range(6):
        with pytest.raises(ErroPrazoEsgotado):
            await provedor.gerar(requisicao(prazo_monotonic=tempo.t - 0.1))
    assert falso.requisicoes == []
    assert await circuito.estado("gemini") is EstadoCircuito.FECHADO


async def test_timeout_por_chamada_vira_transitorio(tempo: Tempo, sono: SonoFalso) -> None:
    async def lento(req: RequisicaoLLM) -> RespostaLLM:
        await asyncio.sleep(5)
        return responder_texto("tarde")

    falso = LLMFalso([lento, lento, responder_texto("ok")], nome="groq")
    provedor, _ = montar(falso, tempo, sono)
    assert (await provedor.gerar(requisicao(timeout_s=0.01))).texto == "ok"
    assert len(falso.requisicoes) == 3


async def test_timeout_esgotado_abre_circuito_como_transitorio(tempo: Tempo, sono: SonoFalso) -> None:
    async def lento(req: RequisicaoLLM) -> RespostaLLM:
        await asyncio.sleep(5)
        return responder_texto("tarde")

    falso = LLMFalso([lento] * 5, nome="groq")
    provedor, circuito = montar(falso, tempo, sono)
    with pytest.raises(ErroLLMTransitorio, match="timeout"):
        await provedor.gerar(requisicao(timeout_s=0.01))
    with pytest.raises(ErroCircuitoAberto):
        await provedor.gerar(requisicao(timeout_s=0.01))
    assert len(falso.requisicoes) == 5
    assert await circuito.estado("groq") is EstadoCircuito.ABERTO


async def test_timeout_limitado_pelo_deadline_nao_conta_contra_o_provedor(tempo: Tempo, sono: SonoFalso) -> None:
    async def lento(req: RequisicaoLLM) -> RespostaLLM:
        await asyncio.sleep(5)
        return responder_texto("tarde")

    falso = LLMFalso([lento] * 6, nome="groq")
    provedor, circuito = montar(falso, tempo, sono)
    for _ in range(5):
        with pytest.raises(ErroPrazoEsgotado):
            await provedor.gerar(requisicao(timeout_s=10.0, prazo_monotonic=tempo.t + 0.01))
    assert await circuito.estado("groq") is EstadoCircuito.FECHADO


@pytest.mark.parametrize("erro", [ErroLLMPermanente("400"), ErroLLMRecusa("refusal")])
async def test_erro_de_requisicao_nao_retenta_nem_abre_circuito(
    tempo: Tempo, sono: SonoFalso, erro: ErroLLMPermanente
) -> None:
    falso = LLMFalso([erro] * 12, nome="claude")
    provedor, circuito = montar(falso, tempo, sono)
    for _ in range(12):
        with pytest.raises(type(erro)):
            await provedor.gerar(requisicao())
    assert len(falso.requisicoes) == 12  # uma chamada por gerar(): sem retry
    assert await circuito.estado("claude") is EstadoCircuito.FECHADO


async def test_bulkhead_limita_concorrencia_por_instancia(tempo: Tempo, sono: SonoFalso) -> None:
    ativos = 0
    pico = 0
    liberar = asyncio.Event()

    async def segura(req: RequisicaoLLM) -> RespostaLLM:
        nonlocal ativos, pico
        ativos += 1
        pico = max(pico, ativos)
        await liberar.wait()
        ativos -= 1
        return responder_texto("ok")

    falso = LLMFalso([segura] * 6)
    provedor, _ = montar(falso, tempo, sono, PoliticaResiliencia(max_concorrentes=2))
    outro, _ = montar(LLMFalso([segura]), tempo, sono, PoliticaResiliencia(max_concorrentes=2))
    tarefas = [asyncio.create_task(provedor.gerar(requisicao())) for _ in range(6)]
    tarefa_outro = asyncio.create_task(outro.gerar(requisicao()))
    for _ in range(10):
        await asyncio.sleep(0)
    assert pico == 3  # 2 desta instância + 1 da outra: o semáforo não é global
    assert len(falso.requisicoes) == 2
    liberar.set()
    resultados = await asyncio.gather(*tarefas, tarefa_outro)
    assert all(r.texto == "ok" for r in resultados)
    assert pico == 3
    assert len(falso.requisicoes) == 6


async def test_sem_credito_abre_circuito_na_hora_sem_retry(tempo: Tempo, sono: SonoFalso) -> None:
    falso = LLMFalso([ErroLLMSemCredito("402 sem crédito"), responder_texto("voltou")], nome="claude")
    provedor, circuito = montar(falso, tempo, sono)
    with pytest.raises(ErroLLMSemCredito):
        await provedor.gerar(requisicao())
    assert len(falso.requisicoes) == 1
    assert sono.esperas == []
    assert await circuito.estado("claude") is EstadoCircuito.ABERTO
    with pytest.raises(ErroCircuitoAberto):
        await provedor.gerar(requisicao())
    assert len(falso.requisicoes) == 1  # pulou sem tocar a API
    tempo.t += 200
    with pytest.raises(ErroCircuitoAberto):
        await provedor.gerar(requisicao())
    tempo.t += 400  # abertura permanente: 10 min; depois 1 sonda
    assert (await provedor.gerar(requisicao())).texto == "voltou"

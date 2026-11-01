"""Roteador de complexidade do motor nativo (arquitetura §2 e §5.2), sem rede e sem `.env`."""

import asyncio

import pytest

from emporio.adaptadores.llm import LLMFalso, PoliticaResiliencia, ProvedorResiliente, responder_texto
from emporio.adaptadores.llm.falso import RoteiroEsgotado
from emporio.adaptadores.memoria import CircuitoStoreMemoria
from emporio.adaptadores.motor.nativo.roteador import (
    CADEIAS,
    TIMEOUTS_PADRAO,
    ErroConfiguracaoRoteador,
    ErroSemProvedor,
    Nivel,
    Roteador,
    interpretar_nivel,
)
from emporio.portas import (
    ErroCircuitoAberto,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMRecusa,
    ErroLLMTransitorio,
    ErroPrazoEsgotado,
    LLMProvider,
    MensagemLLM,
    PapelMensagem,
    RequisicaoLLM,
    RespostaLLM,
)

INICIO = 1000.0


class Tempo:
    """Fonte de tempo monotônica controlada pelo teste."""

    def __init__(self, inicio: float = INICIO) -> None:
        self.t = inicio

    def __call__(self) -> float:
        return self.t


def requisicao(prazo: float | None = INICIO + 30) -> RequisicaoLLM:
    return RequisicaoLLM(
        "manual",
        "data",
        (MensagemLLM(PapelMensagem.USER, "quero devolver"),),
        timeout_s=20.0,
        prazo_monotonic=prazo,
    )


def falsos(*nomes: str) -> dict[str, LLMFalso]:
    return {nome: LLMFalso(nome=nome) for nome in nomes}


def roteador(provedores: dict[str, LLMFalso], tempo: Tempo | None = None, **extras: float) -> Roteador:
    mapa: dict[str, LLMProvider] = dict(provedores)
    return Roteador(mapa, relogio_monotonic=tempo or Tempo(), **extras)


# --- interpretação da resposta do classificador ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("simples", Nivel.SIMPLES),
        ("Simples.", Nivel.SIMPLES),
        ("  MEDIA\n", Nivel.MEDIA),
        ("média", Nivel.MEDIA),
        ("complexa", Nivel.COMPLEXA),
        ("Nível: complexa", Nivel.COMPLEXA),
        ("simple", Nivel.SIMPLES),
        ("medium", Nivel.MEDIA),
        ("complex", Nivel.COMPLEXA),
        # ambíguo: vale o maior nível citado
        ("simples ou media", Nivel.MEDIA),
        ("media/complexa", Nivel.COMPLEXA),
        ("simples, complexa", Nivel.COMPLEXA),
        # nada reconhecível: complexa
        ("não sei", Nivel.COMPLEXA),
        ("", Nivel.COMPLEXA),
        (None, Nivel.COMPLEXA),
        ("simplesmente", Nivel.COMPLEXA),
    ],
)
def test_interpretar_nivel(texto: str | None, esperado: Nivel) -> None:
    assert interpretar_nivel(texto) is esperado


def test_niveis_sao_strenum_com_valores_estaveis() -> None:
    assert [str(n) for n in Nivel] == ["simples", "media", "complexa"]


# --- classificação ------------------------------------------------------------------------------------------


async def test_resposta_ambigua_do_classificador_sobe_para_o_nivel_maior() -> None:
    provs = falsos("groq", "gemini", "claude")
    provs["groq"].roteirizar(responder_texto("simples ou media"))
    assert await roteador(provs).classificar("oi, tem violão?", prazo_monotonic=INICIO + 30) is Nivel.MEDIA


async def test_classificador_e_o_groq_com_chamada_curta_e_sem_tools() -> None:
    provs = falsos("groq", "gemini", "claude")
    provs["groq"].roteirizar(responder_texto("simples"))
    nivel = await roteador(provs).classificar("Qual o horário de sábado?", prazo_monotonic=INICIO + 30)
    assert nivel is Nivel.SIMPLES
    req = provs["groq"].ultima_requisicao
    assert req.tools == ()
    assert req.timeout_s == 5.0
    assert req.prazo_monotonic == INICIO + 5  # o classificador gasta no máximo 5 s do deadline
    assert len(req.mensagens) == 1
    assert req.mensagens[0].role is PapelMensagem.USER
    assert "Qual o horário de sábado?" in req.mensagens[0].content
    assert "complexa" in req.system_cacheavel
    assert provs["gemini"].requisicoes == []
    assert provs["claude"].requisicoes == []


async def test_classificador_respeita_deadline_menor_que_5s() -> None:
    provs = falsos("groq", "gemini")
    provs["groq"].roteirizar(responder_texto("media"))
    await roteador(provs).classificar("preço do Takamine", prazo_monotonic=INICIO + 2)
    assert provs["groq"].ultima_requisicao.prazo_monotonic == INICIO + 2


async def test_classificador_sem_deadline_usa_so_o_proprio_limite() -> None:
    provs = falsos("groq", "gemini")
    provs["groq"].roteirizar(responder_texto("media"))
    await roteador(provs).classificar("preço do Takamine")
    assert provs["groq"].ultima_requisicao.prazo_monotonic == INICIO + 5


async def test_classificador_recebe_contexto_curto_do_historico() -> None:
    provs = falsos("groq", "claude")
    provs["groq"].roteirizar(responder_texto("complexa"))
    historico = (
        MensagemLLM(PapelMensagem.USER, "mensagem antiga demais"),
        MensagemLLM(PapelMensagem.ASSISTANT, "resposta antiga"),
        MensagemLLM(PapelMensagem.USER, "quanto custa o Takamine GD20?"),
        MensagemLLM(PapelMensagem.ASSISTANT, "", tool_calls=()),  # só tool call: ignorada
        MensagemLLM(PapelMensagem.TOOL, '{"preco": 1999}', tool_call_id="c1"),  # resultado de tool: ignorado
        MensagemLLM(PapelMensagem.ASSISTANT, "O Takamine GD20 sai por R$ 1.999,00. " + "x" * 500),
    )
    await roteador(provs).classificar("e em 10x?", historico, prazo_monotonic=INICIO + 30)
    conteudo = provs["groq"].ultima_requisicao.mensagens[0].content
    assert "e em 10x?" in conteudo
    assert "quanto custa o Takamine GD20?" in conteudo
    assert "O Takamine GD20 sai por" in conteudo
    assert "1999" not in conteudo  # nada de resultado de tool
    assert "mensagem antiga demais" not in conteudo  # só as últimas falas
    assert "x" * 300 not in conteudo  # falas longas truncadas


async def test_sem_groq_o_classificador_e_o_proximo_na_preferencia() -> None:
    provs = falsos("claude", "gemini")
    provs["gemini"].roteirizar(responder_texto("media"))
    assert await roteador(provs).classificar("tem guitarra?") is Nivel.MEDIA
    assert provs["claude"].requisicoes == []


@pytest.mark.parametrize(
    "erro",
    [
        ErroLLMTransitorio("503"),
        ErroCircuitoAberto("groq: circuito aberto"),
        ErroLLMAutenticacao("401"),
        ErroLLMPermanente("400"),
        ErroLLMRecusa("recusa"),
        ErroPrazoEsgotado("prazo"),
    ],
)
async def test_falha_do_classificador_vira_complexa(erro: Exception) -> None:
    provs = falsos("groq", "claude")
    provs["groq"].roteirizar(erro)
    assert await roteador(provs).classificar("oi") is Nivel.COMPLEXA


async def test_classificador_lento_vira_complexa_pelo_timeout() -> None:
    async def lento(_req: RequisicaoLLM) -> RespostaLLM:
        await asyncio.sleep(5)
        return responder_texto("simples")

    provs = falsos("groq", "claude")
    provs["groq"].roteirizar(lento)
    r = roteador(provs, timeout_classificador_s=0.01)
    assert await r.classificar("oi") is Nivel.COMPLEXA


async def test_deadline_esgotado_nao_chama_o_classificador() -> None:
    provs = falsos("groq", "claude")
    assert await roteador(provs).classificar("oi", prazo_monotonic=INICIO) is Nivel.COMPLEXA
    assert provs["groq"].requisicoes == []


async def test_bug_de_teste_no_classificador_nao_e_engolido() -> None:
    provs = falsos("groq", "claude")  # roteiro vazio: RoteiroEsgotado não é ErroLLM
    with pytest.raises(RoteiroEsgotado):
        await roteador(provs).classificar("oi")


# --- cadeias ------------------------------------------------------------------------------------------------


def test_cadeias_com_os_tres_provedores() -> None:
    r = roteador(falsos("groq", "gemini", "claude"))
    assert r.cadeia(Nivel.SIMPLES) == ("groq", "gemini", "claude")
    assert r.cadeia(Nivel.MEDIA) == ("gemini", "claude", "groq")
    assert r.cadeia(Nivel.COMPLEXA) == ("claude", "gemini", "groq")
    assert r.precisa_classificar
    assert CADEIAS[Nivel.COMPLEXA] == ("claude", "gemini", "groq")


def test_cadeias_filtradas_pelos_provedores_presentes() -> None:
    r = roteador(falsos("groq", "claude"))
    assert r.cadeia(Nivel.SIMPLES) == ("groq", "claude")
    assert r.cadeia(Nivel.MEDIA) == ("claude", "groq")
    assert r.cadeia(Nivel.COMPLEXA) == ("claude", "groq")
    assert r.precisa_classificar


def test_provedor_desconhecido_entra_no_fim_das_cadeias() -> None:
    r = roteador(falsos("ollama", "gemini"))
    assert r.cadeia(Nivel.COMPLEXA) == ("gemini", "ollama")
    assert r.cadeia(Nivel.SIMPLES) == ("gemini", "ollama")
    assert not r.precisa_classificar


async def test_so_chave_gemini_todos_os_niveis_usam_gemini_sem_classificar() -> None:
    provs = falsos("gemini")
    provs["gemini"].roteirizar(responder_texto("oi!"), responder_texto("R$ 999"), responder_texto("pode sim"))
    r = roteador(provs)
    assert not r.precisa_classificar
    for nivel in Nivel:
        assert r.cadeia(nivel) == ("gemini",)
    assert await r.classificar("me arrependi da compra, posso devolver?") is None
    assert provs["gemini"].requisicoes == []  # classificador não foi chamado
    for nivel in (None, Nivel.SIMPLES, Nivel.COMPLEXA):
        resultado = await r.gerar(nivel, requisicao())
        assert resultado.provedor == "gemini"
        assert resultado.nivel is nivel
    assert len(provs["gemini"].requisicoes) == 3


def test_sem_provedor_falha_na_construcao() -> None:
    with pytest.raises(ErroConfiguracaoRoteador):
        Roteador({})


# --- geração com fallback -------------------------------------------------------------------------------------


async def test_gera_com_o_preferido_do_nivel_e_informa_nivel_e_provedor() -> None:
    provs = falsos("groq", "gemini", "claude")
    provs["gemini"].roteirizar(responder_texto("Temos 3 violões até R$ 1.000"))
    resultado = await roteador(provs).gerar(Nivel.MEDIA, requisicao())
    assert resultado.resposta.texto == "Temos 3 violões até R$ 1.000"
    assert resultado.resposta.provedor == "gemini"
    assert resultado.provedor == "gemini"
    assert resultado.nivel is Nivel.MEDIA
    assert resultado.tentativas == ()


async def test_claude_com_circuito_aberto_cai_no_gemini_sem_tocar_a_api() -> None:
    tempo = Tempo()
    circuito = CircuitoStoreMemoria(fonte_tempo=tempo)
    await circuito.registrar_falha("claude", permanente=True)
    politica = PoliticaResiliencia(max_concorrentes=4)
    claude_falso = LLMFalso([responder_texto("nunca")], nome="claude")
    gemini_falso = LLMFalso([responder_texto("Pode devolver em até 7 dias")], nome="gemini")
    provs: dict[str, LLMProvider] = {
        "claude": ProvedorResiliente(claude_falso, circuito, politica, relogio_monotonic=tempo),
        "gemini": ProvedorResiliente(gemini_falso, circuito, politica, relogio_monotonic=tempo),
    }
    r = Roteador(provs, relogio_monotonic=tempo)
    resultado = await r.gerar(Nivel.COMPLEXA, requisicao())
    assert resultado.provedor == "gemini"
    assert resultado.resposta.texto == "Pode devolver em até 7 dias"
    assert claude_falso.requisicoes == []
    assert [t.provedor for t in resultado.tentativas] == ["claude"]
    assert isinstance(resultado.tentativas[0].erro, ErroCircuitoAberto)


@pytest.mark.parametrize(
    "erro",
    [
        ErroLLMTransitorio("503"),
        ErroCircuitoAberto("aberto"),
        ErroLLMAutenticacao("401"),
        ErroLLMRecusa("recusou"),
        ErroLLMPermanente("400"),
    ],
)
async def test_erros_que_passam_para_o_proximo_provedor(erro: Exception) -> None:
    provs = falsos("claude", "gemini", "groq")
    provs["claude"].roteirizar(erro)
    provs["gemini"].roteirizar(responder_texto("ok"))
    resultado = await roteador(provs).gerar(Nivel.COMPLEXA, requisicao())
    assert resultado.provedor == "gemini"
    assert resultado.tentativas[0].erro is erro
    assert provs["groq"].requisicoes == []


async def test_prazo_esgotado_interrompe_sem_tentar_o_proximo() -> None:
    provs = falsos("claude", "gemini", "groq")
    provs["claude"].roteirizar(ErroPrazoEsgotado("claude: prazo esgotado durante a chamada"))
    with pytest.raises(ErroPrazoEsgotado):
        await roteador(provs).gerar(Nivel.COMPLEXA, requisicao())
    assert provs["gemini"].requisicoes == []
    assert provs["groq"].requisicoes == []


async def test_deadline_ja_esgotado_nao_chama_ninguem() -> None:
    provs = falsos("claude", "gemini")
    with pytest.raises(ErroPrazoEsgotado):
        await roteador(provs).gerar(Nivel.COMPLEXA, requisicao(prazo=INICIO - 1))
    assert provs["claude"].requisicoes == []
    assert provs["gemini"].requisicoes == []


async def test_deadline_esgota_durante_o_fallback() -> None:
    tempo = Tempo()

    def demora_e_falha(_req: RequisicaoLLM) -> RespostaLLM:
        tempo.t += 31
        raise ErroLLMTransitorio("timeout")

    provs = falsos("claude", "gemini")
    provs["claude"].roteirizar(demora_e_falha)
    with pytest.raises(ErroPrazoEsgotado):
        await roteador(provs, tempo).gerar(Nivel.COMPLEXA, requisicao())
    assert provs["gemini"].requisicoes == []


async def test_todos_falham_levanta_erro_com_as_tentativas() -> None:
    provs = falsos("claude", "gemini", "groq")
    erros = {
        "claude": ErroLLMAutenticacao("401"),
        "gemini": ErroLLMTransitorio("503"),
        "groq": ErroLLMRecusa("recusou"),
    }
    for nome, erro in erros.items():
        provs[nome].roteirizar(erro)
    with pytest.raises(ErroSemProvedor) as info:
        await roteador(provs).gerar(Nivel.COMPLEXA, requisicao())
    assert [t.provedor for t in info.value.tentativas] == ["claude", "gemini", "groq"]
    assert [t.erro for t in info.value.tentativas] == [erros["claude"], erros["gemini"], erros["groq"]]
    assert info.value.nivel is Nivel.COMPLEXA


async def test_timeouts_por_provedor_e_prazo_propagado() -> None:
    provs = falsos("claude", "gemini", "groq")
    provs["claude"].roteirizar(ErroLLMTransitorio("503"))
    provs["gemini"].roteirizar(ErroLLMTransitorio("503"))
    provs["groq"].roteirizar(responder_texto("ok"))
    await roteador(provs).gerar(Nivel.COMPLEXA, requisicao(prazo=INICIO + 30))
    assert provs["claude"].ultima_requisicao.timeout_s == 25.0
    assert provs["gemini"].ultima_requisicao.timeout_s == 15.0
    assert provs["groq"].ultima_requisicao.timeout_s == 10.0
    for falso in provs.values():
        req = falso.ultima_requisicao
        assert req.prazo_monotonic == INICIO + 30
        assert req.mensagens == requisicao().mensagens
    assert TIMEOUTS_PADRAO == {"groq": 10.0, "gemini": 15.0, "claude": 25.0}


async def test_provedor_desconhecido_mantem_o_timeout_da_requisicao() -> None:
    provs = falsos("ollama")
    provs["ollama"].roteirizar(responder_texto("ok"))
    await roteador(provs).gerar(None, requisicao())
    assert provs["ollama"].ultima_requisicao.timeout_s == 20.0


async def test_provedor_da_mensagem_e_tentado_primeiro() -> None:
    provs = falsos("claude", "gemini", "groq")
    provs["groq"].roteirizar(responder_texto("continuo eu"))
    resultado = await roteador(provs).gerar(Nivel.COMPLEXA, requisicao(), preferido="groq")
    assert resultado.provedor == "groq"
    assert provs["claude"].requisicoes == []
    assert provs["gemini"].requisicoes == []


async def test_provedor_da_mensagem_falha_e_volta_para_a_cadeia_do_nivel() -> None:
    provs = falsos("claude", "gemini", "groq")
    provs["groq"].roteirizar(ErroLLMTransitorio("503"))
    provs["claude"].roteirizar(responder_texto("assumo eu"))
    resultado = await roteador(provs).gerar(Nivel.COMPLEXA, requisicao(), preferido="groq")
    assert resultado.provedor == "claude"
    assert [t.provedor for t in resultado.tentativas] == ["groq"]
    assert len(provs["groq"].requisicoes) == 1  # não é tentado de novo
    assert provs["gemini"].requisicoes == []


async def test_preferido_ausente_e_ignorado() -> None:
    provs = falsos("gemini")
    provs["gemini"].roteirizar(responder_texto("ok"))
    resultado = await roteador(provs).gerar(Nivel.MEDIA, requisicao(), preferido="claude")
    assert resultado.provedor == "gemini"


async def test_nivel_none_com_cadeias_diferentes_usa_a_de_complexa() -> None:
    provs = falsos("claude", "gemini", "groq")
    provs["claude"].roteirizar(responder_texto("ok"))
    resultado = await roteador(provs).gerar(None, requisicao())
    assert resultado.provedor == "claude"
    assert resultado.nivel is None


async def test_bug_de_teste_na_geracao_nao_cai_no_fallback() -> None:
    provs = falsos("claude", "gemini")
    with pytest.raises(RoteiroEsgotado):
        await roteador(provs).gerar(Nivel.COMPLEXA, requisicao())
    assert provs["gemini"].requisicoes == []

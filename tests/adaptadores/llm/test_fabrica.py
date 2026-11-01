"""Fábrica de provedores: só registra quem tem chave, já embrulhado na resiliência (sem rede)."""

import pytest

from emporio.adaptadores.llm import (
    POLITICA_COMPATIVEL_OPENAI,
    POLITICAS_PADRAO,
    ProvedorClaude,
    ProvedorCompativelOpenAI,
    ProvedorGemini,
    ProvedorGroq,
    ProvedorResiliente,
    criar_provedores,
)
from emporio.adaptadores.memoria import CircuitoStoreMemoria
from emporio.adaptadores.motor.nativo.roteador import Nivel, Roteador
from emporio.configuracao import Settings

from .conftest import SonoFalso, Tempo

CHAVES = ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY")


@pytest.fixture(autouse=True)
def _sem_chaves_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in (*CHAVES, "MODELO_CLAUDE", "MODELO_GEMINI", "MODELO_GROQ", "ESFORCO_CLAUDE", "ESFORCO_GROQ"):
        monkeypatch.delenv(nome, raising=False)


def test_settings_modelos_padrao() -> None:
    s = Settings(_env_file=None)
    assert s.modelo_claude == "claude-opus-5-5"
    assert s.esforco_claude == "low"
    assert s.claude_fallback_servidor is True
    assert s.modelo_gemini == "gemini-2.5-flash"
    assert s.modelo_groq == "openai/gpt-oss-120b"
    assert s.esforco_groq == "low"


def test_sem_chave_nao_registra_nada() -> None:
    assert criar_provedores(Settings(_env_file=None), CircuitoStoreMemoria()) == {}


def test_so_provedores_com_chave_e_politicas_da_arquitetura() -> None:
    s = Settings(_env_file=None, groq_api_key="g", google_api_key="  ")  # type: ignore[arg-type]
    provedores = criar_provedores(s, CircuitoStoreMemoria())
    assert set(provedores) == {"groq"}
    groq = provedores["groq"]
    assert isinstance(groq, ProvedorResiliente)
    assert groq.nome == "groq"
    assert isinstance(groq.interno, ProvedorGroq)
    assert groq.politica.max_concorrentes == 10


def test_todos_os_provedores(tempo: Tempo, sono: SonoFalso) -> None:
    s = Settings(
        _env_file=None,
        anthropic_api_key="a",  # type: ignore[arg-type]
        google_api_key="g",  # type: ignore[arg-type]
        groq_api_key="q",  # type: ignore[arg-type]
        modelo_claude="claude-x",
        esforco_claude="high",
    )
    provedores = criar_provedores(s, CircuitoStoreMemoria(fonte_tempo=tempo), sleep=sono, relogio_monotonic=tempo)
    assert set(provedores) == {"claude", "gemini", "groq"}
    claude = provedores["claude"]
    assert isinstance(claude, ProvedorResiliente)
    assert isinstance(claude.interno, ProvedorClaude)
    assert claude.interno.modelo == "claude-x"
    assert claude.politica.max_concorrentes == 5
    gemini = provedores["gemini"]
    assert isinstance(gemini, ProvedorResiliente)
    assert isinstance(gemini.interno, ProvedorGemini)
    assert gemini.politica.max_concorrentes == 10


def test_politicas_padrao() -> None:
    assert {n: p.max_concorrentes for n, p in POLITICAS_PADRAO.items()} == {"claude": 5, "gemini": 10, "groq": 10}


# --- provedor compatível com OpenAI (LLM_*) ----------------------------------------


def test_compativel_so_e_registrado_quando_configurado() -> None:
    assert criar_provedores(Settings(_env_file=None, llm_modelo="gpt-4o-mini"), CircuitoStoreMemoria()) == {}
    s = Settings(_env_file=None, llm_provedor="deepseek", llm_modelo="deepseek-chat", llm_api_key="sk-x")  # type: ignore[arg-type]
    provedores = criar_provedores(s, CircuitoStoreMemoria())
    assert set(provedores) == {"deepseek"}
    resiliente = provedores["deepseek"]
    assert isinstance(resiliente, ProvedorResiliente)
    assert resiliente.nome == "deepseek"
    assert resiliente.politica is POLITICA_COMPATIVEL_OPENAI and resiliente.politica.max_concorrentes == 10
    interno = resiliente.interno
    assert isinstance(interno, ProvedorCompativelOpenAI)
    assert (interno.modelo, interno.campo_max_tokens) == ("deepseek-chat", "max_tokens")
    assert str(interno._cliente.base_url).startswith("https://api.deepseek.com")


def test_compativel_openai_usa_endpoint_padrao_e_max_completion_tokens() -> None:
    s = Settings(_env_file=None, llm_modelo="gpt-4o-mini", llm_api_key="sk-x")  # type: ignore[arg-type]
    interno = criar_provedores(s, CircuitoStoreMemoria())["openai"]
    assert isinstance(interno, ProvedorResiliente) and isinstance(interno.interno, ProvedorCompativelOpenAI)
    assert interno.interno.campo_max_tokens == "max_completion_tokens"
    assert str(interno.interno._cliente.base_url).startswith("https://api.openai.com/v1")


def test_so_o_compativel_atende_todos_os_niveis_sem_classificar() -> None:
    s = Settings(_env_file=None, llm_provedor="ollama", llm_modelo="qwen3", llm_api_key="ollama")  # type: ignore[arg-type]
    roteador = Roteador(criar_provedores(s, CircuitoStoreMemoria()))
    assert not roteador.precisa_classificar
    assert {roteador.cadeia(n) for n in Nivel} == {("ollama",)}
    assert roteador.cadeia(None) == ("ollama",)


def test_com_gemini_o_compativel_fica_no_fim_da_cadeia() -> None:
    s = Settings(
        _env_file=None,
        google_api_key="g",  # type: ignore[arg-type]
        groq_api_key="q",  # type: ignore[arg-type]
        llm_provedor="openrouter",
        llm_modelo="openai/gpt-4o-mini",
        llm_api_key="sk-or",  # type: ignore[arg-type]
    )
    provedores = criar_provedores(s, CircuitoStoreMemoria())
    assert list(provedores) == ["gemini", "groq", "openrouter"]
    roteador = Roteador(provedores)
    assert roteador.precisa_classificar
    for nivel in Nivel:
        assert roteador.cadeia(nivel)[-1] == "openrouter"
    assert roteador.cadeia(Nivel.SIMPLES) == ("groq", "gemini", "openrouter")
    so_gemini = Roteador(
        criar_provedores(
            Settings(_env_file=None, google_api_key="g", llm_modelo="m", llm_api_key="k"),  # type: ignore[arg-type]
            CircuitoStoreMemoria(),
        )
    )
    assert {so_gemini.cadeia(n) for n in Nivel} == {("gemini", "openai")}
    assert not so_gemini.precisa_classificar  # mesma cadeia em todos os níveis

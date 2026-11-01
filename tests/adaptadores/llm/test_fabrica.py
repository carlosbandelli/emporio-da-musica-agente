"""Fábrica de provedores: só registra quem tem chave, já embrulhado na resiliência (sem rede)."""

import pytest

from emporio.adaptadores.llm import (
    POLITICAS_PADRAO,
    ProvedorClaude,
    ProvedorGemini,
    ProvedorGroq,
    ProvedorResiliente,
    criar_provedores,
)
from emporio.adaptadores.memoria import CircuitoStoreMemoria
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
    assert s.modelo_gemini == "gemini-flash-latest"
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

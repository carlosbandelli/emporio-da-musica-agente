"""Fixtures globais: isola os testes das variáveis `LLM_*` do shell (provedor compatível com OpenAI)."""

import pytest

VARIAVEIS_LLM_COMPATIVEL = ("LLM_PROVEDOR", "LLM_MODELO", "LLM_API_KEY", "LLM_BASE_URL")


@pytest.fixture(autouse=True)
def _sem_llm_compativel_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in VARIAVEIS_LLM_COMPATIVEL:
        monkeypatch.delenv(nome, raising=False)

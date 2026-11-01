"""Proteção contra chamadas reais de LLM nos testes ponta a ponta.

Os testes `@pytest.mark.llm_real` só rodam quando `EMPORIO_LLM_REAL=1` está no ambiente do processo (além de uma
chave de LLM). Assim, um `uv run pytest` normal nunca gasta cota, mesmo com as chaves exportadas no shell.

Rodar manualmente:  `EMPORIO_LLM_REAL=1 GROQ_API_KEY=... uv run pytest tests/e2e/test_llm_real.py -v`
"""

import os

import pytest

VARIAVEL_LIBERACAO = "EMPORIO_LLM_REAL"


@pytest.fixture(autouse=True)
def _pular_llm_real_sem_liberacao(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("llm_real") and os.environ.get(VARIAVEL_LIBERACAO) != "1":
        pytest.skip(f"LLM real desativado por padrão; defina {VARIAVEL_LIBERACAO}=1 para rodar")

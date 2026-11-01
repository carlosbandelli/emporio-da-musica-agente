"""Cenários contra LLM real (opcional). Asserções só sobre as TOOLS chamadas, nunca sobre o texto.

Não rodam em `uv run pytest` normal: precisam de `EMPORIO_LLM_REAL=1` (ver `conftest.py`) e de ao menos uma chave
(`GROQ_API_KEY`, `GOOGLE_API_KEY` ou `ANTHROPIC_API_KEY`) no ambiente do processo. Este arquivo nunca lê arquivo de
ambiente: as chaves vêm só de `os.environ`.

    EMPORIO_LLM_REAL=1 uv run pytest tests/e2e/test_llm_real.py -v
"""

import os
from pathlib import Path

import pytest

from emporio.composicao import Aplicacao, montar
from emporio.portas import MensagemCliente, RespostaAtendimento
from tests.e2e.apoio import CTX, RAIZ, settings_e2e
from tests.modulos.fakes import RelogioFixo, local

pytestmark = pytest.mark.llm_real

CHAVES = ("GROQ_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY")
TERCA_MEIO_DIA = local(2026, 3, 10, 12)


@pytest.fixture
def app_real(tmp_path: Path) -> Aplicacao:
    if not any(os.environ.get(chave) for chave in CHAVES):
        pytest.skip("nenhuma chave de LLM em os.environ (GROQ_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY)")
    return montar(
        settings_e2e(TERCA_MEIO_DIA),  # lê só o ambiente do processo (sem arquivo de ambiente)
        raiz=RAIZ,
        dir_estado=tmp_path,
        relogio=RelogioFixo(TERCA_MEIO_DIA),
    )


async def _perguntar(app: Aplicacao, texto: str) -> RespostaAtendimento:
    return await app.motor.responder(CTX, "e2e-real", MensagemCliente(texto))


async def test_real_violoes_ate_mil_usa_buscar_produtos(app_real: Aplicacao) -> None:
    r = await _perguntar(app_real, "Quais violões vocês têm até R$ 1000?")
    assert "buscar_produtos" in r.tools_chamadas


async def test_real_preco_do_takamine_usa_tool_de_produto(app_real: Aplicacao) -> None:
    r = await _perguntar(app_real, "Quanto custa o Takamine GD20?")
    assert set(r.tools_chamadas) & {"detalhar_produto", "buscar_produtos"}


async def test_real_endereco_nao_usa_tool_de_dados(app_real: Aplicacao) -> None:
    r = await _perguntar(app_real, "Qual o endereço da loja?")
    assert not set(r.tools_chamadas) & {"buscar_produtos", "detalhar_produto", "consultar_pedido", "simular_pagamento"}

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from emporio.adaptadores.llm import LLMFalso
from tests.adaptadores.api.apoio import BASE, app_com

FabricaCliente = Callable[[FastAPI], "httpx.AsyncClient"]


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in (
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "MOTOR_ATENDIMENTO",
        "DATA_REFERENCIA",
        "TENANTS_CONFIG",
        "TENANT_PADRAO",
    ):
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def llm() -> LLMFalso:
    return LLMFalso()


@pytest.fixture
async def cliente(llm: LLMFalso, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente sobre a app montada com `LLMFalso`, com o lifespan rodando."""
    app = app_com(llm, tmp_path)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
    ):
        yield http


@pytest.fixture
async def cliente_sem_provedor(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente sobre a app cuja montagem falha por falta de chave (premissa #19)."""
    app = app_com(None, tmp_path)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
    ):
        yield http

"""Fixtures do Postgres de teste: serviço externo (`TEST_DATABASE_URL`) ou container (testcontainers).

Sem Docker nem `TEST_DATABASE_URL`, os testes que precisam do banco são pulados (premissa #19); os demais rodam.
Cada teste recebe um banco novo (`CREATE DATABASE`); o banco já migrado é clonado de um modelo (`TEMPLATE`).
"""

import os
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from emporio.adaptadores.db.migrar import migrar
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores.conftest import RAIZ

IMAGEM = "postgres:16-alpine"  # a mesma do docker-compose.yml


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "postgres: exige um Postgres real (testcontainers ou TEST_DATABASE_URL)")


def _renderizar(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def servidor_postgres() -> Iterator[URL]:
    """URL administrativa (`postgresql+psycopg://`) de um Postgres 16."""
    externo = os.environ.get("TEST_DATABASE_URL")
    if externo:
        yield make_url(externo)
        return
    try:
        from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

        container = PostgresContainer(IMAGEM, driver="psycopg")
        container.start()
    except Exception as exc:  # Docker ausente, daemon parado ou imagem indisponível
        pytest.skip(f"Postgres indisponível (Docker/testcontainers ou TEST_DATABASE_URL): {type(exc).__name__}")
    try:
        yield make_url(container.get_connection_url())
    finally:
        container.stop()


def _criar_banco(admin: URL, nome: str, modelo: str | None = None) -> URL:
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        c.execute(text(f'CREATE DATABASE "{nome}"' + (f' TEMPLATE "{modelo}"' if modelo else "")))
    engine.dispose()
    return admin.set(database=nome)


def _remover_banco(admin: URL, nome: str) -> None:
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
    engine.dispose()


@pytest.fixture
def banco(servidor_postgres: URL) -> Iterator[URL]:
    """Banco vazio exclusivo do teste."""
    nome = f"t_{uuid.uuid4().hex[:12]}"
    yield _criar_banco(servidor_postgres, nome)
    _remover_banco(servidor_postgres, nome)


@pytest.fixture(scope="session")
def modelo_migrado(servidor_postgres: URL, tenants: Mapping[str, ConfigTenant]) -> Iterator[str]:
    """Banco-modelo com `config/tenants.yaml` migrado e semeado (clonado por `banco_migrado`)."""
    nome = f"modelo_{uuid.uuid4().hex[:8]}"
    url = _criar_banco(servidor_postgres, nome)
    migrar(SecretStr(_renderizar(url)), tenants, RAIZ)
    yield nome
    _remover_banco(servidor_postgres, nome)


@pytest.fixture
def banco_migrado(servidor_postgres: URL, modelo_migrado: str) -> Iterator[URL]:
    """Banco exclusivo do teste, já migrado e semeado."""
    nome = f"t_{uuid.uuid4().hex[:12]}"
    yield _criar_banco(servidor_postgres, nome, modelo_migrado)
    _remover_banco(servidor_postgres, nome)


def texto_url(url: URL) -> str:
    """URL com senha, para `Settings`/subprocessos de teste."""
    return _renderizar(url)


@pytest.fixture
def raiz() -> Path:
    return RAIZ

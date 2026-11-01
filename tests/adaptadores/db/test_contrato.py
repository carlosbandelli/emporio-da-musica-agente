"""Contrato dos repositórios: as mesmas verificações rodam contra os adaptadores CSV e Postgres."""

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from emporio.portas.tenant import ConfigTenant
from tests.adaptadores import contrato_dados as contrato
from tests.adaptadores.db.repos import Repos, repos_csv, repos_postgres


@pytest.fixture(params=["csv", pytest.param("postgres", marks=pytest.mark.postgres)])
def repos(request: pytest.FixtureRequest, tenants: Mapping[str, ConfigTenant], tmp_path: Path) -> Iterator[Repos]:
    if request.param == "csv":
        yield repos_csv(tenants, tmp_path)
        return
    conjunto = repos_postgres(tenants, request.getfixturevalue("banco_migrado"))
    yield conjunto
    conjunto.fechar()


async def test_catalogo_real(repos: Repos) -> None:
    await contrato.verificar_catalogo_real(repos.catalogo)


async def test_filtros(repos: Repos) -> None:
    await contrato.verificar_filtros(repos.catalogo)


async def test_promocoes_todas_as_linhas(repos: Repos) -> None:
    await contrato.verificar_promocoes_todas_as_linhas(repos.catalogo)


async def test_pedidos_real(repos: Repos) -> None:
    await contrato.verificar_pedidos_real(repos.pedidos)


async def test_total_confere_com_itens(repos: Repos) -> None:
    await contrato.verificar_total_confere_com_itens(repos.pedidos, repos.catalogo)


async def test_clientes(repos: Repos) -> None:
    await contrato.verificar_clientes(repos.clientes)


async def test_solicitacoes_idempotente(repos: Repos) -> None:
    await contrato.verificar_solicitacoes_idempotente(repos.solicitacoes, repos.solicitacoes_b)


async def test_solicitacoes_concorrentes_sem_duplicar(repos: Repos) -> None:
    await contrato.verificar_solicitacoes_concorrentes(repos.solicitacoes)


async def test_solicitacoes_status(repos: Repos) -> None:
    await contrato.verificar_solicitacoes_status(repos.solicitacoes, repos.solicitacoes_b)


async def test_solicitacoes_filtros(repos: Repos) -> None:
    await contrato.verificar_solicitacoes_filtros(repos.solicitacoes)

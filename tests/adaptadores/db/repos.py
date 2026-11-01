"""Conjunto de repositórios usado pelos testes de contrato (CSV ou Postgres)."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL

from emporio.adaptadores.csv import CatalogoCsv, ClientesCsv, PedidosCsv, SolicitacoesJsonl
from emporio.adaptadores.db import RepositoriosPostgres, criar_repositorios_postgres
from emporio.configuracao import Settings
from emporio.portas.repositorios import Catalogo, Clientes, Pedidos, Solicitacoes
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores.conftest import RAIZ
from tests.adaptadores.db.conftest import texto_url


@dataclass
class Repos:
    catalogo: Catalogo
    pedidos: Pedidos
    clientes: Clientes
    solicitacoes: Solicitacoes
    solicitacoes_b: Solicitacoes  # outra instância sobre o mesmo armazenamento
    postgres: tuple[RepositoriosPostgres, ...] = ()

    def fechar(self) -> None:
        for r in self.postgres:
            r.fechar()


def repos_csv(tenants: Mapping[str, ConfigTenant], base: Path) -> Repos:
    return Repos(
        catalogo=CatalogoCsv(tenants, RAIZ),
        pedidos=PedidosCsv(tenants, RAIZ),
        clientes=ClientesCsv(tenants, RAIZ),
        solicitacoes=SolicitacoesJsonl(base),
        solicitacoes_b=SolicitacoesJsonl(base),
    )


def settings_postgres(url: URL) -> Settings:
    return Settings(_env_file=None, database_url=texto_url(url))  # type: ignore[call-arg,arg-type]


def repos_postgres(tenants: Mapping[str, ConfigTenant], url: URL) -> Repos:
    a = criar_repositorios_postgres(settings_postgres(url), tenants)
    b = criar_repositorios_postgres(settings_postgres(url), tenants)
    return Repos(a.catalogo, a.pedidos, a.clientes, a.solicitacoes, b.solicitacoes, postgres=(a, b))

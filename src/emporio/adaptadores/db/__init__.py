"""Adaptadores Postgres (schema por tenant), migrações Alembic e seed dos CSVs.

Para a composição: `criar_repositorios_postgres(settings, tenants)`.
Migração: `python -m emporio.adaptadores.db.migrar`.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from emporio.adaptadores.db._acesso import (
    AcessoTenant,
    ErroConfiguracaoBanco,
    ErroTenantDesconhecido,
    criar_engine,
    url_do_banco,
)
from emporio.adaptadores.db.repositorios import (
    CatalogoPostgres,
    ClientesPostgres,
    PedidosPostgres,
    SolicitacoesPostgres,
)
from emporio.configuracao import Settings
from emporio.portas.tenant import ConfigTenant


@dataclass(frozen=True, slots=True)
class RepositoriosPostgres:
    """Os quatro repositórios sobre o mesmo pool. `fechar()` libera as conexões (encerramento da app)."""

    catalogo: CatalogoPostgres
    pedidos: PedidosPostgres
    clientes: ClientesPostgres
    solicitacoes: SolicitacoesPostgres
    _acesso: AcessoTenant

    def fechar(self) -> None:
        """Fecha o pool de conexões."""
        self._acesso.descartar()


def criar_repositorios_postgres(settings: Settings, tenants: Mapping[str, ConfigTenant]) -> RepositoriosPostgres:
    """Cria o pool (sem conectar ainda) e os repositórios. Exige `settings.database_url`."""
    acesso = AcessoTenant(criar_engine(url_do_banco(settings.database_url)), tenants)
    return RepositoriosPostgres(
        catalogo=CatalogoPostgres(acesso),
        pedidos=PedidosPostgres(acesso),
        clientes=ClientesPostgres(acesso),
        solicitacoes=SolicitacoesPostgres(acesso),
        _acesso=acesso,
    )


__all__ = [
    "CatalogoPostgres",
    "ClientesPostgres",
    "ErroConfiguracaoBanco",
    "ErroTenantDesconhecido",
    "PedidosPostgres",
    "RepositoriosPostgres",
    "SolicitacoesPostgres",
    "criar_repositorios_postgres",
]

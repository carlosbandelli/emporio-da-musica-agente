"""Resolução do diretório de dados por tenant e carga preguiçosa (single-flight por instância)."""

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path

from emporio.portas.tenant import ConfigTenant


class ErroTenantDesconhecido(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """O tenant do contexto não existe na configuração do adapter."""


class ResolvedorDados:
    """Resolve `tenant_id` para o diretório de dados (`dados_dir` relativo é resolvido a partir de `raiz`)."""

    def __init__(self, tenants: Mapping[str, ConfigTenant], raiz: Path | None = None) -> None:
        self._tenants = tenants
        self._raiz = raiz if raiz is not None else Path.cwd()

    def dados_dir(self, tenant_id: str) -> Path:
        """Diretório dos CSVs do tenant; levanta `ErroTenantDesconhecido` se o tenant não existe."""
        config = self._tenants.get(tenant_id)
        if config is None:
            disponiveis = ", ".join(sorted(self._tenants)) or "(nenhum)"
            raise ErroTenantDesconhecido(f"Tenant '{tenant_id}' desconhecido. Disponíveis: {disponiveis}")
        return self._raiz / config.dados_dir


class CargaPorTenant[T]:
    """Carrega uma vez por tenant (em thread, sob `asyncio.Lock`) e guarda o resultado na instância."""

    def __init__(self, resolvedor: ResolvedorDados, carregar: Callable[[Path], T]) -> None:
        self._resolvedor = resolvedor
        self._carregar = carregar
        self._lock = asyncio.Lock()
        self._cache: dict[str, T] = {}

    async def obter(self, tenant_id: str) -> T:
        """Dados do tenant, carregando na primeira chamada."""
        dados_dir = self._resolvedor.dados_dir(tenant_id)  # falha cedo para tenant desconhecido
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        async with self._lock:
            if tenant_id not in self._cache:
                self._cache[tenant_id] = await asyncio.to_thread(self._carregar, dados_dir)
            return self._cache[tenant_id]

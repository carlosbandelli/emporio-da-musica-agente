"""`SessaoStoreMemoria`: sessões em memória do processo, com TTL e isoladas por tenant."""

import time
from collections.abc import Callable

from emporio.portas.contexto import TenantContext
from emporio.portas.estado import EstadoSessao


class SessaoStoreMemoria:
    """Implementa `SessaoStore`. Estado por instância; `fonte_tempo` devolve segundos (monotônico) e é injetável."""

    def __init__(self, fonte_tempo: Callable[[], float] = time.monotonic) -> None:
        self._agora = fonte_tempo
        self._dados: dict[tuple[str, str], tuple[EstadoSessao, float]] = {}

    async def obter(self, ctx: TenantContext, sessao_id: str) -> EstadoSessao | None:
        """Estado da sessão, ou `None` se não existe ou expirou (e remove a expirada)."""
        chave = (ctx.tenant_id, sessao_id)
        item = self._dados.get(chave)
        if item is None:
            return None
        estado, expira_em = item
        if self._agora() >= expira_em:
            del self._dados[chave]
            return None
        return estado

    async def salvar(self, ctx: TenantContext, sessao_id: str, estado: EstadoSessao, ttl_s: int) -> None:
        """Grava o estado; expira `ttl_s` segundos depois."""
        self._limpar_expiradas()
        self._dados[(ctx.tenant_id, sessao_id)] = (estado, self._agora() + ttl_s)

    async def apagar(self, ctx: TenantContext, sessao_id: str) -> None:
        """Remove a sessão (sem erro se não existir)."""
        self._dados.pop((ctx.tenant_id, sessao_id), None)

    def _limpar_expiradas(self) -> None:
        agora = self._agora()
        for chave in [k for k, (_, exp) in self._dados.items() if agora >= exp]:
            del self._dados[chave]

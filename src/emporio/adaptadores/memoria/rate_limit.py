"""`RateLimiterMemoria`: token bucket por (tenant, camada, chave), com parâmetros da config do tenant."""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from emporio.portas.contexto import TenantContext
from emporio.portas.estado import ResultadoLimite
from emporio.portas.tenant import ConfigTenant, LimiteBalde

CAMADAS = ("sessao", "usuario", "tenant", "provedor")


class ErroLimiteConfiguracao(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Tenant ou camada sem configuração de limite."""


@dataclass(slots=True)
class _Balde:
    tokens: float
    atualizado_em: float


class RateLimiterMemoria:
    """Implementa `RateLimiter`. Sem `await` dentro de `consumir`, então é atômico no event loop."""

    def __init__(
        self,
        tenants: Mapping[str, ConfigTenant],
        fonte_tempo: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenants = tenants
        self._agora = fonte_tempo
        self._baldes: dict[tuple[str, str, str], _Balde] = {}

    def _limite(self, tenant_id: str, camada: str) -> LimiteBalde:
        config = self._tenants.get(tenant_id)
        if config is None:
            raise ErroLimiteConfiguracao(f"Tenant '{tenant_id}' sem configuração de limites")
        if camada not in CAMADAS:
            raise ErroLimiteConfiguracao(f"Camada de limite desconhecida: '{camada}' (válidas: {', '.join(CAMADAS)})")
        limite: LimiteBalde = getattr(config.limites, camada)
        return limite

    async def consumir(self, ctx: TenantContext, camada: str, chave: str) -> ResultadoLimite:
        """Consome 1 token; se faltar, nega com `retry_after_s` até haver 1 token."""
        limite = self._limite(ctx.tenant_id, camada)
        agora = self._agora()
        id_balde = (ctx.tenant_id, camada, chave)
        balde = self._baldes.get(id_balde)
        if balde is None:
            balde = _Balde(tokens=float(limite.capacidade), atualizado_em=agora)
            self._baldes[id_balde] = balde
        else:
            decorrido = max(0.0, agora - balde.atualizado_em)
            balde.tokens = min(float(limite.capacidade), balde.tokens + decorrido * limite.reposicao_por_s)
            balde.atualizado_em = agora
        if balde.tokens >= 1.0:
            balde.tokens -= 1.0
            return ResultadoLimite(permitido=True)
        return ResultadoLimite(permitido=False, retry_after_s=(1.0 - balde.tokens) / limite.reposicao_por_s)

"""Fábrica do estado em Redis: sessões, rate limit e cache do catálogo sobre um único cliente."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from redis.asyncio import Redis

from emporio.adaptadores.redis._guarda import TIMEOUT_S, GuardaRedis
from emporio.adaptadores.redis.cache_catalogo import CatalogoComCache
from emporio.adaptadores.redis.rate_limit import RateLimiterRedis
from emporio.adaptadores.redis.sessao import SessaoStoreRedis
from emporio.configuracao.settings import Settings
from emporio.portas.estado import RateLimiter, SessaoStore
from emporio.portas.repositorios import Catalogo
from emporio.portas.tenant import ConfigTenant


@dataclass(frozen=True, slots=True)
class EstadoRedis:
    """Peças de estado compartilhado. `cache_catalogo(repo)` devolve um `Catalogo` com cache-aside."""

    sessoes: SessaoStore
    limitador: RateLimiter
    cache_catalogo: Callable[[Catalogo], Catalogo]
    cliente: Redis

    async def fechar(self) -> None:
        """Fecha as conexões (chamar no shutdown)."""
        await self.cliente.aclose()


def criar_estado_redis(
    settings: Settings,
    tenants: Mapping[str, ConfigTenant],
    cliente: Redis | None = None,
) -> EstadoRedis:
    """Monta o estado em Redis a partir de `settings.redis_url` (ou de um `cliente` injetado, nos testes).

    Timeout de 300 ms, sem retry interno (1 tentativa). Nunca loga a URL.
    """
    if cliente is None:
        if settings.redis_url is None:
            raise ValueError("REDIS_URL não configurada")
        cliente = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=TIMEOUT_S,
            socket_connect_timeout=TIMEOUT_S,
            retry_on_timeout=False,
            decode_responses=False,
        )
    guarda = GuardaRedis()
    redis = cliente

    def _cache(repositorio: Catalogo) -> Catalogo:
        return CatalogoComCache(repositorio, redis, guarda)

    return EstadoRedis(
        sessoes=SessaoStoreRedis(redis, guarda),
        limitador=RateLimiterRedis(redis, tenants, guarda),
        cache_catalogo=_cache,
        cliente=redis,
    )

"""Adaptadores de estado compartilhado em Redis (sessão, rate limit, cache do catálogo)."""

from emporio.adaptadores.redis.cache_catalogo import CatalogoComCache
from emporio.adaptadores.redis.fabrica import EstadoRedis, criar_estado_redis
from emporio.adaptadores.redis.rate_limit import ErroLimiteConfiguracao, RateLimiterRedis
from emporio.adaptadores.redis.sessao import SessaoStoreRedis

__all__ = [
    "CatalogoComCache",
    "ErroLimiteConfiguracao",
    "EstadoRedis",
    "RateLimiterRedis",
    "SessaoStoreRedis",
    "criar_estado_redis",
]

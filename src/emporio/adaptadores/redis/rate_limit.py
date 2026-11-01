"""`RateLimiterRedis`: token bucket atômico em Lua, com bucket local de emergência (fail-open)."""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from redis.asyncio import Redis

from emporio.adaptadores.redis._guarda import GuardaRedis, RedisIndisponivelError
from emporio.portas.contexto import TenantContext
from emporio.portas.estado import ResultadoLimite
from emporio.portas.tenant import ConfigTenant, LimiteBalde

logger = logging.getLogger(__name__)

CAMADAS = ("sessao", "usuario", "tenant", "provedor")
MAX_BALDES_LOCAIS = 10_000

# Atômico no servidor; o relógio é o `TIME` do Redis (um só para todas as réplicas).
# ARGV: capacidade, reposição (tokens/s). Retorna {permitido (0/1), retry_after_s (texto)}.
SCRIPT_TOKEN_BUCKET = """
local cap = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local d = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(d[1])
local ts = tonumber(d[2])
if tokens == nil then
  tokens = cap
else
  tokens = math.min(cap, tokens + math.max(0, now - ts) * rate)
end
local permitido = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  permitido = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', string.format('%.6f', tokens), 'ts', string.format('%.6f', now))
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate * 1000) + 1000)
return {permitido, string.format('%.6f', retry)}
"""


class ErroLimiteConfiguracao(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Tenant ou camada sem configuração de limite (erro de configuração: não é fail-open)."""


def chave_balde(tenant_id: str, camada: str, chave: str) -> str:
    """Chave Redis do balde; sempre prefixada pelo tenant (inclusive a camada `provedor`)."""
    return f"emp:{tenant_id}:rl:{camada}:{chave}"


@dataclass(slots=True)
class _BaldeLocal:
    tokens: float
    atualizado_em: float


class RateLimiterRedis:
    """Implementa `RateLimiter`. Redis fora: usa baldes locais por instância (sem coordenação global)."""

    def __init__(
        self,
        cliente: Redis,
        tenants: Mapping[str, ConfigTenant],
        guarda: GuardaRedis | None = None,
        fonte_tempo: Callable[[], float] = time.monotonic,
    ) -> None:
        self._r = cliente
        self._tenants = tenants
        self._guarda = guarda or GuardaRedis()
        self._agora = fonte_tempo
        self._script = cliente.register_script(SCRIPT_TOKEN_BUCKET)  # EVALSHA; NOSCRIPT recai em EVAL
        self._locais: dict[tuple[str, str, str], _BaldeLocal] = {}

    def _limite(self, tenant_id: str, camada: str) -> LimiteBalde:
        config = self._tenants.get(tenant_id)
        if config is None:
            raise ErroLimiteConfiguracao(f"Tenant '{tenant_id}' sem configuração de limites")
        if camada not in CAMADAS:
            raise ErroLimiteConfiguracao(f"Camada de limite desconhecida: '{camada}' (válidas: {', '.join(CAMADAS)})")
        limite: LimiteBalde = getattr(config.limites, camada)
        return limite

    async def consumir(self, ctx: TenantContext, camada: str, chave: str) -> ResultadoLimite:
        """Consome 1 token; se faltar, nega com `retry_after_s`. Redis indisponível: bucket local."""
        limite = self._limite(ctx.tenant_id, camada)
        chave_redis = chave_balde(ctx.tenant_id, camada, chave)
        try:
            resposta = await self._guarda.executar(
                lambda: self._script(keys=[chave_redis], args=[limite.capacidade, limite.reposicao_por_s]),
                "rate_limit",
            )
        except RedisIndisponivelError:
            return self._consumir_local((ctx.tenant_id, camada, chave), limite)
        permitido, retry = resposta
        if int(permitido) == 1:
            return ResultadoLimite(permitido=True)
        return ResultadoLimite(permitido=False, retry_after_s=float(retry))

    def _consumir_local(self, id_balde: tuple[str, str, str], limite: LimiteBalde) -> ResultadoLimite:
        agora = self._agora()
        balde = self._locais.get(id_balde)
        if balde is None:
            if len(self._locais) >= MAX_BALDES_LOCAIS:
                self._locais.pop(next(iter(self._locais)))  # descarta o mais antigo
            balde = _BaldeLocal(tokens=float(limite.capacidade), atualizado_em=agora)
            self._locais[id_balde] = balde
        else:
            decorrido = max(0.0, agora - balde.atualizado_em)
            balde.tokens = min(float(limite.capacidade), balde.tokens + decorrido * limite.reposicao_por_s)
            balde.atualizado_em = agora
        if balde.tokens >= 1.0:
            balde.tokens -= 1.0
            return ResultadoLimite(permitido=True)
        return ResultadoLimite(permitido=False, retry_after_s=(1.0 - balde.tokens) / limite.reposicao_por_s)

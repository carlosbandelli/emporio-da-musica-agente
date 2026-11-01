"""Guarda de resiliência do Redis: timeout de 300 ms, 1 tentativa e pausa curta após falha (arquitetura §5.2)."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

TIMEOUT_S = 0.3
PAUSA_S = 5.0


class RedisIndisponivelError(Exception):
    """Redis fora do ar, lento (> timeout) ou em pausa após falha recente. Capturada dentro do adapter."""


class GuardaRedis:
    """Executa operações com timeout; após uma falha, pula o Redis por `pausa_s` (evita 300 ms por chamada).

    A pausa é por instância (cada réplica descobre sozinha). Passada a pausa, a próxima chamada é a sonda.
    """

    def __init__(
        self,
        timeout_s: float = TIMEOUT_S,
        pausa_s: float = PAUSA_S,
        fonte_tempo: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout_s = timeout_s
        self._pausa_s = pausa_s
        self._agora = fonte_tempo
        self._pausado_ate = 0.0

    @property
    def em_pausa(self) -> bool:
        """True enquanto o Redis está sendo ignorado após uma falha."""
        return self._agora() < self._pausado_ate

    async def executar[T](self, operacao: Callable[[], Awaitable[T]], nome: str) -> T:
        """Roda `operacao()` com timeout. Falhas viram `RedisIndisponivelError` (nunca expõe a URL)."""
        if self.em_pausa:
            raise RedisIndisponivelError(nome)
        try:
            async with asyncio.timeout(self._timeout_s):
                return await operacao()
        except (RedisError, OSError, TimeoutError) as exc:
            self._pausado_ate = self._agora() + self._pausa_s
            logger.warning(
                "Redis indisponível em %s (%s); degradando por %.0f s", nome, type(exc).__name__, self._pausa_s
            )
            raise RedisIndisponivelError(nome) from None

from collections.abc import AsyncIterator, Iterator

import fakeredis
import pytest
from redis.asyncio import Redis
from testcontainers.community.redis import RedisContainer


class Tempo:
    """Relógio monotônico controlável."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
async def fake() -> AsyncIterator[Redis]:
    cliente = fakeredis.FakeAsyncRedis()
    yield cliente
    await cliente.aclose()


@pytest.fixture(scope="session")
def redis_container() -> Iterator[tuple[str, int]]:
    """Redis 7 real (testcontainers) para o script Lua; pula quando não há Docker."""
    try:
        container = RedisContainer("redis:7-alpine")
        container.start()
    except Exception as exc:  # Docker ausente/indisponível
        pytest.skip(f"Docker/Redis indisponível: {type(exc).__name__}")
    try:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379))
    finally:
        container.stop()


@pytest.fixture
async def redis_real(redis_container: tuple[str, int]) -> AsyncIterator[Redis]:
    host, porta = redis_container
    cliente = Redis(host=host, port=porta)
    await cliente.flushdb()
    yield cliente
    await cliente.aclose()

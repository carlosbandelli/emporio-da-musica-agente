"""Contrato de `SessaoStore`: os mesmos casos de `tests/adaptadores/test_memoria.py` em memória e em Redis."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from redis.asyncio import Redis

from emporio.adaptadores.memoria import SessaoStoreMemoria
from emporio.adaptadores.redis import SessaoStoreRedis
from emporio.portas import EstadoSessao, Idioma, SessaoStore, TenantContext
from tests.adaptadores.redis.conftest import Tempo

A = TenantContext(tenant_id="emporio")
B = TenantContext(tenant_id="outro")

Avancar = Callable[[float], Awaitable[None]]


@pytest.fixture(params=["memoria", "redis"])
def store(request: pytest.FixtureRequest, fake: Redis) -> tuple[SessaoStore, Avancar]:
    if request.param == "memoria":
        tempo = Tempo()
        sessao_mem = SessaoStoreMemoria(tempo)

        async def avancar_mem(s: float) -> None:
            tempo.t += s

        return sessao_mem, avancar_mem

    async def avancar_redis(s: float) -> None:
        await asyncio.sleep(s)

    return SessaoStoreRedis(fake), avancar_redis


async def test_sessao_isola_tenants_e_expira(store: tuple[SessaoStore, Avancar]) -> None:
    s, avancar = store
    estado = EstadoSessao(idioma=Idioma.EN_US)
    await s.salvar(A, "s1", estado, ttl_s=2)
    assert await s.obter(A, "s1") == estado
    assert await s.obter(B, "s1") is None
    await avancar(1.0)
    assert await s.obter(A, "s1") == estado
    await avancar(1.1)
    assert await s.obter(A, "s1") is None


async def test_sessao_apagar(store: tuple[SessaoStore, Avancar]) -> None:
    s, _ = store
    await s.salvar(A, "s1", EstadoSessao(), ttl_s=60)
    await s.apagar(A, "s1")
    await s.apagar(A, "s1")
    assert await s.obter(A, "s1") is None

import asyncio
from collections.abc import Mapping

import pytest
from redis.asyncio import Redis

from emporio.adaptadores.redis import ErroLimiteConfiguracao, RateLimiterRedis
from emporio.adaptadores.redis._guarda import GuardaRedis
from emporio.adaptadores.redis.rate_limit import chave_balde
from emporio.portas import TenantContext
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores.redis.conftest import Tempo

A = TenantContext(tenant_id="emporio")
B = TenantContext(tenant_id="outro")

# (camada, capacidade, reposição/s) do tenants.yaml
CAMADAS = [("sessao", 10, 0.2), ("usuario", 30, 0.5), ("tenant", 120, 2.0), ("provedor", 30, 0.5)]


@pytest.mark.parametrize(("camada", "capacidade", "reposicao"), CAMADAS)
async def test_nega_na_camada_certa_com_retry_after(
    redis_real: Redis, tenants: Mapping[str, ConfigTenant], camada: str, capacidade: int, reposicao: float
) -> None:
    rl = RateLimiterRedis(redis_real, tenants)
    for _ in range(capacidade):
        assert (await rl.consumir(A, camada, "k")).permitido
    r = await rl.consumir(A, camada, "k")
    assert not r.permitido
    assert r.retry_after_s is not None
    # o balde repõe um pouco durante o laço: 0 < espera <= 1/reposição
    assert 0 < r.retry_after_s <= 1 / reposicao + 0.01
    # as outras camadas e outras chaves seguem livres
    for outra, _, _ in CAMADAS:
        if outra != camada:
            assert (await rl.consumir(A, outra, "k")).permitido
    assert (await rl.consumir(A, camada, "outra-chave")).permitido


async def test_repoe_com_o_tempo(redis_real: Redis, tenants: Mapping[str, ConfigTenant]) -> None:
    rl = RateLimiterRedis(redis_real, tenants)
    for _ in range(120):  # tenant: 2 tokens/s
        await rl.consumir(A, "tenant", "global")
    assert not (await rl.consumir(A, "tenant", "global")).permitido
    await asyncio.sleep(0.7)
    assert (await rl.consumir(A, "tenant", "global")).permitido


async def test_baldes_com_prefixo_de_tenant_e_ttl(redis_real: Redis, tenants: Mapping[str, ConfigTenant]) -> None:
    rl = RateLimiterRedis(redis_real, tenants)
    await rl.consumir(A, "sessao", "s1")
    await rl.consumir(A, "provedor", "groq")
    chaves = {k.decode() async for k in redis_real.scan_iter()}
    assert chaves == {"emp:emporio:rl:sessao:s1", "emp:emporio:rl:provedor:groq"}
    assert chave_balde("emporio", "tenant", "global") == "emp:emporio:rl:tenant:global"
    assert await redis_real.pttl("emp:emporio:rl:sessao:s1") > 0


async def test_atomico_sob_concorrencia(redis_real: Redis, tenants: Mapping[str, ConfigTenant]) -> None:
    rl = RateLimiterRedis(redis_real, tenants)
    resultados = await asyncio.gather(*(rl.consumir(A, "sessao", "c") for _ in range(25)))
    assert sum(r.permitido for r in resultados) == 10


async def test_fallback_eval_quando_script_nao_esta_em_cache(
    redis_real: Redis, tenants: Mapping[str, ConfigTenant]
) -> None:
    rl = RateLimiterRedis(redis_real, tenants)
    assert (await rl.consumir(A, "sessao", "x")).permitido  # carrega o script
    await redis_real.script_flush()  # NOSCRIPT na próxima: redis-py recai em EVAL
    assert (await rl.consumir(A, "sessao", "x")).permitido


def _redis_fora() -> Redis:
    return Redis(host="127.0.0.1", port=1, socket_timeout=0.3, socket_connect_timeout=0.3)


async def test_redis_fora_usa_bucket_local_de_emergencia(tenants: Mapping[str, ConfigTenant]) -> None:
    tempo = Tempo()
    cliente = _redis_fora()
    rl = RateLimiterRedis(cliente, tenants, GuardaRedis(), fonte_tempo=tempo)
    for _ in range(10):
        assert (await rl.consumir(A, "sessao", "s1")).permitido  # fail-open (não lança)
    r = await rl.consumir(A, "sessao", "s1")
    assert not r.permitido  # o limite continua valendo localmente
    assert r.retry_after_s == pytest.approx(5.0)
    assert (await rl.consumir(A, "sessao", "s2")).permitido
    tempo.t += 5
    assert (await rl.consumir(A, "sessao", "s1")).permitido
    await cliente.aclose()


async def test_erros_de_configuracao_nao_sao_fail_open(tenants: Mapping[str, ConfigTenant]) -> None:
    cliente = _redis_fora()
    rl = RateLimiterRedis(cliente, tenants)
    with pytest.raises(ErroLimiteConfiguracao):
        await rl.consumir(B, "sessao", "x")
    with pytest.raises(ErroLimiteConfiguracao):
        await rl.consumir(A, "inexistente", "x")
    await cliente.aclose()

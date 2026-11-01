from collections.abc import Mapping

import pytest

from emporio.adaptadores.memoria import (
    CircuitoStoreMemoria,
    ErroLimiteConfiguracao,
    PoliticaCircuito,
    RateLimiterMemoria,
    SessaoStoreMemoria,
)
from emporio.portas import EstadoCircuito, EstadoSessao, Idioma, TenantContext
from emporio.portas.tenant import ConfigTenant

A = TenantContext(tenant_id="emporio")
B = TenantContext(tenant_id="outro")


class Tempo:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def test_sessao_isola_tenants_e_expira() -> None:
    tempo = Tempo()
    store = SessaoStoreMemoria(tempo)
    estado = EstadoSessao(idioma=Idioma.EN_US)
    await store.salvar(A, "s1", estado, ttl_s=60)
    assert await store.obter(A, "s1") == estado
    assert await store.obter(B, "s1") is None
    tempo.t += 59
    assert await store.obter(A, "s1") == estado
    tempo.t += 1
    assert await store.obter(A, "s1") is None


async def test_sessao_apagar() -> None:
    store = SessaoStoreMemoria(Tempo())
    await store.salvar(A, "s1", EstadoSessao(), ttl_s=60)
    await store.apagar(A, "s1")
    await store.apagar(A, "s1")
    assert await store.obter(A, "s1") is None


async def test_rate_limiter_bloqueia_e_repoe(tenants: Mapping[str, ConfigTenant]) -> None:
    tempo = Tempo()
    rl = RateLimiterMemoria(tenants, tempo)
    # sessao: capacidade 10, 0.2 tokens/s
    for _ in range(10):
        assert (await rl.consumir(A, "sessao", "s1")).permitido
    r = await rl.consumir(A, "sessao", "s1")
    assert not r.permitido
    assert r.retry_after_s == pytest.approx(5.0)
    assert (await rl.consumir(A, "sessao", "s2")).permitido  # outra chave
    assert (await rl.consumir(A, "usuario", "s1")).permitido  # outra camada
    tempo.t += 2.5
    r = await rl.consumir(A, "sessao", "s1")
    assert not r.permitido
    assert r.retry_after_s == pytest.approx(2.5)
    tempo.t += 2.5
    assert (await rl.consumir(A, "sessao", "s1")).permitido
    tempo.t += 10_000
    for _ in range(10):  # recarga limitada à capacidade
        assert (await rl.consumir(A, "sessao", "s1")).permitido
    assert not (await rl.consumir(A, "sessao", "s1")).permitido


async def test_rate_limiter_erros(tenants: Mapping[str, ConfigTenant]) -> None:
    rl = RateLimiterMemoria(tenants, Tempo())
    with pytest.raises(ErroLimiteConfiguracao):
        await rl.consumir(B, "sessao", "x")
    with pytest.raises(ErroLimiteConfiguracao):
        await rl.consumir(A, "inexistente", "x")


async def _falhar(c: CircuitoStoreMemoria, n: int) -> None:
    for _ in range(n):
        await c.registrar_falha("groq", permanente=False)


async def test_circuito_abre_apos_5_falhas_consecutivas_e_meio_aberto() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(fonte_tempo=tempo)
    await _falhar(c, 4)
    assert await c.estado("groq") is EstadoCircuito.FECHADO
    await _falhar(c, 1)
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    assert await c.estado("gemini") is EstadoCircuito.FECHADO  # por provedor
    tempo.t += 29
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    tempo.t += 1
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO  # sonda
    assert await c.estado("groq") is EstadoCircuito.ABERTO  # só 1 sonda
    await c.registrar_sucesso("groq")
    assert await c.estado("groq") is EstadoCircuito.FECHADO


async def test_circuito_sonda_falha_reabre() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(fonte_tempo=tempo)
    await _falhar(c, 5)
    tempo.t += 30
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO
    await c.registrar_falha("groq", permanente=False)
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    tempo.t += 29
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    tempo.t += 1
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO


async def test_circuito_permanente_abre_na_hora_por_10_min() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(fonte_tempo=tempo)
    await c.registrar_falha("claude", permanente=True)
    assert await c.estado("claude") is EstadoCircuito.ABERTO
    tempo.t += 599
    assert await c.estado("claude") is EstadoCircuito.ABERTO
    tempo.t += 1
    assert await c.estado("claude") is EstadoCircuito.MEIO_ABERTO


async def test_circuito_taxa_de_falha_na_janela() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(fonte_tempo=tempo)
    for _ in range(5):  # alterna: nunca 5 consecutivas
        await c.registrar_falha("g", permanente=False)
        await c.registrar_sucesso("g")
    # 10 chamadas, 5 falhas (50%); a taxa é avaliada a cada falha
    assert await c.estado("g") is EstadoCircuito.FECHADO
    await c.registrar_falha("g", permanente=False)  # 11 chamadas, 6 falhas
    assert await c.estado("g") is EstadoCircuito.ABERTO


async def test_circuito_janela_descarta_antigas() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(PoliticaCircuito(), tempo)
    for _ in range(4):
        await c.registrar_falha("g", permanente=False)
        await c.registrar_sucesso("g")
    tempo.t += 31  # tudo fora da janela
    await c.registrar_falha("g", permanente=False)
    assert await c.estado("g") is EstadoCircuito.FECHADO


async def test_circuito_sucesso_zera_consecutivas() -> None:
    c = CircuitoStoreMemoria(fonte_tempo=Tempo())
    await _falhar(c, 4)
    await c.registrar_sucesso("groq")
    await _falhar(c, 4)
    assert await c.estado("groq") is EstadoCircuito.FECHADO

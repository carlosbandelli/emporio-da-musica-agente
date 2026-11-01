import asyncio
import random
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


class _JitterMaximo(random.Random):
    """Jitter determinístico: `uniform(a, b)` devolve `b`, ou seja, a duração cheia `d`."""

    def uniform(self, a: float, b: float) -> float:
        return b


def _circuito(tempo: Tempo) -> CircuitoStoreMemoria:
    return CircuitoStoreMemoria(fonte_tempo=tempo, aleatorio=_JitterMaximo())


def _duracao_aberta(c: CircuitoStoreMemoria, tempo: Tempo, provedor: str = "groq") -> float:
    return c._provedores[provedor].reabre_em - tempo.t


async def _falhar(c: CircuitoStoreMemoria, n: int) -> None:
    for _ in range(n):
        await c.registrar_falha("groq", permanente=False)


async def test_circuito_abre_apos_5_falhas_consecutivas_e_meio_aberto() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
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
    c = _circuito(tempo)
    await _falhar(c, 5)
    tempo.t += 30
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO
    await c.registrar_falha("groq", permanente=False)
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    tempo.t += 59  # reabertura consecutiva: 30 * 2 = 60 s
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    tempo.t += 1
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO


async def test_circuito_permanente_abre_na_hora_por_10_min() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
    await c.registrar_falha("claude", permanente=True)
    assert await c.estado("claude") is EstadoCircuito.ABERTO
    tempo.t += 599
    assert await c.estado("claude") is EstadoCircuito.ABERTO
    tempo.t += 1
    assert await c.estado("claude") is EstadoCircuito.MEIO_ABERTO


async def test_circuito_taxa_de_falha_na_janela() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
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


async def _abrir_e_falhar_sondas(
    c: CircuitoStoreMemoria, tempo: Tempo, *, permanente: bool, sondas: int
) -> list[float]:
    """Abre o circuito e faz `sondas` sondas falharem; devolve as durações de abertura sucessivas."""
    await c.registrar_falha("groq", permanente=permanente)
    if not permanente:
        await _falhar(c, 4)
    duracoes = [_duracao_aberta(c, tempo)]
    for _ in range(sondas):
        tempo.t += duracoes[-1]
        assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO
        await c.registrar_falha("groq", permanente=permanente)
        duracoes.append(_duracao_aberta(c, tempo))
    return duracoes


async def test_circuito_backoff_transitorio_com_jitter_e_teto() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(fonte_tempo=tempo, aleatorio=random.Random(7))
    duracoes = await _abrir_e_falhar_sondas(c, tempo, permanente=False, sondas=6)
    for dur, d in zip(duracoes, [30, 60, 120, 240, 300, 300, 300], strict=True):
        assert d / 2 <= dur <= d
    assert len({round(x, 6) for x in duracoes[-3:]}) > 1  # jitter realmente varia


async def test_circuito_backoff_permanente_ate_o_teto() -> None:
    tempo = Tempo()
    c = CircuitoStoreMemoria(fonte_tempo=tempo, aleatorio=random.Random(11))
    duracoes = await _abrir_e_falhar_sondas(c, tempo, permanente=True, sondas=4)
    for dur, d in zip(duracoes, [600, 1200, 2400, 3600, 3600], strict=True):
        assert d / 2 <= dur <= d


async def test_circuito_backoff_sem_jitter_e_exponencial_exato() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
    duracoes = await _abrir_e_falhar_sondas(c, tempo, permanente=False, sondas=5)
    assert duracoes == [30, 60, 120, 240, 300, 300]


async def test_circuito_tipo_da_reabertura_segue_a_falha_da_sonda() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
    await _falhar(c, 5)
    tempo.t += 30
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO
    await c.registrar_falha("groq", permanente=True)  # n=1 na escala permanente
    assert _duracao_aberta(c, tempo) == 1200


async def test_circuito_sucesso_da_sonda_zera_o_backoff() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
    duracoes = await _abrir_e_falhar_sondas(c, tempo, permanente=False, sondas=2)
    assert duracoes == [30, 60, 120]
    tempo.t += 120
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO
    await c.registrar_sucesso("groq")
    assert await c.estado("groq") is EstadoCircuito.FECHADO
    await _falhar(c, 5)
    assert _duracao_aberta(c, tempo) == 30  # volta à base


async def test_circuito_singleflight_da_sonda() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
    await _falhar(c, 5)
    tempo.t += 30
    estados = await asyncio.gather(*(c.estado("groq") for _ in range(50)))
    assert estados.count(EstadoCircuito.MEIO_ABERTO) == 1
    assert estados.count(EstadoCircuito.ABERTO) == 49


async def test_circuito_sonda_que_nunca_reporta_e_liberada() -> None:
    tempo = Tempo()
    c = _circuito(tempo)
    await _falhar(c, 5)
    tempo.t += 30
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO
    tempo.t += 29
    assert await c.estado("groq") is EstadoCircuito.ABERTO
    tempo.t += 1
    assert await c.estado("groq") is EstadoCircuito.MEIO_ABERTO

import asyncio
import logging

import pytest
from redis.asyncio import Redis

from emporio.adaptadores.redis import SessaoStoreRedis
from emporio.adaptadores.redis._guarda import GuardaRedis
from emporio.adaptadores.redis.sessao import chave_sessao, serializar_estado
from emporio.portas import EstadoSessao, Idioma, TenantContext
from emporio.portas.llm import ChamadaTool, ConteudoBruto, MensagemLLM, PapelMensagem
from tests.adaptadores.redis.conftest import Tempo

A = TenantContext(tenant_id="emporio")
B = TenantContext(tenant_id="outro")


def _historico_complexo() -> EstadoSessao:
    bruto_claude = ConteudoBruto(
        provedor="claude",
        blocos=(
            {"type": "thinking", "thinking": "Preciso do preço…", "signature": "EqQBCkYI/+abc=="},
            {"type": "tool_use", "id": "tu_1", "name": "detalhar_produto", "input": {"produto_id": 81, "x": 1.5}},
        ),
    )
    bruto_gemini = ConteudoBruto(
        provedor="gemini",
        blocos=({"function_call": {"name": "a", "args": {"n": [1, 2, {"k": None}]}}, "thought_signature": "c2ln\n"},),
    )
    return EstadoSessao(
        idioma=Idioma.PT_BR,
        historico=(
            MensagemLLM(PapelMensagem.USER, "Quanto custa o violão Takamine GD20? 🎸"),
            MensagemLLM(
                PapelMensagem.ASSISTANT,
                "",
                tool_calls=(ChamadaTool("tu_1", "detalhar_produto", {"produto_id": 81, "x": 1.5}),),
                bruto=bruto_claude,
            ),
            MensagemLLM(PapelMensagem.TOOL, "falhou: timeout", tool_call_id="tu_1", erro_tool=True),
            MensagemLLM(PapelMensagem.TOOL, '{"preco": 129900}', tool_call_id="tu_2", erro_tool=False),
            MensagemLLM(PapelMensagem.ASSISTANT, "Custa R$ 1.299,00", bruto=bruto_gemini),
        ),
    )


async def test_round_trip_preserva_bruto_tool_calls_e_erro_tool(fake: Redis) -> None:
    store = SessaoStoreRedis(fake)
    estado = _historico_complexo()
    await store.salvar(A, "s1", estado, ttl_s=60)
    lido = await store.obter(A, "s1")
    assert lido == estado
    assert lido is not None
    assert lido.historico[1].bruto == estado.historico[1].bruto
    assert lido.historico[1].tool_calls == estado.historico[1].tool_calls
    assert lido.historico[2].erro_tool is True
    assert lido.historico[3].erro_tool is False
    # byte a byte: reserializar o que foi lido dá exatamente o mesmo JSON
    assert serializar_estado(lido) == serializar_estado(estado)
    assert (await fake.get(chave_sessao("emporio", "s1"))) == serializar_estado(estado).encode()


async def test_idioma_none_e_historico_vazio(fake: Redis) -> None:
    store = SessaoStoreRedis(fake)
    await store.salvar(A, "s1", EstadoSessao(), ttl_s=60)
    assert await store.obter(A, "s1") == EstadoSessao()


async def test_chaves_sempre_com_prefixo_de_tenant(fake: Redis) -> None:
    store = SessaoStoreRedis(fake)
    await store.salvar(A, "s1", EstadoSessao(), ttl_s=60)
    await store.salvar(B, "s1", EstadoSessao(idioma=Idioma.EN_US), ttl_s=60)
    chaves = {k.decode() async for k in fake.scan_iter()}
    assert chaves == {"emp:emporio:sessao:s1", "emp:outro:sessao:s1"}
    assert await store.obter(B, "s1") == EstadoSessao(idioma=Idioma.EN_US)


async def test_ttl_aplicado(fake: Redis) -> None:
    await SessaoStoreRedis(fake).salvar(A, "s1", EstadoSessao(), ttl_s=90)
    assert 0 < await fake.ttl(chave_sessao("emporio", "s1")) <= 90


async def test_sessao_corrompida_vira_none(fake: Redis, caplog: pytest.LogCaptureFixture) -> None:
    await fake.set(chave_sessao("emporio", "s1"), b"{nao e json")
    with caplog.at_level(logging.ERROR):
        assert await SessaoStoreRedis(fake).obter(A, "s1") is None
    assert "corrompida" in caplog.text


def _redis_fora() -> Redis:
    return Redis(host="127.0.0.1", port=1, socket_timeout=0.3, socket_connect_timeout=0.3)


async def test_redis_fora_sessao_degradada_sem_lancar(caplog: pytest.LogCaptureFixture) -> None:
    cliente = _redis_fora()
    store = SessaoStoreRedis(cliente)
    with caplog.at_level(logging.WARNING):
        await store.salvar(A, "s1", _historico_complexo(), ttl_s=60)  # no-op
        assert await store.obter(A, "s1") is None
        await store.apagar(A, "s1")
    assert "Redis indisponível" in caplog.text
    await cliente.aclose()


class _Lento:
    async def get(self, _chave: str) -> bytes:
        await asyncio.sleep(5)
        return b""


class _Quebrado:
    async def get(self, _chave: str) -> bytes:
        raise ConnectionError("fora")


async def test_redis_lento_estoura_timeout() -> None:
    guarda = GuardaRedis(timeout_s=0.05)
    store = SessaoStoreRedis(_Lento(), guarda)  # type: ignore[arg-type]
    assert await store.obter(A, "s1") is None
    assert guarda.em_pausa


async def test_pausa_evita_nova_espera_e_retoma(fake: Redis) -> None:
    tempo = Tempo()
    guarda = GuardaRedis(pausa_s=5, fonte_tempo=tempo)
    store = SessaoStoreRedis(fake, guarda)
    await store.salvar(A, "s1", EstadoSessao(idioma=Idioma.PT_BR), ttl_s=60)
    quebrado = SessaoStoreRedis(_Quebrado(), guarda)  # type: ignore[arg-type]
    assert await quebrado.obter(A, "s1") is None
    assert guarda.em_pausa
    assert await store.obter(A, "s1") is None  # em pausa: nem tenta (degradada)
    tempo.t += 5
    assert await store.obter(A, "s1") == EstadoSessao(idioma=Idioma.PT_BR)  # sonda após a pausa

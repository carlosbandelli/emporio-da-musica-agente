"""Contrato de métricas (docs/deploy.md §6): 13 nomes, tipos e rótulos exatos; sem rótulo de alta cardinalidade."""

from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from prometheus_client.parser import text_string_to_metric_families

from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.llm import LLMFalso, chamar_tool, responder_texto
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.adaptadores.observabilidade import TelemetriaReal
from emporio.composicao import montar
from emporio.portas import ErroLLMTransitorio, EstadoCircuito, RequisicaoLLM, RespostaLLM, UsoTokens
from tests.adaptadores.api.apoio import BASE, RAIZ, cabecalhos, settings

CONTRATO: dict[str, tuple[str, set[str]]] = {
    "emporio_http_requisicoes_total": ("counter", {"tenant_id", "rota", "metodo", "status"}),
    "emporio_http_duracao_segundos": ("histogram", {"tenant_id", "rota"}),
    "emporio_rate_limit_bloqueios_total": ("counter", {"tenant_id", "camada"}),
    "emporio_roteamento_total": ("counter", {"tenant_id", "nivel"}),
    "emporio_llm_requisicoes_total": ("counter", {"tenant_id", "provedor", "nivel", "resultado"}),
    "emporio_llm_duracao_segundos": ("histogram", {"provedor", "nivel"}),
    "emporio_llm_tokens_total": ("counter", {"tenant_id", "provedor", "tipo"}),
    "emporio_llm_custo_usd_total": ("counter", {"tenant_id", "provedor"}),
    "emporio_llm_fallback_total": ("counter", {"tenant_id", "de", "para"}),
    "emporio_circuito_estado": ("gauge", {"provedor"}),
    "emporio_prompt_cache_total": ("counter", {"tenant_id", "provedor", "resultado"}),
    "emporio_tool_duracao_segundos": ("histogram", {"tenant_id", "tool"}),
    "emporio_tool_chamadas_total": ("counter", {"tenant_id", "tool", "resultado"}),
}


def _familias(texto: str) -> dict[str, tuple[str, set[frozenset[str]]]]:
    """Nome (com `_total` nos counters) -> (tipo, conjuntos de rótulos observados, sem `le`)."""
    saida: dict[str, tuple[str, set[frozenset[str]]]] = {}
    for fam in text_string_to_metric_families(texto):
        if fam.name.endswith("_created"):
            continue
        nome = fam.name + "_total" if fam.type == "counter" else fam.name
        rotulos = {frozenset(k for k in a.labels if k != "le") for a in fam.samples}
        saida[nome] = (fam.type, rotulos)
    return saida


def _emitir_tudo(t: TelemetriaReal) -> None:
    t.http_requisicao("emporio", "/v1/chat", "POST", 200, 0.1)
    t.rate_limit_negado("emporio", "sessao")
    t.roteamento("emporio", "simples")
    t.llm_chamada("emporio", "groq", "simples", "ok", duracao_s=0.2, uso=UsoTokens(10, 5, 2))
    t.llm_fallback("emporio", "groq", "gemini")
    t.circuito_estado("groq", EstadoCircuito.ABERTO)
    t.prompt_cache("emporio", "claude", hit=True)
    t.tool_executada("emporio", "buscar_produtos", True, 0.01)


def test_contrato_nomes_tipos_e_rotulos_exatos() -> None:
    t = TelemetriaReal()
    _emitir_tudo(t)
    familias = _familias(t.exportar_metricas()[0].decode())
    assert set(familias) == set(CONTRATO)
    assert len(CONTRATO) == 13
    for nome, (tipo, rotulos) in CONTRATO.items():
        assert familias[nome][0] == tipo, nome
        assert familias[nome][1] == {frozenset(rotulos)}, nome
        assert "session_id" not in rotulos


def test_registro_por_instancia_nao_vaza_estado() -> None:
    a, b = TelemetriaReal(), TelemetriaReal()
    a.roteamento("emporio", "media")
    assert b"emporio_roteamento_total{" not in b.exportar_metricas()[0]


def _app(llm: LLMFalso, tmp_path: Path, telemetria: TelemetriaReal) -> FastAPI:
    aplicacao = montar(settings(), provedores={"falso": llm}, raiz=RAIZ, dir_estado=tmp_path, telemetria=telemetria)
    return criar_app(lambda: aplicacao)


async def test_metrics_na_api_apos_chat_com_tool(tmp_path: Path) -> None:
    llm = LLMFalso()
    llm.roteirizar(chamar_tool("buscar_produtos", {"texto": "violão"}), responder_texto("Achei alguns violões."))
    app = _app(llm, tmp_path, TelemetriaReal())
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
    ):
        chat = await http.post(
            "/v1/chat", json={"session_id": "sessao-secreta", "mensagem": "violões?"}, headers=cabecalhos()
        )
        assert chat.status_code == 200 and chat.json()["tools_chamadas"] == ["buscar_produtos"]
        await http.post("/v1/chat", json={"session_id": "x", "mensagem": "oi"}, headers=cabecalhos("<hack>"))
        r = await http.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    familias = _familias(r.text)
    assert set(familias) == set(CONTRATO)
    for nome in ("emporio_tool_chamadas_total", "emporio_llm_requisicoes_total", "emporio_http_requisicoes_total"):
        assert all(rotulos == CONTRATO[nome][1] for rotulos in familias[nome][1]), nome
    assert "sessao-secreta" not in r.text and "session_id" not in r.text and "<hack>" not in r.text
    assert 'tenant_id="desconhecido"' in r.text and 'rota="/v1/chat"' in r.text
    assert 'tool="buscar_produtos"' in r.text and 'provedor="falso"' in r.text


async def test_roteador_emite_classificacao_fallback_tokens_e_falha() -> None:
    groq, gemini = LLMFalso(nome="groq"), LLMFalso(nome="gemini")
    gemini.roteirizar(ErroLLMTransitorio("fora"))
    groq.roteirizar(
        responder_texto("media"),
        RespostaLLM(texto="ok", provedor="groq", modelo="m", uso=UsoTokens(entrada=100, saida=20, cache_leitura=50)),
    )
    t = TelemetriaReal()
    roteador = Roteador({"groq": groq, "gemini": gemini}, telemetria=t)
    nivel = await roteador.classificar("preço?", tenant_id="emporio")
    resultado = await roteador.gerar(nivel, RequisicaoLLM("s", "d", ()), tenant_id="emporio")
    assert resultado.provedor == "groq"
    texto = t.exportar_metricas()[0].decode()
    assert (
        'emporio_llm_requisicoes_total{nivel="classificador",provedor="groq",resultado="ok",tenant_id="emporio"} 1.0'
        in texto
    )
    assert 'resultado="transitorio"' in texto
    assert 'emporio_llm_fallback_total{de="gemini",para="groq",tenant_id="emporio"} 1.0' in texto
    assert 'emporio_prompt_cache_total{provedor="groq",resultado="hit",tenant_id="emporio"} 1.0' in texto
    assert 'emporio_llm_tokens_total{provedor="groq",tenant_id="emporio",tipo="entrada"} 100.0' in texto


def test_custo_gemini_cobra_entrada_nao_cacheada_e_cache_sem_dupla_contagem() -> None:
    t = TelemetriaReal()
    # 7000 de prompt com 6000 em cache: o adapter entrega entrada=1000 (não cacheada) e cache_leitura=6000
    t.llm_chamada(
        "emporio", "gemini", "complexo", "ok", duracao_s=1.0, uso=UsoTokens(entrada=1000, saida=0, cache_leitura=6000)
    )
    valor = next(
        a.value
        for f in text_string_to_metric_families(t.exportar_metricas()[0].decode())
        for a in f.samples
        if a.name == "emporio_llm_custo_usd_total"
    )
    assert valor == pytest.approx((1000 * 0.30 + 6000 * 0.03) / 1_000_000)

"""Revisão automática do workflow de exemplo (`deploy/n8n/workflow-atendimento.json`, task 2.7).

Garante a regra da arquitetura §3.1: nenhuma regra de negócio no workflow. Na prática: nenhum nó de código, nenhum
valor monetário ou percentual escrito no JSON, e toda tool é uma chamada HTTP a `/v1/tools/{nome}` com o token de
serviço que o motor envia. Também detecta divergência entre as tools do workflow e as da API (`GET /v1/tools`).
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from emporio.composicao import montar_ferramentas
from emporio.configuracao import carregar_tenants
from emporio.portas import Papel
from tests.contrato.kit import RAIZ
from tests.modulos.fakes import RelogioFixo

WORKFLOW: Final = RAIZ / "deploy" / "n8n" / "workflow-atendimento.json"
TIPO_TOOL_HTTP: Final = "n8n-nodes-base.httpRequestTool"
WEBHOOK: Final = "$('Webhook').first().json.body"
TIPOS_DE_CODIGO: Final = ("n8n-nodes-base.code", "n8n-nodes-base.function", "n8n-nodes-base.functionItem")
TIPOS_PERMITIDOS: Final = frozenset(
    {
        "n8n-nodes-base.webhook",
        "@n8n/n8n-nodes-langchain.agent",
        "@n8n/n8n-nodes-langchain.lmChatGoogleGemini",
        "n8n-nodes-base.respondToWebhook",
        TIPO_TOOL_HTTP,
    }
)
"""Só orquestração: entrada, agente, modelo, tools HTTP e resposta. Nó novo exige revisão (e este teste muda)."""

_DINHEIRO: Final = re.compile(r"(?:R\$|\$|BRL|reais)\s*\d|\d\s*(?:reais|BRL)", re.IGNORECASE)
_PERCENTUAL: Final = re.compile(r"\d\s*%|\bpor\s*cento\b|percent", re.IGNORECASE)
_PRAZOS_E_LIMITES: Final = re.compile(
    r"\b\d+\s*(?:dias|x\b|vezes|horas)|\b(?:frete grátis|sem juros|desconto de)\b", re.IGNORECASE
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return dados


def _tools(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in workflow["nodes"] if n["type"] == TIPO_TOOL_HTTP]


def _definicoes(dir_estado: Path) -> dict[str, Any]:
    tenants = carregar_tenants(RAIZ / "config" / "tenants.yaml")
    fachada = montar_ferramentas(
        tenants, raiz=RAIZ, relogio=RelogioFixo(datetime(2026, 3, 10, tzinfo=UTC)), dir_estado=dir_estado
    )
    return {d.nome: d.parametros for d in fachada.definicoes(Papel.ATENDENTE)}


def test_so_nos_de_orquestracao_e_nenhum_no_de_codigo(workflow: dict[str, Any]) -> None:
    tipos = {n["type"] for n in workflow["nodes"]}
    assert not tipos & set(TIPOS_DE_CODIGO), "nó de código no workflow: regra de negócio não pode morar no n8n"
    assert tipos <= TIPOS_PERMITIDOS, f"tipos de nó não revisados: {sorted(tipos - TIPOS_PERMITIDOS)}"


def test_nenhum_valor_monetario_percentual_ou_prazo_escrito_no_workflow() -> None:
    texto = WORKFLOW.read_text(encoding="utf-8")
    for padrao in (_DINHEIRO, _PERCENTUAL, _PRAZOS_E_LIMITES):
        achado = padrao.search(texto)
        assert achado is None, f"valor de regra de negócio no workflow: {achado.group(0)!r}"


def test_toda_tool_e_http_para_v1_tools_com_o_token_do_motor(workflow: dict[str, Any]) -> None:
    tools = _tools(workflow)
    assert tools, "o workflow precisa chamar as tools da API"
    for no in tools:
        p = no["parameters"]
        assert p["method"] == "POST"
        assert p["url"] == f"={{{{ {WEBHOOK}.tools.url_base }}}}/{no['name']}", no["name"]
        cabecalhos = {h["name"]: h["value"] for h in p["headerParameters"]["parameters"]}
        assert cabecalhos["Authorization"] == f"=Bearer {{{{ {WEBHOOK}.tools.token }}}}"
        assert cabecalhos["X-Tenant-ID"] == f"={{{{ {WEBHOOK}.tenant_id }}}}"
        assert WEBHOOK + ".sessao_id" in p["jsonBody"] and "argumentos" in p["jsonBody"]
        assert p["options"]["response"]["response"]["neverError"] is True  # erro da tool volta ao LLM


def test_tools_do_workflow_sao_as_da_api_com_os_mesmos_campos(workflow: dict[str, Any], tmp_path: Path) -> None:
    definicoes = _definicoes(tmp_path)
    tools = {no["name"]: no for no in _tools(workflow)}
    assert set(tools) == set(definicoes), "tools do workflow divergem de GET /v1/tools"
    conexoes = workflow["connections"]
    for nome, no in tools.items():
        assert conexoes[nome]["ai_tool"][0][0]["node"] == "AI Agent"
        for campo in definicoes[nome].get("properties", {}):
            assert f"{campo} (" in no["parameters"]["jsonBody"], f"{nome}: campo {campo} ausente da descrição"


def test_webhook_autenticado_e_resposta_no_contrato(workflow: dict[str, Any]) -> None:
    nos = {n["name"]: n for n in workflow["nodes"]}
    webhook = nos["Webhook"]["parameters"]
    assert webhook["httpMethod"] == "POST" and webhook["authentication"] == "headerAuth"
    assert webhook["responseMode"] == "responseNode"
    agente = nos["AI Agent"]["parameters"]
    assert agente["text"] == "={{ $json.body.mensagem }}"
    sistema = agente["options"]["systemMessage"]
    assert "sistema.cacheavel" in sistema and "sistema.dinamico" in sistema and "historico" in sistema
    assert agente["options"]["returnIntermediateSteps"] is True
    corpo = nos["Respond to Webhook"]["parameters"]["responseBody"]
    for campo in ("texto:", "tools_chamadas:", "nome:", "argumentos:", "resultado:"):
        assert campo in corpo
    assert workflow["connections"]["Webhook"]["main"][0][0]["node"] == "AI Agent"
    assert workflow["connections"]["AI Agent"]["main"][0][0]["node"] == "Respond to Webhook"


def test_expressoes_nao_fecham_antes_da_hora(workflow: dict[str, Any]) -> None:
    """Cada `{{ ... }}` do n8n fecha uma vez só: `}}` dentro de uma expressão a cortaria no meio."""
    for no in workflow["nodes"]:
        for valor in _textos(no["parameters"]):
            if valor.startswith("="):
                assert valor.count("{{") == valor.count("}}"), f"{no['name']}: expressão desbalanceada"


def _textos(valor: object) -> list[str]:
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, dict):
        return [t for v in valor.values() for t in _textos(v)]
    if isinstance(valor, list):
        return [t for v in valor for t in _textos(v)]
    return []

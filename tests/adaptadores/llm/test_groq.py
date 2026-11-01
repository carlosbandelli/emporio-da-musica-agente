"""Adapter Groq com fake HTTP (`httpx.MockTransport`): formato OpenAI, tool calls e conversão de erros."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from emporio.adaptadores.llm import ProvedorGroq, cliente_groq
from emporio.portas import (
    ChamadaTool,
    ConteudoBruto,
    DefinicaoTool,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMSemCredito,
    ErroLLMTransitorio,
    LLMProvider,
    MensagemLLM,
    PapelMensagem,
    UsoTokens,
)

from .conftest import requisicao

TOOLS = (DefinicaoTool("buscar_produtos", "Busca", {"type": "object", "properties": {"q": {"type": "string"}}}),)


def completion(
    conteudo: str | None = None, tool_calls: list[dict[str, Any]] | None = None, cached: int | None = None
) -> dict[str, Any]:
    mensagem: dict[str, Any] = {"role": "assistant", "content": conteudo}
    if tool_calls:
        mensagem["tool_calls"] = tool_calls
    uso: dict[str, Any] = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
    if cached is not None:
        uso["prompt_tokens_details"] = {"cached_tokens": cached}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop", "message": mensagem}],
        "usage": uso,
    }


def chamada(id_: str, nome: str, argumentos: str) -> dict[str, Any]:
    return {"id": id_, "type": "function", "function": {"name": nome, "arguments": argumentos}}


class Servidor:
    def __init__(self, *respostas: httpx.Response | Callable[[httpx.Request], httpx.Response]) -> None:
        self.respostas = list(respostas)
        self.corpos: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.corpos.append(json.loads(request.content))
        item = self.respostas.pop(0)
        return item(request) if callable(item) else item


def provedor(servidor: Servidor, **kwargs: Any) -> ProvedorGroq:
    cliente = cliente_groq("chave-teste", http_client=httpx.AsyncClient(transport=httpx.MockTransport(servidor)))
    return ProvedorGroq(cliente, **kwargs)


def erro(status: int, mensagem: str = "erro", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": mensagem, "type": "x"}}, headers=headers)


def test_cliente_sem_retry_interno() -> None:
    assert cliente_groq("k").max_retries == 0


async def test_requisicao_formato_openai() -> None:
    servidor = Servidor(httpx.Response(200, json=completion("Oi!")))
    p: LLMProvider = provedor(servidor)
    resp = await p.gerar(requisicao(tools=TOOLS))
    corpo = servidor.corpos[0]
    assert corpo["model"] == "openai/gpt-oss-120b"
    assert corpo["messages"] == [
        {"role": "system", "content": "manual da loja\n\ndata: 2026-03-10"},
        {"role": "user", "content": "oi"},
    ]
    assert corpo["tools"] == [
        {
            "type": "function",
            "function": {"name": "buscar_produtos", "description": "Busca", "parameters": dict(TOOLS[0].parametros)},
        }
    ]
    assert corpo["tool_choice"] == "auto"
    assert corpo["reasoning_effort"] == "low"
    assert corpo["max_completion_tokens"] >= 4096
    assert (resp.texto, resp.provedor, resp.modelo) == ("Oi!", "groq", "openai/gpt-oss-120b")
    assert resp.bruto is None


async def test_sem_tools_nao_envia_tool_choice_e_esforco_opcional() -> None:
    servidor = Servidor(httpx.Response(200, json=completion("Oi!")))
    await provedor(servidor, esforco=None, modelo="llama-3.3-70b-versatile").gerar(requisicao())
    corpo = servidor.corpos[0]
    assert "tools" not in corpo
    assert "tool_choice" not in corpo
    assert "reasoning_effort" not in corpo
    assert corpo["model"] == "llama-3.3-70b-versatile"


async def test_tool_calls_e_uso() -> None:
    resposta = completion(tool_calls=[chamada("call_1", "buscar_produtos", '{"q": "violão"}')], cached=8)
    resp = await provedor(Servidor(httpx.Response(200, json=resposta))).gerar(requisicao(tools=TOOLS))
    assert resp.texto is None
    assert resp.chamadas_tool == (ChamadaTool("call_1", "buscar_produtos", {"q": "violão"}),)
    assert resp.uso == UsoTokens(entrada=4, saida=4, cache_leitura=8)  # entrada = prompt 12 - cache 8


async def test_argumentos_vazios_viram_dict_vazio() -> None:
    resposta = completion(tool_calls=[chamada("call_1", "buscar_produtos", "")])
    resp = await provedor(Servidor(httpx.Response(200, json=resposta))).gerar(requisicao())
    assert resp.chamadas_tool[0].argumentos == {}


@pytest.mark.parametrize("argumentos", ["{nao json", "[1, 2]"])
async def test_argumentos_invalidos_sao_erro_permanente(argumentos: str) -> None:
    resposta = completion(tool_calls=[chamada("call_1", "buscar_produtos", argumentos)])
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(httpx.Response(200, json=resposta))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)


async def test_historico_com_tools() -> None:
    historico = (
        MensagemLLM(PapelMensagem.USER, "violões?"),
        MensagemLLM(
            PapelMensagem.ASSISTANT,
            tool_calls=(ChamadaTool("call_1", "buscar_produtos", {"q": "violão"}),),
            bruto=ConteudoBruto("claude", ({"type": "thinking", "thinking": "x", "signature": "s"},)),
        ),
        MensagemLLM(PapelMensagem.TOOL, "[]", tool_call_id="call_1"),
        MensagemLLM(PapelMensagem.ASSISTANT, "Não achei."),
    )
    servidor = Servidor(httpx.Response(200, json=completion("ok")))
    await provedor(servidor).gerar(requisicao(*historico))
    assert servidor.corpos[0]["messages"][1:] == [
        {"role": "user", "content": "violões?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [chamada("call_1", "buscar_produtos", '{"q": "violão"}')],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "[]"},
        {"role": "assistant", "content": "Não achei."},
    ]


@pytest.mark.parametrize("status", [401, 403])
async def test_autenticacao(status: int) -> None:
    with pytest.raises(ErroLLMAutenticacao):
        await provedor(Servidor(erro(status, "Invalid API Key"))).gerar(requisicao())


async def test_429_com_retry_after() -> None:
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(erro(429, headers={"retry-after": "3.5"}))).gerar(requisicao())
    assert info.value.retry_after_s == 3.5


@pytest.mark.parametrize("status", [408, 409, 500, 502, 503])
async def test_transitorios(status: int) -> None:
    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(erro(status))).gerar(requisicao())


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_permanentes(status: int) -> None:
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(erro(status))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)


@pytest.mark.parametrize("excecao", [httpx.ConnectError("recusada"), httpx.ReadTimeout("lento")])
async def test_conexao_e_timeout(excecao: Exception) -> None:
    def falha(request: httpx.Request) -> httpx.Response:
        raise excecao

    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(falha)).gerar(requisicao())


# --- sem crédito / cota diária ---------------------------------------------------


def erro_groq(status: int, mensagem: str, codigo: str, headers: dict[str, str] | None = None) -> httpx.Response:
    corpo = {"error": {"message": mensagem, "type": "tokens", "code": codigo}}
    return httpx.Response(status, json=corpo, headers=headers)


@pytest.mark.parametrize(
    "mensagem",
    [
        "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on"
        " tokens per day (TPD): Limit 200000, Used 199500, Requested 900. Please try again in 4m19.2s.",
        "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` on requests per day (RPD):"
        " Limit 1000, Used 1000, Requested 1. Please try again in 1m26.4s.",
    ],
)
async def test_cota_diaria_vira_sem_credito(mensagem: str) -> None:
    resp = erro_groq(429, mensagem, "rate_limit_exceeded", headers={"retry-after": "8"})
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(resp)).gerar(requisicao())


@pytest.mark.parametrize("status", [400, 429])
async def test_insufficient_quota_vira_sem_credito(status: int) -> None:
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro_groq(status, "quota", "insufficient_quota"))).gerar(requisicao())


async def test_402_vira_sem_credito() -> None:
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(402, "payment required"))).gerar(requisicao())


async def test_limite_por_minuto_continua_transitorio() -> None:
    mensagem = "Rate limit reached ... on tokens per minute (TPM): Limit 8000, Used 7900. Please try again in 2.1s."
    resp = erro_groq(429, mensagem, "rate_limit_exceeded", headers={"retry-after": "3"})
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(resp)).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMSemCredito)
    assert info.value.retry_after_s == 3.0


async def test_429_com_retry_after_longo_vira_sem_credito() -> None:
    resp = erro_groq(429, "rate limit", "rate_limit_exceeded", headers={"retry-after": "900"})
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(resp)).gerar(requisicao())

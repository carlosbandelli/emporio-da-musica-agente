"""Adapter compatível com OpenAI com fake HTTP (`httpx2.MockTransport` do SDK `openai`): nenhuma rede."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from emporio.adaptadores.llm import ProvedorCompativelOpenAI, cliente_compativel_openai
from emporio.portas import (
    ChamadaTool,
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

TOOLS = (
    DefinicaoTool("buscar_produtos", "Busca", {"type": "object", "properties": {"q": {"type": "string"}}}),
    DefinicaoTool("consultar_politica", "Política", {"type": "object", "properties": {}}),
)


def completion(
    conteudo: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    uso: dict[str, Any] | None = None,
    modelo: str = "deepseek-chat",
) -> dict[str, Any]:
    mensagem: dict[str, Any] = {"role": "assistant", "content": conteudo}
    if tool_calls:
        mensagem["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": modelo,
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop", "message": mensagem}],
        "usage": uso or {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


def chamada(id_: str, nome: str, argumentos: str) -> dict[str, Any]:
    return {"id": id_, "type": "function", "function": {"name": nome, "arguments": argumentos}}


class Servidor:
    def __init__(self, *respostas: httpx2.Response | Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.respostas = list(respostas)
        self.requisicoes: list[httpx2.Request] = []
        self.corpos: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requisicoes.append(request)
        self.corpos.append(json.loads(request.content))
        item = self.respostas.pop(0)
        return item(request) if callable(item) else item


def provedor(
    servidor: Servidor, *, base_url: str | None = "https://api.deepseek.com", **kwargs: Any
) -> ProvedorCompativelOpenAI:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(servidor))
    cliente = cliente_compativel_openai("chave-teste", base_url=base_url, http_client=http)
    kwargs.setdefault("nome", "deepseek")
    kwargs.setdefault("modelo", "deepseek-chat")
    return ProvedorCompativelOpenAI(cliente, **kwargs)


def erro(status: int, mensagem: str = "erro", codigo: str | None = None, **headers: str) -> httpx2.Response:
    corpo = {"error": {"message": mensagem, "type": "x", "code": codigo}}
    return httpx2.Response(status, json=corpo, headers={k.replace("_", "-"): v for k, v in headers.items()})


def test_cliente_sem_retry_interno_e_base_url() -> None:
    assert cliente_compativel_openai("k").max_retries == 0
    assert str(cliente_compativel_openai("k").base_url).startswith("https://api.openai.com/v1")
    assert str(cliente_compativel_openai("k", base_url="http://localhost:11434/v1").base_url).startswith(
        "http://localhost:11434/v1"
    )


async def test_texto_simples_formato_openai_e_endpoint() -> None:
    servidor = Servidor(httpx2.Response(200, json=completion("Oi!")))
    p: LLMProvider = provedor(servidor)
    resp = await p.gerar(requisicao(tools=TOOLS))

    pedido = servidor.requisicoes[0]
    assert str(pedido.url) == "https://api.deepseek.com/chat/completions"
    assert pedido.headers["authorization"] == "Bearer chave-teste"
    corpo = servidor.corpos[0]
    assert corpo["model"] == "deepseek-chat"
    assert corpo["messages"] == [
        {"role": "system", "content": "manual da loja\n\ndata: 2026-03-10"},  # cacheável antes do dinâmico
        {"role": "user", "content": "oi"},
    ]
    assert [t["function"]["name"] for t in corpo["tools"]] == ["buscar_produtos", "consultar_politica"]
    assert corpo["tools"][0] == {
        "type": "function",
        "function": {"name": "buscar_produtos", "description": "Busca", "parameters": dict(TOOLS[0].parametros)},
    }
    assert corpo["tool_choice"] == "auto"
    assert corpo["max_tokens"] >= 4096
    assert "max_completion_tokens" not in corpo
    assert "reasoning_effort" not in corpo and "temperature" not in corpo
    assert (resp.texto, resp.provedor, resp.modelo, resp.bruto) == ("Oi!", "deepseek", "deepseek-chat", None)
    assert resp.chamadas_tool == ()


async def test_sem_tools_nao_envia_tools_e_openai_usa_max_completion_tokens() -> None:
    servidor = Servidor(httpx2.Response(200, json=completion("Oi!", modelo="gpt-4o-mini")))
    p = provedor(servidor, base_url=None, nome="openai", modelo="gpt-4o-mini", campo_max_tokens="max_completion_tokens")
    resp = await p.gerar(requisicao(max_tokens=8000))
    corpo = servidor.corpos[0]
    assert str(servidor.requisicoes[0].url) == "https://api.openai.com/v1/chat/completions"
    assert "tools" not in corpo and "tool_choice" not in corpo
    assert corpo["max_completion_tokens"] == 8000  # pedido maior que o piso vence
    assert "max_tokens" not in corpo
    assert resp.provedor == "openai"


async def test_tool_call_e_uso_com_cache() -> None:
    uso = {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
        "prompt_tokens_details": {"cached_tokens": 8},
    }
    resposta = completion(tool_calls=[chamada("call_1", "buscar_produtos", '{"q": "violão"}')], uso=uso)
    resp = await provedor(Servidor(httpx2.Response(200, json=resposta))).gerar(requisicao(tools=TOOLS))
    assert resp.texto is None
    assert resp.chamadas_tool == (ChamadaTool("call_1", "buscar_produtos", {"q": "violão"}),)
    assert resp.uso == UsoTokens(entrada=4, saida=4, cache_leitura=8)  # entrada = prompt 12 - cache 8


async def test_uso_com_cache_no_formato_deepseek() -> None:
    uso = {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23, "prompt_cache_hit_tokens": 15}
    resp = await provedor(Servidor(httpx2.Response(200, json=completion("ok", uso=uso)))).gerar(requisicao())
    assert resp.uso == UsoTokens(entrada=5, saida=3, cache_leitura=15)


async def test_uso_sem_cache() -> None:
    resp = await provedor(Servidor(httpx2.Response(200, json=completion("ok")))).gerar(requisicao())
    assert resp.uso == UsoTokens(entrada=12, saida=4, cache_leitura=0)


async def test_round_trip_de_tool_com_erro_como_texto() -> None:
    historico = (
        MensagemLLM(PapelMensagem.USER, "violões?"),
        MensagemLLM(PapelMensagem.ASSISTANT, tool_calls=(ChamadaTool("call_1", "buscar_produtos", {"q": "violão"}),)),
        MensagemLLM(PapelMensagem.TOOL, "erro: catálogo indisponível", tool_call_id="call_1", erro_tool=True),
        MensagemLLM(PapelMensagem.ASSISTANT, "Não consegui consultar agora."),
    )
    servidor = Servidor(httpx2.Response(200, json=completion("ok")))
    await provedor(servidor).gerar(requisicao(*historico))
    assert servidor.corpos[0]["messages"][1:] == [
        {"role": "user", "content": "violões?"},
        {"role": "assistant", "content": None, "tool_calls": [chamada("call_1", "buscar_produtos", '{"q": "violão"}')]},
        {"role": "tool", "tool_call_id": "call_1", "content": "erro: catálogo indisponível"},
        {"role": "assistant", "content": "Não consegui consultar agora."},
    ]


async def test_tool_call_sem_id_ganha_id_estavel_e_argumentos_vazios() -> None:
    sem_id = {"type": "function", "function": {"name": "consultar_politica", "arguments": ""}}
    resposta = completion(tool_calls=[chamada("call_a", "buscar_produtos", "{}"), sem_id])
    resp = await provedor(Servidor(httpx2.Response(200, json=resposta))).gerar(requisicao())
    assert resp.chamadas_tool == (
        ChamadaTool("call_a", "buscar_produtos", {}),
        ChamadaTool("call_1", "consultar_politica", {}),
    )


@pytest.mark.parametrize("argumentos", ["{nao json", "[1, 2]"])
async def test_argumentos_invalidos_sao_erro_permanente(argumentos: str) -> None:
    resposta = completion(tool_calls=[chamada("call_1", "buscar_produtos", argumentos)])
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(httpx2.Response(200, json=resposta))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)
    assert "deepseek" in str(info.value)


async def test_resposta_sem_choices_e_transitoria() -> None:
    corpo = completion("x")
    corpo["choices"] = []
    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(httpx2.Response(200, json=corpo))).gerar(requisicao())


# --- erros ------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_autenticacao(status: int) -> None:
    with pytest.raises(ErroLLMAutenticacao) as info:
        await provedor(Servidor(erro(status, "Incorrect API key provided"))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMSemCredito)
    assert "chave-teste" not in str(info.value)


async def test_402_vira_sem_credito() -> None:
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(402, "Insufficient Balance"))).gerar(requisicao())


async def test_insufficient_quota_da_openai_vira_sem_credito() -> None:
    resp = erro(429, "You exceeded your current quota.", "insufficient_quota", retry_after="2")
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(resp)).gerar(requisicao())


async def test_limite_por_dia_vira_sem_credito() -> None:
    resp = erro(429, "Rate limit reached on requests per day (RPD): Limit 200.", "rate_limit_exceeded", retry_after="5")
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(resp)).gerar(requisicao())


async def test_429_com_retry_after_e_transitorio() -> None:
    resp = erro(429, "Rate limit reached on tokens per min (TPM).", "rate_limit_exceeded", retry_after="3.5")
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(resp)).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMSemCredito)
    assert info.value.retry_after_s == 3.5


async def test_429_com_retry_after_ms() -> None:
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(erro(429, retry_after_ms="1500"))).gerar(requisicao())
    assert info.value.retry_after_s == 1.5


async def test_429_com_espera_longa_vira_sem_credito() -> None:
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(429, retry_after="900"))).gerar(requisicao())


@pytest.mark.parametrize("status", [408, 409, 500, 502, 503])
async def test_transitorios(status: int) -> None:
    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(erro(status))).gerar(requisicao())


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_permanentes(status: int) -> None:
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(erro(status, "model not found"))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)


@pytest.mark.parametrize(
    ("excecao", "texto"),
    [(httpx2.ConnectError("recusada"), "conexão"), (httpx2.ReadTimeout("lento"), "timeout")],
)
async def test_conexao_e_timeout(excecao: Exception, texto: str) -> None:
    def falha(request: httpx2.Request) -> httpx2.Response:
        raise excecao

    with pytest.raises(ErroLLMTransitorio, match=texto):
        await provedor(Servidor(falha)).gerar(requisicao())

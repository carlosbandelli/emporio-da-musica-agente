"""Adapter Claude com fake HTTP (`httpx2.MockTransport`): mapeamento de mensagens, bruto, recuperação e erros."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from emporio.adaptadores.llm import ProvedorClaude, cliente_claude
from emporio.portas import (
    ChamadaTool,
    ConteudoBruto,
    DefinicaoTool,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMRecusa,
    ErroLLMTransitorio,
    LLMProvider,
    MensagemLLM,
    PapelMensagem,
    UsoTokens,
)

from .conftest import requisicao

Manipulador = Callable[[httpx2.Request], httpx2.Response]

TOOLS = (
    DefinicaoTool("buscar_produtos", "Busca no catálogo", {"type": "object", "properties": {"q": {"type": "string"}}}),
    DefinicaoTool(
        "detalhar_produto", "Detalha um produto", {"type": "object", "properties": {"id": {"type": "integer"}}}
    ),
)

THINKING = {"type": "thinking", "thinking": "o cliente quer o preço", "signature": "sig-abc"}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "detalhar_produto", "input": {"id": 93}}


def mensagem(conteudo: list[dict[str, Any]], stop: str = "end_turn", **uso: int) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": conteudo,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, **uso},
    }


class Servidor:
    """Fake da API: responde em sequência e grava requisições (corpo JSON e headers)."""

    def __init__(self, *respostas: httpx2.Response | Manipulador) -> None:
        self.respostas = list(respostas)
        self.corpos: list[dict[str, Any]] = []
        self.requisicoes: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requisicoes.append(request)
        self.corpos.append(json.loads(request.content))
        item = self.respostas.pop(0)
        return item(request) if callable(item) else item


def provedor(servidor: Servidor, **kwargs: Any) -> ProvedorClaude:
    cliente = cliente_claude("chave-teste", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(servidor)))
    return ProvedorClaude(cliente, **kwargs)


def ok(conteudo: list[dict[str, Any]], **kw: Any) -> httpx2.Response:
    return httpx2.Response(200, json=mensagem(conteudo, **kw))


def erro(status: int, mensagem_erro: str = "erro", headers: dict[str, str] | None = None) -> httpx2.Response:
    tipo = {400: "invalid_request_error", 401: "authentication_error", 429: "rate_limit_error"}.get(status, "api_error")
    corpo = {"type": "error", "error": {"type": tipo, "message": mensagem_erro}}
    return httpx2.Response(status, json=corpo, headers=headers)


def test_cliente_sem_retry_interno() -> None:
    assert cliente_claude("k").max_retries == 0


async def test_requisicao_segue_as_regras_do_opus_5_5() -> None:
    servidor = Servidor(ok([{"type": "text", "text": "Oi!"}]))
    p: LLMProvider = provedor(servidor)
    await p.gerar(requisicao(tools=TOOLS, timeout_s=25.0))
    corpo = servidor.corpos[0]
    assert corpo["model"] == "claude-opus-5-5"
    assert corpo["system"] == [
        {"type": "text", "text": "manual da loja", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "data: 2026-03-10"},
    ]
    assert corpo["tools"] == [
        {"name": t.nome, "description": t.descricao, "input_schema": dict(t.parametros)} for t in TOOLS
    ]
    assert corpo["max_tokens"] >= 16000
    assert corpo["output_config"] == {"effort": "low"}
    assert corpo["fallbacks"] == "default"
    for proibido in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert proibido not in corpo
    assert corpo["messages"] == [{"role": "user", "content": "oi"}]
    headers = servidor.requisicoes[0].headers
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert headers["authorization"] == "Bearer chave-teste"  # o SDK 2.x envia a API key como Bearer


async def test_flag_de_fallback_desligado_e_esforco_configuravel() -> None:
    servidor = Servidor(ok([{"type": "text", "text": "Oi!"}]))
    await provedor(servidor, fallback_servidor=False, esforco="medium", modelo="outro").gerar(requisicao())
    corpo = servidor.corpos[0]
    assert "fallbacks" not in corpo
    assert corpo["output_config"] == {"effort": "medium"}
    assert corpo["model"] == "outro"
    assert "server-side-fallback" not in servidor.requisicoes[0].headers.get("anthropic-beta", "")


async def test_resposta_le_texto_tools_uso_e_guarda_bruto() -> None:
    conteudo = [THINKING, {"type": "text", "text": "Vou consultar."}, TOOL_USE]
    servidor = Servidor(ok(conteudo, stop="tool_use", cache_read_input_tokens=7))
    resp = await provedor(servidor).gerar(requisicao(tools=TOOLS))
    assert resp.texto == "Vou consultar."  # só blocos `text`, nunca o raciocínio
    assert resp.chamadas_tool == (ChamadaTool("toolu_1", "detalhar_produto", {"id": 93}),)
    assert resp.uso == UsoTokens(entrada=10, saida=5, cache_leitura=7)
    assert (resp.provedor, resp.modelo) == ("claude", "claude-opus-5-5")
    assert resp.bruto == ConteudoBruto("claude", tuple(conteudo))


async def test_resposta_so_com_tool_tem_texto_none() -> None:
    resp = await provedor(Servidor(ok([THINKING, TOOL_USE], stop="tool_use"))).gerar(requisicao())
    assert resp.texto is None


async def test_bruto_do_claude_volta_inalterado_e_tool_results_sao_agrupados() -> None:
    bruto = ConteudoBruto("claude", (THINKING, TOOL_USE, {**TOOL_USE, "id": "toolu_2", "input": {"id": 96}}))
    historico = (
        MensagemLLM(PapelMensagem.USER, "quanto custa o 93 e o 96?"),
        MensagemLLM(
            PapelMensagem.ASSISTANT,
            tool_calls=(ChamadaTool("toolu_1", "detalhar_produto", {"id": 93}),),
            bruto=bruto,
        ),
        MensagemLLM(PapelMensagem.TOOL, '{"preco": 999}', tool_call_id="toolu_1"),
        MensagemLLM(PapelMensagem.TOOL, "produto não encontrado", tool_call_id="toolu_2", erro_tool=True),
    )
    servidor = Servidor(ok([{"type": "text", "text": "R$ 999"}]))
    await provedor(servidor).gerar(requisicao(*historico))
    assert servidor.corpos[0]["messages"] == [
        {"role": "user", "content": "quanto custa o 93 e o 96?"},
        {"role": "assistant", "content": [dict(b) for b in bruto.blocos]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"preco": 999}'},
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "content": "produto não encontrado",
                    "is_error": True,
                },
            ],
        },
    ]


async def test_assistant_de_outro_provedor_e_montado_de_content_e_tool_calls() -> None:
    historico = (
        MensagemLLM(PapelMensagem.USER, "oi"),
        MensagemLLM(
            PapelMensagem.ASSISTANT,
            "Vou ver",
            tool_calls=(ChamadaTool("call_9", "buscar_produtos", {"q": "violão"}),),
            bruto=ConteudoBruto("gemini", ({"text": "Vou ver", "thought_signature": "eA=="},)),
        ),
        MensagemLLM(PapelMensagem.TOOL, "[]", tool_call_id="call_9"),
        MensagemLLM(PapelMensagem.USER, "e aí?"),
    )
    servidor = Servidor(ok([{"type": "text", "text": "nada"}]))
    await provedor(servidor).gerar(requisicao(*historico))
    assert servidor.corpos[0]["messages"][1:] == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Vou ver"},
                {"type": "tool_use", "id": "call_9", "name": "buscar_produtos", "input": {"q": "violão"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_9", "content": "[]"},
                {"type": "text", "text": "e aí?"},
            ],
        },
    ]


async def test_mensagem_system_no_historico_vai_para_o_system() -> None:
    historico = (MensagemLLM(PapelMensagem.SYSTEM, "regra extra"), MensagemLLM(PapelMensagem.USER, "oi"))
    servidor = Servidor(ok([{"type": "text", "text": "ok"}]))
    await provedor(servidor).gerar(requisicao(*historico))
    corpo = servidor.corpos[0]
    assert corpo["system"][-1] == {"type": "text", "text": "regra extra"}
    assert corpo["messages"] == [{"role": "user", "content": "oi"}]


async def test_400_de_thinking_remove_os_blocos_e_tenta_uma_vez() -> None:
    bruto = ConteudoBruto("claude", (THINKING, {"type": "redacted_thinking", "data": "xyz"}, TOOL_USE))
    historico = (
        MensagemLLM(PapelMensagem.USER, "preço do 93?"),
        MensagemLLM(
            PapelMensagem.ASSISTANT, tool_calls=(ChamadaTool("toolu_1", "detalhar_produto", {"id": 93}),), bruto=bruto
        ),
        MensagemLLM(PapelMensagem.TOOL, '{"preco": 999}', tool_call_id="toolu_1"),
    )
    servidor = Servidor(
        erro(400, "messages.1.content.0: Invalid `signature` in `thinking` block"),
        ok([{"type": "text", "text": "R$ 999"}]),
    )
    resp = await provedor(servidor).gerar(requisicao(*historico))
    assert resp.texto == "R$ 999"
    assert len(servidor.corpos) == 2
    assert servidor.corpos[0]["messages"][1]["content"] == [dict(b) for b in bruto.blocos]
    assert servidor.corpos[1]["messages"][1]["content"] == [TOOL_USE]


async def test_400_de_thinking_repetido_desiste_apos_uma_tentativa() -> None:
    historico = (
        MensagemLLM(PapelMensagem.USER, "oi"),
        MensagemLLM(
            PapelMensagem.ASSISTANT, "olá", bruto=ConteudoBruto("claude", (THINKING, {"type": "text", "text": "olá"}))
        ),
        MensagemLLM(PapelMensagem.USER, "tudo bem?"),
    )
    servidor = Servidor(erro(400, "thinking block signature mismatch"), erro(400, "thinking block signature mismatch"))
    with pytest.raises(ErroLLMPermanente):
        await provedor(servidor).gerar(requisicao(*historico))
    assert len(servidor.corpos) == 2


async def test_400_sem_relacao_com_thinking_nao_retenta() -> None:
    servidor = Servidor(erro(400, "max_tokens: too large"))
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(servidor).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)
    assert len(servidor.corpos) == 1


async def test_recusa_vira_erro_permanente_de_requisicao() -> None:
    servidor = Servidor(ok([], stop="refusal"))
    with pytest.raises(ErroLLMRecusa):
        await provedor(servidor).gerar(requisicao())


@pytest.mark.parametrize("status", [401, 403])
async def test_autenticacao(status: int) -> None:
    with pytest.raises(ErroLLMAutenticacao):
        await provedor(Servidor(erro(status, "invalid x-api-key"))).gerar(requisicao())


async def test_429_com_retry_after() -> None:
    servidor = Servidor(erro(429, "rate limit", headers={"retry-after": "2"}))
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(servidor).gerar(requisicao())
    assert info.value.retry_after_s == 2.0


@pytest.mark.parametrize("status", [408, 409, 500, 503, 504, 529])
async def test_transitorios_por_status(status: int) -> None:
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(erro(status))).gerar(requisicao())
    assert info.value.retry_after_s is None


@pytest.mark.parametrize("status", [404, 413, 422])
async def test_permanentes_por_status(status: int) -> None:
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(erro(status))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)


@pytest.mark.parametrize("excecao", [httpx2.ConnectError("recusada"), httpx2.ReadTimeout("lento")])
async def test_conexao_e_timeout_sao_transitorios(excecao: Exception) -> None:
    def falha(request: httpx2.Request) -> httpx2.Response:
        raise excecao

    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(falha)).gerar(requisicao())

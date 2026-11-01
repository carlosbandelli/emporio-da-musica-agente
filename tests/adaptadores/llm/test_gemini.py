"""Adapter Gemini (google-genai) com fake HTTP (`httpx.MockTransport` no cliente async do SDK)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from google.genai import types

from emporio.adaptadores.llm import ProvedorGemini, cliente_gemini
from emporio.portas import (
    ChamadaTool,
    ConteudoBruto,
    DefinicaoTool,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMRecusa,
    ErroLLMSemCredito,
    ErroLLMTransitorio,
    LLMProvider,
    MensagemLLM,
    PapelMensagem,
    RespostaLLM,
    UsoTokens,
)

from .conftest import requisicao

TOOLS = (DefinicaoTool("detalhar_produto", "Detalha", {"type": "object", "properties": {"id": {"type": "integer"}}}),)


def resposta(partes: list[dict[str, Any]], finish: str = "STOP", **extra: Any) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": partes}, "finishReason": finish}],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 6,
            "thoughtsTokenCount": 3,
            "cachedContentTokenCount": 11,
        },
        "modelVersion": "gemini-3-flash",
        "responseId": "r-1",
        **extra,
    }


class Servidor:
    def __init__(self, *respostas: httpx.Response | Callable[[httpx.Request], httpx.Response]) -> None:
        self.respostas = list(respostas)
        self.corpos: list[dict[str, Any]] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.corpos.append(json.loads(request.content))
        item = self.respostas.pop(0)
        return item(request) if callable(item) else item


def provedor(servidor: Servidor, **kwargs: Any) -> ProvedorGemini:
    cliente = cliente_gemini("chave-teste", http_client=httpx.AsyncClient(transport=httpx.MockTransport(servidor)))
    return ProvedorGemini(cliente, **kwargs)


def ok(partes: list[dict[str, Any]], **kw: Any) -> httpx.Response:
    return httpx.Response(200, json=resposta(partes, **kw))


def erro(
    status: int, situacao: str, mensagem: str = "erro", detalhes: list[dict[str, Any]] | None = None
) -> httpx.Response:
    corpo = {"error": {"code": status, "message": mensagem, "status": situacao, "details": detalhes or []}}
    return httpx.Response(status, json=corpo)


async def test_requisicao_tools_e_system() -> None:
    servidor = Servidor(ok([{"text": "Oi!"}]))
    p: LLMProvider = provedor(servidor)
    resp = await p.gerar(requisicao(tools=TOOLS))
    assert servidor.urls[0].endswith("/models/gemini-flash-latest:generateContent")
    corpo = servidor.corpos[0]
    assert corpo["systemInstruction"]["parts"] == [{"text": "manual da loja\n\ndata: 2026-03-10"}]
    assert corpo["contents"] == [{"role": "user", "parts": [{"text": "oi"}]}]
    declaracao = corpo["tools"][0]["functionDeclarations"][0]
    assert declaracao["name"] == "detalhar_produto"
    # o SDK serializa este campo em snake_case; o JSON do protobuf aceita os dois formatos
    assert declaracao["parameters_json_schema"] == dict(TOOLS[0].parametros)
    assert corpo["generationConfig"]["maxOutputTokens"] >= 8192
    assert (resp.texto, resp.provedor, resp.modelo) == ("Oi!", "gemini", "gemini-3-flash")
    assert resp.uso == UsoTokens(entrada=20, saida=9, cache_leitura=11)


async def test_config_desliga_afc_e_repassa_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = cliente_gemini("k")
    capturado: dict[str, Any] = {}

    async def falso_generate(*, model: str, contents: Any, config: types.GenerateContentConfig) -> Any:
        capturado.update(model=model, config=config)
        return types.GenerateContentResponse.model_validate(resposta([{"text": "ok"}]))

    monkeypatch.setattr(cliente.aio.models, "generate_content", falso_generate)
    await ProvedorGemini(cliente, modelo="gemini-x").gerar(requisicao(tools=TOOLS, timeout_s=15.0))
    config: types.GenerateContentConfig = capturado["config"]
    assert capturado["model"] == "gemini-x"
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    assert config.http_options is not None
    assert config.http_options.timeout == 15000


def test_cliente_sem_retry_interno() -> None:
    opcoes = cliente_gemini("k")._api_client._http_options
    assert opcoes.retry_options is not None
    assert opcoes.retry_options.attempts == 1


async def test_function_calls_com_e_sem_id_e_bruto_com_thought_signature() -> None:
    partes = [
        {"text": "pensando", "thought": True},
        {"functionCall": {"name": "detalhar_produto", "args": {"id": 93}}, "thoughtSignature": "c2lnLTE="},
        {"functionCall": {"id": "fc-2", "name": "detalhar_produto", "args": {"id": 96}}},
    ]
    resp = await provedor(Servidor(ok(partes))).gerar(requisicao(tools=TOOLS))
    assert resp.texto is None  # partes `thought` não viram texto
    primeira, segunda = resp.chamadas_tool
    assert (primeira.nome, primeira.argumentos) == ("detalhar_produto", {"id": 93})
    assert primeira.id  # gerado pelo adapter
    assert segunda == ChamadaTool("fc-2", "detalhar_produto", {"id": 96})
    assert resp.bruto is not None
    assert resp.bruto.provedor == "gemini"
    assert len(resp.bruto.blocos) == 3
    json.dumps(list(resp.bruto.blocos))  # serializável


async def _rodada_com_tools() -> tuple[RespostaLLM, Servidor]:
    partes = [
        {"functionCall": {"name": "detalhar_produto", "args": {"id": 93}}, "thoughtSignature": "c2lnLTE="},
        {"functionCall": {"id": "fc-2", "name": "detalhar_produto", "args": {"id": 96}}},
    ]
    primeira = await provedor(Servidor(ok(partes))).gerar(requisicao(tools=TOOLS))
    return primeira, Servidor(ok([{"text": "R$ 999 e R$ 1.299"}]))


async def test_round_trip_do_bruto_e_function_responses_agrupadas() -> None:
    primeira, servidor = await _rodada_com_tools()
    c1, c2 = primeira.chamadas_tool
    historico = (
        MensagemLLM(PapelMensagem.USER, "preço do 93 e do 96?"),
        MensagemLLM(PapelMensagem.ASSISTANT, tool_calls=primeira.chamadas_tool, bruto=primeira.bruto),
        MensagemLLM(PapelMensagem.TOOL, '{"preco": 999}', tool_call_id=c1.id),
        MensagemLLM(PapelMensagem.TOOL, "fora de estoque", tool_call_id=c2.id, erro_tool=True),
    )
    resp = await provedor(servidor).gerar(requisicao(*historico, tools=TOOLS))
    assert resp.texto == "R$ 999 e R$ 1.299"
    contents = servidor.corpos[0]["contents"]
    assert contents[1] == {
        "role": "model",
        "parts": [
            {"functionCall": {"name": "detalhar_produto", "args": {"id": 93}}, "thoughtSignature": "c2lnLTE="},
            {"functionCall": {"id": "fc-2", "name": "detalhar_produto", "args": {"id": 96}}},
        ],
    }
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "detalhar_produto", "response": {"output": '{"preco": 999}'}}},
            {"functionResponse": {"id": "fc-2", "name": "detalhar_produto", "response": {"error": "fora de estoque"}}},
        ],
    }


async def test_assistant_de_outro_provedor_e_montado_sem_ids() -> None:
    historico = (
        MensagemLLM(PapelMensagem.USER, "oi"),
        MensagemLLM(
            PapelMensagem.ASSISTANT,
            "Vou ver",
            tool_calls=(ChamadaTool("toolu_1", "detalhar_produto", {"id": 93}),),
            bruto=ConteudoBruto("claude", ({"type": "thinking", "thinking": "x", "signature": "s"},)),
        ),
        MensagemLLM(PapelMensagem.TOOL, "{}", tool_call_id="toolu_1"),
        MensagemLLM(PapelMensagem.SYSTEM, "regra extra"),
    )
    servidor = Servidor(ok([{"text": "ok"}]))
    await provedor(servidor).gerar(requisicao(*historico))
    corpo = servidor.corpos[0]
    assert corpo["contents"][1:] == [
        {
            "role": "model",
            "parts": [{"text": "Vou ver"}, {"functionCall": {"name": "detalhar_produto", "args": {"id": 93}}}],
        },
        {"role": "user", "parts": [{"functionResponse": {"name": "detalhar_produto", "response": {"output": "{}"}}}]},
    ]
    assert corpo["systemInstruction"]["parts"][0]["text"].endswith("\n\nregra extra")


async def test_prompt_bloqueado_e_recusa() -> None:
    corpo = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 3}}
    with pytest.raises(ErroLLMRecusa):
        await provedor(Servidor(httpx.Response(200, json=corpo))).gerar(requisicao())


async def test_finish_reason_safety_e_recusa() -> None:
    with pytest.raises(ErroLLMRecusa):
        await provedor(Servidor(ok([], finish="SAFETY"))).gerar(requisicao())


async def test_function_call_malformada_e_permanente() -> None:
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(ok([], finish="MALFORMED_FUNCTION_CALL"))).gerar(requisicao())
    assert not isinstance(info.value, (ErroLLMRecusa, ErroLLMAutenticacao))


async def test_chave_invalida_vem_como_400_e_vira_autenticacao() -> None:
    detalhes = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]
    resp = erro(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.", detalhes)
    with pytest.raises(ErroLLMAutenticacao):
        await provedor(Servidor(resp)).gerar(requisicao())


@pytest.mark.parametrize(("status", "situacao"), [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
async def test_autenticacao(status: int, situacao: str) -> None:
    with pytest.raises(ErroLLMAutenticacao):
        await provedor(Servidor(erro(status, situacao))).gerar(requisicao())


async def test_429_usa_retry_info_do_corpo() -> None:
    detalhes = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(erro(429, "RESOURCE_EXHAUSTED", "quota", detalhes))).gerar(requisicao())
    assert info.value.retry_after_s == 3.0


@pytest.mark.parametrize(("status", "situacao"), [(500, "INTERNAL"), (503, "UNAVAILABLE"), (504, "DEADLINE_EXCEEDED")])
async def test_transitorios(status: int, situacao: str) -> None:
    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(erro(status, situacao))).gerar(requisicao())


@pytest.mark.parametrize(("status", "situacao"), [(400, "INVALID_ARGUMENT"), (404, "NOT_FOUND")])
async def test_permanentes(status: int, situacao: str) -> None:
    with pytest.raises(ErroLLMPermanente) as info:
        await provedor(Servidor(erro(status, situacao))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMAutenticacao)


@pytest.mark.parametrize("excecao", [httpx.ConnectError("recusada"), httpx.ReadTimeout("lento")])
async def test_conexao_e_timeout(excecao: Exception) -> None:
    def falha(request: httpx.Request) -> httpx.Response:
        raise excecao

    with pytest.raises(ErroLLMTransitorio):
        await provedor(Servidor(falha)).gerar(requisicao())


# --- sem crédito / cota diária ---------------------------------------------------


def violacao(quota_id: str, valor: str = "250") -> dict[str, Any]:
    return {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [
            {
                "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                "quotaId": quota_id,
                "quotaDimensions": {"location": "global", "model": "gemini-flash"},
                "quotaValue": valor,
            }
        ],
    }


def atraso(segundos: str) -> dict[str, Any]:
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": segundos}


MSG_COTA = "You exceeded your current quota, please check your plan and billing details."


async def test_cota_diaria_vira_sem_credito() -> None:
    detalhes = [violacao("GenerateRequestsPerDayPerProjectPerModel-FreeTier"), atraso("23s")]
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(429, "RESOURCE_EXHAUSTED", MSG_COTA, detalhes))).gerar(requisicao())


async def test_cota_zero_vira_sem_credito() -> None:
    detalhes = [violacao("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", valor="0"), atraso("5s")]
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(429, "RESOURCE_EXHAUSTED", MSG_COTA, detalhes))).gerar(requisicao())


async def test_cota_por_minuto_continua_transitoria() -> None:
    detalhes = [violacao("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", valor="10"), atraso("4s")]
    with pytest.raises(ErroLLMTransitorio) as info:
        await provedor(Servidor(erro(429, "RESOURCE_EXHAUSTED", MSG_COTA, detalhes))).gerar(requisicao())
    assert not isinstance(info.value, ErroLLMSemCredito)
    assert info.value.retry_after_s == 4.0


async def test_retry_delay_longo_vira_sem_credito() -> None:
    detalhes = [atraso("3600s")]
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(429, "RESOURCE_EXHAUSTED", "quota", detalhes))).gerar(requisicao())


async def test_402_vira_sem_credito() -> None:
    with pytest.raises(ErroLLMSemCredito):
        await provedor(Servidor(erro(402, "FAILED_PRECONDITION", "billing"))).gerar(requisicao())

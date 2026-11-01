"""Adapter Gemini (SDK `google-genai`, Gemini Developer API).

O loop de tools é nosso: o automatic function calling do SDK fica desligado e o retry interno também
(`HttpRetryOptions(attempts=1)`). As partes da resposta (com `thought_signature`) voltam inalteradas na rodada
seguinte via `ConteudoBruto`.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import httpx2
from google import genai
from google.genai import errors, types

from emporio.adaptadores.llm._erros import cota_diaria_esgotada, erro_por_status, retry_after_dos_headers
from emporio.portas.llm import (
    ChamadaTool,
    ConteudoBruto,
    ErroLLM,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMRecusa,
    ErroLLMTransitorio,
    MensagemLLM,
    PapelMensagem,
    RequisicaoLLM,
    RespostaLLM,
    UsoTokens,
)

MODELO_PADRAO = "gemini-2.5-flash"
MAX_TOKENS_MINIMO = 8192
_LIMITE_DETALHE = 300
_HTTP_BAD_REQUEST = 400
_HTTP_LIMITE = 429

_RECUSAS = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.RECITATION,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.SPII,
        types.FinishReason.IMAGE_SAFETY,
        types.FinishReason.IMAGE_PROHIBITED_CONTENT,
    }
)
_TOOL_INVALIDA = frozenset(
    {
        types.FinishReason.MALFORMED_FUNCTION_CALL,
        types.FinishReason.UNEXPECTED_TOOL_CALL,
        types.FinishReason.TOO_MANY_TOOL_CALLS,
    }
)
_MARCAS_CHAVE_INVALIDA = ("API_KEY_INVALID", "API_KEY_EXPIRED", "API key not valid", "API key expired")
_TIMEOUTS = (httpx.TimeoutException, httpx2.TimeoutException)
_TRANSPORTE = (httpx.TransportError, httpx2.TransportError)


def cliente_gemini(api_key: str, *, http_client: httpx.AsyncClient | None = None) -> genai.Client:
    """Cliente sem retry interno. `http_client` permite injetar transporte (testes)."""
    opcoes = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1), httpx_async_client=http_client)
    return genai.Client(api_key=api_key, http_options=opcoes)


class ProvedorGemini:
    """Implementa `LLMProvider` para o Gemini."""

    def __init__(
        self,
        cliente: genai.Client,
        *,
        modelo: str = MODELO_PADRAO,
        max_tokens_minimo: int = MAX_TOKENS_MINIMO,
        nome: str = "gemini",
    ) -> None:
        self._cliente = cliente
        self.modelo = modelo
        self._max_tokens_minimo = max_tokens_minimo
        self._nome = nome

    @property
    def nome(self) -> str:
        """Nome do provedor."""
        return self._nome

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Chama `client.aio.models.generate_content` e converte erros do SDK/transporte para a porta."""
        contents, systems = self._contents(req.mensagens)
        try:
            resposta = await self._cliente.aio.models.generate_content(
                model=self.modelo, contents=contents, config=self._config(req, systems)
            )
        except errors.APIError as erro:
            raise _converter(erro) from erro
        except _TIMEOUTS as erro:
            raise ErroLLMTransitorio(f"{self._nome}: timeout") from erro
        except _TRANSPORTE as erro:
            raise ErroLLMTransitorio(f"{self._nome}: falha de conexão") from erro
        return self._resposta(resposta)

    def _config(self, req: RequisicaoLLM, systems: Sequence[str]) -> types.GenerateContentConfig:
        system = "\n\n".join(t for t in (req.system_cacheavel, req.system_dinamico, *systems) if t)
        declaracoes = [
            types.FunctionDeclaration(name=t.nome, description=t.descricao, parameters_json_schema=dict(t.parametros))
            for t in req.tools
        ]
        tools: list[types.ToolUnion] | None = [types.Tool(function_declarations=declaracoes)] if declaracoes else None
        return types.GenerateContentConfig(
            system_instruction=system or None,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=max(req.max_tokens, self._max_tokens_minimo),
            http_options=types.HttpOptions(timeout=round(req.timeout_s * 1000)),
        )

    def _contents(self, mensagens: Sequence[MensagemLLM]) -> tuple[list[types.Content], list[str]]:
        """Histórico → `Content`. Partes do lado `user` consecutivas (function responses e texto) viram um turno.

        Ids de function call só são enviados quando vieram do próprio Gemini (no `bruto`); ids gerados pelo adapter
        ou de outros provedores ficam de fora e a resposta é casada pelo nome da tool.
        """
        contents: list[types.Content] = []
        systems: list[str] = []
        pendentes: list[types.Part] = []
        nomes: dict[str, str] = {}
        ids_nativos: set[str] = set()

        def fechar_user() -> None:
            if pendentes:
                contents.append(types.Content(role="user", parts=list(pendentes)))
                pendentes.clear()

        for m in mensagens:
            if m.role is PapelMensagem.SYSTEM:
                if m.content:
                    systems.append(m.content)
            elif m.role is PapelMensagem.USER:
                if m.content:
                    pendentes.append(types.Part(text=m.content))
            elif m.role is PapelMensagem.TOOL:
                id_chamada = m.tool_call_id or ""
                resultado = {"error": m.content} if m.erro_tool else {"output": m.content}
                funcao = types.FunctionResponse(
                    id=id_chamada if id_chamada in ids_nativos else None,
                    name=nomes.get(id_chamada, ""),
                    response=resultado,
                )
                pendentes.append(types.Part(function_response=funcao))
            else:
                fechar_user()
                nomes.update((c.id, c.nome) for c in m.tool_calls)
                partes = self._partes_assistant(m)
                ids_nativos.update(p.function_call.id for p in partes if p.function_call and p.function_call.id)
                if partes:
                    contents.append(types.Content(role="model", parts=partes))
        fechar_user()
        return contents, systems

    def _partes_assistant(self, m: MensagemLLM) -> list[types.Part]:
        if m.bruto is not None and m.bruto.provedor == self._nome:
            return [types.Part.model_validate_json(json.dumps(dict(b))) for b in m.bruto.blocos]
        partes = [types.Part(text=m.content)] if m.content else []
        partes.extend(
            types.Part(function_call=types.FunctionCall(name=c.nome, args=dict(c.argumentos))) for c in m.tool_calls
        )
        return partes

    def _resposta(self, resposta: types.GenerateContentResponse) -> RespostaLLM:
        if not resposta.candidates:
            bloqueio = resposta.prompt_feedback.block_reason if resposta.prompt_feedback else None
            if bloqueio is not None:
                raise ErroLLMRecusa(f"{self._nome}: prompt bloqueado ({bloqueio})")
            raise ErroLLMTransitorio(f"{self._nome}: resposta sem candidatos")
        candidato = resposta.candidates[0]
        if candidato.finish_reason in _RECUSAS:
            raise ErroLLMRecusa(f"{self._nome}: o modelo recusou responder ({candidato.finish_reason})")
        if candidato.finish_reason in _TOOL_INVALIDA:
            raise ErroLLMPermanente(f"{self._nome}: chamada de tool inválida ({candidato.finish_reason})")
        partes = candidato.content.parts if candidato.content and candidato.content.parts else []
        textos = [p.text for p in partes if p.text and not p.thought]
        chamadas = tuple(
            ChamadaTool(p.function_call.id or _novo_id(), p.function_call.name or "", dict(p.function_call.args or {}))
            for p in partes
            if p.function_call
        )
        blocos = tuple(p.model_dump(mode="json", exclude_none=True) for p in partes)
        uso = resposta.usage_metadata
        return RespostaLLM(
            texto="".join(textos) or None,
            provedor=self._nome,
            modelo=resposta.model_version or self.modelo,
            chamadas_tool=chamadas,
            uso=UsoTokens(
                entrada=(uso.prompt_token_count or 0) if uso else 0,
                saida=((uso.candidates_token_count or 0) + (uso.thoughts_token_count or 0)) if uso else 0,
                cache_leitura=(uso.cached_content_token_count or 0) if uso else 0,
            ),
            bruto=ConteudoBruto(self._nome, blocos) if blocos else None,
        )


def _novo_id() -> str:
    return f"gemini_{uuid.uuid4().hex[:16]}"


def _converter(erro: errors.APIError) -> ErroLLM:
    """`APIError` (`ClientError`/`ServerError`) → erro da porta. Chave inválida chega como 400 no Gemini."""
    status = erro.code if isinstance(erro.code, int) else 0
    detalhe = f"{erro.status} {erro.message}"[:_LIMITE_DETALHE]
    if status == _HTTP_BAD_REQUEST and any(marca in str(erro) for marca in _MARCAS_CHAVE_INVALIDA):
        return ErroLLMAutenticacao(f"gemini: HTTP {status}: {detalhe}")
    headers = getattr(erro.response, "headers", None)
    retry_after = _retry_info(erro.details)
    if retry_after is None and isinstance(headers, Mapping):
        retry_after = retry_after_dos_headers(headers)
    sem_credito = status == _HTTP_LIMITE and _cota_esgotada(erro.details)
    return erro_por_status("gemini", status, detalhe, retry_after, sem_credito=sem_credito)


def _violacoes_de_cota(detalhes: Any) -> list[Mapping[str, Any]]:
    """Violações de `google.rpc.QuotaFailure` no corpo do erro."""
    if not isinstance(detalhes, Mapping):
        return []
    corpo = detalhes.get("error", detalhes)
    itens = corpo.get("details") if isinstance(corpo, Mapping) else None
    violacoes: list[Mapping[str, Any]] = []
    for item in itens if isinstance(itens, list) else ():
        if isinstance(item, Mapping) and str(item.get("@type", "")).endswith("google.rpc.QuotaFailure"):
            lista = item.get("violations")
            violacoes.extend(v for v in (lista if isinstance(lista, list) else ()) if isinstance(v, Mapping))
    return violacoes


def _cota_esgotada(detalhes: Any) -> bool:
    """Cota diária (`quotaId` com `PerDay`) ou cota zero (`quotaValue == "0"`, plano sem acesso ao modelo).

    A mensagem "You exceeded your current quota" sozinha não basta: ela também aparece na cota por minuto.
    """
    return any(
        cota_diaria_esgotada(str(v.get("quotaId", ""))) or str(v.get("quotaValue", "")) == "0"
        for v in _violacoes_de_cota(detalhes)
    )


def _retry_info(detalhes: Any) -> float | None:
    """Lê `google.rpc.RetryInfo.retryDelay` (ex.: `"3s"`) do corpo do erro."""
    if not isinstance(detalhes, Mapping):
        return None
    corpo = detalhes.get("error", detalhes)
    itens = corpo.get("details") if isinstance(corpo, Mapping) else None
    for item in itens if isinstance(itens, list) else ():
        if isinstance(item, Mapping) and str(item.get("@type", "")).endswith("google.rpc.RetryInfo"):
            atraso = str(item.get("retryDelay", "")).removesuffix("s")
            try:
                return max(0.0, float(atraso))
            except ValueError:
                return None
    return None

"""Adapter genérico para APIs compatíveis com a Chat Completions da OpenAI (SDK oficial `openai`).

Um só adapter atende OpenAI, DeepSeek, Mistral, OpenRouter, Together, Ollama local, Gemini pelo endpoint
compatível etc.: muda só `base_url`, `modelo` e a chave (variáveis `LLM_*`, ver `Settings`). Não envia
parâmetros que só alguns provedores aceitam (`reasoning_effort`, `temperature`): o padrão de cada modelo vale.
"""

from typing import Any, Literal, cast

import httpx2
import openai
from openai.types.chat import ChatCompletion
from openai.types.completion_usage import CompletionUsage

from emporio.adaptadores.llm._erros import cota_diaria_esgotada, erro_por_status, retry_after_dos_headers
from emporio.adaptadores.llm._formato_openai import argumentos_tool, mensagens_openai, tools_openai, uso_tokens
from emporio.portas.llm import (
    ChamadaTool,
    ErroLLM,
    ErroLLMTransitorio,
    RequisicaoLLM,
    RespostaLLM,
    UsoTokens,
)

CampoMaxTokens = Literal["max_tokens", "max_completion_tokens"]

MAX_TOKENS_MINIMO = 4096
"""Piso do limite de saída, como no Groq: modelos que raciocinam gastam parte dele antes do texto."""
_LIMITE_DETALHE = 300
_STATUS_COTA = frozenset({400, 429})


def cliente_compativel_openai(
    api_key: str, *, base_url: str | None = None, http_client: httpx2.AsyncClient | None = None
) -> openai.AsyncOpenAI:
    """Cliente assíncrono sem retry interno (`max_retries=0`): o retry é da camada de resiliência.

    `base_url=None` usa o endpoint padrão do SDK (a API da OpenAI). O timeout vai por chamada (`req.timeout_s`).
    """
    return openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, http_client=http_client)


class ProvedorCompativelOpenAI:
    """Implementa `LLMProvider` para qualquer API compatível com a Chat Completions da OpenAI.

    `nome` é o valor de `LLM_PROVEDOR` (aparece na cadeia do roteador, nos logs e nas métricas). `campo_max_tokens`
    escolhe o parâmetro do limite de saída: a API da OpenAI usa `max_completion_tokens` (modelos de raciocínio
    recusam `max_tokens`); os demais provedores compatíveis aceitam `max_tokens`, o nome universal.
    """

    def __init__(
        self,
        cliente: openai.AsyncOpenAI,
        *,
        nome: str,
        modelo: str,
        campo_max_tokens: CampoMaxTokens = "max_tokens",
        max_tokens_minimo: int = MAX_TOKENS_MINIMO,
    ) -> None:
        self._cliente = cliente
        self._nome = nome
        self.modelo = modelo
        self.campo_max_tokens: CampoMaxTokens = campo_max_tokens
        self._max_tokens_minimo = max_tokens_minimo

    @property
    def nome(self) -> str:
        """Nome do provedor (o de `LLM_PROVEDOR`)."""
        return self._nome

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Chama a API e converte erros do SDK para a porta."""
        tools = tools_openai(req.tools)
        limite = max(req.max_tokens, self._max_tokens_minimo)
        try:
            completion = await self._cliente.chat.completions.create(
                model=self.modelo,
                messages=cast(Any, mensagens_openai(req)),
                tools=cast(Any, tools) if tools else openai.omit,
                tool_choice="auto" if tools else openai.omit,
                max_tokens=limite if self.campo_max_tokens == "max_tokens" else openai.omit,
                max_completion_tokens=limite if self.campo_max_tokens == "max_completion_tokens" else openai.omit,
                timeout=req.timeout_s,
            )
        except openai.OpenAIError as erro:
            raise _converter(self._nome, erro) from erro
        return self._resposta(completion)

    def _resposta(self, completion: ChatCompletion) -> RespostaLLM:
        if not completion.choices:
            raise ErroLLMTransitorio(f"{self._nome}: resposta sem choices")
        mensagem = completion.choices[0].message
        chamadas: list[ChamadaTool] = []
        for indice, c in enumerate(mensagem.tool_calls or ()):
            if c.type != "function":  # tools "custom" (só da OpenAI) não são pedidas por este adapter
                continue
            # Alguns servidores compatíveis (ex.: modelos locais) omitem o `id`: gera um estável para o round-trip.
            id_ = c.id or f"call_{indice}"
            chamadas.append(
                ChamadaTool(id_, c.function.name, argumentos_tool(self._nome, c.function.name, c.function.arguments))
            )
        return RespostaLLM(
            texto=mensagem.content or None,
            provedor=self._nome,
            modelo=completion.model or self.modelo,
            chamadas_tool=tuple(chamadas),
            uso=_uso(completion.usage),
        )


def _uso(uso: CompletionUsage | None) -> UsoTokens:
    """Tokens com cache: `prompt_tokens_details.cached_tokens` (OpenAI e a maioria) ou `prompt_cache_hit_tokens`
    (DeepSeek, campo extra fora do tipo do SDK)."""
    if uso is None:
        return UsoTokens()
    cache = uso.prompt_tokens_details.cached_tokens if uso.prompt_tokens_details else None
    if cache is None:
        extra = (uso.model_extra or {}).get("prompt_cache_hit_tokens")
        cache = extra if isinstance(extra, int) else 0
    return uso_tokens(uso.prompt_tokens or 0, uso.completion_tokens or 0, cache)


def _converter(provedor: str, erro: openai.OpenAIError) -> ErroLLM:
    """Exceção do SDK → erro da porta (da mais específica para a mais genérica).

    402, `insufficient_quota` (código que a OpenAI manda no 429 de cota esgotada) e limites "per day" viram sem
    crédito; 401/403 autenticação; 408/409/429 e 5xx transitório com Retry-After; 400/404/422 permanente.
    """
    if isinstance(erro, openai.APIStatusError):
        detalhe = erro.message[:_LIMITE_DETALHE]
        marcas = f"{erro.message} {erro.code or ''} {erro.body}"
        sem_credito = erro.status_code in _STATUS_COTA and cota_diaria_esgotada(marcas)
        retry_after = retry_after_dos_headers(erro.response.headers)
        return erro_por_status(provedor, erro.status_code, detalhe, retry_after, sem_credito=sem_credito)
    if isinstance(erro, openai.APITimeoutError):  # antes de APIConnectionError: é subclasse dela
        return ErroLLMTransitorio(f"{provedor}: timeout")
    if isinstance(erro, openai.APIConnectionError):
        return ErroLLMTransitorio(f"{provedor}: falha de conexão")
    return ErroLLMTransitorio(f"{provedor}: erro do SDK ({type(erro).__name__})")

"""Adapter Groq (chat completions no formato OpenAI)."""

from typing import Any, Literal, cast

import groq
import httpx
from groq.types.chat import ChatCompletion

from emporio.adaptadores.llm._erros import cota_diaria_esgotada, erro_por_status, retry_after_dos_headers
from emporio.adaptadores.llm._formato_openai import argumentos_tool, mensagens_openai, tools_openai, uso_tokens
from emporio.portas.llm import (
    ChamadaTool,
    ErroLLM,
    ErroLLMTransitorio,
    RequisicaoLLM,
    RespostaLLM,
)

EsforcoGroq = Literal["low", "medium", "high"]

MODELO_PADRAO = "openai/gpt-oss-120b"
MAX_TOKENS_MINIMO = 4096
_LIMITE_DETALHE = 300
_STATUS_COTA = frozenset({400, 429})


def cliente_groq(api_key: str, *, http_client: httpx.AsyncClient | None = None) -> groq.AsyncGroq:
    """Cliente assíncrono sem retry interno (`max_retries=0`): o retry é da camada de resiliência."""
    return groq.AsyncGroq(api_key=api_key, max_retries=0, http_client=http_client)


class ProvedorGroq:
    """Implementa `LLMProvider` para o Groq.

    O system prompt vai numa única mensagem `system` (bloco cacheável primeiro, para o cache de prefixo).
    `esforco` vira `reasoning_effort` (só modelos que raciocinam, como o gpt-oss, aceitam; `None` não envia).
    """

    def __init__(
        self,
        cliente: groq.AsyncGroq,
        *,
        modelo: str = MODELO_PADRAO,
        esforco: EsforcoGroq | None = "low",
        max_tokens_minimo: int = MAX_TOKENS_MINIMO,
        nome: str = "groq",
    ) -> None:
        self._cliente = cliente
        self.modelo = modelo
        self.esforco: EsforcoGroq | None = esforco
        self._max_tokens_minimo = max_tokens_minimo
        self._nome = nome

    @property
    def nome(self) -> str:
        """Nome do provedor."""
        return self._nome

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Chama a API e converte erros do SDK para a porta."""
        tools = tools_openai(req.tools)
        try:
            completion = await self._cliente.chat.completions.create(
                model=self.modelo,
                messages=cast(Any, mensagens_openai(req)),
                tools=cast(Any, tools) if tools else groq.omit,
                tool_choice="auto" if tools else groq.omit,
                reasoning_effort=self.esforco if self.esforco is not None else groq.omit,
                max_completion_tokens=max(req.max_tokens, self._max_tokens_minimo),
                timeout=req.timeout_s,
            )
        except groq.GroqError as erro:
            raise _converter(erro) from erro
        return self._resposta(completion)

    def _resposta(self, completion: ChatCompletion) -> RespostaLLM:
        if not completion.choices:
            raise ErroLLMTransitorio(f"{self._nome}: resposta sem choices")
        mensagem = completion.choices[0].message
        chamadas = tuple(
            ChamadaTool(c.id, c.function.name, argumentos_tool(self._nome, c.function.name, c.function.arguments))
            for c in mensagem.tool_calls or ()
        )
        uso = completion.usage
        cache = uso.prompt_tokens_details.cached_tokens if uso and uso.prompt_tokens_details else 0
        return RespostaLLM(
            texto=mensagem.content or None,
            provedor=self._nome,
            modelo=completion.model,
            chamadas_tool=chamadas,
            uso=uso_tokens(uso.prompt_tokens, uso.completion_tokens, cache) if uso else uso_tokens(0, 0, 0),
        )


def _converter(erro: groq.GroqError) -> ErroLLM:
    """Exceção do SDK → erro da porta (da mais específica para a mais genérica)."""
    if isinstance(erro, groq.APIStatusError):
        detalhe = erro.message[:_LIMITE_DETALHE]
        # Cota diária (TPD/RPD, "per day") ou `insufficient_quota`: não volta em segundos, abre o circuito.
        sem_credito = erro.status_code in _STATUS_COTA and cota_diaria_esgotada(f"{erro.message} {erro.body}")
        retry_after = retry_after_dos_headers(erro.response.headers)
        return erro_por_status("groq", erro.status_code, detalhe, retry_after, sem_credito=sem_credito)
    if isinstance(erro, groq.APITimeoutError):
        return ErroLLMTransitorio("groq: timeout")
    if isinstance(erro, groq.APIConnectionError):
        return ErroLLMTransitorio("groq: falha de conexão")
    return ErroLLMTransitorio(f"groq: erro do SDK ({type(erro).__name__})")

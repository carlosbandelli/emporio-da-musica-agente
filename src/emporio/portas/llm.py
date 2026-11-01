"""Porta `LLMProvider`: tipos neutros de provedor para chamada com tools (Groq, Gemini, Claude)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class PapelMensagem(StrEnum):
    """Papel de uma mensagem na conversa com o LLM."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ChamadaTool:
    """Pedido do LLM para executar uma tool."""

    id: str
    nome: str
    argumentos: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MensagemLLM:
    """Mensagem neutra. `tool_call_id` só em `TOOL`; `tool_calls` só em `ASSISTANT`."""

    role: PapelMensagem
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: tuple[ChamadaTool, ...] = ()


@dataclass(frozen=True, slots=True)
class DefinicaoTool:
    """Descrição de uma tool; `parametros` é um JSON Schema."""

    nome: str
    descricao: str
    parametros: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class UsoTokens:
    """Consumo de tokens de uma chamada."""

    entrada: int = 0
    saida: int = 0
    cache_leitura: int = 0


@dataclass(frozen=True, slots=True)
class RequisicaoLLM:
    """Requisição a um provedor.

    O system prompt vem em dois blocos: `system_cacheavel` (manual de políticas, estável por tenant, elegível a
    prompt caching) e `system_dinamico` (idioma, data, contexto da sessão).
    """

    system_cacheavel: str
    system_dinamico: str
    mensagens: tuple[MensagemLLM, ...]
    tools: tuple[DefinicaoTool, ...] = ()
    timeout_s: float = 20.0
    max_tokens: int = 1024


@dataclass(frozen=True, slots=True)
class RespostaLLM:
    """Resposta de um provedor: texto final e/ou chamadas de tool."""

    texto: str | None
    provedor: str
    modelo: str
    chamadas_tool: tuple[ChamadaTool, ...] = ()
    uso: UsoTokens = field(default_factory=UsoTokens)


class ErroLLM(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Base dos erros de provedor."""


class ErroLLMTransitorio(ErroLLM):
    """Timeout, conexão, 429 ou 5xx: pode tentar de novo ou trocar de provedor."""

    def __init__(self, mensagem: str, retry_after_s: float | None = None) -> None:
        super().__init__(mensagem)
        self.retry_after_s = retry_after_s


class ErroLLMPermanente(ErroLLM):
    """400, 401/403, chave expirada: não adianta repetir."""


class LLMProvider(Protocol):
    """Interface única de provedor com tool calling."""

    @property
    def nome(self) -> str:
        """Nome do provedor (`groq`, `gemini`, `claude`, ...)."""
        ...

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Gera uma resposta. Levanta `ErroLLMTransitorio` ou `ErroLLMPermanente`."""
        ...

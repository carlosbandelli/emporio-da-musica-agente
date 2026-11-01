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
class ConteudoBruto:
    """Conteúdo nativo de uma resposta, opaco para o motor (dicts serializáveis em JSON).

    Existe porque alguns provedores exigem que partes da resposta voltem **inalteradas** na rodada seguinte: os
    blocos `thinking` do Claude que acompanham um `tool_use` e as partes com `thought_signature` do Gemini.
    Descartar ou editar esses blocos pode gerar erro 400. Só o adapter cujo nome é `provedor` interpreta `blocos`;
    os demais ignoram e usam `content`/`tool_calls`.
    """

    provedor: str
    blocos: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class MensagemLLM:
    """Mensagem neutra. `tool_call_id` e `erro_tool` só em `TOOL`; `tool_calls` e `bruto` só em `ASSISTANT`.

    `erro_tool=True` indica que `content` descreve uma falha da tool (no Claude vira `is_error`). `bruto` é a
    cópia de `RespostaLLM.bruto` da resposta que originou esta mensagem: o motor deve copiá-lo sem alteração.
    """

    role: PapelMensagem
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: tuple[ChamadaTool, ...] = ()
    erro_tool: bool = False
    bruto: ConteudoBruto | None = None


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
    prompt caching) e `system_dinamico` (idioma, data, contexto da sessão). Para o cache funcionar, o motor não
    deve alterar o histórico já enviado nem o bloco cacheável entre as rodadas.

    `timeout_s` vale por tentativa. `prazo_monotonic` é o deadline total da mensagem do cliente, como instante
    absoluto de `time.monotonic()`; `None` = sem deadline. `max_tokens` é um piso pedido pelo motor: adapters de
    modelos que raciocinam podem usar um limite maior (o raciocínio consome do mesmo limite).
    """

    system_cacheavel: str
    system_dinamico: str
    mensagens: tuple[MensagemLLM, ...]
    tools: tuple[DefinicaoTool, ...] = ()
    timeout_s: float = 20.0
    max_tokens: int = 1024
    prazo_monotonic: float | None = None


@dataclass(frozen=True, slots=True)
class RespostaLLM:
    """Resposta de um provedor: texto final e/ou chamadas de tool.

    `bruto` guarda o conteúdo nativo da resposta (ver `ConteudoBruto`). O motor copia `bruto` para a
    `MensagemLLM` assistant que registra esta resposta no histórico, sem alterar.
    """

    texto: str | None
    provedor: str
    modelo: str
    chamadas_tool: tuple[ChamadaTool, ...] = ()
    uso: UsoTokens = field(default_factory=UsoTokens)
    bruto: ConteudoBruto | None = None


class ErroLLM(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Base dos erros de provedor."""


class ErroLLMTransitorio(ErroLLM):
    """Timeout, conexão, 408/409/429 ou 5xx: pode tentar de novo ou trocar de provedor."""

    def __init__(self, mensagem: str, retry_after_s: float | None = None) -> None:
        super().__init__(mensagem)
        self.retry_after_s = retry_after_s


class ErroCircuitoAberto(ErroLLMTransitorio):
    """O circuito do provedor está aberto: a chamada foi recusada sem tocar na API. O roteador pula o provedor."""


class ErroPrazoEsgotado(ErroLLMTransitorio):
    """O deadline da mensagem acabou (antes ou durante a chamada). Não indica falha do provedor.

    O roteador não deve tentar outro provedor: não há mais tempo.
    """


class ErroLLMPermanente(ErroLLM):
    """Não adianta repetir. Usado diretamente para erro da requisição (400/404/422, resposta inválida).

    Erro de requisição não abre o circuito: o provedor respondeu, o problema é o pedido.
    """


class ErroLLMAutenticacao(ErroLLMPermanente):
    """401/403, chave inválida ou expirada: abre o circuito do provedor na hora."""


class ErroLLMSemCredito(ErroLLMAutenticacao):
    """Créditos ou cota (diária) do provedor esgotados: 402, `billing_error`, cota diária, 429 com espera > 60 s.

    Subclasse de `ErroLLMAutenticacao` de propósito: a conta não pode ser usada até alguém agir (recarregar ou
    esperar o dia virar), então herda o mesmo tratamento (abre o circuito na hora, sem retry; o roteador pula o
    provedor). O tipo próprio permite distinguir "sem crédito" de "chave inválida" em logs e métricas.
    """


class ErroLLMRecusa(ErroLLMPermanente):
    """O modelo recusou responder (política de segurança). Não abre o circuito."""


class LLMProvider(Protocol):
    """Interface única de provedor com tool calling."""

    @property
    def nome(self) -> str:
        """Nome do provedor (`groq`, `gemini`, `claude`, ...)."""
        ...

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Gera uma resposta. Levanta `ErroLLMTransitorio` ou `ErroLLMPermanente` (ou subclasses)."""
        ...

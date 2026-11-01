"""Adapter Claude (Anthropic Messages API, endpoint beta para o fallback de recusa no servidor).

Regras do `claude-opus-5-5` respeitadas aqui: o raciocínio é adaptativo e não pode ser desligado, então não
enviamos `thinking`, `temperature`, `top_p`, `top_k` nem `tool_choice` forçado (todos dão 400); o esforço vai em
`output_config.effort`; `max_tokens` tem piso alto porque o raciocínio consome do mesmo limite. Os blocos
`thinking` de uma resposta voltam inalterados na rodada seguinte via `ConteudoBruto`.
"""

from collections.abc import Sequence
from typing import Any, Literal, cast

import anthropic
import httpx2
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock

from emporio.adaptadores.llm._erros import erro_por_status, retry_after_dos_headers
from emporio.portas.llm import (
    ChamadaTool,
    ConteudoBruto,
    ErroLLM,
    ErroLLMAutenticacao,
    ErroLLMRecusa,
    ErroLLMTransitorio,
    MensagemLLM,
    PapelMensagem,
    RequisicaoLLM,
    RespostaLLM,
    UsoTokens,
)

EsforcoClaude = Literal["low", "medium", "high", "xhigh", "max"]

MODELO_PADRAO = "claude-opus-5-5"
BETA_FALLBACK_SERVIDOR = "server-side-fallback-2026-07-01"
MAX_TOKENS_MINIMO = 16000
_TIPOS_THINKING = frozenset({"thinking", "redacted_thinking"})
_LIMITE_DETALHE = 300
_MARCA_SEM_SALDO = "credit balance"


def cliente_claude(api_key: str, *, http_client: httpx2.AsyncClient | None = None) -> anthropic.AsyncAnthropic:
    """Cliente assíncrono sem retry interno (`max_retries=0`): o retry é da camada de resiliência."""
    return anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, http_client=http_client)


class ProvedorClaude:
    """Implementa `LLMProvider` para o Claude."""

    def __init__(
        self,
        cliente: anthropic.AsyncAnthropic,
        *,
        modelo: str = MODELO_PADRAO,
        esforco: EsforcoClaude = "low",
        fallback_servidor: bool = True,
        max_tokens_minimo: int = MAX_TOKENS_MINIMO,
        nome: str = "claude",
    ) -> None:
        self._cliente = cliente
        self.modelo = modelo
        self.esforco: EsforcoClaude = esforco
        self.fallback_servidor = fallback_servidor
        self._max_tokens_minimo = max_tokens_minimo
        self._nome = nome

    @property
    def nome(self) -> str:
        """Nome do provedor."""
        return self._nome

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Chama a API. Se vier 400 de thinking/assinatura, tira os blocos de raciocínio do histórico e tenta 1 vez."""
        try:
            return await self._enviar(req, sem_thinking=False)
        except anthropic.BadRequestError as erro:
            if not (_erro_de_thinking(erro) and self._historico_tem_thinking(req.mensagens)):
                raise _converter(erro) from erro
        except anthropic.AnthropicError as erro:
            raise _converter(erro) from erro
        try:
            return await self._enviar(req, sem_thinking=True)
        except anthropic.AnthropicError as erro:
            raise _converter(erro) from erro

    async def _enviar(self, req: RequisicaoLLM, *, sem_thinking: bool) -> RespostaLLM:
        mensagens, systems_extras = self._mensagens(req.mensagens, sem_thinking=sem_thinking)
        tools = [{"name": t.nome, "description": t.descricao, "input_schema": dict(t.parametros)} for t in req.tools]
        msg = await self._cliente.beta.messages.create(
            model=self.modelo,
            max_tokens=max(req.max_tokens, self._max_tokens_minimo),
            system=cast(Any, _system(req, systems_extras)),
            messages=cast(Any, mensagens),
            tools=cast(Any, tools) if tools else anthropic.omit,
            output_config={"effort": self.esforco},
            betas=[BETA_FALLBACK_SERVIDOR] if self.fallback_servidor else anthropic.omit,
            fallbacks="default" if self.fallback_servidor else anthropic.omit,
            timeout=req.timeout_s,
        )
        return self._resposta(msg)

    def _resposta(self, msg: BetaMessage) -> RespostaLLM:
        if msg.stop_reason == "refusal":
            raise ErroLLMRecusa(f"{self._nome}: o modelo recusou responder (stop_reason=refusal)")
        textos = [b.text for b in msg.content if isinstance(b, BetaTextBlock)]
        chamadas = tuple(
            ChamadaTool(b.id, b.name, dict(b.input)) for b in msg.content if isinstance(b, BetaToolUseBlock)
        )
        blocos = tuple(b.model_dump(mode="json", by_alias=True, exclude_none=True) for b in msg.content)
        uso = UsoTokens(
            entrada=msg.usage.input_tokens,
            saida=msg.usage.output_tokens,
            cache_leitura=msg.usage.cache_read_input_tokens or 0,
        )
        return RespostaLLM(
            texto="".join(textos) or None,
            provedor=self._nome,
            modelo=msg.model,
            chamadas_tool=chamadas,
            uso=uso,
            bruto=ConteudoBruto(self._nome, blocos),
        )

    def _mensagens(
        self, mensagens: Sequence[MensagemLLM], *, sem_thinking: bool
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Converte o histórico. Blocos do lado `user` consecutivos (tool_results e texto) viram uma só mensagem."""
        saida: list[dict[str, Any]] = []
        systems: list[str] = []
        pendentes: list[dict[str, Any]] = []

        def fechar_user() -> None:
            if not pendentes:
                return
            so_texto = len(pendentes) == 1 and pendentes[0]["type"] == "text"
            saida.append({"role": "user", "content": pendentes[0]["text"] if so_texto else list(pendentes)})
            pendentes.clear()

        for m in mensagens:
            if m.role is PapelMensagem.SYSTEM:
                if m.content:
                    systems.append(m.content)
            elif m.role is PapelMensagem.USER:
                if m.content:
                    pendentes.append({"type": "text", "text": m.content})
            elif m.role is PapelMensagem.TOOL:
                bloco: dict[str, Any] = {"type": "tool_result", "tool_use_id": m.tool_call_id or ""}
                if m.content:
                    bloco["content"] = m.content
                if m.erro_tool:
                    bloco["is_error"] = True
                pendentes.append(bloco)
            else:
                fechar_user()
                blocos = self._blocos_assistant(m, sem_thinking=sem_thinking)
                if blocos:
                    saida.append({"role": "assistant", "content": blocos})
        fechar_user()
        return saida, systems

    def _blocos_assistant(self, m: MensagemLLM, *, sem_thinking: bool) -> list[dict[str, Any]]:
        if m.bruto is not None and m.bruto.provedor == self._nome:
            blocos = [dict(b) for b in m.bruto.blocos]  # verbatim: thinking/assinaturas não podem mudar
            if sem_thinking:
                blocos = [b for b in blocos if b.get("type") not in _TIPOS_THINKING]
            return blocos
        montados: list[dict[str, Any]] = []
        if m.content:
            montados.append({"type": "text", "text": m.content})
        montados.extend(
            {"type": "tool_use", "id": c.id, "name": c.nome, "input": dict(c.argumentos)} for c in m.tool_calls
        )
        return montados

    def _historico_tem_thinking(self, mensagens: Sequence[MensagemLLM]) -> bool:
        return any(
            m.bruto is not None
            and m.bruto.provedor == self._nome
            and any(b.get("type") in _TIPOS_THINKING for b in m.bruto.blocos)
            for m in mensagens
        )


def _system(req: RequisicaoLLM, extras: Sequence[str]) -> list[dict[str, Any]]:
    """Bloco cacheável primeiro (com `cache_control`: o cache cobre tools + este bloco), depois os dinâmicos."""
    blocos: list[dict[str, Any]] = []
    if req.system_cacheavel:
        blocos.append({"type": "text", "text": req.system_cacheavel, "cache_control": {"type": "ephemeral"}})
    if req.system_dinamico:
        blocos.append({"type": "text", "text": req.system_dinamico})
    blocos.extend({"type": "text", "text": texto} for texto in extras)
    return blocos


def _erro_de_thinking(erro: anthropic.BadRequestError) -> bool:
    texto = f"{erro.message} {erro.body}".lower()
    return "thinking" in texto or "signature" in texto


def _converter(erro: anthropic.AnthropicError) -> ErroLLM:
    """Exceção do SDK → erro da porta (da mais específica para a mais genérica)."""
    if isinstance(erro, anthropic.APIStatusError):
        detalhe = erro.message[:_LIMITE_DETALHE]
        # Saldo esgotado chega como 400 "Your credit balance is too low..." ou com `error.type == "billing_error"`
        # (`ErrorType` do SDK 2.3); ambos abrem o circuito.
        sem_credito = erro.type == "billing_error" or _MARCA_SEM_SALDO in erro.message.lower()
        retry_after = retry_after_dos_headers(erro.response.headers)
        return erro_por_status("claude", erro.status_code, detalhe, retry_after, sem_credito=sem_credito)
    if isinstance(erro, anthropic.APITimeoutError):
        return ErroLLMTransitorio("claude: timeout")
    if isinstance(erro, anthropic.APIConnectionError):
        return ErroLLMTransitorio("claude: falha de conexão")
    if isinstance(erro, anthropic.CredentialsError):
        return ErroLLMAutenticacao("claude: credencial ausente ou inválida")
    return ErroLLMTransitorio(f"claude: erro do SDK ({type(erro).__name__})")

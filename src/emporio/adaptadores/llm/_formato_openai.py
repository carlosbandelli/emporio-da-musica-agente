"""Conversão porta ↔ formato Chat Completions da OpenAI, comum ao Groq e ao adapter compatível com OpenAI.

Só monta e lê dicts/strings: não depende de SDK (cada adapter chama o seu cliente e converte os erros dele).
"""

import json
from typing import Any, cast

from emporio.portas.llm import (
    ChamadaTool,
    DefinicaoTool,
    ErroLLMPermanente,
    MensagemLLM,
    PapelMensagem,
    RequisicaoLLM,
    UsoTokens,
)


def tools_openai(tools: tuple[DefinicaoTool, ...]) -> list[dict[str, Any]]:
    """Tools no formato `{"type": "function", ...}`, na ordem recebida (determinística, boa para o cache)."""
    return [
        {
            "type": "function",
            "function": {"name": t.nome, "description": t.descricao, "parameters": dict(t.parametros)},
        }
        for t in tools
    ]


def mensagens_openai(req: RequisicaoLLM) -> list[dict[str, Any]]:
    """Uma única mensagem `system` (bloco cacheável primeiro, para o cache de prefixo) e depois o histórico."""
    system = "\n\n".join(t for t in (req.system_cacheavel, req.system_dinamico) if t)
    saida: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    saida.extend(_mensagem(m) for m in req.mensagens)
    return saida


def _mensagem(m: MensagemLLM) -> dict[str, Any]:
    # `erro_tool` vai como o próprio texto do erro: o formato OpenAI não tem um campo `is_error`.
    if m.role is PapelMensagem.TOOL:
        return {"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content}
    if m.role is PapelMensagem.ASSISTANT and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [_chamada(c) for c in m.tool_calls],
        }
    return {"role": m.role.value, "content": m.content}


def _chamada(c: ChamadaTool) -> dict[str, Any]:
    argumentos = json.dumps(dict(c.argumentos), ensure_ascii=False)
    return {"id": c.id, "type": "function", "function": {"name": c.nome, "arguments": argumentos}}


def argumentos_tool(provedor: str, tool: str, argumentos: str | None) -> dict[str, object]:
    """`arguments` (string JSON) → dict. Vazio vira `{}`; JSON inválido ou não-objeto é erro permanente."""
    if argumentos is None or not argumentos.strip():
        return {}
    try:
        valor = json.loads(argumentos)
    except json.JSONDecodeError as erro:
        raise ErroLLMPermanente(f"{provedor}: argumentos da tool {tool!r} não são JSON válido") from erro
    if not isinstance(valor, dict):
        raise ErroLLMPermanente(f"{provedor}: argumentos da tool {tool!r} não são um objeto JSON")
    return cast(dict[str, object], valor)


def uso_tokens(prompt: int, saida: int, cache: int) -> UsoTokens:
    """`entrada` exclui os tokens lidos do cache (premissa #170): `prompt` é o total informado pela API."""
    return UsoTokens(entrada=max(prompt - cache, 0), saida=saida, cache_leitura=cache)

"""`SessaoStoreRedis`: sessões em JSON no Redis com TTL, isoladas por prefixo de tenant."""

import json
import logging
from typing import Any

from redis.asyncio import Redis

from emporio.adaptadores.redis._guarda import GuardaRedis, RedisIndisponivelError
from emporio.portas.contexto import Idioma, TenantContext
from emporio.portas.estado import EstadoSessao
from emporio.portas.llm import ChamadaTool, ConteudoBruto, MensagemLLM, PapelMensagem

logger = logging.getLogger(__name__)


def chave_sessao(tenant_id: str, sessao_id: str) -> str:
    """Chave Redis da sessão; sempre prefixada pelo tenant."""
    return f"emp:{tenant_id}:sessao:{sessao_id}"


def serializar_estado(estado: EstadoSessao) -> str:
    """JSON fiel do estado (inclui `bruto`, `tool_calls` e `erro_tool`). Falha com `TypeError` se não for JSON."""
    doc = {
        "v": 1,
        "idioma": estado.idioma.value if estado.idioma else None,
        "historico": [_msg_para_dict(m) for m in estado.historico],
    }
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def desserializar_estado(raw: str | bytes) -> EstadoSessao:
    """Inverso de `serializar_estado`."""
    doc = json.loads(raw)
    idioma = Idioma(doc["idioma"]) if doc["idioma"] else None
    return EstadoSessao(historico=tuple(_msg_de_dict(m) for m in doc["historico"]), idioma=idioma)


def _msg_para_dict(m: MensagemLLM) -> dict[str, Any]:
    return {
        "role": m.role.value,
        "content": m.content,
        "tool_call_id": m.tool_call_id,
        "tool_calls": [{"id": c.id, "nome": c.nome, "argumentos": dict(c.argumentos)} for c in m.tool_calls],
        "erro_tool": m.erro_tool,
        "bruto": None
        if m.bruto is None
        else {"provedor": m.bruto.provedor, "blocos": [dict(b) for b in m.bruto.blocos]},
    }


def _msg_de_dict(d: dict[str, Any]) -> MensagemLLM:
    bruto = d["bruto"]
    return MensagemLLM(
        role=PapelMensagem(d["role"]),
        content=d["content"],
        tool_call_id=d["tool_call_id"],
        tool_calls=tuple(ChamadaTool(id=c["id"], nome=c["nome"], argumentos=c["argumentos"]) for c in d["tool_calls"]),
        erro_tool=d["erro_tool"],
        bruto=None if bruto is None else ConteudoBruto(provedor=bruto["provedor"], blocos=tuple(bruto["blocos"])),
    )


class SessaoStoreRedis:
    """Implementa `SessaoStore`. Redis fora: `obter` devolve `None` e `salvar`/`apagar` viram no-op com log."""

    def __init__(self, cliente: Redis, guarda: GuardaRedis | None = None) -> None:
        self._r = cliente
        self._guarda = guarda or GuardaRedis()

    async def obter(self, ctx: TenantContext, sessao_id: str) -> EstadoSessao | None:
        """Estado da sessão, ou `None` se não existe, expirou, está corrompida ou o Redis está indisponível."""
        chave = chave_sessao(ctx.tenant_id, sessao_id)
        try:
            raw = await self._guarda.executar(lambda: self._r.get(chave), "sessao.obter")
        except RedisIndisponivelError:
            return None
        if raw is None:
            return None
        try:
            return desserializar_estado(raw)
        except (ValueError, KeyError, TypeError):
            logger.error("Sessão corrompida no Redis (tenant=%s); tratada como inexistente", ctx.tenant_id)
            return None

    async def salvar(self, ctx: TenantContext, sessao_id: str, estado: EstadoSessao, ttl_s: int) -> None:
        """Grava com expiração; se o Redis estiver fora, a conversa segue sem persistir (log)."""
        chave = chave_sessao(ctx.tenant_id, sessao_id)
        payload = serializar_estado(estado)
        try:
            await self._guarda.executar(lambda: self._r.set(chave, payload, ex=ttl_s), "sessao.salvar")
        except RedisIndisponivelError:
            logger.warning("Sessão não persistida (Redis indisponível); conversa segue sem histórico")

    async def apagar(self, ctx: TenantContext, sessao_id: str) -> None:
        """Remove a sessão (sem erro se não existir ou se o Redis estiver fora)."""
        chave = chave_sessao(ctx.tenant_id, sessao_id)
        try:
            await self._guarda.executar(lambda: self._r.delete(chave), "sessao.apagar")
        except RedisIndisponivelError:
            logger.warning("Sessão não apagada (Redis indisponível)")

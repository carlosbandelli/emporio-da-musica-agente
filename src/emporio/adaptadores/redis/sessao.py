"""`SessaoStoreRedis`: sessões em JSON no Redis com TTL, isoladas por prefixo de tenant."""

import json
import logging
from collections import OrderedDict
from typing import Any

from redis.asyncio import Redis

from emporio.adaptadores.redis._guarda import GuardaRedis, RedisIndisponivelError
from emporio.portas.contexto import Idioma, TenantContext
from emporio.portas.estado import EstadoSessao
from emporio.portas.llm import ChamadaTool, ConteudoBruto, MensagemLLM, PapelMensagem

logger = logging.getLogger(__name__)

MAX_MARCAS = 1024
"""Teto de sessões com leitura degradada lembradas por instância (as mais antigas saem primeiro)."""
VALIDADE_MARCA_S = 1800.0
"""Quanto tempo a marca vale (igual ao TTL padrão da sessão): depois disso o histórico real já expirou."""


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
    """Implementa `SessaoStore`. Redis fora: `obter` devolve `None` e `salvar`/`apagar` viram no-op com log.

    Uma leitura **degradada** (falha ou pausa do Redis, diferente de "chave inexistente") marca a sessão nesta
    instância; o `salvar` seguinte dessa sessão é no-op, para o turno novo não sobrescrever o histórico que continua
    no Redis. A marca sai ao salvar, ao ler com sucesso, ao apagar ou ao expirar (`VALIDADE_MARCA_S`), e a estrutura
    é limitada a `max_marcas` entradas.
    """

    def __init__(self, cliente: Redis, guarda: GuardaRedis | None = None, *, max_marcas: int = MAX_MARCAS) -> None:
        self._r = cliente
        self._guarda = guarda or GuardaRedis()
        self._max_marcas = max_marcas
        self._degradadas: OrderedDict[tuple[str, str], float] = OrderedDict()  # (tenant, sessão) -> expira em

    @property
    def total_marcas(self) -> int:
        """Quantidade de sessões com leitura degradada lembradas (para testes e diagnóstico)."""
        return len(self._degradadas)

    def _marcar(self, tenant_id: str, sessao_id: str) -> None:
        chave = (tenant_id, sessao_id)
        self._degradadas.pop(chave, None)
        self._degradadas[chave] = self._guarda.agora() + VALIDADE_MARCA_S
        while len(self._degradadas) > self._max_marcas:
            self._degradadas.popitem(last=False)

    def _desmarcar(self, tenant_id: str, sessao_id: str) -> bool:
        """Remove a marca; `True` se havia uma marca ainda válida."""
        expira_em = self._degradadas.pop((tenant_id, sessao_id), None)
        return expira_em is not None and self._guarda.agora() < expira_em

    async def obter(self, ctx: TenantContext, sessao_id: str) -> EstadoSessao | None:
        """Estado da sessão, ou `None` se não existe, expirou, está corrompida ou o Redis está indisponível."""
        chave = chave_sessao(ctx.tenant_id, sessao_id)
        try:
            raw = await self._guarda.executar(lambda: self._r.get(chave), "sessao.obter")
        except RedisIndisponivelError:
            self._marcar(ctx.tenant_id, sessao_id)
            return None
        self._desmarcar(ctx.tenant_id, sessao_id)
        if raw is None:
            return None
        try:
            return desserializar_estado(raw)
        except (ValueError, KeyError, TypeError):
            logger.error("Sessão corrompida no Redis (tenant=%s); tratada como inexistente", ctx.tenant_id)
            return None

    async def salvar(self, ctx: TenantContext, sessao_id: str, estado: EstadoSessao, ttl_s: int) -> None:
        """Grava com expiração; se o Redis estiver fora, a conversa segue sem persistir (log).

        Se a leitura anterior desta sessão foi degradada, não grava (uma única vez): o estado recebido só tem o
        turno novo e sobrescreveria o histórico real.
        """
        if self._desmarcar(ctx.tenant_id, sessao_id):
            logger.warning(
                "Sessão não persistida: leitura anterior degradada (tenant=%s); histórico do Redis preservado",
                ctx.tenant_id,
            )
            return
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
            self._desmarcar(ctx.tenant_id, sessao_id)
        except RedisIndisponivelError:
            logger.warning("Sessão não apagada (Redis indisponível)")

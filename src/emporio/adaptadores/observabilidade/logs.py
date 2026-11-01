"""Logs JSON (um objeto por linha) com scrubbing de PII no handler."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO, Final

from opentelemetry import trace

from emporio.adaptadores.observabilidade.pii import mascarar

tenant_atual: ContextVar[str | None] = ContextVar("emporio_tenant_atual", default=None)
"""Tenant do turno em andamento; preenchido pelo span com atributo `tenant_id`."""

_MARCA: Final = "_emporio_json"


class FiltroPII(logging.Filter):
    """Aplica `mascarar` à mensagem já formatada (inclui argumentos) antes de qualquer formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mascarar(record.getMessage())
        record.args = None
        return True


class FormatadorJson(logging.Formatter):
    """timestamp, nivel, logger, mensagem, tenant e trace_id (quando houver); exceção em `excecao`."""

    def format(self, record: logging.LogRecord) -> str:
        campos: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensagem": mascarar(record.getMessage()),
        }
        tenant = tenant_atual.get()
        if tenant:
            campos["tenant"] = tenant
        contexto = trace.get_current_span().get_span_context()
        if contexto.is_valid:
            campos["trace_id"] = format(contexto.trace_id, "032x")
        if record.exc_info:
            campos["excecao"] = mascarar(self.formatException(record.exc_info))
        return json.dumps(campos, ensure_ascii=False)


def configurar_logs_json(nivel: str = "INFO", fluxo: IO[str] | None = None) -> logging.Handler:
    """Instala (idempotente) um handler JSON com filtro de PII no logger raiz e o devolve."""
    raiz = logging.getLogger()
    for existente in list(raiz.handlers):
        if getattr(existente, _MARCA, False):
            raiz.removeHandler(existente)
    handler = logging.StreamHandler(fluxo if fluxo is not None else sys.stdout)
    setattr(handler, _MARCA, True)
    handler.addFilter(FiltroPII())
    handler.setFormatter(FormatadorJson())
    raiz.addHandler(handler)
    raiz.setLevel(nivel)
    return handler

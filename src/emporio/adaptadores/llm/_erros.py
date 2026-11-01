"""Classificação comum de erros HTTP dos provedores (transitório × permanente × autenticação)."""

import math
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime

from emporio.portas.llm import ErroLLM, ErroLLMAutenticacao, ErroLLMPermanente, ErroLLMTransitorio

_TRANSITORIOS_4XX = frozenset({408, 409, 429})
_AUTENTICACAO = frozenset({401, 403})


def erro_por_status(provedor: str, status: int, detalhe: str, retry_after_s: float | None = None) -> ErroLLM:
    """Converte um status HTTP de erro no erro neutro da porta.

    408/409/429 e 5xx → transitório (com `retry_after_s`); 401/403 → autenticação (abre o circuito);
    demais 4xx (400, 404, 413, 422, ...) → permanente de requisição (não abre o circuito).
    """
    mensagem = f"{provedor}: HTTP {status}: {detalhe}"
    if status in _AUTENTICACAO:
        return ErroLLMAutenticacao(mensagem)
    if status in _TRANSITORIOS_4XX or status >= 500:  # noqa: PLR2004
        return ErroLLMTransitorio(mensagem, retry_after_s=retry_after_s)
    return ErroLLMPermanente(mensagem)


def retry_after_dos_headers(headers: Mapping[str, str] | None, agora: float | None = None) -> float | None:
    """Lê `retry-after-ms` ou `retry-after` (segundos ou data HTTP). `None` se ausente ou inválido."""
    if headers is None:
        return None
    ms = headers.get("retry-after-ms")
    if ms is not None:
        valor = _numero(ms)
        if valor is not None:
            return valor / 1000.0
    bruto = headers.get("retry-after")
    if bruto is None:
        return None
    valor = _numero(bruto)
    if valor is not None:
        return valor
    try:
        instante = parsedate_to_datetime(bruto)
    except (TypeError, ValueError):
        return None
    referencia = time.time() if agora is None else agora
    return max(0.0, instante.timestamp() - referencia)


def _numero(texto: str) -> float | None:
    try:
        valor = float(texto.strip())
    except ValueError:
        return None
    if math.isnan(valor) or math.isinf(valor) or valor < 0:
        return None
    return valor

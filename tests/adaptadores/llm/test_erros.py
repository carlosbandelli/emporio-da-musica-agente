"""Classificação comum de status HTTP e leitura de `Retry-After`."""

from datetime import UTC, datetime
from email.utils import format_datetime

import pytest

from emporio.adaptadores.llm._erros import erro_por_status, retry_after_dos_headers
from emporio.portas import ErroLLMAutenticacao, ErroLLMPermanente, ErroLLMTransitorio


@pytest.mark.parametrize(
    ("status", "tipo"),
    [
        (400, ErroLLMPermanente),
        (401, ErroLLMAutenticacao),
        (403, ErroLLMAutenticacao),
        (404, ErroLLMPermanente),
        (408, ErroLLMTransitorio),
        (409, ErroLLMTransitorio),
        (413, ErroLLMPermanente),
        (422, ErroLLMPermanente),
        (429, ErroLLMTransitorio),
        (500, ErroLLMTransitorio),
        (529, ErroLLMTransitorio),
    ],
)
def test_erro_por_status(status: int, tipo: type[Exception]) -> None:
    erro = erro_por_status("x", status, "detalhe", retry_after_s=1.5)
    assert type(erro) is tipo
    assert f"HTTP {status}" in str(erro)
    if isinstance(erro, ErroLLMTransitorio):
        assert erro.retry_after_s == 1.5


def test_retry_after_em_segundos_ms_e_data() -> None:
    assert retry_after_dos_headers(None) is None
    assert retry_after_dos_headers({}) is None
    assert retry_after_dos_headers({"retry-after": "2"}) == 2.0
    assert retry_after_dos_headers({"retry-after-ms": "1500", "retry-after": "9"}) == 1.5
    assert retry_after_dos_headers({"retry-after": "abc"}) is None
    assert retry_after_dos_headers({"retry-after": "-1"}) is None
    referencia = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
    data = format_datetime(datetime(2026, 3, 10, 12, 0, 4, tzinfo=UTC), usegmt=True)
    assert retry_after_dos_headers({"retry-after": data}, agora=referencia.timestamp()) == pytest.approx(4.0)

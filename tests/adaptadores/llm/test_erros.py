"""Classificação comum de status HTTP e leitura de `Retry-After`."""

from datetime import UTC, datetime
from email.utils import format_datetime

import pytest

from emporio.adaptadores.llm._erros import cota_diaria_esgotada, erro_por_status, retry_after_dos_headers
from emporio.portas import ErroLLMAutenticacao, ErroLLMPermanente, ErroLLMSemCredito, ErroLLMTransitorio


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


# --- sem crédito / cota esgotada ---------------------------------------------


def test_402_e_sempre_sem_credito() -> None:
    assert type(erro_por_status("x", 402, "payment required")) is ErroLLMSemCredito


def test_flag_sem_credito_vence_o_status() -> None:
    assert type(erro_por_status("x", 400, "credit balance", sem_credito=True)) is ErroLLMSemCredito
    assert type(erro_por_status("x", 429, "cota diária", 2.0, sem_credito=True)) is ErroLLMSemCredito


def test_429_com_espera_longa_e_sem_credito_e_curta_continua_transitorio() -> None:
    assert type(erro_por_status("x", 429, "rate", retry_after_s=61.0)) is ErroLLMSemCredito
    assert type(erro_por_status("x", 429, "rate", retry_after_s=60.0)) is ErroLLMTransitorio
    assert type(erro_por_status("x", 429, "rate")) is ErroLLMTransitorio
    assert type(erro_por_status("x", 503, "x", retry_after_s=120.0)) is ErroLLMTransitorio  # só 429


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Rate limit reached ... on tokens per day (TPD): Limit 500000", True),
        ("Rate limit reached ... on requests per day (RPD): Limit 1000", True),
        ("You exceeded your current quota", False),
        ('{"code": "insufficient_quota"}', True),
        ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", True),
        ("Rate limit reached ... on tokens per minute (TPM): Limit 6000", False),
        ("limit TPD exceeded", True),
        ("RPD: Limit 1000", True),
        ("STPDX", False),
    ],
)
def test_marcas_de_cota_diaria(texto: str, esperado: bool) -> None:
    assert cota_diaria_esgotada(texto) is esperado

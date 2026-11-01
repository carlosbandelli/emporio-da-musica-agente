"""Funções puras de resolução: subdomínio do Host e `Accept-Language`."""

import pytest

from emporio.adaptadores.api._resolucao import idioma_do_accept_language, tenant_do_host
from emporio.portas import Idioma


@pytest.mark.parametrize(
    ("host", "esperado"),
    [
        ("emporio.exemplo.com", "emporio"),
        ("EMPORIO.exemplo.com:8000", "emporio"),
        ("emporio.api.exemplo.com", "emporio"),
        ("emporio.localhost", "emporio"),
        ("emporio.localhost:8000", "emporio"),
        ("exemplo.com", None),
        ("localhost", None),
        ("localhost:8000", None),
        ("testserver", None),
        ("api", None),  # nome de serviço no Docker
        ("127.0.0.1", None),
        ("127.0.0.1:8000", None),
        ("10.1.2.3", None),
        ("[::1]:8000", None),
        ("", None),
        (None, None),
    ],
)
def test_tenant_do_host(host: str | None, esperado: str | None) -> None:
    assert tenant_do_host(host) == esperado


@pytest.mark.parametrize(
    ("cabecalho", "esperado"),
    [
        ("pt-BR", Idioma.PT_BR),
        ("en-US", Idioma.EN_US),
        ("en", Idioma.EN_US),
        ("pt", Idioma.PT_BR),
        ("en-US,en;q=0.9,pt;q=0.8", Idioma.EN_US),
        ("pt;q=0.5, en;q=0.9", Idioma.EN_US),
        ("fr-FR, en;q=0.5", Idioma.EN_US),
        ("fr-FR", None),
        ("*", None),
        ("en;q=0", None),
        ("en;q=abc", None),
        ("", None),
        (None, None),
    ],
)
def test_idioma_do_accept_language(cabecalho: str | None, esperado: Idioma | None) -> None:
    assert idioma_do_accept_language(cabecalho) is esperado

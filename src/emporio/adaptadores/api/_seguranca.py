"""Leitura do cabeçalho `Authorization` (RFC 6750) e cabeçalhos de resposta dos erros de autenticação. Funções puras."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

ESQUEMA: Final = "bearer"
REALM: Final = 'Bearer realm="emporio"'
CABECALHO_401_AUSENTE: Final[Mapping[str, str]] = MappingProxyType({"WWW-Authenticate": REALM})
CABECALHO_401_INVALIDO: Final[Mapping[str, str]] = MappingProxyType(
    {"WWW-Authenticate": f'{REALM}, error="invalid_token"'}
)


class CabecalhoInvalido(ValueError):  # noqa: N818 - nome em pt-BR do projeto
    """`Authorization` presente mas fora do formato `Bearer <token>`."""


def token_bearer(cabecalho: str | None) -> str | None:
    """Token do `Authorization: Bearer <token>`; `None` se o cabeçalho não veio (ou veio vazio).

    Esquema sem diferenciar caixa; outro esquema (ex.: `Basic`) ou token vazio → `CabecalhoInvalido`.
    """
    if cabecalho is None or not cabecalho.strip():
        return None
    esquema, _, token = cabecalho.strip().partition(" ")
    token = token.strip()
    if esquema.lower() != ESQUEMA or not token or " " in token:
        raise CabecalhoInvalido("Authorization deve ser 'Bearer <token>'")
    return token

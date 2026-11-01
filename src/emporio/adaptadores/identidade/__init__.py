"""Adaptadores de identidade (porta `ProvedorIdentidade`). Hoje: JWT local HS256 (`JwtLocal`)."""

from emporio.adaptadores.identidade.jwt_local import (
    AUDIENCIA,
    EMISSOR,
    SEGREDO_MIN_BYTES,
    TOLERANCIA_RELOGIO_S,
    VALIDADE_MAX_S,
    ErroSegredoJwt,
    JwtLocal,
)

__all__ = [
    "AUDIENCIA",
    "EMISSOR",
    "SEGREDO_MIN_BYTES",
    "TOLERANCIA_RELOGIO_S",
    "VALIDADE_MAX_S",
    "ErroSegredoJwt",
    "JwtLocal",
]

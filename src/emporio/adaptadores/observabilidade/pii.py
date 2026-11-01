"""Mascaramento de dados pessoais (LGPD) em texto de log: e-mail e telefone."""

import re
from typing import Final

MASCARA_EMAIL: Final = "[email]"
MASCARA_TELEFONE: Final = "[telefone]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# DDD (com ou sem parênteses), 9 opcional, 4 + 4 dígitos; prefixo +55 opcional. Cobre "(67) 99999-1234",
# "+55 67 99999-1234", "67999991234" e "67 3341-4444".
_TELEFONE = re.compile(r"(?<![\w])(?:\+55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)")


def mascarar(texto: str) -> str:
    """Troca e-mails e telefones por marcadores."""
    return _TELEFONE.sub(MASCARA_TELEFONE, _EMAIL.sub(MASCARA_EMAIL, texto))

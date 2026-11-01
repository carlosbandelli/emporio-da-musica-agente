"""`JwtLocal`: implementa `ProvedorIdentidade` com PyJWT, HS256 e segredo compartilhado (`Settings.jwt_secret`).

Decisões de segurança:

- Algoritmo fixo (`HS256`) na validação: tokens `alg=none` ou com outro algoritmo são recusados (sem confusão de alg).
- Segredo com no mínimo 32 bytes (RFC 7518 §3.2: chave HMAC >= tamanho do hash). Mais curto → `ErroSegredoJwt`.
- Claims exigidas: `sub`, `tenant_id`, `roles`, `iat`, `exp`, `iss`, `aud`. Papel desconhecido em `roles` → token
  inválido (falha fechada, nunca ignora o papel).
- Tolerância de relógio explícita (`TOLERANCIA_RELOGIO_S`) para `exp`/`iat` entre réplicas.
- Teto de validade na emissão (`VALIDADE_MAX_S`) e de tamanho do token na validação (`TAMANHO_MAX_TOKEN`).
- Mensagens de erro nunca contêm o token nem o segredo.
"""

import time
from collections.abc import Callable, Iterable
from typing import Any, Final

import jwt
from pydantic import SecretStr

from emporio.portas.contexto import Papel
from emporio.portas.identidade import ErroIdentidade, ErroTokenExpirado, Identidade

ALGORITMO: Final = "HS256"
EMISSOR: Final = "emporio"
AUDIENCIA: Final = "emporio-api"
TOLERANCIA_RELOGIO_S: Final = 30
"""Folga aceita em `exp` e `iat` (relógios de réplicas diferentes)."""
VALIDADE_MAX_S: Final = 7 * 24 * 3600
"""Validade máxima de um token emitido (7 dias)."""
SEGREDO_MIN_BYTES: Final = 32
TAMANHO_MAX_TOKEN: Final = 4096
_CLAIMS_EXIGIDAS: Final = ("sub", "tenant_id", "roles", "iat", "exp", "iss", "aud")
_TEXTO_MAX: Final = 128


class ErroSegredoJwt(ValueError):  # noqa: N818 - nome em pt-BR do projeto
    """Segredo ausente, vazio ou curto demais (a mensagem nunca mostra o valor)."""


class JwtLocal:
    """Emite e valida JWT HS256. `relogio` (epoch em segundos) só afeta a emissão; a validação usa a hora real."""

    def __init__(
        self,
        segredo: SecretStr,
        *,
        tolerancia_s: int = TOLERANCIA_RELOGIO_S,
        relogio: Callable[[], float] = time.time,
    ) -> None:
        valor = segredo.get_secret_value()
        if len(valor.strip().encode("utf-8")) < SEGREDO_MIN_BYTES:
            raise ErroSegredoJwt(f"JWT_SECRET deve ter ao menos {SEGREDO_MIN_BYTES} bytes")
        self._segredo = segredo
        self._tolerancia_s = tolerancia_s
        self._relogio = relogio

    def __repr__(self) -> str:
        return f"JwtLocal(algoritmo={ALGORITMO!r}, tolerancia_s={self._tolerancia_s})"

    def emitir(self, *, sub: str, tenant_id: str, papeis: Iterable[Papel], validade_s: int) -> str:
        """Token assinado. `validade_s` entre 1 e `VALIDADE_MAX_S`; `papeis` não pode ser vazio."""
        lista = sorted({Papel(p) for p in papeis})
        if not lista:
            raise ValueError("informe ao menos um papel")
        if not 0 < validade_s <= VALIDADE_MAX_S:
            raise ValueError(f"validade deve estar entre 1 e {VALIDADE_MAX_S} segundos")
        if not _texto_ok(sub) or not _texto_ok(tenant_id):
            raise ValueError(f"sub e tenant_id devem ter de 1 a {_TEXTO_MAX} caracteres")
        agora = int(self._relogio())
        payload: dict[str, Any] = {
            "sub": sub,
            "tenant_id": tenant_id,
            "roles": [p.value for p in lista],
            "iat": agora,
            "exp": agora + validade_s,
            "iss": EMISSOR,
            "aud": AUDIENCIA,
        }
        return jwt.encode(payload, self._segredo.get_secret_value(), algorithm=ALGORITMO)

    def validar(self, token: str) -> Identidade:
        """Identidade do token; `ErroTokenExpirado` ou `ErroIdentidade` (sem detalhes do token)."""
        if not token or len(token) > TAMANHO_MAX_TOKEN:
            raise ErroIdentidade("token ausente ou grande demais")
        try:
            claims = jwt.decode(
                token,
                self._segredo.get_secret_value(),
                algorithms=[ALGORITMO],
                audience=AUDIENCIA,
                issuer=EMISSOR,
                leeway=self._tolerancia_s,
                options={"require": list(_CLAIMS_EXIGIDAS)},
            )
        except jwt.ExpiredSignatureError:
            raise ErroTokenExpirado("token expirado") from None
        except jwt.PyJWTError as erro:
            raise ErroIdentidade(f"token inválido ({type(erro).__name__})") from None
        return _identidade(claims)


def _texto_ok(valor: object) -> bool:
    return isinstance(valor, str) and 0 < len(valor.strip()) <= _TEXTO_MAX


def _identidade(claims: dict[str, Any]) -> Identidade:
    sub, tenant_id, roles = claims.get("sub"), claims.get("tenant_id"), claims.get("roles")
    if not _texto_ok(sub) or not _texto_ok(tenant_id):
        raise ErroIdentidade("claims sub/tenant_id inválidas")
    if not isinstance(roles, list) or not roles or not all(isinstance(r, str) for r in roles):
        raise ErroIdentidade("claim roles inválida")
    try:
        papeis = frozenset(Papel(r) for r in roles)
    except ValueError:
        raise ErroIdentidade("papel desconhecido em roles") from None
    return Identidade(sub=str(sub), tenant_id=str(tenant_id), papeis=papeis)

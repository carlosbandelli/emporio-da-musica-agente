"""`JwtLocal` (task 2.5): emissão, validação, expiração, assinatura, algoritmo, claims e segredo."""

import time

import jwt
import pytest
from pydantic import SecretStr

from emporio.adaptadores.identidade import (
    AUDIENCIA,
    EMISSOR,
    TOLERANCIA_RELOGIO_S,
    VALIDADE_MAX_S,
    ErroSegredoJwt,
    JwtLocal,
)
from emporio.portas import ErroIdentidade, ErroTokenExpirado, Papel

SEGREDO = "segredo-de-teste-com-mais-de-64-bytes-para-cobrir-tambem-o-hs512!!"
OUTRO = "outro-segredo-de-teste-com-mais-de-32-bytes"


def _provedor(segredo: str = SEGREDO, *, deslocamento_s: float = 0) -> JwtLocal:
    return JwtLocal(SecretStr(segredo), relogio=lambda: time.time() + deslocamento_s)


def _assinar(claims: dict[str, object], segredo: str = SEGREDO, algoritmo: str = "HS256") -> str:
    agora = int(time.time())
    base: dict[str, object] = {
        "sub": "u",
        "tenant_id": "emporio",
        "roles": ["atendente"],
        "iat": agora,
        "exp": agora + 60,
        "iss": EMISSOR,
        "aud": AUDIENCIA,
    }
    base.update(claims)
    return jwt.encode({k: v for k, v in base.items() if v is not None}, segredo, algorithm=algoritmo)


def test_ida_e_volta() -> None:
    p = _provedor()
    token = p.emitir(sub="ana", tenant_id="emporio", papeis=[Papel.GERENTE, Papel.ATENDENTE], validade_s=60)
    ident = p.validar(token)
    assert (ident.sub, ident.tenant_id, ident.papeis) == ("ana", "emporio", {Papel.GERENTE, Papel.ATENDENTE})
    claims = jwt.decode(token, SEGREDO, algorithms=["HS256"], audience=AUDIENCIA)
    assert {"sub", "tenant_id", "roles", "iat", "exp"} <= set(claims)
    assert claims["exp"] - claims["iat"] == 60


def test_expirado_levanta_token_expirado() -> None:
    token = _provedor(deslocamento_s=-3600).emitir(sub="a", tenant_id="emporio", papeis=[Papel.GERENTE], validade_s=60)
    with pytest.raises(ErroTokenExpirado):
        _provedor().validar(token)


def test_tolerancia_de_relogio_e_pequena_e_explicita() -> None:
    assert 0 < TOLERANCIA_RELOGIO_S <= 60
    dentro = _provedor(deslocamento_s=-(60 + TOLERANCIA_RELOGIO_S - 5))
    _provedor().validar(dentro.emitir(sub="a", tenant_id="emporio", papeis=[Papel.CLIENTE], validade_s=60))
    fora = _provedor(deslocamento_s=-(60 + TOLERANCIA_RELOGIO_S + 5))
    with pytest.raises(ErroTokenExpirado):
        _provedor().validar(fora.emitir(sub="a", tenant_id="emporio", papeis=[Papel.CLIENTE], validade_s=60))


def test_iat_no_futuro_alem_da_tolerancia_e_invalido() -> None:
    futuro = _provedor(deslocamento_s=3600).emitir(sub="a", tenant_id="emporio", papeis=[Papel.CLIENTE], validade_s=60)
    with pytest.raises(ErroIdentidade):
        _provedor().validar(futuro)


def test_assinatura_de_outro_segredo_e_invalida() -> None:
    token = _provedor(OUTRO).emitir(sub="a", tenant_id="emporio", papeis=[Papel.GERENTE], validade_s=60)
    with pytest.raises(ErroIdentidade) as erro:
        _provedor().validar(token)
    assert not isinstance(erro.value, ErroTokenExpirado)
    assert token not in str(erro.value) and SEGREDO not in str(erro.value)


def test_alg_none_e_outro_algoritmo_sao_recusados() -> None:
    cabecalho_none = jwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
    corpo = _assinar({}).split(".")[1]
    for token in (f"{cabecalho_none}.{corpo}.", _assinar({}, algoritmo="HS512")):
        with pytest.raises(ErroIdentidade):
            _provedor().validar(token)


@pytest.mark.parametrize(
    "claims",
    [
        {"tenant_id": None},
        {"roles": None},
        {"sub": None},
        {"exp": None},
        {"iat": None},
        {"iss": "outro"},
        {"aud": "outra-api"},
        {"roles": []},
        {"roles": ["superusuario"]},
        {"roles": "gerente"},
        {"tenant_id": ""},
        {"tenant_id": 7},
    ],
)
def test_claims_ausentes_ou_invalidas_sao_recusadas(claims: dict[str, object]) -> None:
    with pytest.raises(ErroIdentidade):
        _provedor().validar(_assinar(claims))


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * 5000])
def test_lixo_e_recusado(token: str) -> None:
    with pytest.raises(ErroIdentidade):
        _provedor().validar(token)


@pytest.mark.parametrize("segredo", ["", "   ", "curto", "x" * 31])
def test_segredo_curto_e_recusado_sem_mostrar_o_valor(segredo: str) -> None:
    with pytest.raises(ErroSegredoJwt) as erro:
        JwtLocal(SecretStr(segredo))
    assert "32" in str(erro.value)
    if segredo.strip():
        assert segredo not in str(erro.value)


def test_repr_nao_mostra_o_segredo() -> None:
    assert SEGREDO not in repr(_provedor())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"validade_s": 0},
        {"validade_s": VALIDADE_MAX_S + 1},
        {"papeis": []},
        {"tenant_id": ""},
        {"sub": " "},
    ],
)
def test_emissao_invalida(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"sub": "a", "tenant_id": "emporio", "papeis": [Papel.CLIENTE], "validade_s": 60}
    with pytest.raises(ValueError):
        _provedor().emitir(**{**base, **kwargs})  # type: ignore[arg-type]

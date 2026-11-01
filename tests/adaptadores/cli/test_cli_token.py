"""`emporio token` (task 2.5): emite JWT com o segredo de `Settings`; sem segredo → saída 2."""

from pathlib import Path

import jwt
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from emporio.adaptadores.cli import SAIDA_CONFIGURACAO, criar_app
from emporio.adaptadores.identidade import AUDIENCIA, JwtLocal
from emporio.composicao import ErroConfiguracao, montar_identidade
from emporio.configuracao import Settings
from emporio.portas import Papel

RAIZ = Path(__file__).resolve().parents[3]
SEGREDO = "segredo-de-teste-da-cli-com-mais-de-32-bytes"


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)


def _settings(segredo: str | None) -> Settings:
    return Settings(_env_file=None, tenants_config=RAIZ / "config" / "tenants.yaml", jwt_secret=segredo)  # type: ignore[call-arg]


def _cli(segredo: str | None):  # type: ignore[no-untyped-def]
    return criar_app(montar_ident=lambda: montar_identidade(_settings(segredo)))


def test_emite_token_valido_so_no_stdout() -> None:
    r = CliRunner().invoke(
        _cli(SEGREDO), ["token", "--tenant", "emporio", "--papel", "gerente", "--sub", "ana", "--expira-em", "120"]
    )
    assert r.exit_code == 0, r.output
    emitido = r.stdout.strip()
    ident = JwtLocal(SecretStr(SEGREDO)).validar(emitido)
    assert (ident.sub, ident.tenant_id, ident.papeis) == ("ana", "emporio", {Papel.GERENTE})
    claims = jwt.decode(emitido, SEGREDO, algorithms=["HS256"], audience=AUDIENCIA)
    assert claims["exp"] - claims["iat"] == 120
    assert SEGREDO not in r.output


def test_varios_papeis_e_padroes() -> None:
    r = CliRunner().invoke(
        _cli(SEGREDO), ["token", "--tenant", "emporio", "--papel", "atendente", "--papel", "GERENTE"]
    )
    assert r.exit_code == 0, r.output
    claims = jwt.decode(r.stdout.strip(), SEGREDO, algorithms=["HS256"], audience=AUDIENCIA)
    assert sorted(claims["roles"]) == ["atendente", "gerente"]
    assert claims["sub"] == "cli-local" and claims["exp"] - claims["iat"] == 3600


@pytest.mark.parametrize("segredo", [None, "", "curto"])
def test_sem_segredo_valido_sai_com_2(segredo: str | None) -> None:
    r = CliRunner().invoke(_cli(segredo), ["token", "--tenant", "emporio", "--papel", "gerente"])
    assert r.exit_code == SAIDA_CONFIGURACAO == 2
    assert "JWT_SECRET" in r.output and "Traceback" not in r.output


@pytest.mark.parametrize(
    "args",
    [
        ["--expira-em", "0"],
        ["--expira-em", str(30 * 24 * 3600)],
    ],
)
def test_validade_invalida_sai_com_2(args: list[str]) -> None:
    r = CliRunner().invoke(_cli(SEGREDO), ["token", "--tenant", "emporio", "--papel", "gerente", *args])
    assert r.exit_code == 2, r.output


def test_papel_desconhecido_e_recusado() -> None:
    r = CliRunner().invoke(_cli(SEGREDO), ["token", "--tenant", "emporio", "--papel", "root"])
    assert r.exit_code == 2


def test_padrao_le_o_segredo_do_ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sem fábrica injetada, a CLI usa `carregar_settings()`: o segredo vem só de `JWT_SECRET`."""
    monkeypatch.chdir(tmp_path)  # sem arquivo de ambiente do repositório
    monkeypatch.setenv("JWT_SECRET", SEGREDO)
    r = CliRunner().invoke(criar_app(), ["token", "--tenant", "emporio", "--papel", "cliente"])
    assert r.exit_code == 0, r.output
    JwtLocal(SecretStr(SEGREDO)).validar(r.stdout.strip())

    monkeypatch.delenv("JWT_SECRET")
    assert CliRunner().invoke(criar_app(), ["token", "--tenant", "emporio", "--papel", "cliente"]).exit_code == 2


@pytest.mark.parametrize("segredo", [None, " ", "x" * 31])
def test_montar_identidade_recusa_sem_mostrar_o_valor(segredo: str | None) -> None:
    with pytest.raises(ErroConfiguracao) as erro:
        montar_identidade(_settings(segredo))
    assert "JWT_SECRET" in str(erro.value)
    if segredo and segredo.strip():
        assert segredo not in str(erro.value)

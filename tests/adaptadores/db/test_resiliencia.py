"""Retry, configuração e mensagens do pacote `db` (não precisam de Postgres)."""

from collections.abc import Mapping
from types import TracebackType

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from emporio.adaptadores.db import _acesso
from emporio.adaptadores.db import migrar as mod_migrar
from emporio.adaptadores.db._acesso import AcessoTenant, ErroConfiguracaoBanco, url_do_banco
from emporio.adaptadores.db.repositorios import CatalogoPostgres
from emporio.portas import TenantContext
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores.conftest import RAIZ

CTX = TenantContext(tenant_id="emporio")


def _erro_operacional() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("conexão perdida"))


class _Conexao:
    def __enter__(self) -> "_Conexao":
        return self

    def __exit__(self, *args: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None


class _Engine:
    """Engine de mentira: `connect()` falha `falhas_conexao` vezes e depois devolve uma conexão vazia."""

    def __init__(self, falhas_conexao: int = 0) -> None:
        self.falhas_conexao = falhas_conexao
        self.conexoes = 0

    def connect(self) -> _Conexao:
        self.conexoes += 1
        if self.conexoes <= self.falhas_conexao:
            raise _erro_operacional()
        return _Conexao()

    def dispose(self) -> None:
        return None


def _acesso_falso(tenants: Mapping[str, ConfigTenant], engine: _Engine) -> AcessoTenant:
    return AcessoTenant(engine, tenants)  # type: ignore[arg-type]


async def test_leitura_repete_uma_vez_em_erro_operacional(tenants: Mapping[str, ConfigTenant]) -> None:
    engine = _Engine()
    acesso = _acesso_falso(tenants, engine)
    chamadas = 0

    def transacao(schema: str, funcao: object, conexao: object) -> str:
        nonlocal chamadas
        chamadas += 1
        if chamadas == 1:
            raise _erro_operacional()
        return "ok"

    acesso._transacao = transacao  # type: ignore[method-assign,assignment]
    assert await acesso.ler(CTX, lambda c: "x") == "ok"
    assert chamadas == 2


async def test_leitura_desiste_apos_duas_tentativas(tenants: Mapping[str, ConfigTenant]) -> None:
    engine = _Engine()
    acesso = _acesso_falso(tenants, engine)
    chamadas = 0

    def transacao(schema: str, funcao: object, conexao: object) -> str:
        nonlocal chamadas
        chamadas += 1
        raise _erro_operacional()

    acesso._transacao = transacao  # type: ignore[method-assign,assignment]
    with pytest.raises(OperationalError):
        await acesso.ler(CTX, lambda c: "x")
    assert chamadas == _acesso.TENTATIVAS == 2


async def test_escrita_nao_repete_depois_de_conectar(tenants: Mapping[str, ConfigTenant]) -> None:
    acesso = _acesso_falso(tenants, _Engine())
    chamadas = 0

    def transacao(schema: str, funcao: object, conexao: object) -> str:
        nonlocal chamadas
        chamadas += 1
        raise _erro_operacional()

    acesso._transacao = transacao  # type: ignore[method-assign,assignment]
    with pytest.raises(OperationalError):
        await acesso.escrever(CTX, lambda c: "x")
    assert chamadas == 1


async def test_escrita_repete_a_abertura_da_conexao(tenants: Mapping[str, ConfigTenant]) -> None:
    engine = _Engine(falhas_conexao=1)
    acesso = _acesso_falso(tenants, engine)
    acesso._transacao = lambda schema, funcao, conexao: "gravado"  # type: ignore[method-assign,assignment]
    assert await acesso.escrever(CTX, lambda c: "x") == "gravado"
    assert engine.conexoes == 2


async def test_erro_de_negocio_nao_e_repetido(tenants: Mapping[str, ConfigTenant]) -> None:
    acesso = _acesso_falso(tenants, _Engine())
    chamadas = 0

    def transacao(schema: str, funcao: object, conexao: object) -> str:
        nonlocal chamadas
        chamadas += 1
        raise ValueError("dado inválido")

    acesso._transacao = transacao  # type: ignore[method-assign,assignment]
    with pytest.raises(ValueError):
        await acesso.ler(CTX, lambda c: "x")
    assert chamadas == 1


def test_url_ausente_ou_invalida() -> None:
    with pytest.raises(ErroConfiguracaoBanco, match="DATABASE_URL"):
        url_do_banco(None)
    with pytest.raises(ErroConfiguracaoBanco, match="postgresql\\+psycopg"):
        url_do_banco(SecretStr("postgresql://u:SEGREDO@h/db"))
    try:
        url_do_banco(SecretStr("mysql://u:SEGREDO@h/db"))
    except ErroConfiguracaoBanco as exc:
        assert "SEGREDO" not in str(exc)


def test_schema_invalido_e_recusado(tenants: Mapping[str, ConfigTenant]) -> None:
    ruim = {"emporio": tenants["emporio"].model_copy(update={"schema_db": 'x"; DROP SCHEMA emporio; --'})}
    with pytest.raises(ErroConfiguracaoBanco, match="schema"):
        AcessoTenant(_Engine(), ruim)  # type: ignore[arg-type]


def test_metodos_publicos_do_catalogo_tem_ctx() -> None:
    assert hasattr(CatalogoPostgres, "listar_produtos")


def test_main_sem_database_url_sai_com_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(RAIZ)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    original = mod_migrar.Settings
    monkeypatch.setattr(mod_migrar, "Settings", lambda: original(_env_file=None))  # type: ignore[call-arg]
    assert mod_migrar.main([]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_main_banco_fora_do_ar_sai_com_1_sem_vazar_senha(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(RAIZ)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:SEGREDO-XYZ@127.0.0.1:1/x")
    original = mod_migrar.Settings
    monkeypatch.setattr(mod_migrar, "Settings", lambda: original(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(
        mod_migrar,
        "criar_engine",
        lambda url, com_timeout=True: create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 1}),
    )
    assert mod_migrar.main([]) == 1
    saida = capsys.readouterr()
    assert "SEGREDO-XYZ" not in saida.out + saida.err
    assert "Falha na migração" in saida.err

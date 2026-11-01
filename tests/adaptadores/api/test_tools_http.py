"""Tools via HTTP (task 2.6): `/v1/tools` entrega o mesmo resultado do caso de uso, com RBAC e tenant."""

import json
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr

from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.identidade import JwtLocal
from emporio.composicao import Aplicacao
from emporio.portas import Papel, TenantContext
from tests.adaptadores.api.apoio import BASE
from tests.adaptadores.api.test_rbac import SEGREDO, _aplicacao, _sem_segredo_do_ambiente  # noqa: F401

CHAMADAS: Final[Mapping[str, Mapping[str, Any]]] = {
    "buscar_produtos": {"categoria": "Violões", "preco_max": 1000},
    "detalhar_produto": {"nome": "Takamine GD20"},
    "simular_pagamento": {"itens": [{"produto_id": 95, "quantidade": 1}], "forma": "pix", "cidade": "Campo Grande"},
    "consultar_pedido": {"numero": 1, "email_ou_telefone": "pedro.oliveira@jmail.com"},
    "status_loja": {},
    "registrar_solicitacao": {"tipo": "reclamacao", "descricao": "Caixa chegou amassada", "contato": "a@b.com"},
}
NOMES: Final = tuple(CHAMADAS)


def _token(*papeis: Papel, tenant: str = "emporio") -> str:
    provedor = JwtLocal(SecretStr(SEGREDO), relogio=time.time)
    return provedor.emitir(sub="n8n", tenant_id=tenant, papeis=papeis, validade_s=600)


def _auth(*papeis: Papel, tenant: str = "emporio", rota_tenant: str = "emporio") -> Mapping[str, str]:
    return {"X-Tenant-ID": rota_tenant, "Authorization": f"Bearer {_token(*papeis, tenant=tenant)}"}


@pytest.fixture
def aplicacao(tmp_path: Path) -> Aplicacao:
    return _aplicacao(tmp_path, SEGREDO)


@pytest.fixture
async def cliente(aplicacao: Aplicacao) -> AsyncIterator[httpx.AsyncClient]:
    app = criar_app(lambda: aplicacao)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
    ):
        yield http


def _ctx(papel: Papel) -> TenantContext:
    return TenantContext(tenant_id="emporio", papel=papel)


@pytest.mark.parametrize("papel", [Papel.ATENDENTE, Papel.GERENTE])
@pytest.mark.parametrize("nome", NOMES)
async def test_http_igual_ao_caso_de_uso(
    cliente: httpx.AsyncClient, aplicacao: Aplicacao, nome: str, papel: Papel
) -> None:
    assert aplicacao.ferramentas is not None
    esperado = (
        await aplicacao.ferramentas.executar(_ctx(papel), nome, CHAMADAS[nome], sessao_id="sessao-http")
    ).para_dict()
    r = await cliente.post(
        f"/v1/tools/{nome}",
        json={"argumentos": CHAMADAS[nome], "session_id": "sessao-http"},
        headers=_auth(papel),
    )
    assert r.status_code == 200, r.text
    assert r.json() == esperado
    assert r.json()["ok"] is True
    assert r.headers["Cache-Control"] == "no-store"


async def test_consulta_de_preco_traz_dado_real(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(
        "/v1/tools/detalhar_produto", json={"argumentos": {"nome": "Takamine GD20"}}, headers=_auth(Papel.ATENDENTE)
    )
    assert "Takamine GD20" in json.dumps(r.json(), ensure_ascii=False)


@pytest.mark.parametrize("papel", [Papel.CLIENTE, Papel.ADMIN_PLATAFORMA])
async def test_sem_tools_usar_403(cliente: httpx.AsyncClient, papel: Papel) -> None:
    lista = await cliente.get("/v1/tools", headers=_auth(papel))
    exec_ = await cliente.post("/v1/tools/status_loja", json={}, headers=_auth(papel))
    for r in (lista, exec_):
        assert r.status_code == 403
        assert r.json()["codigo"] == "sem_permissao"


async def test_sem_token_401(cliente: httpx.AsyncClient) -> None:
    for r in (
        await cliente.get("/v1/tools", headers={"X-Tenant-ID": "emporio"}),
        await cliente.post("/v1/tools/status_loja", json={}, headers={"X-Tenant-ID": "emporio"}),
    ):
        assert r.status_code == 401
        assert r.json()["codigo"] == "nao_autenticado"
        assert r.headers["WWW-Authenticate"].startswith("Bearer")


async def test_token_invalido_401(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/v1/tools", headers={"X-Tenant-ID": "emporio", "Authorization": "Bearer lixo"})
    assert r.status_code == 401
    assert r.json()["codigo"] == "token_invalido"


async def test_tenant_divergente_403(cliente: httpx.AsyncClient) -> None:
    headers = _auth(Papel.ATENDENTE, tenant="outra", rota_tenant="emporio")
    for r in (
        await cliente.get("/v1/tools", headers=headers),
        await cliente.post("/v1/tools/status_loja", json={}, headers=headers),
    ):
        assert r.status_code == 403
        assert r.json()["codigo"] == "tenant_divergente"


async def test_tool_desconhecida_404(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/tools/nao_existe", json={}, headers=_auth(Papel.ATENDENTE))
    assert r.status_code == 404
    assert r.json()["codigo_erro"] == "tool_desconhecida"
    assert r.json()["ok"] is False


async def test_argumentos_invalidos_422_com_corpo_da_tool(cliente: httpx.AsyncClient, aplicacao: Aplicacao) -> None:
    assert aplicacao.ferramentas is not None
    esperado = (
        await aplicacao.ferramentas.executar(_ctx(Papel.ATENDENTE), "consultar_pedido", {"numero": "x"})
    ).para_dict()
    r = await cliente.post(
        "/v1/tools/consultar_pedido", json={"argumentos": {"numero": "x"}}, headers=_auth(Papel.ATENDENTE)
    )
    assert r.status_code == 422
    assert r.json() == esperado
    assert r.json()["codigo_erro"] == "argumentos_invalidos"


async def test_corpo_malformado_422_sem_eco(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(
        "/v1/tools/status_loja", json={"argumentos": [], "segredo-x": 1}, headers=_auth(Papel.ATENDENTE)
    )
    assert r.status_code == 422
    assert r.json()["codigo"] == "dados_invalidos"
    assert "segredo-x" not in r.text


@pytest.mark.parametrize("papel", [Papel.ATENDENTE, Papel.GERENTE])
async def test_listagem_igual_as_definicoes_do_papel(
    cliente: httpx.AsyncClient, aplicacao: Aplicacao, papel: Papel
) -> None:
    assert aplicacao.ferramentas is not None
    esperado = [
        {"nome": d.nome, "descricao": d.descricao, "parametros": dict(d.parametros)}
        for d in aplicacao.ferramentas.definicoes(papel)
    ]
    r = await cliente.get("/v1/tools", headers=_auth(papel))
    assert r.status_code == 200
    assert r.json()["tools"] == esperado
    assert [t["nome"] for t in r.json()["tools"]] == list(NOMES)


async def test_listagem_filtrada_por_papel(
    cliente: httpx.AsyncClient, aplicacao: Aplicacao, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uma tool restrita ao gerente some da lista do atendente e é recusada na execução."""
    from emporio.modulos.atendimento.aplicacao import FerramentasLoja, Tool  # noqa: PLC0415

    ferramentas = aplicacao.ferramentas
    assert isinstance(ferramentas, FerramentasLoja)
    base = next(iter(ferramentas._tools.values()))
    ferramentas.registrar(Tool("so_gerente", "restrita", base.argumentos, frozenset({Papel.GERENTE}), base.funcao))
    atendente = await cliente.get("/v1/tools", headers=_auth(Papel.ATENDENTE))
    gerente = await cliente.get("/v1/tools", headers=_auth(Papel.GERENTE))
    assert "so_gerente" not in [t["nome"] for t in atendente.json()["tools"]]
    assert "so_gerente" in [t["nome"] for t in gerente.json()["tools"]]
    r = await cliente.post("/v1/tools/so_gerente", json={}, headers=_auth(Papel.ATENDENTE))
    assert r.status_code == 403
    assert r.json()["codigo_erro"] == "tool_nao_permitida"


async def test_registrar_solicitacao_idempotente_via_http(cliente: httpx.AsyncClient, aplicacao: Aplicacao) -> None:
    corpo = {"argumentos": CHAMADAS["registrar_solicitacao"], "session_id": "s-idem"}
    primeira = await cliente.post("/v1/tools/registrar_solicitacao", json=corpo, headers=_auth(Papel.ATENDENTE))
    segunda = await cliente.post("/v1/tools/registrar_solicitacao", json=corpo, headers=_auth(Papel.ATENDENTE))
    assert primeira.status_code == segunda.status_code == 200
    protocolo = primeira.json()["dados"]["protocolo"]
    assert segunda.json()["dados"]["protocolo"] == protocolo
    assert aplicacao.solicitacoes is not None
    gravadas = await aplicacao.solicitacoes.listar(_ctx(Papel.ATENDENTE))
    assert [s.id for s in gravadas].count(protocolo) == 1


async def test_sem_jwt_secret_503(tmp_path: Path) -> None:
    app = criar_app(lambda: _aplicacao(tmp_path, None))
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
    ):
        r = await http.get("/v1/tools", headers={"X-Tenant-ID": "emporio", "Authorization": "Bearer x"})
    assert r.status_code == 503

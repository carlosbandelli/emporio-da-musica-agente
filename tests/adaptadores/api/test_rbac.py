"""RBAC na API (task 2.5): matriz papel × endpoint, dupla checagem de tenant, 401/403/503, endpoints admin."""

import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
import yaml
from asgi_lifespan import LifespanManager
from pydantic import SecretStr

from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.identidade import JwtLocal
from emporio.adaptadores.llm import LLMFalso, responder_texto
from emporio.composicao import Aplicacao, montar
from emporio.configuracao import Settings
from emporio.modulos.atendimento.dominio import Solicitacao, StatusSolicitacao, TipoSolicitacao
from emporio.portas import Papel, TenantContext
from tests.adaptadores.api.apoio import BASE, RAIZ, REF

SEGREDO: Final = "segredo-de-teste-da-api-com-bem-mais-de-32-bytes"
CORPO_CHAT: Final = {"session_id": "s1", "mensagem": "oi"}
CTX: Final = TenantContext(tenant_id="emporio")


@pytest.fixture(autouse=True)
def _sem_segredo_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)


def _tenants_dois(destino: Path) -> Path:
    """`tenants.yaml` com um segundo tenant (`outra`) para testar a checagem de tenant."""
    bruto: dict[str, Any] = yaml.safe_load((RAIZ / "config" / "tenants.yaml").read_text(encoding="utf-8"))
    outra = dict(bruto["tenants"]["emporio"])
    outra["schema"] = "outra"
    bruto["tenants"]["outra"] = outra
    caminho = destino / "tenants-dois.yaml"
    caminho.write_text(yaml.safe_dump(bruto, allow_unicode=True), encoding="utf-8")
    return caminho


def _aplicacao(tmp_path: Path, segredo: str | None) -> Aplicacao:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, tenants_config=_tenants_dois(tmp_path), data_referencia=REF, jwt_secret=segredo
    )
    llm = LLMFalso([responder_texto("Oi! Sou a Sônia.") for _ in range(100)])
    return montar(settings, provedores={"falso": llm}, raiz=RAIZ, dir_estado=tmp_path)


async def _semear(aplicacao: Aplicacao) -> None:
    assert aplicacao.solicitacoes is not None
    for i, tipo in enumerate((TipoSolicitacao.RECLAMACAO, TipoSolicitacao.OUTROS, TipoSolicitacao.RECLAMACAO), 1):
        await aplicacao.solicitacoes.registrar(
            CTX,
            Solicitacao(
                id=f"EMP-{i}",
                tipo=tipo,
                descricao=f"descrição {i}",
                sessao_id="s",
                chave_idempotencia=f"k{i}",
                criada_em=datetime(2026, 3, 1, tzinfo=UTC),
                contato="cliente@example.com",
            ),
        )


async def _cliente(aplicacao: Aplicacao) -> AsyncIterator[httpx.AsyncClient]:
    app = criar_app(lambda: aplicacao)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
    ):
        yield http


@pytest.fixture
async def aplicacao(tmp_path: Path) -> Aplicacao:
    montada = _aplicacao(tmp_path, SEGREDO)
    await _semear(montada)
    return montada


@pytest.fixture
async def cliente(aplicacao: Aplicacao) -> AsyncIterator[httpx.AsyncClient]:
    async for http in _cliente(aplicacao):
        yield http


@pytest.fixture
async def cliente_sem_segredo(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    async for http in _cliente(_aplicacao(tmp_path, None)):
        yield http


def token(
    *papeis: Papel, tenant: str = "emporio", segredo: str = SEGREDO, deslocamento_s: float = 0, sub: str = "ana"
) -> str:
    provedor = JwtLocal(SecretStr(segredo), relogio=lambda: time.time() + deslocamento_s)
    return provedor.emitir(sub=sub, tenant_id=tenant, papeis=papeis, validade_s=600)


def auth(valor: str, tenant: str = "emporio", **extras: str) -> Mapping[str, str]:
    return {"X-Tenant-ID": tenant, "Authorization": f"Bearer {valor}", **extras}


# --- matriz papel × endpoint -----------------------------------------------------------------------------------------
ENDPOINTS: Final = {
    "listar": ("GET", "/v1/admin/solicitacoes", None),
    "mudar_status": ("PATCH", "/v1/admin/solicitacoes/EMP-1", {"status": "em_atendimento"}),
    "ler_config": ("GET", "/v1/admin/config", None),
    "editar_config": ("PUT", "/v1/admin/config", {"deadline_mensagem_s": 25}),
    "chat": ("POST", "/v1/chat", CORPO_CHAT),
    "tools_listar": ("GET", "/v1/tools", None),
    "tools_executar": ("POST", "/v1/tools/status_loja", {}),
}
PERMITIDOS: Final = {
    Papel.CLIENTE: {"chat"},
    Papel.ATENDENTE: {"chat", "listar", "mudar_status", "tools_listar", "tools_executar"},
    Papel.GERENTE: {"chat", "listar", "mudar_status", "ler_config", "editar_config", "tools_listar", "tools_executar"},
    Papel.ADMIN_PLATAFORMA: set(),
    Papel.SERVICO_TOOLS: {"tools_listar", "tools_executar"},
}


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
@pytest.mark.parametrize("papel", list(Papel))
async def test_matriz_papel_por_endpoint(cliente: httpx.AsyncClient, papel: Papel, endpoint: str) -> None:
    metodo, rota, corpo = ENDPOINTS[endpoint]
    r = await cliente.request(metodo, rota, json=corpo, headers=auth(token(papel)))
    if endpoint in PERMITIDOS[papel]:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 403, r.text
        assert r.json()["codigo"] == "sem_permissao"


ADMIN: Final = [e for e in ENDPOINTS if e != "chat"]


@pytest.mark.parametrize("endpoint", ADMIN)
async def test_admin_sem_token_401_com_www_authenticate(cliente: httpx.AsyncClient, endpoint: str) -> None:
    metodo, rota, corpo = ENDPOINTS[endpoint]
    r = await cliente.request(metodo, rota, json=corpo, headers={"X-Tenant-ID": "emporio"})
    assert r.status_code == 401
    assert r.json()["codigo"] == "nao_autenticado"
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_token_de_outro_tenant_403(cliente: httpx.AsyncClient, endpoint: str) -> None:
    metodo, rota, corpo = ENDPOINTS[endpoint]
    r = await cliente.request(metodo, rota, json=corpo, headers=auth(token(Papel.GERENTE, tenant="outra")))
    assert r.status_code == 403
    assert r.json()["codigo"] == "tenant_divergente"


async def test_token_de_outro_tenant_pelo_subdominio_403(cliente: httpx.AsyncClient) -> None:
    cabecalhos = {"Host": "emporio.exemplo.com", "Authorization": f"Bearer {token(Papel.GERENTE, tenant='outra')}"}
    r = await cliente.get("/v1/admin/solicitacoes", headers=cabecalhos)
    assert r.status_code == 403 and r.json()["codigo"] == "tenant_divergente"
    ok = {"Host": "emporio.exemplo.com", "Authorization": f"Bearer {token(Papel.GERENTE)}"}
    assert (await cliente.get("/v1/admin/solicitacoes", headers=ok)).status_code == 200


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_token_expirado_401(cliente: httpx.AsyncClient, endpoint: str) -> None:
    metodo, rota, corpo = ENDPOINTS[endpoint]
    vencido = token(Papel.GERENTE, deslocamento_s=-7200)
    r = await cliente.request(metodo, rota, json=corpo, headers=auth(vencido))
    assert r.status_code == 401
    assert r.json()["codigo"] == "token_invalido"
    assert 'error="invalid_token"' in r.headers["WWW-Authenticate"]
    assert vencido not in r.text


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_assinatura_invalida_401(cliente: httpx.AsyncClient, endpoint: str) -> None:
    metodo, rota, corpo = ENDPOINTS[endpoint]
    falso = token(Papel.GERENTE, segredo="outro-segredo-qualquer-com-mais-de-32-bytes")
    r = await cliente.request(metodo, rota, json=corpo, headers=auth(falso))
    assert r.status_code == 401 and r.json()["codigo"] == "token_invalido"
    assert falso not in r.text


@pytest.mark.parametrize("cabecalho", ["Basic dXNlcjpzZW5oYQ==", "Bearer", "Bearer a b", "token-sem-esquema"])
async def test_authorization_malformado_401(cliente: httpx.AsyncClient, cabecalho: str) -> None:
    r = await cliente.get("/v1/admin/solicitacoes", headers={"X-Tenant-ID": "emporio", "Authorization": cabecalho})
    assert r.status_code == 401 and r.json()["codigo"] == "token_invalido"


async def test_mensagens_de_erro_traduzidas(cliente: httpx.AsyncClient) -> None:
    pt = await cliente.get("/v1/admin/solicitacoes", headers={"X-Tenant-ID": "emporio"})
    en = await cliente.get("/v1/admin/solicitacoes", headers={"X-Tenant-ID": "emporio", "Accept-Language": "en-US"})
    assert "Autenticação necessária" in pt.json()["mensagem"]
    assert "Authentication required" in en.json()["mensagem"]
    proibido = await cliente.get(
        "/v1/admin/config", headers=auth(token(Papel.ATENDENTE), **{"Accept-Language": "en-US"})
    )
    assert proibido.json()["mensagem"] == "Your role is not allowed to perform this operation."


# --- chat público ----------------------------------------------------------------------------------------------------
async def test_chat_sem_token_e_publico(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json=CORPO_CHAT, headers={"X-Tenant-ID": "emporio"})
    assert r.status_code == 200 and r.json()["texto"] == "Oi! Sou a Sônia."


async def test_chat_sem_token_continua_publico_sem_segredo(cliente_sem_segredo: httpx.AsyncClient) -> None:
    r = await cliente_sem_segredo.post("/v1/chat", json=CORPO_CHAT, headers={"X-Tenant-ID": "emporio"})
    assert r.status_code == 200


async def test_papel_do_chat_vem_do_token(aplicacao: Aplicacao, cliente: httpx.AsyncClient) -> None:
    papeis: list[Papel] = []
    original = aplicacao.motor.responder

    async def espiao(ctx: TenantContext, *args: Any, **kwargs: Any) -> Any:
        papeis.append(ctx.papel)
        return await original(ctx, *args, **kwargs)

    object.__setattr__(aplicacao.motor, "responder", espiao)
    await cliente.post("/v1/chat", json=CORPO_CHAT, headers={"X-Tenant-ID": "emporio"})
    await cliente.post("/v1/chat", json=CORPO_CHAT, headers=auth(token(Papel.ATENDENTE)))
    assert papeis == [Papel.CLIENTE, Papel.ATENDENTE]


# --- segredo só de Settings ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("endpoint", ADMIN)
async def test_sem_segredo_protegidos_respondem_503(cliente_sem_segredo: httpx.AsyncClient, endpoint: str) -> None:
    metodo, rota, corpo = ENDPOINTS[endpoint]
    r = await cliente_sem_segredo.request(metodo, rota, json=corpo, headers=auth(token(Papel.GERENTE)))
    assert r.status_code == 503
    assert r.json()["codigo"] == "autenticacao_indisponivel"


async def test_chat_com_token_sem_segredo_503(cliente_sem_segredo: httpx.AsyncClient) -> None:
    r = await cliente_sem_segredo.post("/v1/chat", json=CORPO_CHAT, headers=auth(token(Papel.CLIENTE)))
    assert r.status_code == 503


async def test_segredo_do_ambiente_nao_e_lido_quando_settings_nao_tem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`montar` só usa `settings.jwt_secret`: a variável no processo não vaza para um `Settings` explícito."""
    monkeypatch.setenv("JWT_SECRET", SEGREDO)
    aplicacao = _aplicacao(tmp_path, None)
    assert aplicacao.identidade is None


async def test_segredo_curto_desativa_a_identidade(tmp_path: Path) -> None:
    assert _aplicacao(tmp_path, "curto-demais").identidade is None


# --- solicitações ----------------------------------------------------------------------------------------------------
async def test_listar_com_filtros_e_sem_cache(cliente: httpx.AsyncClient) -> None:
    cab = auth(token(Papel.ATENDENTE))
    todas = await cliente.get("/v1/admin/solicitacoes", headers=cab)
    assert [s["id"] for s in todas.json()["solicitacoes"]] == ["EMP-1", "EMP-2", "EMP-3"]
    assert todas.headers["Cache-Control"] == "no-store"
    assert "chave_idempotencia" not in todas.json()["solicitacoes"][0]
    outros = await cliente.get("/v1/admin/solicitacoes", params={"tipo": "outros"}, headers=cab)
    assert [s["id"] for s in outros.json()["solicitacoes"]] == ["EMP-2"]
    await cliente.patch("/v1/admin/solicitacoes/EMP-3", json={"status": "resolvida"}, headers=cab)
    abertas = await cliente.get("/v1/admin/solicitacoes", params={"status": "aberta"}, headers=cab)
    assert [s["id"] for s in abertas.json()["solicitacoes"]] == ["EMP-1", "EMP-2"]
    invalido = await cliente.get("/v1/admin/solicitacoes", params={"status": "xyz"}, headers=cab)
    assert invalido.status_code == 422 and invalido.json()["codigo"] == "dados_invalidos"


async def test_fluxo_de_status_com_auditoria(cliente: httpx.AsyncClient) -> None:
    cab = auth(token(Papel.ATENDENTE, sub="ana"))
    r1 = await cliente.patch("/v1/admin/solicitacoes/EMP-1", json={"status": "em_atendimento"}, headers=cab)
    assert r1.status_code == 200
    assert (r1.json()["status"], r1.json()["atualizada_por"]) == ("em_atendimento", "ana")
    assert r1.json()["atualizada_em"] is not None
    r2 = await cliente.patch("/v1/admin/solicitacoes/EMP-1", json={"status": "resolvida"}, headers=cab)
    assert r2.status_code == 200 and r2.json()["status"] == StatusSolicitacao.RESOLVIDA.value
    volta = await cliente.patch("/v1/admin/solicitacoes/EMP-1", json={"status": "aberta"}, headers=cab)
    assert volta.status_code == 409 and volta.json()["codigo"] == "transicao_invalida"


async def test_patch_inexistente_invalido_ou_de_outro_tenant(aplicacao: Aplicacao, cliente: httpx.AsyncClient) -> None:
    cab = auth(token(Papel.ATENDENTE))
    assert (
        await cliente.patch("/v1/admin/solicitacoes/NAO-EXISTE", json={"status": "resolvida"}, headers=cab)
    ).status_code == 404
    assert (await cliente.patch("/v1/admin/solicitacoes/EMP-1", json={"status": "xyz"}, headers=cab)).status_code == 422
    extra = await cliente.patch("/v1/admin/solicitacoes/EMP-1", json={"status": "resolvida", "x": 1}, headers=cab)
    assert extra.status_code == 422
    caminho = await cliente.patch("/v1/admin/solicitacoes/a.b", json={"status": "resolvida"}, headers=cab)
    assert caminho.status_code == 422
    # A solicitação EMP-1 é do tenant `emporio`: um atendente de `outra` (no próprio tenant) não a encontra.
    cab_outra = auth(token(Papel.ATENDENTE, tenant="outra"), tenant="outra")
    r = await cliente.patch("/v1/admin/solicitacoes/EMP-1", json={"status": "resolvida"}, headers=cab_outra)
    assert r.status_code == 404
    lista = await cliente.get("/v1/admin/solicitacoes", headers=cab_outra)
    assert lista.json() == {"solicitacoes": []}


# --- config ----------------------------------------------------------------------------------------------------------
async def test_put_config_vale_em_memoria_e_aparece_no_chat(aplicacao: Aplicacao, cliente: httpx.AsyncClient) -> None:
    cab = auth(token(Papel.GERENTE))
    corpo = {
        "persona": {"nome": "Bia", "resumo": "Atendente simpática da loja."},
        "termos_fora_de_escopo": {"pt-BR": ["palheta"], "en-US": ["pick"]},
    }
    r = await cliente.put("/v1/admin/config", json=corpo, headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["persona"]["nome"] == "Bia"
    assert r.json()["termos_fora_de_escopo"] == {"pt-BR": ["palheta"], "en-US": ["pick"]}
    assert r.headers["Cache-Control"] == "no-store"
    assert aplicacao.tenants["emporio"].persona.nome == "Bia"
    assert aplicacao.tenants["outra"].persona.nome != "Bia"  # só o tenant do token muda
    chat = await cliente.post("/v1/chat", json=CORPO_CHAT, headers={"X-Tenant-ID": "emporio"})
    assert chat.json()["persona"] == "Bia"
    lida = await cliente.get("/v1/admin/config", headers=cab)
    assert lida.json()["persona"]["nome"] == "Bia"


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"schema": "outra"},
        {"dados_dir": "/etc"},
        {"persona": {"nome": "", "resumo": "x"}},
        {"deadline_mensagem_s": -1},
    ],
)
async def test_put_config_invalido_422_sem_mudar_nada(
    aplicacao: Aplicacao, cliente: httpx.AsyncClient, corpo: dict[str, object]
) -> None:
    antes = aplicacao.tenants["emporio"]
    r = await cliente.put("/v1/admin/config", json=corpo, headers=auth(token(Papel.GERENTE)))
    assert r.status_code == 422 and r.json()["codigo"] == "dados_invalidos"
    assert aplicacao.tenants["emporio"] == antes

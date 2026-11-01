"""Isolamento por tenant: sem tenant não há consulta; schemas não se enxergam; `search_path` não vaza."""

import inspect
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from emporio.adaptadores.db import (
    CatalogoPostgres,
    ClientesPostgres,
    ErroTenantDesconhecido,
    PedidosPostgres,
    SolicitacoesPostgres,
)
from emporio.adaptadores.db.migrar import migrar
from emporio.portas import FiltroProdutos, TenantContext
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores import contrato_dados as contrato
from tests.adaptadores.conftest import RAIZ
from tests.adaptadores.db.conftest import texto_url
from tests.adaptadores.db.repos import Repos, repos_postgres

pytestmark = pytest.mark.postgres

PFX = "desafio_tecnico_ai_eng - "
CTX_A = TenantContext(tenant_id="emporio")
CTX_B = TenantContext(tenant_id="loja_b")


@pytest.fixture
def dois_tenants(tenants: Mapping[str, ConfigTenant], tmp_path: Path) -> Mapping[str, ConfigTenant]:
    """`emporio` (CSVs reais) e `loja_b` (um produto, um cliente e um pedido próprios)."""
    dados = tmp_path / "dados_b"
    dados.mkdir()
    (dados / f"{PFX}products.csv").write_text(
        "product_id,price_brl,name,category_id,description,stock_quantity,status,specs,created_at\n"
        "500,99.9,Ukulele da Loja B,1,Somente na loja B,4,active,{},2024-01-01\n",
        encoding="utf-8",
    )
    (dados / f"{PFX}categories.csv").write_text("category_id,name,description\n1,Cordas,x\n", encoding="utf-8")
    (dados / f"{PFX}promotions.csv").write_text(
        "promotion_id,product_id,discount_percent,description,is_active\n", encoding="utf-8"
    )
    (dados / f"{PFX}customers.csv").write_text(
        "customer_id,name,phone,email,city\n7000,Beto,1,beto@b.com,Dourados\n", encoding="utf-8"
    )
    (dados / f"{PFX}orders.csv").write_text(
        "order_id,customer_id,order_date,status,total_brl,payment_method,tracking_code,estimated_delivery,notes\n"
        "1,7000,2026-01-01,pending,99.9,pix,,,\n",
        encoding="utf-8",
    )
    (dados / f"{PFX}order_items.csv").write_text("order_id,quantity,product_id\n1,1,500\n", encoding="utf-8")
    config_b = tenants["emporio"].model_copy(update={"schema_db": "loja_b", "dados_dir": str(dados)})
    return {"emporio": tenants["emporio"], "loja_b": config_b}


@pytest.fixture
def repos(banco: URL, dois_tenants: Mapping[str, ConfigTenant]) -> Iterator[Repos]:
    migrar(SecretStr(texto_url(banco)), dois_tenants, RAIZ)
    conjunto = repos_postgres(dois_tenants, banco)
    yield conjunto
    conjunto.fechar()


async def test_schemas_isolados(repos: Repos) -> None:
    assert len(await repos.catalogo.listar_produtos(CTX_A, FiltroProdutos(incluir_inativos=True))) == 65
    produtos_b = await repos.catalogo.listar_produtos(CTX_B, FiltroProdutos(incluir_inativos=True))
    assert [p.id for p in produtos_b] == [500]
    assert await repos.catalogo.obter_produto(CTX_B, 81) is None  # existe só no tenant A
    assert await repos.catalogo.obter_produto(CTX_A, 500) is None  # existe só no tenant B
    assert [c.id for c in await repos.catalogo.listar_categorias(CTX_B)] == [1]
    cliente_b = await repos.clientes.obter_cliente(CTX_B, 7000)
    assert cliente_b is not None
    assert cliente_b.cidade == "Dourados"
    assert await repos.clientes.obter_cliente(CTX_A, 7000) is None
    pedido_b = await repos.pedidos.obter_pedido(CTX_B, 1)
    pedido_a = await repos.pedidos.obter_pedido(CTX_A, 1)
    assert pedido_b is not None
    assert pedido_a is not None
    assert pedido_b.cliente_id == 7000
    assert pedido_a.cliente_id == 3


async def test_solicitacoes_isoladas(repos: Repos) -> None:
    await repos.solicitacoes.registrar(CTX_A, contrato.solicitacao_exemplo("so-na-a"))
    assert [s.chave_idempotencia for s in await repos.solicitacoes.listar(CTX_A)] == ["so-na-a"]
    assert await repos.solicitacoes.listar(CTX_B) == ()
    # mesma chave em tenants diferentes não colide: a unicidade é por schema
    b = await repos.solicitacoes.registrar(CTX_B, contrato.solicitacao_exemplo("so-na-a", "texto da loja B"))
    assert b.descricao == "texto da loja B"


async def test_tenant_desconhecido_levanta_erro(repos: Repos) -> None:
    fantasma = TenantContext(tenant_id="fantasma")
    with pytest.raises(ErroTenantDesconhecido, match="fantasma"):
        await repos.catalogo.listar_categorias(fantasma)
    with pytest.raises(ErroTenantDesconhecido):
        await repos.pedidos.obter_pedido(fantasma, 1)
    with pytest.raises(ErroTenantDesconhecido):
        await repos.clientes.obter_cliente(fantasma, 1)
    with pytest.raises(ErroTenantDesconhecido):
        await repos.solicitacoes.listar(fantasma)


def test_todo_metodo_publico_exige_ctx_primeiro() -> None:
    """Consulta sem tenant é impossível por construção: nenhum método público aceita ficar sem `ctx`."""
    for classe in (CatalogoPostgres, PedidosPostgres, ClientesPostgres, SolicitacoesPostgres):
        metodos = [n for n, f in inspect.getmembers(classe, inspect.isfunction) if not n.startswith("_")]
        assert metodos, classe
        for nome in metodos:
            parametros = list(inspect.signature(getattr(classe, nome)).parameters.values())
            assert parametros[1].name == "ctx", f"{classe.__name__}.{nome}"
            assert parametros[1].default is inspect.Parameter.empty
            assert parametros[1].annotation is TenantContext


def test_conexao_sem_tenant_nao_enxerga_tabelas(banco: URL, dois_tenants: Mapping[str, ConfigTenant]) -> None:
    """Sem `search_path` do tenant as tabelas não existem: o schema `public` fica vazio."""
    migrar(SecretStr(texto_url(banco)), dois_tenants, RAIZ)
    engine = create_engine(banco)
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('produtos')")).scalar_one() is None
        assert c.execute(text("SELECT to_regclass('emporio.produtos')")).scalar_one() is not None
        publicas = c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar_one()
        assert publicas == 0
        with pytest.raises(Exception, match="produtos"):
            c.execute(text("SELECT * FROM produtos"))
    engine.dispose()


async def test_search_path_nao_vaza_pelo_pool(repos: Repos) -> None:
    """O `search_path` vale só na transação: a conexão devolvida ao pool volta ao padrão."""
    await repos.catalogo.listar_categorias(CTX_B)
    engine = repos.postgres[0]._acesso._engine
    with engine.connect() as c:
        caminho = c.execute(text("SHOW search_path")).scalar_one()
    assert "loja_b" not in caminho
    assert "emporio" not in caminho


async def test_timeout_de_consulta_de_2_segundos(repos: Repos) -> None:
    engine = repos.postgres[0]._acesso._engine
    with engine.connect() as c, pytest.raises(OperationalError):
        c.execute(text("SELECT pg_sleep(5)"))


async def test_servico_indisponivel_mensagem_sem_senha(tenants: Mapping[str, ConfigTenant]) -> None:
    from emporio.adaptadores.db import criar_repositorios_postgres  # noqa: PLC0415
    from emporio.configuracao import Settings  # noqa: PLC0415

    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:SEGREDO-XYZ@127.0.0.1:1/x")  # type: ignore[call-arg,arg-type]
    repos = criar_repositorios_postgres(settings, tenants)
    with pytest.raises(OperationalError) as info:
        await repos.catalogo.listar_categorias(CTX_A)
    assert "SEGREDO-XYZ" not in str(info.value)
    repos.fechar()

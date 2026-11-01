import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from emporio.adaptadores.csv import (
    CatalogoCsv,
    ClientesCsv,
    ErroTenantDesconhecido,
    PedidosCsv,
    SolicitacoesJsonl,
    _leitura,
)
from emporio.adaptadores.csv._leitura import interpretar_pagamento, para_centavos
from emporio.modulos.catalogo.dominio import Produto
from emporio.modulos.pedidos.dominio import FormaPagamento, MetodoPagamento
from emporio.portas import FiltroProdutos, TenantContext
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores import contrato_dados as contrato
from tests.adaptadores.conftest import RAIZ

CTX = TenantContext(tenant_id="emporio")
PFX = "desafio_tecnico_ai_eng - "
Tenants = Mapping[str, ConfigTenant]


def test_para_centavos() -> None:
    assert para_centavos("599.9") == 59990
    assert para_centavos("1049") == 104900
    assert para_centavos("159,90") == 15990
    assert para_centavos("0.005") == 1
    for ruim in ("", "abc", "-1", "nan"):
        with pytest.raises(ValueError):
            para_centavos(ruim)


def test_interpretar_pagamento() -> None:
    assert interpretar_pagamento("pix") == MetodoPagamento(FormaPagamento.PIX, 1)
    assert interpretar_pagamento("debit") == MetodoPagamento(FormaPagamento.DEBITO, 1)
    assert interpretar_pagamento("credit_6x") == MetodoPagamento(FormaPagamento.CREDITO, 6)
    with pytest.raises(ValueError):
        interpretar_pagamento("cheque")


async def test_catalogo_real(tenants: Tenants) -> None:
    await contrato.verificar_catalogo_real(CatalogoCsv(tenants, RAIZ))


async def test_filtros(tenants: Tenants) -> None:
    await contrato.verificar_filtros(CatalogoCsv(tenants, RAIZ))


async def test_promocoes_todas_as_linhas(tenants: Tenants) -> None:
    await contrato.verificar_promocoes_todas_as_linhas(CatalogoCsv(tenants, RAIZ))


async def test_pedidos_real(tenants: Tenants) -> None:
    await contrato.verificar_pedidos_real(PedidosCsv(tenants, RAIZ))


async def test_total_confere_com_itens(tenants: Tenants) -> None:
    await contrato.verificar_total_confere_com_itens(PedidosCsv(tenants, RAIZ), CatalogoCsv(tenants, RAIZ))


async def test_clientes(tenants: Tenants) -> None:
    await contrato.verificar_clientes(ClientesCsv(tenants, RAIZ))


async def test_tenant_desconhecido(tenants: Tenants) -> None:
    with pytest.raises(ErroTenantDesconhecido, match="outra"):
        await CatalogoCsv(tenants, RAIZ).listar_categorias(TenantContext(tenant_id="outra"))


async def test_carga_unica_sob_concorrencia(tenants: Tenants, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = 0
    original = _leitura.ler_produtos

    def contando(d: Path) -> tuple[Produto, ...]:
        nonlocal chamadas
        chamadas += 1
        return original(d)

    monkeypatch.setattr(_leitura, "ler_produtos", contando)
    cat = CatalogoCsv(tenants, RAIZ)
    await asyncio.gather(*(cat.obter_produto(CTX, 81) for _ in range(10)))
    assert chamadas == 1


def _escrever(dir_: Path, tabela: str, texto: str) -> None:
    (dir_ / f"{PFX}{tabela}.csv").write_text(texto, encoding="utf-8")


def _tenant_sintetico(tenants: Tenants) -> Tenants:
    return {"emporio": tenants["emporio"].model_copy(update={"dados_dir": "dados"})}


async def test_linhas_ruins_sao_tratadas(tenants: Tenants, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    d = tmp_path / "dados"
    d.mkdir()
    cab = "product_id,price_brl,name,category_id,description,stock_quantity,status,specs,created_at\n"
    _escrever(
        d,
        "products",
        cab
        + '81,10.5,Bom,1,desc,3,active,"{""a"":1}",2021-01-01\n'
        + "82,20,SpecsRuins,1,desc,3,active,{quebrado,2021-01-01\n"
        + "abc,20,IdRuim,1,desc,3,active,{},2021-01-01\n"
        + "84,xx,PrecoRuim,1,desc,3,active,{},2021-01-01\n"
        + "85,20,StatusRuim,1,desc,3,esgotado,{},2021-01-01\n"
        + "81,30,Duplicado,1,desc,3,active,{},2021-01-01\n"
        + "86,20,DataRuim,1,desc,3,active,[1],ontem\n",
    )
    _escrever(d, "categories", "category_id,name,description\n1,Guitarras,x\nz,Ruim,y\n")
    _escrever(
        d,
        "promotions",
        "promotion_id,product_id,discount_percent,description,is_active\n1,81,10,BF,1\n2,81,0,zero,1\n",
    )
    cat = CatalogoCsv(_tenant_sintetico(tenants), tmp_path)
    with caplog.at_level(logging.WARNING):
        produtos = await cat.listar_produtos(CTX, FiltroProdutos(incluir_inativos=True))
        cats = await cat.listar_categorias(CTX)
        promos = await cat.listar_promocoes(CTX, 81)
    assert [p.id for p in produtos] == [81, 82, 86]
    por_id = {p.id: p for p in produtos}
    assert por_id[81].preco_centavos == 1050
    assert por_id[82].specs == {}  # JSON inválido: mantém com specs vazias
    assert por_id[86].specs == {}
    assert por_id[86].criado_em is None
    assert [c.id for c in cats] == [1]
    assert len(promos) == 1
    assert "linha 3" in caplog.text
    assert "linha 4" in caplog.text


async def test_pedidos_ruins_e_sem_pii_nos_logs(
    tenants: Tenants, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    d = tmp_path / "dados"
    d.mkdir()
    _escrever(d, "order_items", "order_id,quantity,product_id\n1,2,81\n1,0,82\n1,x,83\n")
    _escrever(
        d,
        "orders",
        "order_id,customer_id,order_date,status,total_brl,payment_method,tracking_code,estimated_delivery,notes\n"
        "1,1,2026-01-01,shipped,100.50,credit_3x,BR1,2026-01-10,\n"
        "2,1,2026-01-01,shipped,abc,pix,,,\n"
        "3,1,2026-01-01,voando,10,pix,,,\n"
        "4,1,2026-01-01,pending,10,cheque,,,\n",
    )
    _escrever(
        d, "customers", "customer_id,name,phone,email,city\n1,Fulana,(67) 99999-0000,fulana@x.com,CG\nq,Ruim,1,2,3\n"
    )
    t = _tenant_sintetico(tenants)
    with caplog.at_level(logging.WARNING):
        pedidos = PedidosCsv(t, tmp_path)
        p1 = await pedidos.obter_pedido(CTX, 1)
        p2 = await pedidos.obter_pedido(CTX, 2)
        p3 = await pedidos.obter_pedido(CTX, 3)
        p4 = await pedidos.obter_pedido(CTX, 4)
        await ClientesCsv(t, tmp_path).obter_cliente(CTX, 1)
    assert p1 is not None
    assert p1.total_centavos == 10050
    assert [(i.produto_id, i.quantidade) for i in p1.itens] == [(81, 2)]
    assert p2 is not None
    assert p2.total_centavos is None
    assert p3 is None
    assert p4 is None
    assert "fulana@x.com" not in caplog.text
    assert "99999" not in caplog.text


async def test_solicitacoes_idempotente(tmp_path: Path) -> None:
    repo = SolicitacoesJsonl(tmp_path)
    await contrato.verificar_solicitacoes_idempotente(repo, SolicitacoesJsonl(tmp_path))
    linhas = (tmp_path / "emporio" / "solicitacoes.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 2
    assert await repo.listar(TenantContext(tenant_id="outro")) == ()


async def test_solicitacoes_concorrentes_sem_duplicar(tmp_path: Path) -> None:
    await contrato.verificar_solicitacoes_concorrentes(SolicitacoesJsonl(tmp_path))


async def test_solicitacoes_tenant_invalido(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await SolicitacoesJsonl(tmp_path).listar(TenantContext(tenant_id="../x"))

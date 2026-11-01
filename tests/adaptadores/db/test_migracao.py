"""`migrar`: idempotência, advisory lock, processos concorrentes e seed."""

import asyncio
import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from emporio.adaptadores.csv import CatalogoCsv
from emporio.adaptadores.db import criar_repositorios_postgres
from emporio.adaptadores.db.migrar import CHAVE_LOCK, ErroMigracao, migrar, revisao_head
from emporio.portas import FiltroProdutos, TenantContext
from emporio.portas.tenant import ConfigTenant
from tests.adaptadores.conftest import RAIZ
from tests.adaptadores.db.conftest import texto_url
from tests.adaptadores.db.repos import settings_postgres

pytestmark = pytest.mark.postgres

PFX = "desafio_tecnico_ai_eng - "


def _contar(url: URL, sql: str) -> int:
    engine = create_engine(url)
    with engine.connect() as c:
        valor = c.execute(text(sql)).scalar_one()
    engine.dispose()
    return int(valor)


def test_migrar_idempotente(banco: URL, tenants: Mapping[str, ConfigTenant]) -> None:
    primeira = migrar(SecretStr(texto_url(banco)), tenants, RAIZ)
    segunda = migrar(SecretStr(texto_url(banco)), tenants, RAIZ)
    assert [r.migrou for r in primeira] == [True]
    assert primeira[0].revisao_atual == revisao_head()
    assert primeira[0].seed.inseridas > 0
    assert [r.migrou for r in segunda] == [False]
    assert segunda[0].seed.inseridas == 0
    assert _contar(banco, "SELECT count(*) FROM emporio.produtos") == 65
    assert _contar(banco, "SELECT count(*) FROM emporio.promocoes") == 25
    assert _contar(banco, "SELECT count(*) FROM emporio.alembic_version") == 1
    itens = _contar(banco, "SELECT count(*) FROM emporio.itens_pedido")
    assert itens > 0
    migrar(SecretStr(texto_url(banco)), tenants, RAIZ)
    assert _contar(banco, "SELECT count(*) FROM emporio.itens_pedido") == itens


def test_migrar_sem_seed_nao_insere_dados(banco: URL, tenants: Mapping[str, ConfigTenant]) -> None:
    migrar(SecretStr(texto_url(banco)), tenants, RAIZ, com_seed=False)
    assert _contar(banco, "SELECT count(*) FROM emporio.produtos") == 0


def test_dois_migradores_concorrentes_em_threads_migram_uma_vez(
    banco: URL, tenants: Mapping[str, ConfigTenant]
) -> None:
    barreira = threading.Barrier(2)
    resultados: list[tuple[bool, ...]] = []
    erros: list[BaseException] = []

    def rodar() -> None:
        try:
            barreira.wait(timeout=10)
            resultados.append(tuple(r.migrou for r in migrar(SecretStr(texto_url(banco)), tenants, RAIZ)))
        except BaseException as exc:
            erros.append(exc)

    threads = [threading.Thread(target=rodar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not erros
    assert sorted(resultados) == [(False,), (True,)]
    assert _contar(banco, "SELECT count(*) FROM emporio.produtos") == 65


def _executar_modulo(url: URL) -> subprocess.Popen[str]:
    env = {**os.environ, "DATABASE_URL": texto_url(url), "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen(
        [sys.executable, "-m", "emporio.adaptadores.db.migrar"],
        cwd=RAIZ,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def test_modulo_migrar_dois_subprocessos_concorrentes(banco: URL) -> None:
    procs = [_executar_modulo(banco) for _ in range(2)]
    saidas = [p.communicate(timeout=180) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], saidas
    stdouts = [s[0] for s in saidas]
    assert sum("migrado" in s for s in stdouts) == 1, stdouts
    assert sum("em dia" in s for s in stdouts) == 1, stdouts
    assert _contar(banco, "SELECT count(*) FROM emporio.produtos") == 65
    senha = banco.password or ""
    assert senha
    assert all(senha not in texto for par in saidas for texto in par)


def test_modulo_migrar_idempotente_duas_execucoes(banco: URL) -> None:
    out = ""
    for _ in range(2):
        p = _executar_modulo(banco)
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
    assert "em dia" in out


def test_lock_ocupado_esgota_o_tempo(banco: URL, tenants: Mapping[str, ConfigTenant]) -> None:
    engine = create_engine(banco, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        c.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CHAVE_LOCK})
        with pytest.raises(ErroMigracao, match="Tempo esgotado"):
            migrar(SecretStr(texto_url(banco)), tenants, RAIZ, timeout_lock_s=0.5)
        c.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CHAVE_LOCK})
    engine.dispose()
    assert migrar(SecretStr(texto_url(banco)), tenants, RAIZ)[0].migrou  # liberado: segue normalmente


def _escrever(dir_: Path, tabela: str, texto: str) -> None:
    (dir_ / f"{PFX}{tabela}.csv").write_text(texto, encoding="utf-8")


def test_seed_com_dados_sujos_igual_ao_csv(banco: URL, tenants: Mapping[str, ConfigTenant], tmp_path: Path) -> None:
    """A limpeza do seed (cópia em `db`, por causa da regra C5) deve coincidir com a do adaptador CSV."""
    dados = tmp_path / "dados"
    dados.mkdir()
    cab = "product_id,price_brl,name,category_id,description,stock_quantity,status,specs,created_at\n"
    _escrever(
        dados,
        "products",
        cab
        + '81,10.5,Bom,1,desc,3,active,"{""a"":1}",2021-01-01\n'
        + "82,20,SpecsRuins,1,desc,3,active,{quebrado,2021-01-01\n"
        + "abc,20,IdRuim,1,desc,3,active,{},2021-01-01\n"
        + "84,xx,PrecoRuim,1,desc,3,active,{},2021-01-01\n"
        + "81,30,Duplicado,1,desc,3,active,{},2021-01-01\n"
        + "86,20,DataRuim,1,desc,3,active,[1],ontem\n",
    )
    _escrever(dados, "categories", "category_id,name,description\n1,Guitarras,x\nz,Ruim,y\n")
    _escrever(
        dados,
        "promotions",
        "promotion_id,product_id,discount_percent,description,is_active\n1,81,10,BF,1\n2,81,0,zero,1\n",
    )
    _escrever(dados, "customers", "customer_id,name,phone,email,city\n1,Fulana,1,f@x.com,CG\n")
    _escrever(
        dados,
        "orders",
        "order_id,customer_id,order_date,status,total_brl,payment_method,tracking_code,estimated_delivery,notes\n"
        "1,1,2026-01-01,pending,10,pix,,,\n",
    )
    _escrever(dados, "order_items", "order_id,quantity,product_id\n1,2,81\n")
    sintetico = {"emporio": tenants["emporio"].model_copy(update={"dados_dir": "dados"})}
    migrar(SecretStr(texto_url(banco)), sintetico, tmp_path)

    repos = criar_repositorios_postgres(settings_postgres(banco), sintetico)
    ctx = TenantContext(tenant_id="emporio")
    filtro = FiltroProdutos(incluir_inativos=True)

    async def verificar() -> None:
        do_banco = await repos.catalogo.listar_produtos(ctx, filtro)
        assert do_banco == await CatalogoCsv(sintetico, tmp_path).listar_produtos(ctx, filtro)
        assert [p.id for p in do_banco] == [81, 82, 86]
        assert len(await repos.catalogo.listar_promocoes(ctx, 81)) == 1
        assert [c.id for c in await repos.catalogo.listar_categorias(ctx)] == [1]

    try:
        asyncio.run(verificar())
    finally:
        repos.fechar()

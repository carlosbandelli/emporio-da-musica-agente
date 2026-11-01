"""Casos de uso sobre os CSVs reais de `data/` (adapters reais, relógio fixo)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporio.adaptadores.csv import CatalogoCsv, ClientesCsv, PedidosCsv, SolicitacoesJsonl
from emporio.modulos.atendimento.aplicacao import FerramentasLoja, ServicoAtendimento
from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from emporio.modulos.comercial.aplicacao import PromocaoEntrada, ServicoComercial, precificar
from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from emporio.portas import FiltroProdutos
from tests.modulos.fakes import CTX, RAIZ, RelogioFixo, resolver_config


@pytest.fixture
def lojas(tmp_path):  # type: ignore[no-untyped-def]
    tenants = {"emporio": resolver_config("emporio")}
    catalogo = CatalogoCsv(tenants, RAIZ)
    relogio = RelogioFixo(datetime(2026, 3, 10, 15, tzinfo=UTC))
    politicas = ServicoPoliticas(relogio, resolver_config)
    return FerramentasLoja(
        catalogo=ServicoCatalogo(catalogo, resolver_config),
        comercial=ServicoComercial(catalogo, politicas),
        pedidos=ServicoPedidos(PedidosCsv(tenants, RAIZ), ClientesCsv(tenants, RAIZ), catalogo, relogio, politicas),
        politicas=politicas,
        atendimento=ServicoAtendimento(SolicitacoesJsonl(tmp_path), relogio),
    )


async def test_violoes_ate_mil_reais_no_catalogo_real(lojas: FerramentasLoja) -> None:
    r = await lojas._catalogo.buscar_produtos(CTX, categoria="Violões", preco_max_reais=Decimal(1000))
    assert r.produtos
    assert all(p.categoria == "Violões" for p in r.produtos)
    assert all(p.preco_vigente.centavos <= 100_000 for p in r.produtos)
    repo = CatalogoCsv({"emporio": resolver_config("emporio")}, RAIZ)
    esperado = set()
    for p in await repo.listar_produtos(CTX, FiltroProdutos(categoria_id=5)):
        promos = [PromocaoEntrada(x.desconto_percentual, x.ativa) for x in await repo.listar_promocoes(CTX, p.id)]
        if precificar(p.preco_centavos, promos, CTX.idioma).preco_vigente.centavos <= 100_000:
            esperado.add(p.id)
    assert r.total_encontrado == len(esperado)
    assert {p.id for p in r.produtos} <= esperado


async def test_takamine_gd20_no_catalogo_real(lojas: FerramentasLoja) -> None:
    d = await lojas._catalogo.detalhar_produto(CTX, nome="Takamine GD20")
    assert d.encontrado and d.id == 95 and "GD20" in d.nome
    assert d.preco_tabela is not None and d.preco_tabela.texto == "R$ 2.199,00"


async def test_acessorio_no_catalogo_real(lojas: FerramentasLoja) -> None:
    for consulta in ("cordas para violão", "guitar strings"):
        assert (await lojas._catalogo.buscar_produtos(CTX, texto=consulta)).fora_de_escopo


async def test_instrumento_com_caracteristica_de_acessorio_nao_e_fora_de_escopo(lojas: FerramentasLoja) -> None:
    """'violão com cordas de aço' pede um instrumento; o termo de acessório é só característica."""
    r = await lojas._catalogo.buscar_produtos(CTX, texto="violão com cordas de aço")
    assert not r.fora_de_escopo
    assert r.produtos and all(p.categoria == "Violões" for p in r.produtos)


async def test_pedido_entregue_real_com_janelas(lojas: FerramentasLoja) -> None:
    clientes = await ClientesCsv({"emporio": resolver_config("emporio")}, RAIZ).obter_cliente(CTX, 3)
    assert clientes is not None
    r = await lojas._pedidos.consultar_pedido(CTX, 1, clientes.email)
    assert r.encontrado and r.status == "delivered" and r.devolucao is not None
    assert {j.tipo: j.situacao for j in r.devolucao.janelas}["arrependimento"] == "fora"  # entregue em 2025-10
    errado = await lojas._pedidos.consultar_pedido(CTX, 1, "naoexiste@exemplo.com")
    assert not errado.encontrado and errado.status is None and errado.itens == ()

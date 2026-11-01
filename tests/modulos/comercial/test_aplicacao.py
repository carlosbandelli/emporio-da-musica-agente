"""Casos de uso comerciais: preço vigente (`precificar`) e `simular_pagamento`."""

import pytest

from emporio.modulos.comercial.aplicacao import ItemSimulacao, PromocaoEntrada, ServicoComercial, precificar
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from emporio.portas import Idioma
from tests.modulos.fakes import CTX, PRODUTOS, CatalogoFalso, RelogioFixo, local, produto, resolver_config


def servico() -> ServicoComercial:
    extras = tuple(produto(100 + n, f"Preço {v}", v) for n, v in enumerate((149, 150, 319, 320, 700, 1200)))
    politicas = ServicoPoliticas(RelogioFixo(local(2026, 3, 9)), resolver_config)
    return ServicoComercial(CatalogoFalso(PRODUTOS + extras), politicas)


def item(produto_id: int, quantidade: int = 1) -> ItemSimulacao:
    return ItemSimulacao(produto_id=produto_id, quantidade=quantidade)


def test_precificar_sem_promocao_pix_com_5_por_cento() -> None:
    p = precificar(9990, [], Idioma.PT_BR)
    assert p.preco_vigente.centavos == 9990
    assert p.preco_pix.centavos == 9491  # premissa #29
    assert p.pix_tem_desconto and not p.em_promocao and not p.promocao_encerrada
    assert p.preco_vigente.texto == "R$ 99,90"


def test_precificar_com_promocao_pix_nao_acumula() -> None:
    p = precificar(100_000, [PromocaoEntrada(percentual=20, ativa=True, descricao="BF")], Idioma.PT_BR)
    assert p.preco_tabela.centavos == 100_000
    assert p.preco_vigente.centavos == 80_000
    assert p.desconto_percentual == 20
    assert p.preco_pix.centavos == 80_000 and not p.pix_tem_desconto


def test_precificar_promocao_encerrada_usa_preco_de_tabela() -> None:
    p = precificar(70_000, [PromocaoEntrada(percentual=15, ativa=False)], Idioma.PT_BR)
    assert p.promocao_encerrada and not p.em_promocao
    assert p.preco_vigente.centavos == 70_000


def test_precificar_formata_en_us() -> None:
    assert precificar(129_900, [], Idioma.EN_US).preco_tabela.texto == "R$1,299.00"


@pytest.mark.parametrize(
    ("produto_id", "maximo"),
    [(100, 2), (101, 3), (102, 3), (103, 4), (104, 7), (105, 12)],  # R$149, 150, 319, 320, 700, 1200
)
async def test_parcelas_max_por_faixa(produto_id: int, maximo: int) -> None:
    r = await servico().simular_pagamento(CTX, [item(produto_id)])
    assert r.ok and r.parcelas_max == maximo


async def test_pix_sem_promocao_e_com_promocao() -> None:
    sem = await servico().simular_pagamento(CTX, [item(1)], forma="pix")
    assert sem.total_vigente is not None and sem.total_vigente.centavos == 50_000
    assert sem.total_pix is not None and sem.total_pix.centavos == 47_500
    assert sem.simulacao is not None and sem.simulacao.total.centavos == 47_500
    com = await servico().simular_pagamento(CTX, [item(8)], forma="pix")
    assert com.total_vigente is not None and com.total_vigente.centavos == 88_000
    assert com.total_pix is not None and com.total_pix.centavos == 88_000  # sem 5% extra


async def test_pix_por_item_mistura_com_e_sem_promocao() -> None:
    r = await servico().simular_pagamento(CTX, [item(1, 2), item(8)])
    assert r.total_vigente is not None and r.total_vigente.centavos == 100_000 + 88_000
    assert r.total_pix is not None and r.total_pix.centavos == 95_000 + 88_000


async def test_credito_parcelado_usa_valor_vigente_e_sobra_na_primeira() -> None:
    r = await servico().simular_pagamento(CTX, [item(8)], forma="credito", parcelas=3)
    assert r.simulacao is not None
    assert r.simulacao.parcelas == 3
    assert r.simulacao.valor_primeira_parcela is not None
    assert r.simulacao.valor_primeira_parcela.centavos == 29_334
    assert r.simulacao.valor_demais_parcelas is not None
    assert r.simulacao.valor_demais_parcelas.centavos == 29_333


async def test_parcelas_invalidas_viram_resultado_de_erro() -> None:
    r = await servico().simular_pagamento(CTX, [item(101)], forma="credito", parcelas=6)
    assert not r.ok and r.erro
    assert r.parcelas_max == 3
    fora = await servico().simular_pagamento(CTX, [item(1)], parcelas=13)
    assert not fora.ok and fora.erro


async def test_parcelas_com_forma_nao_credito_e_erro() -> None:
    r = await servico().simular_pagamento(CTX, [item(1)], forma="pix", parcelas=3)
    assert not r.ok and r.erro


async def test_pagamento_combinado_so_acima_de_2000() -> None:
    abaixo = await servico().simular_pagamento(CTX, [item(105)])
    acima = await servico().simular_pagamento(CTX, [item(105, 2)])
    assert abaixo.pagamento_combinado_permitido is False
    assert acima.pagamento_combinado_permitido is True


async def test_frete_so_com_cidade_e_usa_total_vigente() -> None:
    sem = await servico().simular_pagamento(CTX, [item(1)])
    assert sem.frete is None
    cg = await servico().simular_pagamento(CTX, [item(1)], cidade="Campo Grande")
    assert cg.frete is not None and cg.frete.gratis  # exatamente R$ 500 (premissa #12)
    fora = await servico().simular_pagamento(CTX, [item(1)], cidade="Dourados")
    assert fora.frete is not None and fora.frete.requer_cotacao


async def test_produto_inexistente_indisponivel_e_estoque_insuficiente() -> None:
    nao = await servico().simular_pagamento(CTX, [item(9999)])
    assert not nao.ok and nao.erro
    desc = await servico().simular_pagamento(CTX, [item(5)])
    assert not desc.ok and desc.erro
    pouco = await servico().simular_pagamento(CTX, [item(1, 50)])
    assert pouco.ok and pouco.itens[0].estoque_suficiente is False and pouco.avisos


async def test_itens_repetidos_somam_quantidade_e_lista_vazia_e_erro() -> None:
    r = await servico().simular_pagamento(CTX, [item(1), item(1, 2)])
    assert len(r.itens) == 1 and r.itens[0].quantidade == 3
    vazio = await servico().simular_pagamento(CTX, [])
    assert not vazio.ok


async def test_forma_de_pagamento_desconhecida_e_erro() -> None:
    r = await servico().simular_pagamento(CTX, [item(1)], forma="cheque")
    assert not r.ok and r.erro and "pix" in r.erro

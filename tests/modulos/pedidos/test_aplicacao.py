"""Casos de uso de pedidos: verificação de identidade (premissa #15) e janelas de devolução."""

from datetime import datetime

import pytest

from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from tests.modulos.fakes import CTX, CatalogoFalso, ClientesFalso, PedidosFalso, RelogioFixo, local, resolver_config


def servico(instante: datetime | None = None) -> ServicoPedidos:
    relogio = RelogioFixo(instante or local(2026, 3, 10))
    return ServicoPedidos(
        PedidosFalso(), ClientesFalso(), CatalogoFalso(), relogio, ServicoPoliticas(relogio, resolver_config)
    )


@pytest.mark.parametrize("prova", ["maria.souza@exemplo.com", " MARIA.SOUZA@EXEMPLO.COM ", "(67) 99999-1234"])
async def test_identidade_por_email_ou_telefone(prova: str) -> None:
    r = await servico().consultar_pedido(CTX, 100, prova)
    assert r.encontrado and r.status == "delivered"


@pytest.mark.parametrize("telefone", ["67999991234", "5567999991234", "+55 (67) 99999-1234", "99999-1234", "999991234"])
async def test_telefone_com_ou_sem_ddd_e_pais(telefone: str) -> None:
    assert (await servico().consultar_pedido(CTX, 100, telefone)).encontrado


@pytest.mark.parametrize("prova", ["outra@exemplo.com", "(67) 99999-0000", "1234", "", "   ", "@", "maria.souza"])
async def test_prova_errada_nao_revela_nada_e_e_igual_a_pedido_inexistente(prova: str) -> None:
    errado = await servico().consultar_pedido(CTX, 100, prova)
    inexistente = await servico().consultar_pedido(CTX, 99999, "maria.souza@exemplo.com")
    assert errado == inexistente
    assert errado.encontrado is False
    assert errado.status is None and errado.itens == () and errado.cliente_primeiro_nome is None
    assert errado.total is None and errado.rastreio is None and errado.devolucao is None
    assert "Maria" not in errado.model_dump_json()


async def test_prova_de_outro_cliente_nao_serve() -> None:
    r = await servico().consultar_pedido(CTX, 100, "joao@exemplo.com")
    assert not r.encontrado and r.status is None


async def test_sucesso_traz_dados_sem_pii() -> None:
    r = await servico().consultar_pedido(CTX, 100, "maria.souza@exemplo.com")
    assert r.cliente_primeiro_nome == "Maria"
    assert [(i.nome, i.quantidade) for i in r.itens] == [("Violão Alfa", 2), ("Guitarra Eta", 1)]
    assert r.total is not None and r.total.centavos == 220_000 and r.total.texto == "R$ 2.200,00"
    assert r.pagamento == "credito" and r.parcelas == 6
    assert r.rastreio == "BR123" and str(r.previsao_entrega) == "2026-03-05"
    bruto = r.model_dump_json().lower()
    assert "exemplo.com" not in bruto and "99999" not in bruto and "souza" not in bruto


async def test_janela_de_devolucao_usa_entrega_como_recebimento() -> None:
    dentro = await servico(local(2026, 3, 12)).consultar_pedido(CTX, 100, "maria.souza@exemplo.com")
    assert dentro.devolucao is not None and dentro.recebimento_estimado is True
    j = {x.tipo: x for x in dentro.devolucao.janelas}
    assert j["arrependimento"].situacao == "dentro" and j["arrependimento"].dias_restantes == 0
    fora = await servico(local(2026, 3, 13)).consultar_pedido(CTX, 100, "maria.souza@exemplo.com")
    assert fora.devolucao is not None
    assert {x.tipo: x.situacao for x in fora.devolucao.janelas}["arrependimento"] == "fora"


async def test_pedido_pendente_nao_tem_janelas_do_recebimento_iniciadas() -> None:
    r = await servico().consultar_pedido(CTX, 101, "joao@exemplo.com")
    assert r.status == "pending" and r.rastreio is None
    assert r.devolucao is not None
    sit = {x.tipo: x.situacao for x in r.devolucao.janelas}
    assert sit["arrependimento"] == "nao_iniciada" and sit["defeito"] == "dentro"


async def test_pedido_cancelado_traz_motivo_e_janelas_nao_aplicaveis() -> None:
    r = await servico().consultar_pedido(CTX, 102, "(67) 3333-4444")
    assert r.status == "cancelled" and r.observacoes == "Cancelado a pedido do cliente"
    assert r.devolucao is not None and {x.situacao for x in r.devolucao.janelas} == {"nao_aplicavel"}

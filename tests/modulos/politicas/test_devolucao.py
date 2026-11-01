"""Testes de avaliar_janelas (premissas #5, #6 e #7)."""

from datetime import date, timedelta

import pytest

from emporio.modulos.politicas.dominio import (
    ErroPoliticas,
    JanelaDevolucao,
    SituacaoJanela,
    StatusPedido,
    TipoJanela,
    avaliar_janelas,
    janela_de,
)

COMPRA = date(2026, 1, 5)
RECEBIMENTO = date(2026, 1, 10)


def avaliar(
    hoje: date, status: str = "delivered", recebimento: date | None = RECEBIMENTO
) -> tuple[JanelaDevolucao, ...]:
    return avaliar_janelas(data_compra=COMPRA, status_pedido=status, data_recebimento=recebimento, hoje=hoje)


def test_retorna_as_quatro_janelas_em_ordem() -> None:
    assert [j.tipo for j in avaliar(RECEBIMENTO)] == [
        TipoJanela.ARREPENDIMENTO,
        TipoJanela.TROCA_PREFERENCIA,
        TipoJanela.DEFEITO,
        TipoJanela.GARANTIA_LEGAL,
    ]


def test_dia_zero_do_recebimento_esta_dentro_com_7_dias_restantes() -> None:
    janela = janela_de(avaliar(RECEBIMENTO), TipoJanela.ARREPENDIMENTO)
    assert janela.situacao is SituacaoJanela.DENTRO
    assert janela.prazo_final == RECEBIMENTO + timedelta(days=7)
    assert janela.dias_restantes == 7


def test_arrependimento_dia_7_dentro_dia_8_fora() -> None:
    dia7 = janela_de(avaliar(RECEBIMENTO + timedelta(days=7)), TipoJanela.ARREPENDIMENTO)
    dia8 = janela_de(avaliar(RECEBIMENTO + timedelta(days=8)), TipoJanela.ARREPENDIMENTO)
    assert dia7.situacao is SituacaoJanela.DENTRO
    assert dia7.dias_restantes == 0
    assert dia8.situacao is SituacaoJanela.FORA
    assert dia8.dias_restantes is None
    assert dia8.prazo_final == date(2026, 1, 17)


def test_troca_por_preferencia_dia_7_dentro_dia_8_fora() -> None:
    dia7 = janela_de(avaliar(RECEBIMENTO + timedelta(days=7)), TipoJanela.TROCA_PREFERENCIA)
    dia8 = janela_de(avaliar(RECEBIMENTO + timedelta(days=8)), TipoJanela.TROCA_PREFERENCIA)
    assert (dia7.situacao, dia8.situacao) == (SituacaoJanela.DENTRO, SituacaoJanela.FORA)


def test_defeito_conta_a_partir_da_compra() -> None:
    dia30 = janela_de(avaliar(COMPRA + timedelta(days=30)), TipoJanela.DEFEITO)
    dia31 = janela_de(avaliar(COMPRA + timedelta(days=31)), TipoJanela.DEFEITO)
    assert dia30.situacao is SituacaoJanela.DENTRO
    assert dia30.prazo_final == date(2026, 2, 4)
    assert dia31.situacao is SituacaoJanela.FORA


def test_defeito_nao_usa_a_data_de_recebimento() -> None:
    # 32 dias após a compra, mas só 27 após o recebimento: defeito já expirou.
    hoje = COMPRA + timedelta(days=32)
    assert janela_de(avaliar(hoje), TipoJanela.DEFEITO).situacao is SituacaoJanela.FORA
    assert janela_de(avaliar(hoje), TipoJanela.GARANTIA_LEGAL).situacao is SituacaoJanela.DENTRO


def test_garantia_legal_90_dias_do_recebimento() -> None:
    dia90 = janela_de(avaliar(RECEBIMENTO + timedelta(days=90)), TipoJanela.GARANTIA_LEGAL)
    dia91 = janela_de(avaliar(RECEBIMENTO + timedelta(days=91)), TipoJanela.GARANTIA_LEGAL)
    assert dia90.situacao is SituacaoJanela.DENTRO
    assert dia90.prazo_final == date(2026, 4, 10)
    assert dia91.situacao is SituacaoJanela.FORA


@pytest.mark.parametrize("status", ["pending", "confirmed", "shipped"])
def test_pedido_nao_entregue_deixa_janelas_do_recebimento_nao_iniciadas(status: str) -> None:
    janelas = avaliar(COMPRA + timedelta(days=2), status=status, recebimento=None)
    for tipo in (TipoJanela.ARREPENDIMENTO, TipoJanela.TROCA_PREFERENCIA, TipoJanela.GARANTIA_LEGAL):
        janela = janela_de(janelas, tipo)
        assert janela.situacao is SituacaoJanela.NAO_INICIADA
        assert janela.prazo_final is None
        assert janela.dias_restantes is None


def test_pedido_nao_entregue_ignora_data_de_recebimento_estimada() -> None:
    janelas = avaliar(COMPRA + timedelta(days=2), status="shipped", recebimento=RECEBIMENTO)
    assert janela_de(janelas, TipoJanela.ARREPENDIMENTO).situacao is SituacaoJanela.NAO_INICIADA


def test_defeito_em_pedido_nao_entregue_segue_o_prazo_da_compra() -> None:
    janela = janela_de(avaliar(COMPRA + timedelta(days=2), status="pending", recebimento=None), TipoJanela.DEFEITO)
    assert janela.situacao is SituacaoJanela.DENTRO
    assert janela.prazo_final == date(2026, 2, 4)
    assert janela.dias_restantes == 28


def test_recebimento_estimado_no_futuro_deixa_janelas_nao_iniciadas() -> None:
    janelas = avaliar(RECEBIMENTO - timedelta(days=1))
    janela = janela_de(janelas, TipoJanela.ARREPENDIMENTO)
    assert janela.situacao is SituacaoJanela.NAO_INICIADA
    assert janela.prazo_final == date(2026, 1, 17)
    assert janela.dias_restantes is None


def test_pedido_cancelado_nao_se_aplica() -> None:
    for janela in avaliar(COMPRA + timedelta(days=1), status="cancelled", recebimento=None):
        assert janela.situacao is SituacaoJanela.NAO_APLICAVEL
        assert janela.prazo_final is None
        assert janela.dias_restantes is None


def test_aceita_enum_de_status() -> None:
    janelas = avaliar_janelas(
        data_compra=COMPRA, status_pedido=StatusPedido.DELIVERED, data_recebimento=RECEBIMENTO, hoje=RECEBIMENTO
    )
    assert janela_de(janelas, TipoJanela.ARREPENDIMENTO).situacao is SituacaoJanela.DENTRO


def test_descricao_em_portugues() -> None:
    janela = janela_de(avaliar(RECEBIMENTO), TipoJanela.ARREPENDIMENTO)
    assert "arrependimento" in janela.descricao.lower()
    assert "7 dias" in janela.descricao


def test_status_desconhecido_e_erro() -> None:
    with pytest.raises(ErroPoliticas, match="Status de pedido"):
        avaliar(RECEBIMENTO, status="em_transito")


def test_entregue_sem_data_de_recebimento_e_erro() -> None:
    with pytest.raises(ErroPoliticas, match="recebimento"):
        avaliar(RECEBIMENTO, recebimento=None)


def test_recebimento_antes_da_compra_e_erro() -> None:
    with pytest.raises(ErroPoliticas):
        avaliar(RECEBIMENTO, recebimento=COMPRA - timedelta(days=1))


def test_hoje_antes_da_compra_e_erro() -> None:
    with pytest.raises(ErroPoliticas):
        avaliar(COMPRA - timedelta(days=1))

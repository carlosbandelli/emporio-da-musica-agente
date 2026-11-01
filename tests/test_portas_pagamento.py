"""Máquina de estados e erros da porta `GatewayPagamento` (`docs/pagamento.md` §2 e §3)."""

import pytest

from emporio.portas.pagamento import (
    ESTADOS_TERMINAIS,
    TRANSICOES,
    AssinaturaInvalida,
    ErroGateway,
    ErroPermanente,
    ErroTransitorio,
    PagamentoNaoEncontrado,
    StatusPagamento,
    transicao_permitida,
)

S = StatusPagamento


def test_todo_status_tem_linha_na_tabela() -> None:
    assert set(TRANSICOES) == set(StatusPagamento)


def test_terminais() -> None:
    assert {S.RECUSADO, S.EXPIRADO, S.CANCELADO, S.ESTORNADO} == ESTADOS_TERMINAIS


@pytest.mark.parametrize(
    ("de", "para"),
    [
        (S.CRIADO, S.PENDENTE),
        (S.CRIADO, S.CANCELADO),
        (S.PENDENTE, S.APROVADO),
        (S.PENDENTE, S.AUTORIZADO),
        (S.PENDENTE, S.RECUSADO),
        (S.PENDENTE, S.EXPIRADO),
        (S.PENDENTE, S.CANCELADO),
        (S.AUTORIZADO, S.APROVADO),
        (S.AUTORIZADO, S.CANCELADO),
        (S.APROVADO, S.ESTORNADO_PARCIAL),
        (S.APROVADO, S.ESTORNADO),
        (S.APROVADO, S.CONTESTADO),
        (S.ESTORNADO_PARCIAL, S.ESTORNADO_PARCIAL),
        (S.ESTORNADO_PARCIAL, S.ESTORNADO),
        (S.CONTESTADO, S.APROVADO),
        (S.CONTESTADO, S.ESTORNADO),
    ],
)
def test_transicoes_do_documento(de: StatusPagamento, para: StatusPagamento) -> None:
    assert transicao_permitida(de, para)


def test_total_de_transicoes_e_exatamente_o_do_documento() -> None:
    assert sum(len(destinos) for destinos in TRANSICOES.values()) == 16


@pytest.mark.parametrize(
    ("de", "para"),
    [
        (S.APROVADO, S.PENDENTE),  # regressão
        (S.CRIADO, S.APROVADO),  # pula a emissão
        (S.PENDENTE, S.ESTORNADO),  # estorno sem aprovação
        (S.AUTORIZADO, S.ESTORNADO),
        (S.ESTORNADO, S.APROVADO),
        (S.APROVADO, S.CANCELADO),  # aprovado se estorna, não se cancela
    ],
)
def test_transicoes_proibidas(de: StatusPagamento, para: StatusPagamento) -> None:
    assert not transicao_permitida(de, para)


@pytest.mark.parametrize("erro", [ErroTransitorio, ErroPermanente, AssinaturaInvalida, PagamentoNaoEncontrado])
def test_erros_derivam_de_erro_gateway(erro: type[Exception]) -> None:
    assert issubclass(erro, ErroGateway)
    assert not issubclass(ErroTransitorio, ErroPermanente)

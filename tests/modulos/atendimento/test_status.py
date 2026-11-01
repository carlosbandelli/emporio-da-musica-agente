"""Ciclo de vida do status de uma solicitação (task 2.5)."""

from datetime import UTC, datetime

import pytest

from emporio.modulos.atendimento.dominio import (
    ErroTransicaoStatus,
    Solicitacao,
    StatusSolicitacao,
    TipoSolicitacao,
    origens_permitidas,
    transicao_permitida,
)

S = StatusSolicitacao
EM = datetime(2026, 3, 10, 12, tzinfo=UTC)

PERMITIDAS = {
    (S.ABERTA, S.EM_ATENDIMENTO),
    (S.ABERTA, S.RESOLVIDA),
    (S.EM_ATENDIMENTO, S.ABERTA),
    (S.EM_ATENDIMENTO, S.RESOLVIDA),
    (S.RESOLVIDA, S.EM_ATENDIMENTO),
}


@pytest.mark.parametrize("atual", list(S))
@pytest.mark.parametrize("novo", list(S))
def test_matriz_de_transicoes(atual: S, novo: S) -> None:
    assert transicao_permitida(atual, novo) is (atual == novo or (atual, novo) in PERMITIDAS)


def test_origens_permitidas() -> None:
    assert origens_permitidas(S.ABERTA) == {S.ABERTA, S.EM_ATENDIMENTO}
    assert origens_permitidas(S.RESOLVIDA) == {S.ABERTA, S.EM_ATENDIMENTO, S.RESOLVIDA}


def _s() -> Solicitacao:
    return Solicitacao("p1", TipoSolicitacao.RECLAMACAO, "d", "s", "k", EM)


def test_nova_solicitacao_comeca_aberta_sem_auditoria() -> None:
    s = _s()
    assert s.status is S.ABERTA and s.atualizada_em is None and s.atualizada_por is None


def test_com_status_grava_quem_e_quando() -> None:
    nova = _s().com_status(S.EM_ATENDIMENTO, por="ana", em=EM)
    assert (nova.status, nova.atualizada_por, nova.atualizada_em) == (S.EM_ATENDIMENTO, "ana", EM)
    assert nova.id == "p1" and nova.chave_idempotencia == "k"


def test_transicao_invalida_levanta() -> None:
    resolvida = _s().com_status(S.RESOLVIDA, por="ana", em=EM)
    with pytest.raises(ErroTransicaoStatus):
        resolvida.com_status(S.ABERTA, por="ana", em=EM)

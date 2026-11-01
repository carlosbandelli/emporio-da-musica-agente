"""Testes de calcular_frete (premissas #11 e #12)."""

import dataclasses

import pytest

from emporio.modulos.politicas.dominio import (
    ErroCidadeInvalida,
    ErroPoliticas,
    ModalidadeFrete,
    MotivoCotacao,
    calcular_frete,
    normalizar_cidade,
)


def test_campo_grande_abaixo_de_500_cobra_35() -> None:
    frete = calcular_frete("Campo Grande", 49999)
    assert frete.valor_centavos == 3500
    assert frete.gratis is False
    assert frete.requer_cotacao is False
    assert frete.motivo_cotacao is None
    assert frete.em_campo_grande is True
    assert (frete.prazo_min_dias_uteis, frete.prazo_max_dias_uteis) == (1, 3)


def test_campo_grande_exatamente_500_e_gratis() -> None:
    frete = calcular_frete("Campo Grande", 50000)
    assert frete.valor_centavos == 0
    assert frete.gratis is True
    assert frete.requer_cotacao is False


def test_campo_grande_opcao_unica_de_entrega_local() -> None:
    frete = calcular_frete("Campo Grande", 10000)
    assert [(o.modalidade, o.prazo_min_dias_uteis, o.prazo_max_dias_uteis) for o in frete.opcoes] == [
        (ModalidadeFrete.ENTREGA_LOCAL, 1, 3)
    ]


@pytest.mark.parametrize(
    "entrada",
    [
        "campo grande/ms",
        "CAMPO GRANDE - MS",
        "Campo  Grande",
        "  campo grande  ",
        "Campo Grande/MS",
        "campo grande - ms",
        "Campo Grande, MS",
        "CAMPO GRANDE MS",
        "Campo Grande / Mato Grosso do Sul",
        "Cámpo Grandé",
    ],
)
def test_variacoes_de_campo_grande_sao_normalizadas(entrada: str) -> None:
    assert normalizar_cidade(entrada) == "campo grande"
    assert calcular_frete(entrada, 50000).em_campo_grande is True


@pytest.mark.parametrize(("entrada", "esperada"), [("São Paulo", "sao paulo"), ("DOURADOS - MS", "dourados")])
def test_normalizar_cidade_outros_casos(entrada: str, esperada: str) -> None:
    assert normalizar_cidade(entrada) == esperada


def test_campo_grande_de_outro_estado_nao_e_a_loja() -> None:
    assert calcular_frete("Campo Grande/RJ", 90000).em_campo_grande is False


def test_outra_cidade_lista_modalidades_sem_valor_e_pede_cotacao() -> None:
    frete = calcular_frete("Dourados/MS", 120000)
    assert frete.em_campo_grande is False
    assert frete.valor_centavos is None
    assert frete.gratis is False
    assert frete.requer_cotacao is True
    assert frete.motivo_cotacao is MotivoCotacao.FORA_DE_CAMPO_GRANDE
    prazos = {o.modalidade: (o.prazo_min_dias_uteis, o.prazo_max_dias_uteis) for o in frete.opcoes}
    assert prazos == {
        ModalidadeFrete.PAC: (5, 12),
        ModalidadeFrete.SEDEX: (2, 5),
        ModalidadeFrete.JADLOG: (3, 8),
    }
    assert frete.prazo_min_dias_uteis == 2
    assert frete.prazo_max_dias_uteis == 12


def test_instrumento_grande_em_campo_grande_exige_cotacao_especial() -> None:
    frete = calcular_frete("Campo Grande", 900000, instrumento_grande=True)
    assert frete.requer_cotacao is True
    assert frete.motivo_cotacao is MotivoCotacao.INSTRUMENTO_GRANDE
    assert frete.valor_centavos is None
    assert frete.gratis is False
    assert "cotação especial" in frete.mensagem.lower()


def test_instrumento_grande_fora_de_campo_grande_mantem_modalidades() -> None:
    frete = calcular_frete("Sao Paulo", 900000, instrumento_grande=True)
    assert frete.motivo_cotacao is MotivoCotacao.INSTRUMENTO_GRANDE
    assert frete.requer_cotacao is True
    assert len(frete.opcoes) == 3


def test_mensagens_em_portugues() -> None:
    assert "R$ 35,00" in calcular_frete("Campo Grande", 100).mensagem
    assert "grátis" in calcular_frete("Campo Grande", 50000).mensagem.lower()
    assert "cotação" in calcular_frete("Corumbá", 100).mensagem.lower()


def test_resultado_e_imutavel() -> None:
    frete = calcular_frete("Campo Grande", 100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frete.valor_centavos = 0  # type: ignore[misc]


@pytest.mark.parametrize("cidade", ["", "   ", "/MS", "- MS"])
def test_cidade_vazia_e_invalida(cidade: str) -> None:
    with pytest.raises(ErroCidadeInvalida):
        calcular_frete(cidade, 1000)


def test_total_negativo_e_invalido() -> None:
    with pytest.raises(ErroPoliticas):
        calcular_frete("Campo Grande", -1)

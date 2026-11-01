from decimal import Decimal

import pytest

from emporio.modulos.comum import Dinheiro, ErroDinheiro


class TestConstrucao:
    def test_de_centavos(self) -> None:
        assert Dinheiro(12345).centavos == 12345
        assert Dinheiro.de_centavos(12345) == Dinheiro(12345)

    def test_zero(self) -> None:
        assert Dinheiro.zero() == Dinheiro(0)
        assert Dinheiro.zero().centavos == 0

    @pytest.mark.parametrize(
        ("entrada", "centavos"),
        [
            ("1299.00", 129900),
            ("1299", 129900),
            ("0.05", 5),
            (Decimal("19.9"), 1990),
            (Decimal("10.005"), 1001),  # ROUND_HALF_UP
            ("10.004", 1000),
            (7, 700),
        ],
    )
    def test_de_reais(self, entrada: str | Decimal | int, centavos: int) -> None:
        assert Dinheiro.de_reais(entrada).centavos == centavos

    @pytest.mark.parametrize("entrada", [1.5, True, None, "abc", "NaN", "Infinity", "-1.00"])
    def test_de_reais_rejeita_entrada_invalida(self, entrada: object) -> None:
        with pytest.raises(ErroDinheiro):
            Dinheiro.de_reais(entrada)  # type: ignore[arg-type]

    @pytest.mark.parametrize("centavos", [-1, 1.5, True, "10", None])
    def test_centavos_invalidos(self, centavos: object) -> None:
        with pytest.raises(ErroDinheiro):
            Dinheiro(centavos)  # type: ignore[arg-type]

    def test_imutavel(self) -> None:
        dinheiro = Dinheiro(100)
        with pytest.raises(AttributeError):
            dinheiro.centavos = 5  # type: ignore[misc]

    def test_hashable(self) -> None:
        assert len({Dinheiro(100), Dinheiro(100), Dinheiro(200)}) == 2

    def test_para_reais(self) -> None:
        assert Dinheiro(129900).para_reais() == Decimal("1299.00")
        assert Dinheiro(5).para_reais() == Decimal("0.05")


class TestAritmetica:
    def test_soma(self) -> None:
        assert Dinheiro(100) + Dinheiro(250) == Dinheiro(350)

    def test_subtracao(self) -> None:
        assert Dinheiro(350) - Dinheiro(250) == Dinheiro(100)
        assert Dinheiro(100) - Dinheiro(100) == Dinheiro.zero()

    def test_subtracao_negativa_rejeitada(self) -> None:
        with pytest.raises(ErroDinheiro, match="negativ"):
            Dinheiro(100) - Dinheiro(101)

    def test_multiplicacao_por_inteiro(self) -> None:
        assert Dinheiro(250) * 3 == Dinheiro(750)
        assert 3 * Dinheiro(250) == Dinheiro(750)
        assert Dinheiro(250) * 0 == Dinheiro.zero()

    @pytest.mark.parametrize("fator", [-1, 1.5, True, Decimal(2)])
    def test_multiplicacao_rejeita_fator_invalido(self, fator: object) -> None:
        with pytest.raises(ErroDinheiro):
            Dinheiro(250) * fator  # type: ignore[operator]

    def test_operacao_com_tipo_estranho_nao_suportada(self) -> None:
        with pytest.raises(TypeError):
            Dinheiro(100) + 5  # type: ignore[operator]
        with pytest.raises(TypeError):
            Dinheiro(100) - 5  # type: ignore[operator]

    @pytest.mark.parametrize(
        ("centavos", "percentual", "esperado"),
        [
            (10000, 5, 500),
            (10000, Decimal("12.5"), 1250),
            (999, 5, 50),  # 49,95 -> 50 (half up)
            (990, 5, 50),  # 49,5 -> 50
            (989, 5, 49),  # 49,45 -> 49
            (0, 10, 0),
            (10000, 0, 0),
            (10000, 100, 10000),
        ],
    )
    def test_aplicar_percentual(self, centavos: int, percentual: int | Decimal, esperado: int) -> None:
        assert Dinheiro(centavos).aplicar_percentual(percentual).centavos == esperado

    @pytest.mark.parametrize(
        ("centavos", "percentual", "esperado"),
        [
            (100000, 5, 95000),
            (9990, 5, 9491),  # 9490,5 -> 9491 (half up)
            (10000, 10, 9000),
            (10000, 0, 10000),
            (10000, 100, 0),
            (10001, Decimal("33.33"), 6668),  # 6667,6667 -> 6668
        ],
    )
    def test_aplicar_desconto(self, centavos: int, percentual: int | Decimal, esperado: int) -> None:
        assert Dinheiro(centavos).aplicar_desconto(percentual).centavos == esperado

    @pytest.mark.parametrize("percentual", [-1, 101, Decimal("100.01"), 5.0, True, "5"])
    def test_percentual_invalido(self, percentual: object) -> None:
        with pytest.raises(ErroDinheiro):
            Dinheiro(100).aplicar_percentual(percentual)  # type: ignore[arg-type]
        with pytest.raises(ErroDinheiro):
            Dinheiro(100).aplicar_desconto(percentual)  # type: ignore[arg-type]


class TestComparacao:
    def test_ordem(self) -> None:
        assert Dinheiro(100) < Dinheiro(101)
        assert Dinheiro(101) > Dinheiro(100)
        assert Dinheiro(100) <= Dinheiro(100)
        assert Dinheiro(100) >= Dinheiro(100)
        assert Dinheiro(100) != Dinheiro(101)

    def test_ordenacao(self) -> None:
        assert sorted([Dinheiro(3), Dinheiro(1), Dinheiro(2)]) == [Dinheiro(1), Dinheiro(2), Dinheiro(3)]

    def test_sem_igualdade_com_numeros(self) -> None:
        assert Dinheiro(100) != 100


class TestFormatacao:
    @pytest.mark.parametrize(
        ("centavos", "locale", "esperado"),
        [
            (129900, "pt-BR", "R$ 1.299,00"),
            (129900, "en-US", "R$1,299.00"),
            (129900, "pt_BR", "R$ 1.299,00"),
            (5, "pt-BR", "R$ 0,05"),
            (0, "pt-BR", "R$ 0,00"),
            (33334, "pt-BR", "R$ 333,34"),
            (123456789, "pt-BR", "R$ 1.234.567,89"),
            (123456789, "en-US", "R$1,234,567.89"),
        ],
    )
    def test_formatar(self, centavos: int, locale: str, esperado: str) -> None:
        assert Dinheiro(centavos).formatar(locale) == esperado

    def test_locale_padrao_pt_br(self) -> None:
        assert Dinheiro(129900).formatar() == "R$ 1.299,00"
        assert str(Dinheiro(129900)) == "R$ 1.299,00"

    def test_sem_espaco_inseparavel(self) -> None:
        assert chr(0xA0) not in Dinheiro(129900).formatar("pt-BR")

    def test_locale_invalido(self) -> None:
        with pytest.raises(ErroDinheiro, match="locale"):
            Dinheiro(100).formatar("xx-ZZ-invalido")

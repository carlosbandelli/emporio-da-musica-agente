import pytest

from emporio.modulos.comercial.dominio import ErroComercial, ErroParcelamento, Parcelamento
from emporio.modulos.comum import Dinheiro


def r(valor: str) -> Dinheiro:
    return Dinheiro.de_reais(valor)


class TestParcelasMax:
    @pytest.mark.parametrize(
        ("valor", "maximo"),
        [
            ("0.01", 1),
            ("49.99", 1),
            ("50.00", 1),  # 2x pediria 100
            ("99.99", 1),
            ("100.00", 2),
            ("149.99", 2),
            ("150.00", 3),  # limite da faixa 1-3x
            ("319.99", 3),  # 4x pede 320
            ("320.00", 4),  # limite exato de 4x
            ("399.99", 4),
            ("400.00", 5),
            ("479.99", 5),
            ("480.00", 6),  # limite da faixa 4-6x
            ("699.99", 6),  # 7x pede 700
            ("700.00", 7),  # limite exato de 7x
            ("799.99", 7),
            ("800.00", 8),
            ("1000.00", 10),
            ("1199.99", 11),
            ("1200.00", 12),  # limite de 12x
            ("1299.00", 12),
            ("50000.00", 12),  # nunca passa de 12x
        ],
    )
    def test_limites_de_cada_faixa(self, valor: str, maximo: int) -> None:
        assert Parcelamento.parcelas_max(r(valor)) == maximo

    def test_parcelas_validas_formam_prefixo(self) -> None:
        # Varre R$0,01..R$1.500,00 (passo de 13 centavos) e a vizinhança de cada limite n x mínimo(n):
        # o conjunto de parcelas válidas é sempre o prefixo 1..k.
        minimos = {n: Parcelamento.minimo_parcela(n).centavos for n in range(2, 13)}
        limites = [minimo * n + d for n, minimo in minimos.items() for d in (-1, 0, 1)]
        for centavos in [*range(1, 150_001, 13), *limites]:
            valor = Dinheiro(centavos)
            validas = [n for n in range(1, 13) if Parcelamento.parcela_permitida(valor, n)]
            assert validas == list(range(1, len(validas) + 1)), centavos
            assert validas[-1] == Parcelamento.parcelas_max(valor)

    def test_valor_zero_rejeitado(self) -> None:
        with pytest.raises(ErroParcelamento):
            Parcelamento.parcelas_max(Dinheiro.zero())


class TestMinimoPorParcela:
    @pytest.mark.parametrize(
        ("n", "minimo"),
        [(1, 50), (2, 50), (3, 50), (4, 80), (5, 80), (6, 80), (7, 100), (8, 100), (12, 100)],
    )
    def test_minimo(self, n: int, minimo: int) -> None:
        assert Parcelamento.minimo_parcela(n) == Dinheiro(minimo * 100)

    @pytest.mark.parametrize("n", [0, -1, 13, 1.5, True])
    def test_minimo_n_invalido(self, n: object) -> None:
        with pytest.raises(ErroParcelamento):
            Parcelamento.minimo_parcela(n)  # type: ignore[arg-type]


class TestSimular:
    def test_mil_reais_em_3x_sobra_na_primeira(self) -> None:
        parcelas = Parcelamento.simular(r("1000.00"), 3)
        assert parcelas == (r("333.34"), r("333.33"), r("333.33"))

    @pytest.mark.parametrize(
        ("valor", "n"),
        [("1000.00", 3), ("1299.00", 12), ("99.99", 1), ("0.01", 1), ("319.99", 3), ("1200.00", 12), ("700.01", 7)],
    )
    def test_soma_das_parcelas_e_o_total(self, valor: str, n: int) -> None:
        parcelas = Parcelamento.simular(r(valor), n)
        assert len(parcelas) == n
        assert sum((p.centavos for p in parcelas), 0) == r(valor).centavos
        # só a primeira pode diferir, e nunca por mais que n-1 centavos
        assert all(p == parcelas[-1] for p in parcelas[1:])
        assert 0 <= parcelas[0].centavos - parcelas[-1].centavos < n

    def test_divisao_exata(self) -> None:
        assert Parcelamento.simular(r("1200.00"), 12) == (r("100.00"),) * 12

    def test_a_vista_e_uma_parcela(self) -> None:
        assert Parcelamento.simular(r("30.00"), 1) == (r("30.00"),)

    def test_n_acima_do_permitido_tem_mensagem_clara(self) -> None:
        with pytest.raises(ErroParcelamento, match=r"R\$ 319,99 em 4x.*R\$ 80,00.*máximo 3x"):
            Parcelamento.simular(r("319.99"), 4)

    def test_abaixo_de_50_so_a_vista(self) -> None:
        with pytest.raises(ErroParcelamento, match="2x"):
            Parcelamento.simular(r("49.99"), 2)

    @pytest.mark.parametrize("n", [0, -3, 13, 2.0, True, "3"])
    def test_n_invalido(self, n: object) -> None:
        with pytest.raises(ErroParcelamento):
            Parcelamento.simular(r("1000.00"), n)  # type: ignore[arg-type]

    def test_valor_zero_rejeitado(self) -> None:
        with pytest.raises(ErroParcelamento):
            Parcelamento.simular(Dinheiro.zero(), 1)

    def test_erro_e_comercial(self) -> None:
        assert issubclass(ErroParcelamento, ErroComercial)

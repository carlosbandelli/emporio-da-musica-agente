from decimal import Decimal

import pytest

from emporio.modulos.comercial.dominio import ErroComercial, ErroPromocao, PrecoVigente, Promocao
from emporio.modulos.comum import Dinheiro


def promo(percentual: int | Decimal, *, ativa: bool = True, descricao: str = "") -> Promocao:
    return Promocao(percentual=percentual, ativa=ativa, descricao=descricao)


class TestPrecoVigente:
    def test_sem_promocao_nenhuma(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"))
        assert preco.preco_tabela == Dinheiro.de_reais("1000.00")
        assert preco.preco_vigente == Dinheiro.de_reais("1000.00")
        assert preco.percentual_aplicado is None
        assert preco.promocao_aplicada is None
        assert not preco.em_promocao

    def test_so_promocoes_inativas(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"), [promo(20, ativa=False), promo(30, ativa=False)])
        assert preco.preco_vigente == Dinheiro.de_reais("1000.00")
        assert not preco.em_promocao
        assert preco.percentual_aplicado is None

    def test_promocao_ativa(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"), [promo(15, descricao="Semana do Músico")])
        assert preco.em_promocao
        assert preco.percentual_aplicado == 15
        assert preco.preco_tabela == Dinheiro.de_reais("1000.00")
        assert preco.preco_vigente == Dinheiro.de_reais("850.00")
        assert preco.promocao_aplicada is not None
        assert preco.promocao_aplicada.descricao == "Semana do Músico"

    def test_varios_registros_so_ativos_valem(self) -> None:
        # Como os produtos 93/96/99: várias linhas, a maioria inativa.
        promocoes = [promo(50, ativa=False), promo(10), promo(40, ativa=False)]
        preco = PrecoVigente(Dinheiro.de_reais("500.00"), promocoes)
        assert preco.percentual_aplicado == 10
        assert preco.preco_vigente == Dinheiro.de_reais("450.00")

    def test_promocoes_nao_acumulam_vence_a_maior(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"), [promo(10), promo(25), promo(15)])
        assert preco.percentual_aplicado == 25
        assert preco.preco_vigente == Dinheiro.de_reais("750.00")  # não 1000 * 0,9 * 0,75 * 0,85

    def test_empate_fica_com_a_primeira(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("100.00"), [promo(10, descricao="A"), promo(10, descricao="B")])
        assert preco.promocao_aplicada is not None
        assert preco.promocao_aplicada.descricao == "A"

    def test_percentual_decimal(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("999.00"), [promo(Decimal("12.5"))])
        assert preco.preco_vigente == Dinheiro.de_reais("874.13")  # 874,125 -> half up

    def test_promocao_ativa_de_zero_por_cento_e_ignorada(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("100.00"), [promo(0)])
        assert not preco.em_promocao
        assert preco.percentual_aplicado is None
        assert preco.preco_pix == Dinheiro.de_reais("95.00")

    def test_promocao_de_100_por_cento_zera_o_preco(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("100.00"), [promo(100)])
        assert preco.preco_vigente == Dinheiro.zero()

    def test_aceita_qualquer_iteravel(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("100.00"), (p for p in [promo(10)]))
        assert preco.percentual_aplicado == 10
        assert preco.promocoes == (promo(10),)


class TestPix:
    def test_pix_sem_promocao_5_por_cento_sobre_tabela(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"))
        assert preco.preco_pix == Dinheiro.de_reais("950.00")
        assert preco.percentual_pix == 5
        assert preco.pix_tem_desconto

    def test_pix_com_promocao_nao_tem_desconto_extra(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"), [promo(20)])
        assert preco.preco_vigente == Dinheiro.de_reais("800.00")
        assert preco.preco_pix == Dinheiro.de_reais("800.00")  # nem 760,00
        assert preco.percentual_pix is None
        assert not preco.pix_tem_desconto

    def test_pix_com_promocao_inativa_ainda_tem_5_por_cento(self) -> None:
        preco = PrecoVigente(Dinheiro.de_reais("1000.00"), [promo(20, ativa=False)])
        assert preco.preco_pix == Dinheiro.de_reais("950.00")

    def test_pix_arredonda_half_up(self) -> None:
        # 99,90 * 0,95 = 94,905 -> 94,91
        assert PrecoVigente(Dinheiro.de_reais("99.90")).preco_pix == Dinheiro.de_reais("94.91")


class TestValidacao:
    @pytest.mark.parametrize("percentual", [-1, 101, 5.0, True])
    def test_percentual_invalido(self, percentual: object) -> None:
        with pytest.raises(ErroPromocao):
            Promocao(percentual=percentual, ativa=True)  # type: ignore[arg-type]

    def test_erro_promocao_e_erro_comercial(self) -> None:
        assert issubclass(ErroPromocao, ErroComercial)

    def test_promocao_imutavel(self) -> None:
        p = promo(10)
        with pytest.raises(AttributeError):
            p.ativa = False  # type: ignore[misc]

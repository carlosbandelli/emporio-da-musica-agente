import pytest

from emporio.modulos.comercial.dominio import (
    ErroComercial,
    ErroPlanoPagamento,
    FormaPagamento,
    ParteDePagamento,
    PlanoPagamento,
)
from emporio.modulos.comum import Dinheiro


def r(valor: str) -> Dinheiro:
    return Dinheiro.de_reais(valor)


def parte(forma: FormaPagamento, valor: str, parcelas: int | None = None) -> ParteDePagamento:
    return ParteDePagamento(forma=forma, valor=r(valor), parcelas=parcelas)


PIX, CREDITO, DEBITO, BOLETO = (
    FormaPagamento.PIX,
    FormaPagamento.CREDITO,
    FormaPagamento.DEBITO,
    FormaPagamento.BOLETO,
)


class TestPlanoUnico:
    @pytest.mark.parametrize("forma", [PIX, DEBITO, BOLETO])
    def test_a_vista_sem_minimo(self, forma: FormaPagamento) -> None:
        plano = PlanoPagamento.unico(forma, r("0.01"))
        assert not plano.combinado
        assert plano.total == r("0.01")
        assert plano.partes[0].parcelas == 1

    def test_credito_12x(self) -> None:
        plano = PlanoPagamento.unico(CREDITO, r("1299.00"), parcelas=12)
        assert plano.partes[0].valores_parcelas[0] == r("108.25")
        assert sum(p.centavos for p in plano.partes[0].valores_parcelas) == 129900

    def test_credito_sem_parcelas_e_a_vista(self) -> None:
        assert PlanoPagamento.unico(CREDITO, r("30.00")).partes[0].parcelas == 1

    def test_valor_alto_unico_nao_precisa_ser_combinado(self) -> None:
        assert not PlanoPagamento.unico(PIX, r("9999.00")).combinado

    def test_credito_acima_do_permitido(self) -> None:
        with pytest.raises(ErroPlanoPagamento, match="parcela"):
            PlanoPagamento.unico(CREDITO, r("319.99"), parcelas=4)

    @pytest.mark.parametrize("forma", [PIX, DEBITO, BOLETO])
    def test_parcelar_fora_do_credito_recusado(self, forma: FormaPagamento) -> None:
        with pytest.raises(ErroPlanoPagamento, match="crédito"):
            PlanoPagamento.unico(forma, r("1000.00"), parcelas=2)

    def test_total_zero_recusado(self) -> None:
        with pytest.raises(ErroPlanoPagamento):
            PlanoPagamento.unico(PIX, Dinheiro.zero())


class TestCombinacao:
    def test_exatamente_2000_recusado(self) -> None:
        with pytest.raises(ErroPlanoPagamento, match=r"R\$ 2\.000,00"):
            PlanoPagamento.criar(r("2000.00"), [parte(PIX, "1000.00"), parte(CREDITO, "1000.00", 3)])

    def test_2000_01_aceito(self) -> None:
        plano = PlanoPagamento.criar(r("2000.01"), [parte(PIX, "1000.00"), parte(CREDITO, "1000.01", 3)])
        assert plano.combinado
        assert plano.total == r("2000.01")
        assert len(plano.partes) == 2

    def test_abaixo_de_2000_recusado(self) -> None:
        with pytest.raises(ErroPlanoPagamento):
            PlanoPagamento.criar(r("1500.00"), [parte(PIX, "500.00"), parte(DEBITO, "1000.00")])

    def test_soma_das_partes_deve_bater_exato(self) -> None:
        with pytest.raises(ErroPlanoPagamento, match="soma"):
            PlanoPagamento.criar(r("3000.00"), [parte(PIX, "1000.00"), parte(DEBITO, "1999.99")])
        with pytest.raises(ErroPlanoPagamento, match="soma"):
            PlanoPagamento.criar(r("3000.00"), [parte(PIX, "1000.00"), parte(DEBITO, "2000.01")])

    def test_cada_parte_de_credito_obedece_o_parcelamento_do_proprio_valor(self) -> None:
        # No total (R$ 3.000) caberiam 12x, mas a parte de crédito tem só R$ 300: 4x pede R$ 320.
        with pytest.raises(ErroPlanoPagamento, match="parcela"):
            PlanoPagamento.criar(r("3000.00"), [parte(PIX, "2700.00"), parte(CREDITO, "300.00", 4)])
        plano = PlanoPagamento.criar(r("3000.00"), [parte(PIX, "2700.00"), parte(CREDITO, "300.00", 3)])
        assert plano.partes[1].valores_parcelas == (r("100.00"),) * 3

    def test_parte_de_credito_na_borda_da_faixa(self) -> None:
        plano = PlanoPagamento.criar(r("2400.00"), [parte(PIX, "1920.00"), parte(CREDITO, "480.00", 6)])
        assert plano.partes[1].parcelas == 6

    def test_partes_com_valor_zero_recusadas(self) -> None:
        with pytest.raises(ErroPlanoPagamento):
            PlanoPagamento.criar(r("3000.00"), [parte(PIX, "3000.00"), parte(DEBITO, "0.00")])

    def test_combinacao_sem_desconto_pix(self) -> None:
        # O total é exatamente o informado: o plano não aplica 5% sobre a parte PIX.
        plano = PlanoPagamento.criar(r("3000.00"), [parte(PIX, "1000.00"), parte(DEBITO, "2000.00")])
        assert plano.partes[0].valor == r("1000.00")
        assert sum((p.valor for p in plano.partes), Dinheiro.zero()) == plano.total

    def test_tres_partes(self) -> None:
        plano = PlanoPagamento.criar(
            r("5000.00"), [parte(PIX, "1000.00"), parte(DEBITO, "1000.00"), parte(CREDITO, "3000.00", 12)]
        )
        assert len(plano.partes) == 3

    def test_duas_partes_com_a_mesma_forma_sao_permitidas(self) -> None:
        plano = PlanoPagamento.criar(r("3000.00"), [parte(CREDITO, "1500.00", 3), parte(CREDITO, "1500.00", 6)])
        assert plano.combinado

    def test_sem_partes_recusado(self) -> None:
        with pytest.raises(ErroPlanoPagamento):
            PlanoPagamento.criar(r("3000.00"), [])

    def test_uma_parte_so_com_valor_diferente_do_total(self) -> None:
        with pytest.raises(ErroPlanoPagamento, match="soma"):
            PlanoPagamento.criar(r("100.00"), [parte(PIX, "90.00")])

    def test_erro_e_comercial(self) -> None:
        assert issubclass(ErroPlanoPagamento, ErroComercial)


class TestParte:
    def test_credito_sem_parcelas_vira_1(self) -> None:
        assert parte(CREDITO, "100.00").parcelas == 1

    def test_forma_a_vista_tem_uma_parcela_do_valor_inteiro(self) -> None:
        assert parte(PIX, "250.00").valores_parcelas == (r("250.00"),)

    def test_forma_aceita_valor_textual(self) -> None:
        assert FormaPagamento("pix") is PIX
        assert FormaPagamento("credito") is CREDITO

    @pytest.mark.parametrize("parcelas", [0, -1, 13, True, 2.0])
    def test_parcelas_invalidas(self, parcelas: object) -> None:
        with pytest.raises(ErroPlanoPagamento):
            ParteDePagamento(forma=CREDITO, valor=r("1000.00"), parcelas=parcelas)  # type: ignore[arg-type]

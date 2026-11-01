"""Regras de domínio comerciais: preço vigente (promoção e PIX), parcelamento e plano de pagamento.

Código puro e determinístico: sem I/O, sem `float`. Valores em `Dinheiro` (centavos), arredondamento `ROUND_HALF_UP`.
Fonte das regras: manual da loja v2.1 e `docs/premissas.md` (#4 faixas de parcela, #17 sobra na 1ª parcela).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from emporio.modulos.comum import Dinheiro, ErroDinheiro, validar_percentual

PERCENTUAL_PIX: Final = 5
"""Desconto do PIX, só sobre o preço de tabela e só quando não há promoção ativa."""

MAX_PARCELAS: Final = 12
"""Parcelamento máximo, sempre sem juros."""

LIMITE_COMBINACAO: Final = Dinheiro(200_000)
"""Combinar formas de pagamento só é permitido em compras *acima* deste valor (R$ 2.000,00, exclusivo)."""

# (maior nº de parcelas da faixa, valor mínimo de cada parcela em centavos) — premissa #4.
_FAIXAS_PARCELA: Final = ((3, 5_000), (6, 8_000), (MAX_PARCELAS, 10_000))


# --- Erros --------------------------------------------------------------------
class ErroComercial(Exception):  # noqa: N818 - nome do domínio em pt-BR
    """Base dos erros de regra comercial. As mensagens são em pt-BR e seguras para mostrar ao cliente."""


class ErroPromocao(ErroComercial):
    """Promoção com dados inválidos (ex.: percentual fora de 0..100)."""


class ErroParcelamento(ErroComercial):
    """Parcelamento impossível pela regra da loja (nº de parcelas ou parcela mínima)."""


class ErroPlanoPagamento(ErroComercial):
    """Plano de pagamento inconsistente ou que viola a regra de combinação."""


# --- Preço vigente ------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Promocao:
    """Promoção como o módulo comercial a enxerga (sem depender dos tipos do catálogo)."""

    percentual: int | Decimal
    ativa: bool
    descricao: str = ""

    def __post_init__(self) -> None:
        try:
            validar_percentual(self.percentual)
        except ErroDinheiro as erro:
            raise ErroPromocao(f"Percentual de promoção inválido: {erro}") from erro


@dataclass(frozen=True, slots=True, init=False)
class PrecoVigente:
    """Preço de tabela de um produto + suas promoções: o que vale hoje e o preço no PIX.

    Regras: só promoções ativas (e com percentual > 0) contam; promoções não acumulam, vence a de maior
    percentual (empate: a primeira da lista); o PIX dá 5% sobre o preço de tabela apenas quando não há promoção.
    """

    preco_tabela: Dinheiro
    promocoes: tuple[Promocao, ...]

    def __init__(self, preco_tabela: Dinheiro, promocoes: Iterable[Promocao] = ()) -> None:
        object.__setattr__(self, "preco_tabela", preco_tabela)
        object.__setattr__(self, "promocoes", tuple(promocoes))

    @property
    def promocao_aplicada(self) -> Promocao | None:
        """A promoção que vale (maior percentual entre as ativas), ou `None`."""
        melhor: Promocao | None = None
        for promocao in self.promocoes:
            if not promocao.ativa or Decimal(promocao.percentual) <= 0:
                continue
            if melhor is None or Decimal(promocao.percentual) > Decimal(melhor.percentual):
                melhor = promocao
        return melhor

    @property
    def em_promocao(self) -> bool:
        return self.promocao_aplicada is not None

    @property
    def percentual_aplicado(self) -> int | Decimal | None:
        """Percentual de desconto da promoção aplicada, ou `None` sem promoção."""
        promocao = self.promocao_aplicada
        return None if promocao is None else promocao.percentual

    @property
    def preco_vigente(self) -> Dinheiro:
        """Preço de tabela, ou o preço promocional quando há promoção ativa."""
        percentual = self.percentual_aplicado
        return self.preco_tabela if percentual is None else self.preco_tabela.aplicar_desconto(percentual)

    @property
    def pix_tem_desconto(self) -> bool:
        return not self.em_promocao

    @property
    def percentual_pix(self) -> int | None:
        """5% quando o PIX tem desconto; `None` com promoção ativa (não acumula)."""
        return PERCENTUAL_PIX if self.pix_tem_desconto else None

    @property
    def preco_pix(self) -> Dinheiro:
        """Preço no PIX: 5% sobre a tabela sem promoção; igual ao preço promocional com promoção."""
        if self.pix_tem_desconto:
            return self.preco_tabela.aplicar_desconto(PERCENTUAL_PIX)
        return self.preco_vigente


# --- Parcelamento -------------------------------------------------------------
class Parcelamento:
    """Crédito em até 12x sem juros, com parcela mínima por faixa (1–3x R$50; 4–6x R$80; 7–12x R$100)."""

    @staticmethod
    def _validar_n(n: object) -> int:
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PARCELAS:
            raise ErroParcelamento(f"O número de parcelas deve ser um inteiro de 1 a {MAX_PARCELAS}, recebi {n!r}.")
        return n

    @staticmethod
    def _validar_valor(valor: Dinheiro) -> None:
        if valor.centavos <= 0:
            raise ErroParcelamento("Não há como parcelar um valor zerado.")

    @staticmethod
    def minimo_parcela(n: int) -> Dinheiro:
        """Valor mínimo de cada parcela para parcelar em `n` vezes."""
        n = Parcelamento._validar_n(n)
        for ate, minimo in _FAIXAS_PARCELA:
            if n <= ate:
                return Dinheiro(minimo)
        raise AssertionError("inalcançável: n já foi validado")  # pragma: no cover

    @staticmethod
    def parcela_permitida(valor: Dinheiro, n: int) -> bool:
        """`n` parcelas são permitidas se `valor >= n * mínimo(n)`. À vista (1x) é sempre permitido."""
        n = Parcelamento._validar_n(n)
        return n == 1 or valor >= Parcelamento.minimo_parcela(n) * n

    @staticmethod
    def parcelas_max(valor: Dinheiro) -> int:
        """Maior nº de parcelas permitido (as válidas formam sempre o prefixo 1..k)."""
        Parcelamento._validar_valor(valor)
        return max(n for n in range(1, MAX_PARCELAS + 1) if Parcelamento.parcela_permitida(valor, n))

    @staticmethod
    def simular(valor: Dinheiro, n: int) -> tuple[Dinheiro, ...]:
        """Valores das `n` parcelas; a sobra da divisão vai para a 1ª (R$ 1.000,00 em 3x = 333,34 + 333,33 + 333,33).

        Raises:
            ErroParcelamento: `n` fora de 1..12, valor zerado ou parcela abaixo do mínimo da faixa.
        """
        n = Parcelamento._validar_n(n)
        Parcelamento._validar_valor(valor)
        if not Parcelamento.parcela_permitida(valor, n):
            maximo = Parcelamento.parcelas_max(valor)
            minimo = Parcelamento.minimo_parcela(n)
            limite = "só à vista (1x)" if maximo == 1 else f"no máximo {maximo}x"
            raise ErroParcelamento(
                f"Parcelar {valor} em {n}x não é possível: cada parcela precisa ter no mínimo {minimo}. "
                f"Para esse valor, {limite}."
            )
        base, sobra = divmod(valor.centavos, n)
        return (Dinheiro(base + sobra), *(Dinheiro(base) for _ in range(n - 1)))


# --- Plano de pagamento -------------------------------------------------------
class FormaPagamento(StrEnum):
    PIX = "pix"
    CREDITO = "credito"
    DEBITO = "debito"
    BOLETO = "boleto"


@dataclass(frozen=True, slots=True)
class ParteDePagamento:
    """Uma fatia do pagamento: forma, valor e (só no crédito) nº de parcelas."""

    forma: FormaPagamento
    valor: Dinheiro
    parcelas: int | None = None

    def __post_init__(self) -> None:
        if self.valor.centavos <= 0:
            raise ErroPlanoPagamento("Cada parte do pagamento precisa ter valor maior que zero.")
        if self.forma is not FormaPagamento.CREDITO:
            if self.parcelas not in (None, 1):
                raise ErroPlanoPagamento("Só o cartão de crédito pode ser parcelado; as outras formas são à vista.")
            object.__setattr__(self, "parcelas", 1)
            return
        n = 1 if self.parcelas is None else self.parcelas
        try:
            Parcelamento.simular(self.valor, n)
        except ErroParcelamento as erro:
            raise ErroPlanoPagamento(f"Parte no crédito inválida: {erro}") from erro
        object.__setattr__(self, "parcelas", n)

    @property
    def valores_parcelas(self) -> tuple[Dinheiro, ...]:
        """Valores de cada parcela (uma só, o próprio valor, nas formas à vista)."""
        if self.forma is FormaPagamento.CREDITO:
            return Parcelamento.simular(self.valor, self.parcelas or 1)
        return (self.valor,)


@dataclass(frozen=True, slots=True)
class PlanoPagamento:
    """Como o total será pago. `total` é o valor já calculado pelo chamador (vigente ou PIX).

    Uma só forma não tem mínimo. Combinar formas (2+ partes) só é permitido acima de R$ 2.000,00 (exclusivo); as
    partes somam exatamente o total, cada crédito obedece ao `Parcelamento` do próprio valor, e um plano combinado
    não aplica desconto de PIX (o total é o preço vigente).
    """

    total: Dinheiro
    partes: tuple[ParteDePagamento, ...]

    def __post_init__(self) -> None:
        if not self.partes:
            raise ErroPlanoPagamento("O plano de pagamento precisa de ao menos uma parte.")
        soma = sum((parte.valor for parte in self.partes), Dinheiro.zero())
        if soma != self.total:
            raise ErroPlanoPagamento(f"A soma das partes ({soma}) difere do total ({self.total}).")
        if len(self.partes) > 1 and self.total <= LIMITE_COMBINACAO:
            raise ErroPlanoPagamento(
                f"Combinar formas de pagamento só é possível em compras acima de {LIMITE_COMBINACAO} "
                f"(o total é {self.total})."
            )

    @property
    def combinado(self) -> bool:
        return len(self.partes) > 1

    @classmethod
    def criar(cls, total: Dinheiro, partes: Sequence[ParteDePagamento]) -> PlanoPagamento:
        """Cria e valida um plano com uma ou mais partes."""
        return cls(total=total, partes=tuple(partes))

    @classmethod
    def unico(cls, forma: FormaPagamento, total: Dinheiro, parcelas: int | None = None) -> PlanoPagamento:
        """Plano de uma única forma de pagamento (sem valor mínimo)."""
        return cls.criar(total, [ParteDePagamento(forma=forma, valor=total, parcelas=parcelas)])

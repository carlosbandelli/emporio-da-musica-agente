"""`Dinheiro`: valor monetário em reais, guardado em centavos inteiros (nunca `float`)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from babel.core import UnknownLocaleError
from babel.numbers import format_currency

_CEM: Final = Decimal(100)
_UM_CENTAVO: Final = Decimal("0.01")
_MOEDA: Final = "BRL"
_LOCALE_PADRAO: Final = "pt-BR"
_ESPACOS_ESPECIAIS: Final = str.maketrans({chr(0xA0): " ", chr(0x202F): " "})


class ErroDinheiro(ValueError):  # noqa: N818 - nome do domínio em pt-BR
    """Valor monetário ou operação inválida (negativo, tipo errado, locale desconhecido)."""


def _e_inteiro(valor: object) -> bool:
    """`True` só para `int` de verdade (`bool` é subclasse de `int`, mas não é quantidade)."""
    return isinstance(valor, int) and not isinstance(valor, bool)


def validar_percentual(percentual: object) -> Decimal:
    """Valida um percentual entre 0 e 100 (`int` ou `Decimal`) e o devolve como `Decimal`.

    Raises:
        ErroDinheiro: se for `float`, `bool`, texto ou estiver fora de 0..100.
    """
    if isinstance(percentual, bool) or not isinstance(percentual, int | Decimal):
        raise ErroDinheiro(f"Percentual deve ser int ou Decimal, recebi {type(percentual).__name__}.")
    valor = Decimal(percentual)
    if not valor.is_finite() or not Decimal(0) <= valor <= _CEM:
        raise ErroDinheiro(f"Percentual deve estar entre 0 e 100, recebi {percentual}.")
    return valor


@dataclass(frozen=True, order=True, slots=True)
class Dinheiro:
    """Valor em reais (BRL) como centavos inteiros, imutável e nunca negativo.

    Arredondamentos usam sempre `ROUND_HALF_UP`.
    """

    centavos: int

    def __post_init__(self) -> None:
        if not _e_inteiro(self.centavos):
            raise ErroDinheiro(f"Centavos deve ser int, recebi {type(self.centavos).__name__}.")
        if self.centavos < 0:
            raise ErroDinheiro(f"Valor monetário não pode ser negativo ({self.centavos} centavos).")

    # --- construtores ------------------------------------------------------
    @classmethod
    def de_centavos(cls, centavos: int) -> Dinheiro:
        """Cria a partir de centavos inteiros."""
        return cls(centavos)

    @classmethod
    def zero(cls) -> Dinheiro:
        """R$ 0,00."""
        return cls(0)

    @classmethod
    def de_reais(cls, reais: Decimal | str | int) -> Dinheiro:
        """Cria a partir de reais (`Decimal`, texto como `"1299.00"` ou `int`), arredondando em `HALF_UP`.

        Raises:
            ErroDinheiro: para `float`, `bool`, texto inválido ou valor negativo.
        """
        if isinstance(reais, bool) or not isinstance(reais, Decimal | str | int):
            raise ErroDinheiro(f"Reais deve ser Decimal, str ou int, recebi {type(reais).__name__}.")
        try:
            decimal = Decimal(reais)
        except InvalidOperation as erro:
            raise ErroDinheiro(f"Valor em reais inválido: {reais!r}.") from erro
        if not decimal.is_finite():
            raise ErroDinheiro(f"Valor em reais inválido: {reais!r}.")
        return cls(int((decimal * _CEM).to_integral_value(rounding=ROUND_HALF_UP)))

    # --- conversão e formatação -------------------------------------------
    def para_reais(self) -> Decimal:
        """Valor em reais como `Decimal` com 2 casas."""
        return (Decimal(self.centavos) / _CEM).quantize(_UM_CENTAVO)

    def formatar(self, locale: str = _LOCALE_PADRAO) -> str:
        """Formata como moeda BRL no locale pedido: `pt-BR` -> `R$ 1.299,00`, `en-US` -> `R$1,299.00`.

        Espaços inseparáveis do babel viram espaços comuns (melhor para chat e terminal).

        Raises:
            ErroDinheiro: se o locale for desconhecido.
        """
        try:
            texto = format_currency(self.para_reais(), _MOEDA, locale=locale.replace("-", "_"))
        except (UnknownLocaleError, ValueError) as erro:
            raise ErroDinheiro(f"locale desconhecido: {locale!r}.") from erro
        return texto.translate(_ESPACOS_ESPECIAIS)

    def __str__(self) -> str:
        return self.formatar()

    # --- aritmética --------------------------------------------------------
    def __add__(self, outro: object) -> Dinheiro:
        if not isinstance(outro, Dinheiro):
            return NotImplemented
        return Dinheiro(self.centavos + outro.centavos)

    def __sub__(self, outro: object) -> Dinheiro:
        if not isinstance(outro, Dinheiro):
            return NotImplemented
        if outro.centavos > self.centavos:
            raise ErroDinheiro(f"Subtração resultaria em valor negativo ({self} - {outro}).")
        return Dinheiro(self.centavos - outro.centavos)

    def __mul__(self, fator: object) -> Dinheiro:
        if isinstance(fator, bool) or not isinstance(fator, int) or fator < 0:
            raise ErroDinheiro(f"Só é possível multiplicar por int não negativo, recebi {fator!r}.")
        return Dinheiro(self.centavos * fator)

    __rmul__ = __mul__

    def aplicar_percentual(self, percentual: int | Decimal) -> Dinheiro:
        """`percentual`% deste valor (ex.: o valor do desconto), arredondado em `HALF_UP`."""
        fracao = validar_percentual(percentual) / _CEM
        return Dinheiro(int((Decimal(self.centavos) * fracao).to_integral_value(rounding=ROUND_HALF_UP)))

    def aplicar_desconto(self, percentual: int | Decimal) -> Dinheiro:
        """Valor final após `percentual`% de desconto, arredondado uma única vez em `HALF_UP`."""
        fracao = (_CEM - validar_percentual(percentual)) / _CEM
        return Dinheiro(int((Decimal(self.centavos) * fracao).to_integral_value(rounding=ROUND_HALF_UP)))

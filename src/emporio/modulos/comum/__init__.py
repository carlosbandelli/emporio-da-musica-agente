"""Shared kernel: tipos simples (ex.: Dinheiro) usados por todos os módulos."""

from emporio.modulos.comum.dinheiro import Dinheiro, ErroDinheiro, validar_percentual
from emporio.modulos.comum.montante import Montante, montante

__all__ = ["Dinheiro", "ErroDinheiro", "Montante", "montante", "validar_percentual"]

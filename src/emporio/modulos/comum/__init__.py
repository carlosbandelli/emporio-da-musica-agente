"""Shared kernel: tipos simples (ex.: Dinheiro) usados por todos os módulos."""

from emporio.modulos.comum.dinheiro import Dinheiro, ErroDinheiro, validar_percentual

__all__ = ["Dinheiro", "ErroDinheiro", "validar_percentual"]

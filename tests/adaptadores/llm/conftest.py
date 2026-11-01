"""Utilitários dos testes de LLM: relógio monotônico falso e `sleep` que só avança esse relógio."""

import pytest

from emporio.portas import MensagemLLM, PapelMensagem, RequisicaoLLM


class Tempo:
    """Fonte de tempo monotônica controlada pelo teste."""

    def __init__(self, inicio: float = 1000.0) -> None:
        self.t = inicio

    def __call__(self) -> float:
        return self.t


class SonoFalso:
    """`sleep` injetável: registra as esperas e avança o `Tempo`, sem dormir."""

    def __init__(self, tempo: Tempo) -> None:
        self.tempo = tempo
        self.esperas: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.tempo.t += segundos


@pytest.fixture
def tempo() -> Tempo:
    return Tempo()


@pytest.fixture
def sono(tempo: Tempo) -> SonoFalso:
    return SonoFalso(tempo)


def requisicao(*mensagens: MensagemLLM, **extras: object) -> RequisicaoLLM:
    """Requisição mínima; sem mensagens, usa um `user` "oi"."""
    msgs = mensagens or (MensagemLLM(PapelMensagem.USER, "oi"),)
    return RequisicaoLLM("manual da loja", "data: 2026-03-10", msgs, **extras)  # type: ignore[arg-type]

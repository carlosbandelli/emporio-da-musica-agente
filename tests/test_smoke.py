"""Teste de fumaça: pacote importável e CLI responde a --help."""

from typer.testing import CliRunner

import emporio
from emporio.adaptadores.cli import app


def test_versao_do_pacote() -> None:
    assert emporio.__version__ == "0.1.0"


def test_cli_help_menciona_o_projeto() -> None:
    resultado = CliRunner().invoke(app, ["--help"])
    assert resultado.exit_code == 0
    assert "Empório da Música" in resultado.output

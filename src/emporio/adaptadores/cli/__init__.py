"""Adaptador de entrada CLI (Typer). Só conhece `emporio.portas` e `emporio.composicao` (regra C4)."""

import typer

import emporio

app = typer.Typer(
    name="emporio",
    help="Empório da Música: agente de atendimento por texto (Sônia).",
    no_args_is_help=True,
)


@app.callback()
def principal() -> None:
    """Empório da Música: agente de atendimento por texto (Sônia)."""


@app.command()
def versao() -> None:
    """Mostra a versão instalada."""
    typer.echo(emporio.__version__)


__all__ = ["app"]

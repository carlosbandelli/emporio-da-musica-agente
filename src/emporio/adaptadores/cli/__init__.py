"""Adaptador de entrada CLI (Typer). Só conhece `emporio.portas` e `emporio.composicao` (regra C4).

A CLI não monta nada sozinha: recebe as fábricas por `criar_app(...)` (padrão: composição real). Os testes passam
fábricas que montam a aplicação com `LLMFalso`, sem tocar `adaptadores.llm`. Não há estado global mutável.

Códigos de saída: 0 ok; 2 erro de configuração (sem chave, tenant desconhecido, motor inválido, YAML inválido).
"""

import asyncio
import contextlib
import sys
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

import emporio
from emporio.adaptadores.cli._gravacao import GravadorMarkdown
from emporio.composicao import (
    Aplicacao,
    DiagnosticoDetalhado,
    DiagnosticoLocal,
    ErroConfiguracao,
    carregar_settings,
    montar,
    montar_diagnostico,
)
from emporio.portas import DiagnosticoInfo, Idioma, MensagemCliente, Papel, TenantContext

SAIDA_CONFIGURACAO = 2
COMANDOS_SAIDA = frozenset({"sair", "exit", "quit"})

FabricaAplicacao = Callable[[], Aplicacao]
FabricaDiagnostico = Callable[[], DiagnosticoLocal]


def _montar_padrao() -> Aplicacao:
    return montar(carregar_settings())


def _diagnostico_padrao() -> DiagnosticoLocal:
    return montar_diagnostico(carregar_settings())


def _utf8() -> None:
    """Console do Windows costuma ser cp1252: força UTF-8 para os acentos da Sônia."""
    for fluxo in (sys.stdout, sys.stderr, sys.stdin):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigurar(encoding="utf-8")


def _falhar(mensagem: str) -> typer.Exit:
    typer.echo(mensagem, err=True)
    return typer.Exit(SAIDA_CONFIGURACAO)


def criar_app(
    montar_aplicacao: FabricaAplicacao = _montar_padrao,
    montar_diag: FabricaDiagnostico = _diagnostico_padrao,
) -> typer.Typer:
    """Cria a aplicação Typer com as fábricas injetadas (testes trocam; produção usa a composição real)."""
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

    @app.command()
    def chat(
        tenant: Annotated[str | None, typer.Option(help="Tenant (padrão: TENANT_PADRAO).")] = None,
        idioma: Annotated[
            Idioma | None, typer.Option(case_sensitive=False, help="Idioma (padrão: o do tenant).")
        ] = None,
        gravar: Annotated[
            Path | None, typer.Option("--gravar", help="Salva a conversa neste arquivo Markdown, turno a turno.")
        ] = None,
    ) -> None:
        """Conversa com a Sônia no terminal (modo local, mesmo processo)."""
        _utf8()
        try:
            aplicacao = montar_aplicacao()
        except ErroConfiguracao as exc:
            raise _falhar(f"Erro de configuração: {exc}") from None

        tenant_id = tenant or aplicacao.tenant_padrao
        config = aplicacao.tenants.get(tenant_id)
        if config is None:
            raise _falhar(
                f"Erro de configuração: tenant '{tenant_id}' não existe. "
                f"Disponíveis: {', '.join(sorted(aplicacao.tenants))}."
            )
        idioma_final = idioma or config.idioma_padrao
        if idioma_final not in config.idiomas_suportados:
            suportados = ", ".join(i.value for i in config.idiomas_suportados)
            raise _falhar(f"Erro de configuração: idioma '{idioma_final.value}' não suportado. Use: {suportados}.")

        ctx = TenantContext(tenant_id=tenant_id, idioma=idioma_final, papel=Papel.CLIENTE)
        persona = config.persona.nome
        gravador = GravadorMarkdown(gravar) if gravar else None
        if gravador:
            gravador.cabecalho(
                persona=persona,
                tenant=tenant_id,
                idioma=idioma_final.value,
                provedores=aplicacao.provedores,
                motor=aplicacao.motor_ativo,
                data_referencia=aplicacao.data_referencia,
                agora=datetime.now().astimezone(),
            )
        _cabecalho(aplicacao, persona, ctx, gravar)

        try:
            asyncio.run(_conversar(aplicacao, ctx, persona, gravador))
        except KeyboardInterrupt:
            typer.echo(f"\n{persona}: Até logo!")

    @app.command()
    def diagnostico(
        tenant: Annotated[str | None, typer.Option(help="Tenant (padrão: TENANT_PADRAO).")] = None,
    ) -> None:
        """Mostra o estado da configuração (provedores com chave, dados, manual). Nunca imprime chaves."""
        _utf8()
        try:
            servico = montar_diag()
        except ErroConfiguracao as exc:
            raise _falhar(f"Erro de configuração: {exc}") from None
        ctx = TenantContext(tenant_id=tenant or servico.tenant_padrao)
        _imprimir_diagnostico(asyncio.run(servico.diagnosticar(ctx)))

    return app


def _cabecalho(aplicacao: Aplicacao, persona: str, ctx: TenantContext, gravar: Path | None) -> None:
    referencia = aplicacao.data_referencia.date().isoformat() if aplicacao.data_referencia else "data real"
    typer.echo(f"Empório da Música - atendimento com {persona}")
    typer.echo(
        f"Tenant: {ctx.tenant_id} | idioma: {ctx.idioma.value} | motor: {aplicacao.motor_ativo} | "
        f"provedores: {', '.join(aplicacao.provedores)} | data de referência: {referencia}"
    )
    if gravar:
        typer.echo(f"Gravando a conversa em {gravar}")
    typer.echo("Digite 'sair' (ou Ctrl+C) para encerrar.\n")


async def _conversar(aplicacao: Aplicacao, ctx: TenantContext, persona: str, gravador: GravadorMarkdown | None) -> None:
    """Um único event loop por execução; a leitura é bloqueante de propósito.

    `input()` roda direto na corrotina, sem `to_thread`: nada mais precisa do loop enquanto se espera o cliente, e uma
    thread presa em `input()` travaria o encerramento do `asyncio.run` após Ctrl+C.
    """
    sessao_id = str(uuid.uuid4())
    while True:
        try:
            texto = input("Você: ").strip()
        except EOFError:
            typer.echo(f"\n{persona}: Até logo!")
            return
        if not texto:
            continue
        if texto.lower() in COMANDOS_SAIDA:
            typer.echo(f"{persona}: Até logo!")
            return
        resposta = await aplicacao.motor.responder(ctx, sessao_id, MensagemCliente(texto))
        typer.echo(f"{persona}: {resposta.texto}\n")
        if gravador:
            gravador.turno(persona, texto, resposta)


def _imprimir_diagnostico(info: DiagnosticoInfo) -> None:
    typer.echo("Diagnóstico do Empório da Música")
    typer.echo(f"- Provedores com chave: {', '.join(info.provedores_com_chave) or 'nenhum'}")
    typer.echo(f"- Motor ativo: {info.motor_ativo}")
    if isinstance(info, DiagnosticoDetalhado):
        typer.echo(f"- Tenant: {info.tenant_id} (válido: {'sim' if info.tenant_valido else 'não'})")
        if info.dados_carregados:
            typer.echo(f"- Dados: {info.n_produtos} produtos e {info.n_pedidos} pedidos carregados")
        else:
            typer.echo("- Dados: não carregados")
        typer.echo(f"- Manual de políticas: {'encontrado' if info.manual_encontrado else 'não encontrado'}")
    else:
        typer.echo(f"- Tenant: {info.tenant_id}")
        typer.echo(f"- Dados carregados: {'sim' if info.dados_carregados else 'não'}")
    referencia = info.data_referencia.date().isoformat() if info.data_referencia else "não definida (data real)"
    typer.echo(f"- Data de referência: {referencia}")
    if info.avisos:
        typer.echo("\nAvisos:")
        for aviso in info.avisos:
            typer.echo(f"  ! {aviso}")
    elif info.provedores_com_chave:
        typer.echo("\nTudo certo: `emporio chat` está pronto para usar.")


app = criar_app()

__all__ = ["SAIDA_CONFIGURACAO", "app", "criar_app"]

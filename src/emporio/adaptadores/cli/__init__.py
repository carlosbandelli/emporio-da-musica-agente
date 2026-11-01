"""Adaptador de entrada CLI (Typer). Só conhece `emporio.portas` e `emporio.composicao` (regra C4).

A CLI não monta nada sozinha: recebe as fábricas por `criar_app(...)` (padrão: composição real). Os testes passam
fábricas que montam a aplicação com `LLMFalso`, sem tocar `adaptadores.llm`. Não há estado global mutável.

Dois modos de `chat`: local (padrão, motor no mesmo processo) e remoto (`--api URL`: cada mensagem vai para
`POST {URL}/v1/chat` com `X-Tenant-ID` e o `session_id` da execução, via `httpx`; nada é montado localmente).

Códigos de saída: 0 ok; 2 erro de configuração (sem chave, tenant desconhecido, motor inválido, YAML inválido);
3 API remota inalcançável.
"""

import asyncio
import contextlib
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
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
from emporio.portas import (
    CodigoResposta,
    DiagnosticoInfo,
    Idioma,
    MensagemCliente,
    Papel,
    RespostaAtendimento,
    TenantContext,
)

SAIDA_CONFIGURACAO = 2
SAIDA_API_INALCANCAVEL = 3
TIMEOUT_API_S = 60.0
"""Acima do deadline de mensagem do tenant (30 s): a API responde com a mensagem fixa antes de a CLI desistir."""
PERSONA_REMOTA_PADRAO = "Atendente"
COMANDOS_SAIDA = frozenset({"sair", "exit", "quit"})

FabricaAplicacao = Callable[[], Aplicacao]
FabricaDiagnostico = Callable[[], DiagnosticoLocal]
FabricaClienteHttp = Callable[[str], httpx.AsyncClient]
"""Dada a URL base da API, devolve o cliente HTTP (os testes injetam um transporte ASGI)."""
Turno = tuple[str, RespostaAtendimento]
"""(nome da persona, resposta) de uma mensagem."""
Responder = Callable[[str], Awaitable[Turno]]


class _ErroRemoto(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """A API respondeu com erro de borda (400/404/422...): mostra a mensagem e segue a conversa."""


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


def _cliente_http_padrao(url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=url, timeout=TIMEOUT_API_S)


def _falhar(mensagem: str) -> typer.Exit:
    typer.echo(mensagem, err=True)
    return typer.Exit(SAIDA_CONFIGURACAO)


def criar_app(
    montar_aplicacao: FabricaAplicacao = _montar_padrao,
    montar_diag: FabricaDiagnostico = _diagnostico_padrao,
    cliente_http: FabricaClienteHttp = _cliente_http_padrao,
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
        api: Annotated[
            str | None,
            typer.Option("--api", help="URL base da API (modo remoto): cada mensagem vai para POST {URL}/v1/chat."),
        ] = None,
    ) -> None:
        """Conversa com a Sônia no terminal (local, no mesmo processo; ou remoto com --api URL)."""
        _utf8()
        if api:
            _chat_remoto(api, tenant, idioma, gravar, cliente_http)
            return
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
            asyncio.run(_conversar(_responder_local(aplicacao, ctx, persona, gravador), persona))
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


def _responder_local(
    aplicacao: Aplicacao, ctx: TenantContext, persona: str, gravador: GravadorMarkdown | None
) -> Responder:
    sessao_id = str(uuid.uuid4())

    async def responder(texto: str) -> Turno:
        resposta = await aplicacao.motor.responder(ctx, sessao_id, MensagemCliente(texto))
        if gravador:
            gravador.turno(persona, texto, resposta)
        return persona, resposta

    return responder


async def _conversar(responder: Responder, persona: str) -> None:
    """Um único event loop por execução; a leitura é bloqueante de propósito.

    `input()` roda direto na corrotina, sem `to_thread`: nada mais precisa do loop enquanto se espera o cliente, e uma
    thread presa em `input()` travaria o encerramento do `asyncio.run` após Ctrl+C.
    """
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
        try:
            persona, resposta = await responder(texto)
        except _ErroRemoto as erro:
            typer.echo(f"Erro da API: {erro}\n", err=True)
            continue
        typer.echo(f"{persona}: {resposta.texto}\n")


# --- modo remoto ------------------------------------------------------------------------------------------------------
def _chat_remoto(
    url: str, tenant: str | None, idioma: Idioma | None, gravar: Path | None, cliente_http: FabricaClienteHttp
) -> None:
    if tenant is None:
        try:
            tenant = carregar_settings().tenant_padrao
        except ErroConfiguracao as exc:
            raise _falhar(f"Erro de configuração: {exc}") from None
    gravador = GravadorMarkdown(gravar) if gravar else None
    typer.echo("Empório da Música - atendimento (modo remoto)")
    typer.echo(f"API: {url} | tenant: {tenant} | idioma: {idioma.value if idioma else 'padrão do tenant'}")
    if gravar:
        typer.echo(f"Gravando a conversa em {gravar}")
    typer.echo("Digite 'sair' (ou Ctrl+C) para encerrar.\n")
    try:
        asyncio.run(_conversar_remoto(url, tenant, idioma, gravador, cliente_http))
    except KeyboardInterrupt:
        typer.echo(f"\n{PERSONA_REMOTA_PADRAO}: Até logo!")


async def _conversar_remoto(
    url: str, tenant: str, idioma: Idioma | None, gravador: GravadorMarkdown | None, cliente_http: FabricaClienteHttp
) -> None:
    sessao_id = str(uuid.uuid4())
    cabecalho_gravado = False

    async with cliente_http(url) as cliente:

        async def responder(texto: str) -> Turno:
            nonlocal cabecalho_gravado
            persona, resposta = await _enviar(cliente, url, tenant, sessao_id=sessao_id, texto=texto, idioma=idioma)
            if gravador:
                if not cabecalho_gravado:
                    gravador.cabecalho(
                        persona=persona,
                        tenant=tenant,
                        idioma=resposta.idioma.value,
                        provedores=(resposta.provedor,) if resposta.provedor else (),
                        motor=f"remoto ({url})",
                        data_referencia=None,
                        agora=datetime.now().astimezone(),
                    )
                    cabecalho_gravado = True
                gravador.turno(persona, texto, resposta)
            return persona, resposta

        await _conversar(responder, PERSONA_REMOTA_PADRAO)


async def _enviar(
    cliente: httpx.AsyncClient, url: str, tenant: str, *, sessao_id: str, texto: str, idioma: Idioma | None
) -> Turno:
    corpo: dict[str, str] = {"session_id": sessao_id, "mensagem": texto}
    if idioma is not None:
        corpo["idioma"] = idioma.value
    try:
        http = await cliente.post("/v1/chat", json=corpo, headers={"X-Tenant-ID": tenant})
    except httpx.HTTPError as erro:
        typer.echo(f"Não consegui falar com a API em {url} ({type(erro).__name__}).", err=True)
        raise typer.Exit(SAIDA_API_INALCANCAVEL) from None
    try:
        dados = http.json()
    except ValueError:
        raise _ErroRemoto(f"resposta inesperada (HTTP {http.status_code})") from None
    if not isinstance(dados, dict):
        raise _ErroRemoto(f"resposta inesperada (HTTP {http.status_code})")
    if "texto" not in dados:  # erro de borda: {codigo, mensagem}
        raise _ErroRemoto(str(dados.get("mensagem", f"HTTP {http.status_code}")))
    resposta = RespostaAtendimento(
        texto=str(dados["texto"]),
        idioma=Idioma(dados["idioma"]),
        nivel=dados.get("nivel"),
        provedor=dados.get("provedor"),
        tools_chamadas=tuple(dados.get("tools_chamadas") or ()),
        degradada=bool(dados.get("degradada", False)),
        codigo=CodigoResposta(dados["codigo"]) if dados.get("codigo") else None,
        retry_after_s=dados.get("retry_after_s"),
    )
    return str(dados.get("persona") or PERSONA_REMOTA_PADRAO), resposta


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

__all__ = ["SAIDA_API_INALCANCAVEL", "SAIDA_CONFIGURACAO", "app", "criar_app"]

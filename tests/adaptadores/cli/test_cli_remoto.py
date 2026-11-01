"""CLI `chat --api URL` (task 2.1): conversa contra a app FastAPI por transporte ASGI em memória. Sem rede."""

from pathlib import Path

import httpx
import pytest
import typer
from fastapi import FastAPI
from typer.testing import CliRunner

from emporio.adaptadores.cli import SAIDA_API_INALCANCAVEL, criar_app
from emporio.adaptadores.llm import LLMFalso, responder_texto
from emporio.composicao import Aplicacao
from tests.adaptadores.api.apoio import TransporteComLifespan, app_com

URL = "http://api.teste"


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("GOOGLE_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "TENANT_PADRAO", "DATA_REFERENCIA"):
        monkeypatch.delenv(nome, raising=False)


def _nao_monta() -> Aplicacao:
    raise AssertionError("o modo remoto não deve montar a aplicação localmente")


def _cli_para(app_api: FastAPI) -> typer.Typer:
    def cliente(url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=TransporteComLifespan(app_api), base_url=url)

    return criar_app(_nao_monta, cliente_http=cliente)


def test_chat_remoto_envia_para_a_api_e_mostra_a_resposta(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Oi! Sou a Sônia."), responder_texto("Abrimos de segunda a sexta.")])
    cli = _cli_para(app_com(falso, tmp_path))
    r = CliRunner().invoke(cli, ["chat", "--api", URL, "--tenant", "emporio"], input="oi\nque horas abrem?\nsair\n")
    assert r.exit_code == 0, r.output
    assert "modo remoto" in r.output and URL in r.output
    assert "Sônia: Oi! Sou a Sônia." in r.output and "Abrimos de segunda a sexta." in r.output
    # mesmo session_id nas duas mensagens: o segundo turno enxerga o primeiro
    assert [m.content for m in falso.ultima_requisicao.mensagens] == ["oi", "Oi! Sou a Sônia.", "que horas abrem?"]


def test_chat_remoto_tenant_desconhecido_mostra_erro_e_segue(tmp_path: Path) -> None:
    cli = _cli_para(app_com(LLMFalso(), tmp_path))
    r = CliRunner().invoke(cli, ["chat", "--api", URL, "--tenant", "nao-existe"], input="oi\nsair\n")
    assert r.exit_code == 0, r.output
    assert "Loja não encontrada" in r.output  # erro de borda traduzido, sem traceback
    assert "Traceback" not in r.output


def test_chat_remoto_respeita_idioma_e_grava_a_conversa(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Hi there!")])
    cli = _cli_para(app_com(falso, tmp_path))
    arquivo = tmp_path / "conversa.md"
    r = CliRunner().invoke(
        cli,
        ["chat", "--api", URL, "--tenant", "emporio", "--idioma", "en-US", "--gravar", str(arquivo)],
        input="hello\nsair\n",
    )
    assert r.exit_code == 0, r.output
    texto = arquivo.read_text(encoding="utf-8")
    assert "# Conversa com Sônia" in texto
    assert "**Cliente:** hello" in texto and "Hi there!" in texto
    assert "- **Idioma:** en-US" in texto and "remoto (" in texto


def test_chat_remoto_api_sem_provedor_mostra_a_mensagem_fixa(tmp_path: Path) -> None:
    cli = _cli_para(app_com(None, tmp_path))
    r = CliRunner().invoke(cli, ["chat", "--api", URL, "--tenant", "emporio"], input="oi\nsair\n")
    assert r.exit_code == 0, r.output
    assert "fora do ar" in r.output


def test_chat_remoto_api_inalcancavel_sai_com_codigo_3() -> None:
    def recusa(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=request)

    cli = criar_app(
        _nao_monta,
        cliente_http=lambda url: httpx.AsyncClient(transport=httpx.MockTransport(recusa), base_url=url),
    )
    r = CliRunner().invoke(cli, ["chat", "--api", URL, "--tenant", "emporio"], input="oi\nsair\n")
    assert r.exit_code == SAIDA_API_INALCANCAVEL
    assert "Não consegui falar com a API" in r.output

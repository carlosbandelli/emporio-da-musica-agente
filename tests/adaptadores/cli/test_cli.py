"""CLI (task 1.12): chat com LLMFalso, sem chave, diagnóstico sem vazar chaves, gravação em Markdown. Sem rede."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporio.adaptadores.cli import criar_app
from emporio.adaptadores.llm import LLMFalso, chamar_tool, responder_texto
from emporio.composicao import Aplicacao, carregar_settings, montar, montar_diagnostico
from emporio.configuracao import Settings

RAIZ = Path(__file__).resolve().parents[3]
CHAVES = ("GOOGLE_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY")
REF = datetime(2026, 3, 10, 15, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in (*CHAVES, "MOTOR_ATENDIMENTO", "DATA_REFERENCIA", "TENANTS_CONFIG", "TENANT_PADRAO"):
        monkeypatch.delenv(nome, raising=False)


def _settings(**valores: object) -> Settings:
    return Settings(_env_file=None, tenants_config=RAIZ / "config" / "tenants.yaml", **valores)  # type: ignore[arg-type]


def _cli(tmp_path: Path, falso: LLMFalso | None, **valores: object):
    """App Typer cujas fábricas montam com LLMFalso (ou sem provedor) e sem ler o arquivo de ambiente."""
    settings = _settings(**valores)

    def montar_aplicacao() -> Aplicacao:
        provedores = {"falso": falso} if falso else None
        return montar(
            carregar_settings(_env_file=None, tenants_config=RAIZ / "config" / "tenants.yaml", **valores)
            if valores.get("motor_atendimento")
            else settings,
            provedores=provedores,
            raiz=RAIZ,
            dir_estado=tmp_path,
        )

    return criar_app(montar_aplicacao, lambda: montar_diagnostico(settings, raiz=RAIZ))


def test_conversa_de_dois_turnos_e_sair(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Oi! Sou a Sônia."), responder_texto("Abrimos de segunda a sexta.")])
    r = CliRunner().invoke(_cli(tmp_path, falso), ["chat"], input="oi\nque horas abrem?\nsair\n")
    assert r.exit_code == 0, r.output
    assert "Oi! Sou a Sônia." in r.output and "Abrimos de segunda a sexta." in r.output
    assert "falso" in r.output  # cabeçalho lista os provedores
    assert len(falso.requisicoes) == 2


@pytest.mark.parametrize("encerrar", ["exit\n", "quit\n", ""])
def test_exit_quit_e_eof_encerram_com_elegancia(tmp_path: Path, encerrar: str) -> None:
    r = CliRunner().invoke(_cli(tmp_path, LLMFalso()), ["chat"], input=encerrar)
    assert r.exit_code == 0 and "Traceback" not in r.output


def test_sem_chave_sai_com_2_e_mensagem_clara_sem_traceback(tmp_path: Path) -> None:
    r = CliRunner().invoke(_cli(tmp_path, None), ["chat"])
    assert r.exit_code == 2
    assert "GROQ_API_KEY" in r.output and "emporio diagnostico" in r.output
    assert "Traceback" not in r.output


def test_motor_desconhecido_sai_com_2_sem_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOTOR_ATENDIMENTO", "xyz")
    app = criar_app(lambda: montar(carregar_settings(_env_file=None), raiz=RAIZ, dir_estado=tmp_path))
    r = CliRunner().invoke(app, ["chat"])
    assert r.exit_code == 2
    assert "MOTOR_ATENDIMENTO" in r.output and "xyz" in r.output and "Traceback" not in r.output


def test_tenant_desconhecido_sai_com_2(tmp_path: Path) -> None:
    r = CliRunner().invoke(_cli(tmp_path, LLMFalso()), ["chat", "--tenant", "nao-existe"])
    assert r.exit_code == 2 and "nao-existe" in r.output and "emporio" in r.output


def test_idioma_en_us_chega_ao_motor(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Hi!")])
    r = CliRunner().invoke(_cli(tmp_path, falso), ["chat", "--idioma", "en-US"], input="hello\nsair\n")
    assert r.exit_code == 0 and "en-US" in r.output and "Hi!" in r.output


def test_gravar_gera_markdown_com_cabecalho_turnos_e_metadados(tmp_path: Path) -> None:
    falso = LLMFalso(
        [chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}), responder_texto("Sai por R$ 2.199,00.")]
    )
    destino = tmp_path / "saida" / "conversa.md"
    app = _cli(tmp_path, falso, data_referencia=REF)
    r = CliRunner().invoke(app, ["chat", "--gravar", str(destino)], input="quanto custa o Takamine GD20?\nsair\n")
    assert r.exit_code == 0, r.output
    md = destino.read_text(encoding="utf-8")
    assert md.startswith("# Conversa com Sônia")
    assert "**Gravada em:**" in md and "2026-03-10" in md
    assert "**Tenant:** emporio" in md and "**Idioma:** pt-BR" in md
    assert "**Provedores com chave:** falso" in md and "**Motor:** nativo" in md
    assert "**Cliente:** quanto custa o Takamine GD20?" in md
    assert "**Sônia:** Sai por R$ 2.199,00." in md
    assert "detalhar_produto" in md and " · falso · " in md


def test_gravar_e_incremental_e_nao_inclui_chaves(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("um"), responder_texto("dois")])
    destino = tmp_path / "c.md"
    r = CliRunner().invoke(
        _cli(tmp_path, falso, groq_api_key="sk-teste-123"), ["chat", "--gravar", str(destino)], input="a\nb\nsair\n"
    )
    md = destino.read_text(encoding="utf-8")
    assert md.count("**Cliente:**") == 2 and "sk-teste" not in md and "sk-teste" not in r.output


def test_diagnostico_mascara_chaves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "sk-teste-123")
    monkeypatch.setenv("GOOGLE_API_KEY", "sk-teste-456")
    app = criar_app(montar_diag=lambda: montar_diagnostico(carregar_settings(_env_file=None), raiz=RAIZ))
    r = CliRunner().invoke(app, ["diagnostico"])
    assert r.exit_code == 0, r.output
    assert "groq" in r.output and "gemini" in r.output
    for pedaco in ("sk-teste", "123", "456", "teste"):
        assert pedaco not in r.output


def test_diagnostico_sem_chave_funciona_e_explica_o_que_falta() -> None:
    app = criar_app(montar_diag=lambda: montar_diagnostico(carregar_settings(_env_file=None), raiz=RAIZ))
    r = CliRunner().invoke(app, ["diagnostico"])
    assert r.exit_code == 0, r.output
    assert "nenhum" in r.output and "GROQ_API_KEY" in r.output
    assert "65 produtos" in r.output and "Manual de políticas: encontrado" in r.output
    assert "Motor ativo: nativo" in r.output and "válido: sim" in r.output


def test_diagnostico_tenant_invalido_nao_quebra() -> None:
    app = criar_app(montar_diag=lambda: montar_diagnostico(carregar_settings(_env_file=None), raiz=RAIZ))
    r = CliRunner().invoke(app, ["diagnostico", "--tenant", "x"])
    assert r.exit_code == 0 and "válido: não" in r.output and "não existe" in r.output


def test_versao_e_ajuda() -> None:
    app = criar_app()
    assert CliRunner().invoke(app, ["versao"]).exit_code == 0
    # Sem cores: no CI (GITHUB_ACTIONS) o rich força ANSI e os códigos quebram a busca por "--gravar".
    r = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "GITHUB_ACTIONS": "", "FORCE_COLOR": ""}).invoke(
        app, ["chat", "--help"]
    )
    assert r.exit_code == 0 and "--gravar" in r.output and "--idioma" in r.output and "--tenant" in r.output

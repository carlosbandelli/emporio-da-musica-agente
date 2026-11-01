"""Testes de Settings, TenantContext e do carregador de tenants (sem rede, sem ler o arquivo de ambiente real)."""

import dataclasses
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from emporio.configuracao import ErroConfiguracao, Settings, carregar_tenant, carregar_tenants, obter_tenant
from emporio.portas import Idioma, Papel, TenantContext

RAIZ = Path(__file__).resolve().parents[1]
YAML_REAL = RAIZ / "config" / "tenants.yaml"


def _config_valida() -> dict[str, object]:
    return yaml.safe_load(YAML_REAL.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _escrever(tmp_path: Path, conteudo: object) -> Path:
    caminho = tmp_path / "tenants.yaml"
    texto = conteudo if isinstance(conteudo, str) else yaml.safe_dump(conteudo, allow_unicode=True)
    caminho.write_text(texto, encoding="utf-8")
    return caminho


# --- TenantContext ---------------------------------------------------------


def test_tenant_context_e_imutavel() -> None:
    ctx = TenantContext(tenant_id="emporio")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.tenant_id = "outro"  # type: ignore[misc]
    assert ctx.idioma is Idioma.PT_BR
    assert ctx.papel is Papel.CLIENTE


# --- Settings --------------------------------------------------------------


def test_settings_motor_desconhecido_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOTOR_ATENDIMENTO", "xyz")
    with pytest.raises(ValidationError, match="motor_atendimento"):
        Settings(_env_file=None)


def test_settings_padroes(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("MOTOR_ATENDIMENTO", "GOOGLE_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "DATA_REFERENCIA"):
        monkeypatch.delenv(nome, raising=False)
    s = Settings(_env_file=None)
    assert s.motor_atendimento == "nativo"
    assert s.data_referencia is None
    assert s.tenant_padrao == "emporio"
    assert s.tenants_config == Path("config/tenants.yaml")
    assert s.provedores_com_chave() == ()


def test_settings_le_variaveis_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_REFERENCIA", "2026-03-10T10:00:00-04:00")
    monkeypatch.setenv("TENANTS_CONFIG", "outro.yaml")
    s = Settings(_env_file=None)
    assert s.data_referencia is not None
    assert s.data_referencia.year == 2026
    assert s.tenants_config == Path("outro.yaml")


def test_repr_nao_expoe_chaves() -> None:
    s = Settings(_env_file=None, google_api_key="segredo-google-123", groq_api_key="segredo-groq-456")  # type: ignore[arg-type]
    texto = repr(s) + str(s)
    assert "segredo-google-123" not in texto
    assert "segredo-groq-456" not in texto


def test_provedores_com_chave_lista_so_quem_tem_chave(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("GOOGLE_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(nome, raising=False)
    s = Settings(_env_file=None, groq_api_key="x", google_api_key="  ")  # type: ignore[arg-type]
    assert s.provedores_com_chave() == ("groq",)
    s2 = Settings(_env_file=None, anthropic_api_key="a", google_api_key="g", groq_api_key="q")  # type: ignore[arg-type]
    assert s2.provedores_com_chave() == ("claude", "gemini", "groq")


# --- Carregador de tenants ---------------------------------------------------


def test_yaml_real_carrega_tenant_emporio() -> None:
    tenants = carregar_tenants(YAML_REAL)
    cfg = tenants["emporio"]
    assert cfg.persona.nome == "Sônia"
    assert cfg.schema_db == "emporio"
    assert cfg.idioma_padrao is Idioma.PT_BR
    assert cfg.contatos.telefone == "(67) 3341-4444"
    assert cfg.contatos.whatsapp == "(67) 3321-4500"
    assert cfg.contatos.email == "contato@emporiodamusica.com.br"
    assert cfg.deadline_mensagem_s == 30
    assert "palhetas" in cfg.termos_fora_de_escopo.pt_br
    assert "picks" in cfg.termos_fora_de_escopo.en_us


def test_tenant_inexistente_lista_disponiveis() -> None:
    with pytest.raises(ErroConfiguracao) as exc:
        carregar_tenant(YAML_REAL, "loja_fantasma")
    assert "loja_fantasma" in str(exc.value)
    assert "emporio" in str(exc.value)


def test_obter_tenant_existente() -> None:
    assert obter_tenant(carregar_tenants(YAML_REAL), "emporio").nome == "Empório da Música"


def test_yaml_invalido_gera_erro_claro(tmp_path: Path) -> None:
    caminho = _escrever(tmp_path, "tenants: [\n  oops: : :")
    with pytest.raises(ErroConfiguracao, match="YAML inválido"):
        carregar_tenants(caminho)


def test_arquivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao, match="Não foi possível ler"):
        carregar_tenants(tmp_path / "nao_existe.yaml")


def test_estrutura_sem_chave_tenants(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao, match="tenants"):
        carregar_tenants(_escrever(tmp_path, {"outra": 1}))


def test_campo_obrigatorio_ausente_cita_o_campo(tmp_path: Path) -> None:
    dados = _config_valida()
    del dados["tenants"]["emporio"]["persona"]["nome"]  # type: ignore[index]
    with pytest.raises(ErroConfiguracao) as exc:
        carregar_tenants(_escrever(tmp_path, dados))
    assert "emporio.persona.nome" in str(exc.value)


def test_campo_com_valor_invalido_cita_o_campo(tmp_path: Path) -> None:
    dados = _config_valida()
    dados["tenants"]["emporio"]["limites"]["sessao"]["capacidade"] = 0  # type: ignore[index]
    with pytest.raises(ErroConfiguracao) as exc:
        carregar_tenants(_escrever(tmp_path, dados))
    assert "emporio.limites.sessao.capacidade" in str(exc.value)


def test_idioma_desconhecido_e_campo_extra_sao_rejeitados(tmp_path: Path) -> None:
    dados = _config_valida()
    dados["tenants"]["emporio"]["idioma_padrao"] = "fr-FR"  # type: ignore[index]
    with pytest.raises(ErroConfiguracao, match="idioma_padrao"):
        carregar_tenants(_escrever(tmp_path, dados))
    dados = _config_valida()
    dados["tenants"]["emporio"]["campo_novo"] = 1  # type: ignore[index]
    with pytest.raises(ErroConfiguracao, match="campo_novo"):
        carregar_tenants(_escrever(tmp_path, dados))


def test_config_tenant_e_imutavel() -> None:
    cfg = carregar_tenant(YAML_REAL, "emporio")
    with pytest.raises(ValidationError):
        cfg.nome = "outro"  # type: ignore[misc]

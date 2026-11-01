"""Composition root (task 1.10): seleção do motor, provedores e montagem com os dados reais. Sem rede e sem `.env`."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from emporio.adaptadores.llm import LLMFalso, chamar_tool
from emporio.adaptadores.motor.n8n import AgenteN8n
from emporio.adaptadores.motor.nativo import AgenteNativo
from emporio.composicao import (
    MOTORES_DISPONIVEIS,
    Aplicacao,
    ErroSemChaveLLM,
    carregar_settings,
    montar,
    montar_diagnostico,
)
from emporio.configuracao import ErroConfiguracao, Settings
from emporio.portas import MensagemCliente, PapelMensagem, RequisicaoLLM, RespostaLLM, TenantContext
from tests.modulos.fakes import RelogioFixo

RAIZ = Path(__file__).resolve().parents[1]
CHAVES = ("GOOGLE_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in (
        *CHAVES,
        "MOTOR_ATENDIMENTO",
        "DATA_REFERENCIA",
        "TENANTS_CONFIG",
        "TENANT_PADRAO",
        "JWT_SECRET",
        "N8N_WEBHOOK_URL",
        "N8N_WEBHOOK_SEGREDO",
        "N8N_URL_API",
    ):
        monkeypatch.delenv(nome, raising=False)


def settings(**valores: object) -> Settings:
    return Settings(_env_file=None, tenants_config=RAIZ / "config" / "tenants.yaml", **valores)  # type: ignore[arg-type]


def test_motor_desconhecido_e_erro_de_configuracao_na_inicializacao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOTOR_ATENDIMENTO", "xyz")
    with pytest.raises(ErroConfiguracao, match=r"MOTOR_ATENDIMENTO.*xyz.*nativo"):
        carregar_settings(_env_file=None)


def test_montar_recusa_motor_desconhecido_mesmo_sem_validacao() -> None:
    s = Settings.model_construct(motor_atendimento="xyz", tenants_config=RAIZ / "config" / "tenants.yaml")
    with pytest.raises(ErroConfiguracao, match="xyz"):
        montar(s, provedores={"falso": LLMFalso()}, raiz=RAIZ)


SEGREDO_JWT = "segredo-jwt-da-composicao-com-bem-mais-de-32-bytes"
URL_N8N = "http://n8n:5678/webhook/emporio-atendimento"


def test_motor_n8n_sem_url_e_erro_de_configuracao_claro(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao, match="N8N_WEBHOOK_URL"):
        montar(settings(motor_atendimento="n8n", jwt_secret=SEGREDO_JWT), raiz=RAIZ, dir_estado=tmp_path)


def test_motor_n8n_com_url_invalida_e_erro_de_configuracao(tmp_path: Path) -> None:
    s = settings(motor_atendimento="n8n", jwt_secret=SEGREDO_JWT, n8n_webhook_url="n8n:5678/webhook")
    with pytest.raises(ErroConfiguracao, match="http"):
        montar(s, raiz=RAIZ, dir_estado=tmp_path)


def test_motor_n8n_sem_jwt_secret_e_erro_de_configuracao(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao, match="JWT_SECRET"):
        montar(settings(motor_atendimento="n8n", n8n_webhook_url=URL_N8N), raiz=RAIZ, dir_estado=tmp_path)


def test_motor_n8n_monta_agente_n8n_sem_chave_de_llm(tmp_path: Path) -> None:
    s = settings(motor_atendimento="n8n", n8n_webhook_url=URL_N8N, jwt_secret=SEGREDO_JWT)
    app = montar(s, raiz=RAIZ, dir_estado=tmp_path)
    assert isinstance(app.motor, AgenteN8n) and app.motor_ativo == "n8n"
    assert app.provedores == ("n8n",) and app.identidade is not None and app.ferramentas is not None
    assert "n8n" in MOTORES_DISPONIVEIS


def test_carregar_settings_aceita_n8n() -> None:
    s = carregar_settings(_env_file=None, motor_atendimento="n8n", n8n_webhook_segredo="segredo-que-nao-aparece")
    assert s.motor_atendimento == "n8n" and s.n8n_url_api == "http://app:8000"
    assert "segredo-que-nao-aparece" not in repr(s)


def test_sem_chave_e_sem_injecao_e_erro_de_configuracao_claro(tmp_path: Path) -> None:
    with pytest.raises(ErroSemChaveLLM, match="GROQ_API_KEY") as erro:
        montar(settings(), raiz=RAIZ, dir_estado=tmp_path)
    assert isinstance(erro.value, ErroConfiguracao)


def test_carregar_settings_valido_devolve_settings() -> None:
    s = carregar_settings(_env_file=None, motor_atendimento="nativo")
    assert s.motor_atendimento == "nativo" and "nativo" in MOTORES_DISPONIVEIS


def test_tenant_padrao_inexistente_falha_na_montagem(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao, match="nao-existe"):
        montar(settings(tenant_padrao="nao-existe"), provedores={"falso": LLMFalso()}, raiz=RAIZ, dir_estado=tmp_path)


def test_chave_configurada_cria_provedor_real_sem_chamar_rede(tmp_path: Path) -> None:
    app = montar(settings(groq_api_key="gsk-teste"), raiz=RAIZ, dir_estado=tmp_path)
    assert app.provedores == ("groq",)
    assert "gsk-teste" not in repr(app)


async def test_montar_com_llm_falso_responde_com_os_dados_reais(tmp_path: Path) -> None:
    def responder(req: RequisicaoLLM) -> RespostaLLM:
        tool = [m for m in req.mensagens if m.role is PapelMensagem.TOOL][-1]
        assert '"R$ 2.199,00"' in tool.content
        return RespostaLLM("O Takamine GD20 sai por R$ 2.199,00.", "", "")

    falso = LLMFalso([chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}), responder])
    app = montar(
        settings(),
        provedores={"falso": falso},
        relogio=RelogioFixo(datetime(2026, 3, 10, 15, tzinfo=UTC)),
        raiz=RAIZ,
        dir_estado=tmp_path,
    )

    assert isinstance(app, Aplicacao) and isinstance(app.motor, AgenteNativo)
    assert app.motor_ativo == "nativo" and app.tenant_padrao == "emporio" and "emporio" in app.tenants
    assert app.provedores == ("falso",)
    r = await app.motor.responder(TenantContext("emporio"), "s1", MensagemCliente("quanto custa o Takamine GD20?"))
    assert r.degradada is False and "R$ 2.199,00" in r.texto and r.tools_chamadas == ("detalhar_produto",)


async def test_redis_fora_do_ar_nao_derruba_o_chat(tmp_path: Path) -> None:
    """Com REDIS_URL apontando para um Redis inexistente, o chat segue (fail-open: sem histórico, limite local)."""
    falso = LLMFalso([RespostaLLM("Olá! Como posso ajudar?", "", "")])
    app = montar(
        settings(redis_url="redis://127.0.0.1:1/0"),
        provedores={"falso": falso},
        relogio=RelogioFixo(datetime(2026, 3, 10, 15, tzinfo=UTC)),
        raiz=RAIZ,
        dir_estado=tmp_path,
    )
    r = await app.motor.responder(TenantContext("emporio"), "s1", MensagemCliente("oi"))
    assert r.degradada is False and r.texto == "Olá! Como posso ajudar?"


async def test_diagnostico_do_motor_n8n_aponta_o_que_falta_sem_pedir_chave_de_llm() -> None:
    diag = montar_diagnostico(settings(motor_atendimento="n8n"), raiz=RAIZ)
    info = await diag.diagnosticar(TenantContext("emporio"))
    assert any("N8N_WEBHOOK_URL" in a for a in info.avisos) and any("JWT_SECRET" in a for a in info.avisos)
    assert not any("GROQ_API_KEY" in a for a in info.avisos)

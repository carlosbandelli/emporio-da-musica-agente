"""Testes do manual convertido e do system prompt do motor nativo (sem rede, sem ler o arquivo de ambiente)."""

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest

from emporio.adaptadores.motor.nativo.prompt import PromptSistema, carregar_politicas, montar_prompt
from emporio.configuracao.tenants import ErroConfiguracao, carregar_tenant
from emporio.portas.contexto import Idioma
from emporio.portas.tenant import ConfigTenant

RAIZ = Path(__file__).resolve().parents[3]
SABADO = date(2026, 3, 14)
SEGUNDA = date(2026, 3, 16)
TOOLS = (
    "buscar_produtos",
    "detalhar_produto",
    "simular_pagamento",
    "consultar_pedido",
    "status_loja",
    "registrar_solicitacao",
)


@pytest.fixture(scope="module")
def config() -> ConfigTenant:
    return carregar_tenant(RAIZ / "config" / "tenants.yaml", "emporio")


@pytest.fixture(scope="module")
def politicas(config: ConfigTenant) -> str:
    return carregar_politicas(RAIZ / config.politicas_arquivo)


@pytest.fixture(scope="module")
def prompts(config: ConfigTenant, politicas: str) -> Mapping[tuple[Idioma, date], PromptSistema]:
    return {(i, d): montar_prompt(config, politicas, i, d) for i in Idioma for d in (SABADO, SEGUNDA)}


# --- manual convertido ---------------------------------------------------------------------------------------------


def test_manual_tem_as_dez_secoes_na_ordem(politicas: str) -> None:
    numeros = [int(n) for n in re.findall(r"^## (\d+)\. ", politicas, flags=re.MULTILINE)]
    assert numeros == list(range(1, 11))
    subsecoes = re.findall(r"^### (\d+\.\d+) ", politicas, flags=re.MULTILINE)
    esperadas = [
        "1.1",
        "1.2",
        "3.1",
        "4.1",
        "4.2",
        "4.3",
        "4.4",
        "5.1",
        "5.2",
        "5.3",
        "6.1",
        "6.2",
        "7.1",
        "7.2",
        "7.3",
        "8.1",
        "8.2",
        "8.3",
    ]
    assert subsecoes == esperadas


@pytest.mark.parametrize(
    "trecho",
    [
        # horários (§2)
        "| Segunda a Sexta-feira | 09:00 às 18:00 |",
        "| Sábado | 09:00 às 13:00 |",
        "| Domingo e Feriados | Fechado |",
        # pagamento (§3, §6.2)
        "5% de desconto sobre o preço de tabela",
        "O desconto de PIX (5%) não se aplica sobre preços já promocionais.",
        "Até 12x sem juros",
        "exceto abaixo de R$ 50,00",
        "de 4x a 6x: sem juros, parcela mínima de R$ 80,00",
        "de 7x a 12x: sem juros, parcela mínima de R$ 100,00",
        "acima de R$ 2.000,00",
        # prazos (§4, §8)
        "até 7 (sete) dias corridos após o recebimento",
        "até 30 (trinta) dias corridos após a compra",
        "permitidas em até 7 dias",
        "90 (noventa) dias",
        # frete (§5)
        "Frete grátis para pedidos acima de R$ 500,00.",
        "taxa fixa de R$ 35,00",
        "| PAC (Correios) | 5 a 12 dias úteis |",
        "| SEDEX (Correios) | 2 a 5 dias úteis |",
        "| Jadlog (.package) | 3 a 8 dias úteis |",
        # itens não elegíveis (§4.4) e contatos como no original (premissas #1-#2)
        "setup, regulagem especial",
        '"venda final"',
        "Boquilhas de instrumentos de sopro",
        "(67) 3341-4444",
        "(67) 3321-4500",
        "contato@empóriodamusica.com.br",
        "Rua 14 de Maio, 3200",
        "24 horas úteis.",
    ],
)
def test_manual_preserva_valores_chave(politicas: str, trecho: str) -> None:
    assert trecho in politicas


def test_carregar_politicas_remove_comentario_html(politicas: str) -> None:
    assert "<!--" not in politicas
    assert politicas.startswith("# EMPÓRIO DA MÚSICA")


def test_carregar_politicas_inexistente_levanta_erro(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao, match="manual de políticas"):
        carregar_politicas(tmp_path / "nao_existe.md")


def test_carregar_politicas_vazio_levanta_erro(tmp_path: Path) -> None:
    caminho = tmp_path / "politicas.md"
    caminho.write_text("<!-- só comentário -->\n\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="vazio"):
        carregar_politicas(caminho)


def test_carregar_politicas_normaliza_quebras_de_linha(tmp_path: Path) -> None:
    caminho = tmp_path / "politicas.md"
    caminho.write_bytes("## 1. Seção\r\n\r\nTexto.\r\n".encode())
    assert carregar_politicas(caminho) == "## 1. Seção\n\nTexto."


# --- prompt renderizado --------------------------------------------------------------------------------------------


def test_cacheavel_tem_persona_manual_idiomas_e_tools(
    config: ConfigTenant, politicas: str, prompts: Mapping[tuple[Idioma, date], PromptSistema]
) -> None:
    cacheavel = prompts[(Idioma.PT_BR, SABADO)].cacheavel
    assert f"Você é a {config.persona.nome}" in cacheavel
    assert config.nome in cacheavel
    assert politicas in cacheavel
    assert "pt-BR (português do Brasil)" in cacheavel
    assert "en-US (English)" in cacheavel
    for tool in TOOLS:
        assert tool in cacheavel, tool


def test_cacheavel_tem_contatos_e_termos_do_tenant(
    config: ConfigTenant, prompts: Mapping[tuple[Idioma, date], PromptSistema]
) -> None:
    cacheavel = prompts[(Idioma.PT_BR, SABADO)].cacheavel
    for contato in (config.contatos.telefone, config.contatos.whatsapp, config.contatos.email):
        assert contato in cacheavel
    for termo in (*config.termos_fora_de_escopo.pt_br, *config.termos_fora_de_escopo.en_us):
        assert termo in cacheavel


def test_cacheavel_identico_entre_idiomas_e_datas(prompts: Mapping[tuple[Idioma, date], PromptSistema]) -> None:
    assert len({p.cacheavel for p in prompts.values()}) == 1


def test_cacheavel_estavel_entre_chamadas(config: ConfigTenant, politicas: str) -> None:
    a = montar_prompt(config, politicas, Idioma.PT_BR, SABADO)
    b = montar_prompt(config, politicas, Idioma.EN_US, SEGUNDA)
    assert a.cacheavel.encode("utf-8") == b.cacheavel.encode("utf-8")


def test_cacheavel_sem_data_nem_idioma_da_sessao(prompts: Mapping[tuple[Idioma, date], PromptSistema]) -> None:
    cacheavel = prompts[(Idioma.PT_BR, SABADO)].cacheavel
    assert "Idioma desta sessão" not in cacheavel
    assert "14/03/2026" not in cacheavel
    assert "2026-03-14" not in cacheavel


def test_dinamico_muda_com_idioma_e_data(prompts: Mapping[tuple[Idioma, date], PromptSistema]) -> None:
    assert len({p.dinamico for p in prompts.values()}) == len(prompts)
    pt = prompts[(Idioma.PT_BR, SABADO)].dinamico
    en = prompts[(Idioma.EN_US, SABADO)].dinamico
    assert "Responda em português" in pt
    assert "Reply in English" in en
    assert "Hoje é sábado, 14/03/2026" in pt
    assert "Hoje é segunda-feira, 16/03/2026" in prompts[(Idioma.PT_BR, SEGUNDA)].dinamico


def test_dinamico_nao_contem_hora(prompts: Mapping[tuple[Idioma, date], PromptSistema]) -> None:
    for prompt in prompts.values():
        assert not re.search(r"\d{1,2}:\d{2}", prompt.dinamico)
        assert not re.search(r"\b\d{1,2}h\b", prompt.dinamico)
        assert "status_loja" in prompt.dinamico


def test_montar_prompt_rejeita_manual_vazio(config: ConfigTenant) -> None:
    with pytest.raises(ErroConfiguracao):
        montar_prompt(config, "  \n", Idioma.PT_BR, SABADO)


def test_montar_prompt_rejeita_idioma_nao_suportado(config: ConfigTenant, politicas: str) -> None:
    so_portugues = config.model_copy(update={"idiomas_suportados": (Idioma.PT_BR,)})
    with pytest.raises(ValueError, match="en-US"):
        montar_prompt(so_portugues, politicas, Idioma.EN_US, SABADO)
    cacheavel = montar_prompt(so_portugues, politicas, Idioma.PT_BR, SABADO).cacheavel
    assert "en-US (English)" not in cacheavel

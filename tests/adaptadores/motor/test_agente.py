"""`AgenteNativo` (task 1.10): loop agentic, histórico, idioma, deadline, guarda de valores. Sem rede e sem `.env`."""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from emporio.adaptadores.llm import LLMFalso, chamar_tool, chamar_tools, responder_texto
from emporio.adaptadores.memoria import RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.comum.guarda import (
    NOTA_INTERNA_PREFIXO,
    centavos_de,
    valores_monetarios,
    valores_nao_verificados,
    valores_permitidos,
)
from emporio.adaptadores.motor.comum.historico import cortar_historico
from emporio.adaptadores.motor.comum.idioma import detectar_idioma, pedido_de_troca, resolver_idioma
from emporio.adaptadores.motor.comum.prompt import carregar_politicas
from emporio.adaptadores.motor.nativo import AgenteNativo, ChaveMensagem, mensagem_fixa
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.composicao import montar_ferramentas
from emporio.configuracao.tenants import carregar_tenants
from emporio.modulos.atendimento.aplicacao import ArgsStatusLoja, FerramentasLoja, ResultadoTool, Tool
from emporio.portas import (
    ChamadaTool,
    ConteudoBruto,
    DefinicaoTool,
    ErroLLMTransitorio,
    ErroPrazoEsgotado,
    Idioma,
    LLMProvider,
    MensagemCliente,
    MensagemLLM,
    Papel,
    PapelMensagem,
    RequisicaoLLM,
    RespostaLLM,
    TenantContext,
)
from emporio.portas.tenant import ConfigTenant, LimiteBalde
from tests.modulos.fakes import RelogioFixo

RAIZ = Path(__file__).resolve().parents[3]
DATA = datetime(2026, 3, 10, 15, tzinfo=UTC)  # terça-feira
CTX = TenantContext("emporio")
CTX_EN = TenantContext("emporio", idioma=Idioma.EN_US)
WHATSAPP = "(67) 3321-4500"


def config(**alteracoes: object) -> ConfigTenant:
    base = carregar_tenants(RAIZ / "config" / "tenants.yaml")["emporio"]
    return base.model_copy(update=alteracoes)


@dataclass
class Montagem:
    agente: AgenteNativo
    sessoes: SessaoStoreMemoria
    ferramentas: FerramentasLoja


def montar_agente(
    provedores: Mapping[str, LLMProvider] | LLMFalso,
    tmp_path: Path,
    *,
    tenants: Mapping[str, ConfigTenant] | None = None,
    ferramentas: object | None = None,
    **opcoes: int,
) -> Montagem:
    if isinstance(provedores, LLMFalso):
        provedores = {"falso": provedores}
    tenants = tenants or {"emporio": config()}
    relogio = RelogioFixo(DATA)
    loja = montar_ferramentas(tenants, raiz=RAIZ, relogio=relogio, dir_estado=tmp_path)
    sessoes = SessaoStoreMemoria()
    agente = AgenteNativo(
        tenants=tenants,
        politicas={tid: carregar_politicas(RAIZ / c.politicas_arquivo) for tid, c in tenants.items()},
        roteador=Roteador(provedores),
        ferramentas=ferramentas or loja,  # type: ignore[arg-type]
        sessoes=sessoes,
        limitador=RateLimiterMemoria(tenants),
        relogio=relogio,
        **opcoes,
    )
    return Montagem(agente, sessoes, loja)


def ultimo_resultado_tool(req: RequisicaoLLM) -> dict[str, object]:
    tool = [m for m in req.mensagens if m.role is PapelMensagem.TOOL][-1]
    resultado: dict[str, object] = json.loads(tool.content)
    return resultado


def preco_vigente(req: RequisicaoLLM) -> str:
    dados = ultimo_resultado_tool(req)["dados"]
    assert isinstance(dados, dict)
    texto: str = dados["preco_vigente"]["texto"]
    return texto


async def perguntar(m: Montagem, texto: str, ctx: TenantContext = CTX, sessao: str = "s1"):  # type: ignore[no-untyped-def]
    return await m.agente.responder(ctx, sessao, MensagemCliente(texto))


# --- DoD da task -----------------------------------------------------------------------------------------------------
async def test_pergunta_de_preco_chama_detalhar_produto_e_repete_o_valor_da_tool(tmp_path: Path) -> None:
    falso = LLMFalso(
        [
            chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}),
            lambda req: responder_texto(f"O Takamine GD20 sai por {preco_vigente(req)} à vista."),
        ]
    )
    m = montar_agente(falso, tmp_path)

    r = await perguntar(m, "Oi! Quanto custa o Takamine GD20?")

    assert r.tools_chamadas == ("detalhar_produto",)
    assert r.degradada is False and r.provedor == "falso" and r.idioma is Idioma.PT_BR
    preco_da_tool = preco_vigente(falso.ultima_requisicao)
    assert preco_da_tool == "R$ 2.199,00"
    assert [v.centavos for v in valores_monetarios(r.texto)] == [219900]
    assert preco_da_tool in r.texto


async def test_limite_de_iteracoes_devolve_mensagem_fixa_e_nao_persiste_rodada_incompleta(tmp_path: Path) -> None:
    falso = LLMFalso([chamar_tool("status_loja") for _ in range(3)])
    m = montar_agente(falso, tmp_path, max_iteracoes=3)

    r = await perguntar(m, "vocês estão abertos?")

    assert r.degradada is True
    assert r.texto == mensagem_fixa(ChaveMensagem.LIMITE_ITERACOES, Idioma.PT_BR, contatos=config().contatos)
    assert len(falso.requisicoes) == 3 and r.tools_chamadas == ("status_loja",) * 3
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None
    assert [(x.role, x.content) for x in estado.historico] == [
        (PapelMensagem.USER, "vocês estão abertos?"),
        (PapelMensagem.ASSISTANT, r.texto),
    ]
    assert not any(x.tool_calls for x in estado.historico)


async def test_papel_cliente_nao_recebe_tools_administrativas(tmp_path: Path) -> None:
    async def relatorio(ctx: TenantContext, args: BaseModel, sessao: str | None) -> BaseModel:
        return args

    falso = LLMFalso([responder_texto("Oi!"), responder_texto("Olá, gerente!")])
    m = montar_agente(falso, tmp_path)
    m.ferramentas.registrar(
        Tool("relatorio_vendas", "Relatório (admin).", ArgsStatusLoja, frozenset({Papel.GERENTE}), relatorio)
    )

    await perguntar(m, "oi", ctx=TenantContext("emporio", papel=Papel.CLIENTE), sessao="cli")
    await perguntar(m, "oi", ctx=TenantContext("emporio", papel=Papel.GERENTE), sessao="ger")

    nomes_cliente = [t.nome for t in falso.requisicoes[0].tools]
    nomes_gerente = [t.nome for t in falso.requisicoes[1].tools]
    assert "relatorio_vendas" not in nomes_cliente and "detalhar_produto" in nomes_cliente
    assert "relatorio_vendas" in nomes_gerente
    assert nomes_cliente == [t.nome for t in m.ferramentas.definicoes(Papel.CLIENTE)]  # ordem determinística


# --- validação de entrada (I1) e rate limit --------------------------------------------------------------------------
@pytest.mark.parametrize("texto", ["", "   \n\t "])
async def test_mensagem_vazia_nao_chama_llm(tmp_path: Path, texto: str) -> None:
    falso = LLMFalso()
    m = montar_agente(falso, tmp_path)
    r = await perguntar(m, texto)
    assert r.texto == mensagem_fixa(ChaveMensagem.VAZIA, Idioma.PT_BR)
    assert r.degradada is False and r.nivel is None and r.provedor is None
    assert falso.requisicoes == []
    assert await m.sessoes.obter(CTX, "s1") is None


async def test_mensagem_longa_nao_chama_llm(tmp_path: Path) -> None:
    falso = LLMFalso()
    m = montar_agente(falso, tmp_path)
    limite = config().limites.tamanho_max_mensagem
    r = await perguntar(m, "a" * (limite + 1))
    assert str(limite) in r.texto and r.degradada is False
    assert falso.requisicoes == []


async def test_rate_limit_da_sessao_devolve_mensagem_fixa(tmp_path: Path) -> None:
    base = config()
    limites = base.limites.model_copy(update={"sessao": LimiteBalde(capacidade=1, reposicao_por_s=0.001)})
    falso = LLMFalso([responder_texto("Oi! Como posso ajudar?")])
    m = montar_agente(falso, tmp_path, tenants={"emporio": base.model_copy(update={"limites": limites})})

    primeira = await perguntar(m, "oi")
    segunda = await perguntar(m, "oi de novo")

    assert primeira.degradada is False
    assert segunda.degradada is True
    assert segunda.texto == mensagem_fixa(ChaveMensagem.LIMITE, Idioma.PT_BR)
    assert len(falso.requisicoes) == 1


# --- histórico (I2) e conteúdo bruto ---------------------------------------------------------------------------------
async def test_segunda_mensagem_enxerga_a_primeira_e_system_cacheavel_estavel(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Oi! Sou a Sônia."), responder_texto("Você disse oi.")])
    m = montar_agente(falso, tmp_path)

    await perguntar(m, "oi, tudo bem?")
    await perguntar(m, "o que eu disse?")

    primeira, segunda = falso.requisicoes
    assert [(x.role, x.content) for x in segunda.mensagens] == [
        (PapelMensagem.USER, "oi, tudo bem?"),
        (PapelMensagem.ASSISTANT, "Oi! Sou a Sônia."),
        (PapelMensagem.USER, "o que eu disse?"),
    ]
    assert primeira.system_cacheavel == segunda.system_cacheavel
    assert "10/03/2026" in segunda.system_dinamico
    assert primeira.prazo_monotonic is not None


async def test_bruto_e_copiado_sem_alteracao_e_reenviado(tmp_path: Path) -> None:
    bruto_tool = ConteudoBruto("falso", ({"type": "thinking", "thinking": "...", "signature": "abc"},))
    bruto_final = ConteudoBruto("falso", ({"type": "text", "text": "fim"},))
    pedido_tool = chamar_tool("status_loja", id="call_1")
    falso = LLMFalso(
        [
            RespostaLLM(None, "falso", "m", chamadas_tool=pedido_tool.chamadas_tool, bruto=bruto_tool),
            RespostaLLM("Estamos abertos!", "falso", "m", bruto=bruto_final),
            responder_texto("De nada!"),
        ]
    )
    m = montar_agente(falso, tmp_path)

    await perguntar(m, "estão abertos?")
    await perguntar(m, "valeu")

    segunda_rodada = falso.requisicoes[1].mensagens
    assert segunda_rodada[1].role is PapelMensagem.ASSISTANT and segunda_rodada[1].bruto is bruto_tool
    assert segunda_rodada[2].role is PapelMensagem.TOOL and segunda_rodada[2].tool_call_id == "call_1"
    assert segunda_rodada[2].erro_tool is False
    turno_seguinte = falso.requisicoes[2].mensagens
    assert turno_seguinte[:3] == segunda_rodada
    assert turno_seguinte[3].bruto is bruto_final


async def test_varias_tools_executadas_em_ordem_e_erro_de_tool_marcado(tmp_path: Path) -> None:
    falso = LLMFalso(
        [
            chamar_tools(
                ChamadaTool("a", "status_loja", {}),
                ChamadaTool("b", "detalhar_produto", {}),  # sem argumentos: ok=False
                ChamadaTool("c", "nao_existe", {}),
            ),
            responder_texto("Pronto."),
        ]
    )
    m = montar_agente(falso, tmp_path)
    r = await perguntar(m, "abertos? e o preço?")

    tools = [x for x in falso.ultima_requisicao.mensagens if x.role is PapelMensagem.TOOL]
    assert [(t.tool_call_id, t.erro_tool) for t in tools] == [("a", False), ("b", True), ("c", True)]
    assert r.tools_chamadas == ("status_loja", "detalhar_produto", "nao_existe")


# --- falhas (I4) e deadline (I5) -------------------------------------------------------------------------------------
async def test_deadline_estourado_devolve_mensagem_fixa(tmp_path: Path) -> None:
    async def lento(req: RequisicaoLLM) -> RespostaLLM:
        await asyncio.sleep(5)
        return responder_texto("tarde demais")

    falso = LLMFalso([lento])
    m = montar_agente(falso, tmp_path, tenants={"emporio": config(deadline_mensagem_s=0.05)})

    r = await perguntar(m, "oi")

    assert r.degradada is True
    assert r.texto == mensagem_fixa(ChaveMensagem.DEADLINE, Idioma.PT_BR, contatos=config().contatos)
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None and [x.content for x in estado.historico] == ["oi", r.texto]


async def test_prazo_esgotado_no_provedor_devolve_mensagem_fixa(tmp_path: Path) -> None:
    m = montar_agente(LLMFalso([ErroPrazoEsgotado("acabou")]), tmp_path)
    r = await perguntar(m, "oi")
    assert r.degradada and r.texto == mensagem_fixa(ChaveMensagem.DEADLINE, Idioma.PT_BR, contatos=config().contatos)


async def test_todos_os_provedores_fora_devolve_indisponivel(tmp_path: Path) -> None:
    m = montar_agente(LLMFalso([ErroLLMTransitorio("503")]), tmp_path)
    r = await perguntar(m, "oi")
    assert r.degradada is True
    assert r.texto == mensagem_fixa(ChaveMensagem.INDISPONIVEL, Idioma.PT_BR, contatos=config().contatos)
    assert WHATSAPP in r.texto


async def test_excecao_inesperada_do_llm_nao_vaza(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    m = montar_agente(LLMFalso([RuntimeError("bug")]), tmp_path)
    with caplog.at_level(logging.DEBUG):
        r = await perguntar(m, "meu email é maria@exemplo.com, cadê meu pedido?")
    assert r.degradada is True
    assert r.texto == mensagem_fixa(ChaveMensagem.ERRO, Idioma.PT_BR, contatos=config().contatos)
    assert "RuntimeError" in caplog.text
    assert "maria@exemplo.com" not in caplog.text


class FerramentasQuebradas:
    def definicoes(self, papel: Papel) -> tuple[DefinicaoTool, ...]:
        return ()

    async def executar(
        self, ctx: TenantContext, nome: str, argumentos: Mapping[str, object], *, sessao_id: str | None = None
    ) -> ResultadoTool:
        raise RuntimeError("backend fora")


async def test_excecao_inesperada_de_tool_nao_vaza_nem_persiste_rodada(tmp_path: Path) -> None:
    m = montar_agente(LLMFalso([chamar_tool("status_loja")]), tmp_path, ferramentas=FerramentasQuebradas())
    r = await perguntar(m, "abertos?")
    assert r.degradada is True
    assert r.texto == mensagem_fixa(ChaveMensagem.ERRO, Idioma.PT_BR, contatos=config().contatos)
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None and len(estado.historico) == 2 and not estado.historico[1].tool_calls


async def test_tenant_desconhecido_devolve_mensagem_fixa(tmp_path: Path) -> None:
    falso = LLMFalso()
    m = montar_agente(falso, tmp_path)
    r = await perguntar(m, "oi", ctx=TenantContext("outra-loja"))
    assert r.degradada is True and r.texto == mensagem_fixa(ChaveMensagem.ERRO, Idioma.PT_BR)
    assert falso.requisicoes == []


async def test_resposta_vazia_tenta_de_novo_uma_vez(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto(""), responder_texto("Oi!")])
    m = montar_agente(falso, tmp_path)
    r = await perguntar(m, "oi")
    assert r.texto == "Oi!" and r.degradada is False

    falso2 = LLMFalso([responder_texto(""), responder_texto("  ")])
    r2 = await perguntar(montar_agente(falso2, tmp_path), "oi")
    assert r2.degradada is True


# --- guarda de valores (I6) ------------------------------------------------------------------------------------------
async def test_preco_inventado_passa_por_rodada_corretiva_e_corrige(tmp_path: Path) -> None:
    falso = LLMFalso(
        [
            chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}),
            responder_texto("O Takamine GD20 custa R$ 999,00."),
            lambda req: responder_texto(f"Corrigindo: o Takamine GD20 custa {preco_vigente(req)}."),
        ]
    )
    m = montar_agente(falso, tmp_path)

    r = await perguntar(m, "quanto custa o Takamine GD20?")

    assert r.degradada is False and "R$ 2.199,00" in r.texto and "999" not in r.texto
    nota = falso.requisicoes[2].mensagens[-1]
    assert nota.role is PapelMensagem.USER and nota.content.startswith(NOTA_INTERNA_PREFIXO)
    assert "R$ 999,00" in nota.content
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None and estado.historico[-1].content == r.texto
    assert estado.historico[-2] == nota  # tudo o que foi enviado ao LLM, na ordem


async def test_preco_inventado_duas_vezes_cai_na_mensagem_fixa(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Custa R$ 999,00."), responder_texto("Custa R$ 999,00 mesmo.")])
    m = montar_agente(falso, tmp_path)

    r = await perguntar(m, "quanto custa o Takamine GD20?")

    assert r.degradada is True and "999" not in r.texto
    assert r.texto == mensagem_fixa(ChaveMensagem.VALOR_NAO_VERIFICADO, Idioma.PT_BR, contatos=config().contatos)
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None and [x.content for x in estado.historico] == ["quanto custa o Takamine GD20?", r.texto]


async def test_valores_do_manual_e_do_cliente_sao_aceitos(tmp_path: Path) -> None:
    falso = LLMFalso(
        [
            responder_texto("Em Campo Grande o frete é grátis acima de R$ 500,00; abaixo disso, R$ 35,00."),
            responder_texto("Claro! Procuro violões até R$ 1.000,00 pra você."),
        ]
    )
    m = montar_agente(falso, tmp_path)
    assert (await perguntar(m, "qual o frete?")).degradada is False
    assert (await perguntar(m, "quero violões até 1000 reais")).degradada is False
    assert len(falso.requisicoes) == 2


def test_parser_de_valores_monetarios() -> None:
    assert centavos_de("1.299,00") == 129900
    assert centavos_de("1,299.00") == 129900
    assert centavos_de("1299") == 129900
    assert centavos_de("2.000") == 200000
    assert centavos_de("80,5") == 8050
    assert centavos_de("1,2,3") is None
    texto = "De R$ 2.199,00 por R$1,979.10, ou $183.25 em 12x; nada de 12 vezes."
    assert [v.centavos for v in valores_monetarios(texto)] == [219900, 197910, 18325]


def test_valores_permitidos_vem_de_tools_cliente_e_manual() -> None:
    mensagens = (
        MensagemLLM(PapelMensagem.USER, "violões até 1.500?"),
        MensagemLLM(PapelMensagem.TOOL, '{"preco":{"centavos":219900,"texto":"R$ 2.199,00"},"pix":"R$ 2.089,05"}'),
        MensagemLLM(PapelMensagem.ASSISTANT, "Custa R$ 777,00"),  # valores da Sônia não contam
        MensagemLLM(PapelMensagem.USER, f"{NOTA_INTERNA_PREFIXO} R$ 888,00"),  # nem os da nota interna
    )
    permitidos = valores_permitidos(mensagens, "Frete grátis acima de R$ 500,00.")
    assert {150000, 219900, 208905, 50000} <= permitidos
    assert 77700 not in permitidos and 88800 not in permitidos
    assert valores_nao_verificados("R$ 2.199,00 ou R$ 777,00", permitidos) == ("R$ 777,00",)


# --- idioma (I7) -----------------------------------------------------------------------------------------------------
async def test_primeira_mensagem_em_ingles_define_idioma_e_mensagens_fixas(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Hi! How can I help?"), ErroLLMTransitorio("503")])
    m = montar_agente(falso, tmp_path)

    r1 = await perguntar(m, "Hi, how much is the Takamine GD20?")
    r2 = await perguntar(m, "ok")

    assert r1.idioma is Idioma.EN_US and "English" in falso.requisicoes[0].system_dinamico
    assert r2.idioma is Idioma.EN_US and r2.degradada
    assert r2.texto == mensagem_fixa(ChaveMensagem.INDISPONIVEL, Idioma.EN_US, contatos=config().contatos)
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None and estado.idioma is Idioma.EN_US


async def test_mensagem_ambigua_usa_idioma_do_canal_sem_fixar_na_sessao(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Hi!"), responder_texto("Olá!")])
    m = montar_agente(falso, tmp_path)

    r1 = await perguntar(m, "Takamine GD20", ctx=CTX_EN)
    estado = await m.sessoes.obter(CTX, "s1")
    assert r1.idioma is Idioma.EN_US and estado is not None and estado.idioma is None

    r2 = await perguntar(m, "quanto custa?", ctx=CTX_EN)
    assert r2.idioma is Idioma.PT_BR


async def test_pedido_explicito_troca_o_idioma(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto("Oi!"), responder_texto("Sure!"), responder_texto("Claro!")])
    m = montar_agente(falso, tmp_path)
    assert (await perguntar(m, "oi, bom dia")).idioma is Idioma.PT_BR
    assert (await perguntar(m, "can we speak English?")).idioma is Idioma.EN_US
    assert (await perguntar(m, "pode ser em português?")).idioma is Idioma.PT_BR


async def test_ferramentas_recebem_o_idioma_da_sessao(tmp_path: Path) -> None:
    falso = LLMFalso(
        [
            chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}),
            lambda req: responder_texto(f"It costs {preco_vigente(req)}."),
        ]
    )
    m = montar_agente(falso, tmp_path)
    r = await perguntar(m, "What is the price of the Takamine GD20?")
    assert r.idioma is Idioma.EN_US and "R$2,199.00" in r.texto and r.degradada is False


def test_deteccao_de_idioma() -> None:
    assert detectar_idioma("Oi, bom dia!") is Idioma.PT_BR
    assert detectar_idioma("olá, quanto custa o violão?") is Idioma.PT_BR
    assert detectar_idioma("Hello! What is the price?") is Idioma.EN_US
    assert detectar_idioma("Takamine GD20") is None
    assert detectar_idioma("ok") is None
    assert pedido_de_troca("Can we speak English, please?") is Idioma.EN_US
    assert pedido_de_troca("English") is Idioma.EN_US
    assert pedido_de_troca("pode ser em português?") is Idioma.PT_BR
    assert pedido_de_troca("como se fala violão em inglês?") is None
    assert pedido_de_troca("how do you say guitar in Portuguese?") is None
    suportados = (Idioma.PT_BR,)
    assert resolver_idioma("Hello, what is the price?", None, suportados) is None


# --- corte do histórico ----------------------------------------------------------------------------------------------
def _turno_com_tool(i: int) -> list[MensagemLLM]:
    bruto = ConteudoBruto("claude", ({"type": "thinking", "i": i},))
    chamada = ChamadaTool(f"c{i}", "status_loja", {})
    return [
        MensagemLLM(PapelMensagem.USER, f"pergunta {i}"),
        MensagemLLM(PapelMensagem.ASSISTANT, "", tool_calls=(chamada,), bruto=bruto),
        MensagemLLM(PapelMensagem.TOOL, "{}", tool_call_id=f"c{i}"),
        MensagemLLM(PapelMensagem.ASSISTANT, f"resposta {i}", bruto=bruto),
    ]


def test_corte_de_historico_remove_turnos_inteiros_e_bruto() -> None:
    historico = tuple(m for i in range(12) for m in _turno_com_tool(i))  # 48 mensagens
    cortado = cortar_historico(historico, 40)

    assert len(cortado) <= 40
    assert cortado[0].role is PapelMensagem.USER and not cortado[0].content.startswith(NOTA_INTERNA_PREFIXO)
    assert cortado[0].content == "pergunta 2"
    assert all(m.bruto is None for m in cortado)
    ids_pedidos = {c.id for m in cortado for c in m.tool_calls}
    assert all(m.tool_call_id in ids_pedidos for m in cortado if m.role is PapelMensagem.TOOL)
    assert cortar_historico(historico[:40], 40) == historico[:40]  # abaixo do limite: intacto, com bruto


def test_corte_nunca_comeca_numa_nota_interna() -> None:
    nota = MensagemLLM(PapelMensagem.USER, f"{NOTA_INTERNA_PREFIXO} corrija")
    turno = [
        MensagemLLM(PapelMensagem.USER, "p"),
        MensagemLLM(PapelMensagem.ASSISTANT, "R$ 1,00"),
        nota,
        MensagemLLM(PapelMensagem.ASSISTANT, "ok"),
    ]
    cortado = cortar_historico(tuple(turno * 3), 6)
    assert cortado[0].content == "p" and len(cortado) == 4


async def test_historico_longo_e_cortado_ao_persistir(tmp_path: Path) -> None:
    falso = LLMFalso([responder_texto(f"resposta {i}") for i in range(5)])
    m = montar_agente(falso, tmp_path, max_mensagens_historico=4)
    for i in range(5):
        await perguntar(m, f"oi {i}")
    estado = await m.sessoes.obter(CTX, "s1")
    assert estado is not None and [x.content for x in estado.historico] == ["oi 3", "resposta 3", "oi 4", "resposta 4"]


# --- isolamento (I3/I8) ----------------------------------------------------------------------------------------------
async def test_mesmo_sessao_id_em_tenants_diferentes_nao_compartilha_historico(tmp_path: Path) -> None:
    outra = config(nome="Outra Loja")
    falso = LLMFalso([responder_texto("Oi A"), responder_texto("Oi B")])
    m = montar_agente(falso, tmp_path, tenants={"emporio": config(), "outra": outra})

    await perguntar(m, "oi, sou da A", ctx=CTX)
    await perguntar(m, "oi, sou da B", ctx=TenantContext("outra"))

    assert [x.content for x in falso.requisicoes[1].mensagens] == ["oi, sou da B"]
    assert "Outra Loja" in falso.requisicoes[1].system_cacheavel
    assert "Outra Loja" not in falso.requisicoes[0].system_cacheavel

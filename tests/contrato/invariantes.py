"""Verificações I1-I8 da porta `MotorDeAtendimento` (arquitetura §3.2), reutilizáveis e independentes do motor.

Cada `verificar_iN(fabrica, tmp_path)` monta o motor pela fábrica, conversa só pela porta e levanta `AssertionError`
se o invariante for violado. Os testes de `test_motor_atendimento.py` chamam estas funções para cada motor
registrado; o teste meta as chama contra um motor quebrado de propósito e exige que falhem.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from emporio.adaptadores.llm import chamar_tool, responder_texto
from emporio.portas import (
    ErroLLMTransitorio,
    Idioma,
    MensagemCliente,
    PapelMensagem,
    RequisicaoLLM,
    RespostaAtendimento,
    RespostaLLM,
    TenantContext,
)
from tests.contrato.kit import (
    NOME_LOJA_B,
    TENANT_A,
    TENANT_B,
    WHATSAPP_A,
    WHATSAPP_B,
    FabricaMotor,
    KitMotor,
    tenant_emporio,
    tenants_ab,
)

CTX = TenantContext(TENANT_A)
CTX_B = TenantContext(TENANT_B)
PRECO_TAKAMINE = "R$ 2.199,00"
PRECO_INVENTADO = "R$ 999,00"


async def perguntar(kit: KitMotor, texto: str, *, ctx: TenantContext = CTX, sessao: str = "s1") -> RespostaAtendimento:
    """Uma mensagem pela porta. Exceção do motor vira `AssertionError` (I4: a porta nunca lança)."""
    try:
        return await kit.motor.responder(ctx, sessao, MensagemCliente(texto))
    except Exception as erro:
        raise AssertionError(f"o motor lançou {type(erro).__name__}: {erro}") from erro


def _kit(fabrica: FabricaMotor, tmp_path: Path, **config: object) -> KitMotor:
    """Kit só com o tenant `emporio`, com ajustes opcionais (ex.: `deadline_mensagem_s`)."""
    return fabrica({TENANT_A: tenant_emporio().model_copy(update=config)}, tmp_path)


def _sem_chamar_llm(kit: KitMotor) -> None:
    assert kit.llm.requisicoes == [], "o motor chamou o LLM numa mensagem inválida"
    assert kit.tools.chamadas == []


def _sistema(requisicao: RequisicaoLLM) -> str:
    return requisicao.system_cacheavel + requisicao.system_dinamico


def _confere_mensagem_fixa(resposta: RespostaAtendimento, idioma: Idioma) -> None:
    assert resposta.degradada is True, "falha de LLM/backend sem degradada=True"
    assert resposta.texto.strip(), "mensagem fixa vazia"
    assert resposta.idioma is idioma
    for vazamento in ("RuntimeError", "bug do provedor", "a@b.com", "Traceback", "banco fora"):
        assert vazamento not in resposta.texto, f"a mensagem fixa vazou detalhe interno: {vazamento!r}"


# --- I1 --------------------------------------------------------------------------------------------------------------
async def verificar_i1(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I1: texto não vazio para mensagem válida; vazia ou acima do limite → mensagem fixa, sem chamar o LLM."""
    kit = _kit(fabrica, tmp_path)
    kit.llm.roteirizar(responder_texto("Oi! Sou a Sônia, como posso ajudar?"))
    resposta = await perguntar(kit, "oi, tudo bem?")
    assert resposta.texto.strip(), "texto vazio para mensagem válida"
    assert resposta.degradada is False

    limite = tenant_emporio().limites.tamanho_max_mensagem
    for i, invalida in enumerate(["", "   \n\t ", "a" * (limite + 1)]):
        kit = _kit(fabrica, tmp_path)  # LLM sem roteiro: qualquer chamada ficaria registrada em `requisicoes`
        resposta = await perguntar(kit, invalida, sessao=f"invalida-{i}")
        assert resposta.texto.strip(), f"mensagem inválida #{i} sem texto de resposta"
        assert resposta.idioma is Idioma.PT_BR
        _sem_chamar_llm(kit)


# --- I2 --------------------------------------------------------------------------------------------------------------
async def verificar_i2(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I2: a 2ª mensagem da sessão enxerga a 1ª (o LLM recebe o histórico; o `SessaoStore`, se exposto, o guarda)."""
    kit = _kit(fabrica, tmp_path)
    kit.llm.roteirizar(responder_texto("Oi! Sou a Sônia."), responder_texto("Você disse oi."))
    await perguntar(kit, "oi, meu violão é azul")
    await perguntar(kit, "o que eu disse?")

    conteudos = [m.content for m in kit.llm.ultima_requisicao.mensagens]
    assert "oi, meu violão é azul" in conteudos, "a 2ª chamada ao LLM não trouxe a 1ª mensagem"
    assert "Oi! Sou a Sônia." in conteudos, "a 2ª chamada ao LLM não trouxe a resposta anterior"
    assert conteudos[-1] == "o que eu disse?"
    if kit.sessoes is not None:
        estado = await kit.sessoes.obter(CTX, "s1")
        assert estado is not None and len(estado.historico) >= 4


# --- I3 --------------------------------------------------------------------------------------------------------------
async def verificar_i3(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I3: mesmo `sessao_id` em tenants diferentes não compartilha histórico nem configuração."""
    kit = fabrica(tenants_ab(tmp_path), tmp_path)
    kit.llm.roteirizar(
        responder_texto("Oi, loja A!"),
        responder_texto("Oi, loja B!"),
        ErroLLMTransitorio("503 A"),
        ErroLLMTransitorio("503 B"),
    )
    await perguntar(kit, "mensagem secreta da loja A", ctx=CTX, sessao="compartilhada")
    await perguntar(kit, "mensagem da loja B", ctx=CTX_B, sessao="compartilhada")

    requisicao_a, requisicao_b = kit.llm.requisicoes
    assert [m.content for m in requisicao_b.mensagens] == ["mensagem da loja B"], "histórico da A vazou para a B"
    assert NOME_LOJA_B in _sistema(requisicao_b), "a loja B não recebeu a própria configuração"
    assert NOME_LOJA_B not in _sistema(requisicao_a), "a configuração da B vazou para a A"

    # A configuração também aparece nas mensagens fixas (contatos do tenant).
    falha_a = await perguntar(kit, "e agora?", ctx=CTX, sessao="compartilhada")
    falha_b = await perguntar(kit, "e agora?", ctx=CTX_B, sessao="compartilhada")
    assert falha_a.degradada and falha_b.degradada
    assert WHATSAPP_A in falha_a.texto and WHATSAPP_B not in falha_a.texto
    assert WHATSAPP_B in falha_b.texto and WHATSAPP_A not in falha_b.texto
    assert all("loja B" not in m.content for m in kit.llm.requisicoes[2].mensagens), "a A passou a ver dados da B"


# --- I4 --------------------------------------------------------------------------------------------------------------
async def verificar_i4(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I4: falha de LLM ou de backend (tool) nunca vira exceção: mensagem fixa traduzida, `degradada=True`."""
    falhas_llm: list[BaseException] = [ErroLLMTransitorio("503"), RuntimeError("bug do provedor com e-mail a@b.com")]
    for i, falha in enumerate(falhas_llm):
        kit = _kit(fabrica, tmp_path)
        kit.llm.roteirizar(falha)
        resposta = await perguntar(kit, "quanto custa o Takamine GD20?", sessao=f"llm-{i}")
        _confere_mensagem_fixa(resposta, Idioma.PT_BR)

    kit = _kit(fabrica, tmp_path)  # backend de tools fora do ar
    kit.tools.falha = RuntimeError("banco fora do ar")
    kit.llm.roteirizar(chamar_tool("status_loja"))
    resposta = await perguntar(kit, "vocês estão abertos?")
    _confere_mensagem_fixa(resposta, Idioma.PT_BR)

    kit = _kit(fabrica, tmp_path)  # sem roteiro: o LLM falso esgotado também é uma falha de LLM
    resposta = await perguntar(kit, "oi")
    _confere_mensagem_fixa(resposta, Idioma.PT_BR)


# --- I5 --------------------------------------------------------------------------------------------------------------
async def verificar_i5(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I5: estourou o deadline do tenant → mensagem fixa, `degradada=True`, sem esperar o LLM lento."""

    async def lento(req: RequisicaoLLM) -> RespostaLLM:
        await asyncio.sleep(30)
        return responder_texto("tarde demais")

    kit = _kit(fabrica, tmp_path, deadline_mensagem_s=0.05)
    kit.llm.roteirizar(lento)
    inicio = time.monotonic()
    resposta = await perguntar(kit, "oi")
    decorrido = time.monotonic() - inicio

    assert decorrido < 3, f"o motor esperou {decorrido:.1f}s com deadline de 0,05s"
    _confere_mensagem_fixa(resposta, Idioma.PT_BR)
    assert "tarde demais" not in resposta.texto


# --- I6 --------------------------------------------------------------------------------------------------------------
async def verificar_i6(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I6: um preço só aparece se a tool de preço foi chamada (as duas metades abaixo).

    Cobre só preço; estoque e status de pedido ficam a cargo do prompt no `nativo` (ver README desta pasta).
    """
    await verificar_i6_com_tool(fabrica, tmp_path)
    await verificar_i6_sem_tool(fabrica, tmp_path)


async def verificar_i6_com_tool(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I6, parte 1: com `detalhar_produto` chamada, o preço do texto é o devolvido pela tool."""
    kit = _kit(fabrica, tmp_path)
    kit.llm.roteirizar(
        chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}),
        lambda req: responder_texto(f"O Takamine GD20 sai por {_preco_da_tool(req)} à vista."),
    )
    resposta = await perguntar(kit, "Quanto custa o Takamine GD20?")
    assert "detalhar_produto" in kit.tools.chamadas, "a tool detalhar_produto não foi executada"
    assert "detalhar_produto" in resposta.tools_chamadas, "detalhar_produto ausente de tools_chamadas"
    assert PRECO_TAKAMINE in resposta.texto and resposta.degradada is False, "o preço da tool não chegou ao texto"


async def verificar_i6_sem_tool(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I6, parte 2: o LLM insiste em um preço sem chamar tool (inclusive após correção): o texto final não o traz."""
    kit = _kit(fabrica, tmp_path)
    kit.llm.roteirizar(
        responder_texto(f"O Takamine GD20 custa {PRECO_INVENTADO}."),
        responder_texto(f"Custa {PRECO_INVENTADO} mesmo."),
    )
    resposta = await perguntar(kit, "Quanto custa o Takamine GD20?")
    assert kit.tools.chamadas == [] and resposta.tools_chamadas == ()
    assert "999" not in resposta.texto, "o texto final trouxe um preço que nenhuma tool devolveu"


def _preco_da_tool(req: RequisicaoLLM) -> str:
    tool = [m for m in req.mensagens if m.role is PapelMensagem.TOOL][-1]
    texto: str = json.loads(tool.content)["dados"]["preco_vigente"]["texto"]
    return texto


# --- I7 --------------------------------------------------------------------------------------------------------------
async def verificar_i7(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I7: responde no idioma da sessão (pt-BR ou en-US), inclusive nas mensagens fixas."""
    kit = _kit(fabrica, tmp_path)
    kit.llm.roteirizar(
        responder_texto("Oi! Posso ajudar?"),
        ErroLLMTransitorio("503"),
        responder_texto("Hi! How can I help?"),
        ErroLLMTransitorio("503"),
    )
    pt = await perguntar(kit, "Oi, bom dia! Vocês vendem violão?", sessao="pt")
    pt_falha = await perguntar(kit, "ok", sessao="pt")
    en = await perguntar(kit, "Hi, how much is the Takamine GD20?", sessao="en")
    en_falha = await perguntar(kit, "ok", sessao="en")  # o idioma fica na sessão

    assert pt.idioma is Idioma.PT_BR and pt_falha.idioma is Idioma.PT_BR
    assert en.idioma is Idioma.EN_US and en_falha.idioma is Idioma.EN_US
    assert pt_falha.degradada and en_falha.degradada
    assert pt_falha.texto != en_falha.texto, "a mensagem fixa não foi traduzida"
    assert "try again" in en_falha.texto, "a mensagem fixa en-US não está em inglês"
    assert "try again" not in pt_falha.texto


# --- I8 --------------------------------------------------------------------------------------------------------------
async def verificar_i8(fabrica: FabricaMotor, tmp_path: Path) -> None:
    """I8: sem estado global: duas instâncias (tenants diferentes) em `asyncio.gather` não se contaminam."""
    tenants = tenants_ab(tmp_path)
    kit_a = fabrica({TENANT_A: tenants[TENANT_A]}, tmp_path / "a")
    kit_b = fabrica({TENANT_B: tenants[TENANT_B]}, tmp_path / "b")

    def com_pausa(texto: str) -> Callable[[RequisicaoLLM], Awaitable[RespostaLLM]]:
        async def resposta(req: RequisicaoLLM) -> RespostaLLM:
            await asyncio.sleep(0.02)  # força o entrelaçamento das duas conversas
            return responder_texto(texto)

        return resposta

    kit_a.llm.roteirizar(com_pausa("Resposta da loja A."), ErroLLMTransitorio("503"))
    kit_b.llm.roteirizar(com_pausa("Resposta da loja B."), ErroLLMTransitorio("503"))

    async def conversa(kit: KitMotor, ctx: TenantContext, texto: str) -> list[RespostaAtendimento]:
        primeira = await perguntar(kit, texto, ctx=ctx, sessao="mesma")
        return [primeira, await perguntar(kit, "e agora?", ctx=ctx, sessao="mesma")]

    resultados_a, resultados_b = await asyncio.gather(
        conversa(kit_a, CTX, "pergunta da A"), conversa(kit_b, CTX_B, "pergunta da B")
    )

    assert resultados_a[0].texto == "Resposta da loja A." and resultados_b[0].texto == "Resposta da loja B."
    assert [m.content for m in kit_a.llm.requisicoes[0].mensagens] == ["pergunta da A"]
    assert [m.content for m in kit_b.llm.requisicoes[0].mensagens] == ["pergunta da B"]
    assert all("pergunta da B" not in m.content for r in kit_a.llm.requisicoes for m in r.mensagens)
    assert all("pergunta da A" not in m.content for r in kit_b.llm.requisicoes for m in r.mensagens)
    assert WHATSAPP_A in resultados_a[1].texto and WHATSAPP_B not in resultados_a[1].texto
    assert WHATSAPP_B in resultados_b[1].texto and WHATSAPP_A not in resultados_b[1].texto

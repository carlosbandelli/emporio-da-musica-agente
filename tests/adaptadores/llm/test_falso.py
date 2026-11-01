"""`LLMFalso`: roteiro de respostas, exceções e callables, com gravação das requisições."""

import pytest

from emporio.adaptadores.llm import LLMFalso, RoteiroEsgotado, chamar_tool, chamar_tools, responder_texto
from emporio.portas import ChamadaTool, ErroLLM, ErroLLMTransitorio, LLMProvider, RequisicaoLLM, RespostaLLM

from .conftest import requisicao


async def test_roteiro_em_ordem_e_grava_requisicoes() -> None:
    falso = LLMFalso([chamar_tool("detalhar_produto", {"produto_id": 93}), responder_texto("Custa R$ 999,00")])
    r1, r2 = requisicao(), requisicao()
    primeira = await falso.gerar(r1)
    segunda = await falso.gerar(r2)
    assert primeira.texto is None
    assert primeira.chamadas_tool[0].nome == "detalhar_produto"
    assert primeira.chamadas_tool[0].argumentos == {"produto_id": 93}
    assert segunda.texto == "Custa R$ 999,00"
    assert falso.requisicoes == [r1, r2]
    assert falso.ultima_requisicao is r2
    assert falso.restantes == 0


async def test_helpers_preenchem_provedor_com_o_nome_do_falso() -> None:
    falso = LLMFalso([responder_texto("oi")], nome="groq", modelo="m-1")
    resp = await falso.gerar(requisicao())
    assert (resp.provedor, resp.modelo) == ("groq", "m-1")
    assert falso.nome == "groq"


async def test_resposta_explicita_mantem_provedor() -> None:
    falso = LLMFalso([RespostaLLM("x", provedor="gemini", modelo="g")])
    assert (await falso.gerar(requisicao())).provedor == "gemini"


async def test_item_excecao_e_levantado() -> None:
    falso = LLMFalso([ErroLLMTransitorio("429", retry_after_s=1.0), responder_texto("ok")])
    with pytest.raises(ErroLLMTransitorio):
        await falso.gerar(requisicao())
    assert (await falso.gerar(requisicao())).texto == "ok"
    assert len(falso.requisicoes) == 2


async def test_item_callable_sincrono_e_assincrono() -> None:
    def eco(req: RequisicaoLLM) -> RespostaLLM:
        return responder_texto(req.mensagens[-1].content)

    async def eco_async(req: RequisicaoLLM) -> RespostaLLM:
        return responder_texto(req.mensagens[-1].content.upper())

    falso = LLMFalso([eco, eco_async], nome="claude")
    assert (await falso.gerar(requisicao())).texto == "oi"
    resp = await falso.gerar(requisicao())
    assert (resp.texto, resp.provedor) == ("OI", "claude")


async def test_roteiro_esgotado_nao_e_erro_de_llm() -> None:
    falso = LLMFalso()
    with pytest.raises(RoteiroEsgotado, match="roteiro") as info:
        await falso.gerar(requisicao())
    assert not isinstance(info.value, ErroLLM)  # o roteador não pode engolir bug de teste como falha de provedor


async def test_roteirizar_acrescenta_itens_e_chamar_tools() -> None:
    falso = LLMFalso().roteirizar(chamar_tools(("a", {}), ChamadaTool("id-x", "b", {"k": 1}), texto="vou ver"))
    resp = await falso.gerar(requisicao())
    assert [c.nome for c in resp.chamadas_tool] == ["a", "b"]
    assert resp.chamadas_tool[1].id == "id-x"
    assert resp.texto == "vou ver"
    assert len({c.id for c in resp.chamadas_tool}) == 2


def test_falso_satisfaz_o_protocol() -> None:
    provedor: LLMProvider = LLMFalso()
    assert provedor.nome == "falso"

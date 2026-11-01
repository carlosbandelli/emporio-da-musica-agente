"""Testes de forma das portas e dos tipos de domínio (imutabilidade, valores e conformidade estrutural)."""

import dataclasses
from datetime import UTC, datetime

import pytest

from emporio.modulos.atendimento.dominio import Solicitacao, TipoSolicitacao
from emporio.modulos.catalogo.dominio import Produto, StatusProduto
from emporio.modulos.pedidos.dominio import FormaPagamento, MetodoPagamento, Pedido, StatusPedido
from emporio.portas import (
    ErroLLM,
    ErroLLMPermanente,
    ErroLLMTransitorio,
    FiltroProdutos,
    Idioma,
    MensagemCliente,
    MensagemLLM,
    MotorDeAtendimento,
    PapelMensagem,
    RespostaAtendimento,
    TenantContext,
)


def test_resposta_atendimento_padroes_e_frozen() -> None:
    r = RespostaAtendimento(texto="Oi!", idioma=Idioma.PT_BR)
    assert (r.nivel, r.provedor, r.tools_chamadas, r.degradada) == (None, None, (), False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.texto = "x"  # type: ignore[misc]


def test_erros_llm_hierarquia() -> None:
    erro = ErroLLMTransitorio("429", retry_after_s=2.0)
    assert isinstance(erro, ErroLLM)
    assert erro.retry_after_s == 2.0
    assert issubclass(ErroLLMPermanente, ErroLLM)
    assert not issubclass(ErroLLMPermanente, ErroLLMTransitorio)


def test_filtro_produtos_so_ativos_por_padrao() -> None:
    assert FiltroProdutos().incluir_inativos is False


def test_dominio_e_imutavel_e_enums_batem_com_csv() -> None:
    assert {s.value for s in StatusProduto} == {"active", "discontinued", "coming_soon"}
    assert {s.value for s in StatusPedido} == {"pending", "confirmed", "shipped", "delivered", "cancelled"}
    produto = Produto(1, "Violão", 1, "d", 99900, 3, StatusProduto.ATIVO, {"cor": "natural"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        produto.estoque = 0  # type: ignore[misc]
    pedido = Pedido(1, 3, StatusPedido.PENDENTE, MetodoPagamento(FormaPagamento.PIX), ())
    assert pedido.codigo_rastreio is None
    sol = Solicitacao("s1", TipoSolicitacao.OUTROS, "d", "sess", "k", datetime.now(UTC))
    assert sol.contato is None


class _MotorEco:
    async def responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        return RespostaAtendimento(texto=mensagem.texto, idioma=ctx.idioma)


async def test_motor_conforme_ao_protocol() -> None:
    motor: MotorDeAtendimento = _MotorEco()
    resp = await motor.responder(TenantContext("emporio"), "s", MensagemCliente("oi"))
    assert resp.texto == "oi"
    assert MensagemLLM(PapelMensagem.USER, "oi").tool_calls == ()

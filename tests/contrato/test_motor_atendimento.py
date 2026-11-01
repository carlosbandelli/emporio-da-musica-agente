"""Suíte de contrato da porta `MotorDeAtendimento` (invariantes I1-I8, arquitetura §3.2).

Cada teste roda uma vez por motor registrado (`fabricas.MOTORES`, via a fixture `motor_factory`). A lógica de cada
invariante está em `invariantes.py`, para o teste meta provar que ela detecta um motor que a viola.
"""

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import pytest

from emporio.adaptadores.llm import LLMFalso
from emporio.portas import (
    MensagemCliente,
    MensagemLLM,
    PapelMensagem,
    RequisicaoLLM,
    RespostaAtendimento,
    TenantContext,
)
from emporio.portas.tenant import ConfigTenant
from tests.contrato import invariantes
from tests.contrato.fabricas import MOTORES
from tests.contrato.kit import EspiaoTools, FabricaMotor, KitMotor


async def test_i1_texto_nao_vazio_e_mensagem_invalida_sem_llm(motor_factory: FabricaMotor, tmp_path: Path) -> None:
    await invariantes.verificar_i1(motor_factory, tmp_path)


async def test_i2_segunda_mensagem_enxerga_a_primeira(motor_factory: FabricaMotor, tmp_path: Path) -> None:
    await invariantes.verificar_i2(motor_factory, tmp_path)


async def test_i3_mesmo_sessao_id_em_tenants_diferentes_nao_se_mistura(
    motor_factory: FabricaMotor, tmp_path: Path
) -> None:
    await invariantes.verificar_i3(motor_factory, tmp_path)


async def test_i4_falha_de_llm_ou_backend_nao_lanca_e_degrada(motor_factory: FabricaMotor, tmp_path: Path) -> None:
    await invariantes.verificar_i4(motor_factory, tmp_path)


async def test_i5_respeita_o_deadline_do_tenant(motor_factory: FabricaMotor, tmp_path: Path) -> None:
    await invariantes.verificar_i5(motor_factory, tmp_path)


async def test_i6_preco_so_aparece_com_a_tool_chamada(motor_factory: FabricaMotor, tmp_path: Path) -> None:
    await invariantes.verificar_i6(motor_factory, tmp_path)


async def test_i7_responde_no_idioma_da_sessao(motor_factory: FabricaMotor, tmp_path: Path) -> None:
    await invariantes.verificar_i7(motor_factory, tmp_path)


async def test_i8_instancias_de_tenants_diferentes_nao_se_contaminam(
    motor_factory: FabricaMotor, tmp_path: Path
) -> None:
    await invariantes.verificar_i8(motor_factory, tmp_path)


def test_o_nativo_esta_registrado() -> None:
    """A suíte nunca fica vazia por engano: `nativo` é sempre exercitado."""
    assert "nativo" in MOTORES


# --- Prova de que a suíte detecta violações --------------------------------------------------------------------------
class MotorQuebrado:
    """Viola I4 (lança a exceção do LLM) e I6 (devolve o texto do LLM cru, preço inventado sem chamar tool)."""

    def __init__(self, kit: KitMotor) -> None:
        self._kit = kit

    async def responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        pedido = RequisicaoLLM("", "", (MensagemLLM(PapelMensagem.USER, mensagem.texto),))
        resposta = await self._kit.llm.gerar(pedido)  # falha do LLM propaga (I4)
        return RespostaAtendimento(texto=resposta.texto or "", idioma=ctx.idioma)  # sem guarda de valores (I6)


def fabrica_quebrada(tenants: Mapping[str, ConfigTenant], dir_estado: Path) -> KitMotor:
    """Fábrica do motor quebrado. Fica só neste arquivo: não entra em `MOTORES` (senão a suíte ficaria vermelha)."""
    kit = KitMotor(motor=None, llm=LLMFalso(), tools=EspiaoTools(None))  # type: ignore[arg-type]
    kit.motor = MotorQuebrado(kit)
    return kit


@pytest.mark.parametrize(
    ("verificar", "motivo"),
    [
        (invariantes.verificar_i4, "o motor lançou"),
        (invariantes.verificar_i6_sem_tool, "nenhuma tool devolveu"),
        (invariantes.verificar_i6_com_tool, "detalhar_produto"),
    ],
    ids=["i4", "i6-preco-inventado", "i6-sem-tool-chamada"],
)
async def test_meta_motor_quebrado_falha_em_i4_e_i6(
    verificar: Callable[[FabricaMotor, Path], Awaitable[None]], motivo: str, tmp_path: Path
) -> None:
    """Cada verificação falha pelo motivo certo (e não por acidente de montagem)."""
    with pytest.raises(AssertionError, match=motivo):
        await verificar(fabrica_quebrada, tmp_path)


async def test_meta_motor_quebrado_nao_esta_registrado() -> None:
    assert fabrica_quebrada not in MOTORES.values()

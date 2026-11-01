"""Registro de motores da suíte de contrato: `nativo` e `api` (o mesmo motor através do `POST /v1/chat`).

A Fase 2 registra `n8n` aqui (task 2.7)."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Final

from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.llm import LLMFalso
from emporio.adaptadores.memoria import RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.nativo import AgenteNativo
from emporio.adaptadores.motor.nativo.prompt import carregar_politicas
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.composicao import Aplicacao, montar_ferramentas
from emporio.portas.tenant import ConfigTenant
from tests.contrato.kit import DATA_REFERENCIA, RAIZ, EspiaoTools, FabricaMotor, KitMotor
from tests.contrato.motor_via_api import MotorViaApi
from tests.modulos.fakes import RelogioFixo


def fabrica_nativo(tenants: Mapping[str, ConfigTenant], dir_estado: Path) -> KitMotor:
    """`AgenteNativo` com `LLMFalso` único (o roteador não classifica com um só provedor), CSVs reais e espião de tools.

    Monta à mão, como `composicao.montar`, porque `montar` não aceita trocar a fachada de tools pelo espião.
    """
    llm = LLMFalso()
    relogio = RelogioFixo(DATA_REFERENCIA)
    espiao = EspiaoTools(montar_ferramentas(tenants, raiz=RAIZ, relogio=relogio, dir_estado=dir_estado))
    sessoes = SessaoStoreMemoria()
    motor = AgenteNativo(
        tenants=tenants,
        politicas={tid: carregar_politicas(RAIZ / c.politicas_arquivo) for tid, c in tenants.items()},
        roteador=Roteador({"falso": llm}),
        ferramentas=espiao,
        sessoes=sessoes,
        limitador=RateLimiterMemoria(tenants),
        relogio=relogio,
    )
    return KitMotor(motor=motor, llm=llm, tools=espiao, sessoes=sessoes)


def fabrica_api(tenants: Mapping[str, ConfigTenant], dir_estado: Path) -> KitMotor:
    """O `AgenteNativo` do kit atrás da API FastAPI; os testes conversam por `POST /v1/chat` (`MotorViaApi`).

    O LLM falso e o espião de tools continuam os do kit nativo, então roteiros e verificações são idênticos.
    """
    kit = fabrica_nativo(tenants, dir_estado)
    aplicacao = Aplicacao(
        motor=kit.motor,
        motor_ativo="nativo",
        tenants=tenants,
        tenant_padrao=next(iter(tenants)),
        provedores=("falso",),
    )
    return replace(kit, motor=MotorViaApi(criar_app(lambda: aplicacao)))


MOTORES: Final[Mapping[str, FabricaMotor]] = {
    "nativo": fabrica_nativo,
    "api": fabrica_api,
    # Fase 2 (task 2.7): "n8n": fabrica_n8n,
}
"""Motores registrados: nome → fábrica. Cada invariante roda uma vez por entrada."""

"""Registro de motores da suíte de contrato: `nativo`, `api` (o mesmo motor através do `POST /v1/chat`) e `n8n`
(`AgenteN8n` falando com um workflow simulado que chama as tools reais por HTTP)."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Final

import httpx
from pydantic import SecretStr

from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.identidade import JwtLocal
from emporio.adaptadores.llm import LLMFalso
from emporio.adaptadores.memoria import CircuitoStoreMemoria, RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.comum.prompt import carregar_politicas
from emporio.adaptadores.motor.n8n import AgenteN8n
from emporio.adaptadores.motor.nativo import AgenteNativo
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.composicao import Aplicacao, montar_ferramentas
from emporio.portas.tenant import ConfigTenant
from tests.contrato.kit import DATA_REFERENCIA, RAIZ, EspiaoTools, FabricaMotor, KitMotor
from tests.contrato.motor_via_api import MotorViaApi
from tests.contrato.n8n_simulado import N8nSimulado
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


SEGREDO_JWT: Final = "segredo-jwt-da-suite-de-contrato-com-mais-de-32-bytes"
SEGREDO_WEBHOOK: Final = "segredo-do-webhook-de-teste"
URL_WEBHOOK: Final = "http://n8n.teste/webhook/emporio-atendimento"
URL_API: Final = "http://testserver"


def fabrica_n8n(tenants: Mapping[str, ConfigTenant], dir_estado: Path) -> KitMotor:
    """`AgenteN8n` → webhook simulado (`httpx.MockTransport`) → `LLMFalso` do kit → `POST /v1/tools/{nome}` da API.

    A API é a app FastAPI real montada em memória com a fachada de tools envolvida pelo espião do kit e a identidade
    JWT que valida o token de serviço que o motor emite por turno. O espião registra as tools do lado servidor.
    """
    llm = LLMFalso()
    relogio = RelogioFixo(DATA_REFERENCIA)
    espiao = EspiaoTools(montar_ferramentas(tenants, raiz=RAIZ, relogio=relogio, dir_estado=dir_estado))
    identidade = JwtLocal(SecretStr(SEGREDO_JWT))
    sessoes = SessaoStoreMemoria()
    n8n = N8nSimulado(llm, segredo=SEGREDO_WEBHOOK)
    motor = AgenteN8n(
        webhook_url=URL_WEBHOOK,
        segredo_webhook=SEGREDO_WEBHOOK,
        url_tools=URL_API,
        tenants=tenants,
        politicas={tid: carregar_politicas(RAIZ / c.politicas_arquivo) for tid, c in tenants.items()},
        http=httpx.AsyncClient(transport=n8n.transporte()),
        identidade=identidade,
        circuito=CircuitoStoreMemoria(),
        sessoes=sessoes,
        limitador=RateLimiterMemoria(tenants),
        relogio=relogio,
    )
    aplicacao = Aplicacao(
        motor=motor,
        motor_ativo="n8n",
        tenants=tenants,
        tenant_padrao=next(iter(tenants)),
        provedores=("n8n",),
        identidade=identidade,
        ferramentas=espiao,
    )
    n8n.conectar_api(criar_app(lambda: aplicacao))
    return KitMotor(motor=motor, llm=llm, tools=espiao, sessoes=sessoes)


MOTORES: Final[Mapping[str, FabricaMotor]] = {
    "nativo": fabrica_nativo,
    "api": fabrica_api,
    "n8n": fabrica_n8n,
}
"""Motores registrados: nome → fábrica. Cada invariante roda uma vez por entrada."""

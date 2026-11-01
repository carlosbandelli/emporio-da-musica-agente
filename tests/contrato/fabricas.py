"""Registro de motores da suíte de contrato. Hoje só `nativo`; a Fase 2 registra `n8n` aqui."""

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from emporio.adaptadores.llm import LLMFalso
from emporio.adaptadores.memoria import RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.nativo import AgenteNativo
from emporio.adaptadores.motor.nativo.prompt import carregar_politicas
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.composicao import montar_ferramentas
from emporio.portas.tenant import ConfigTenant
from tests.contrato.kit import DATA_REFERENCIA, RAIZ, EspiaoTools, FabricaMotor, KitMotor
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


MOTORES: Final[Mapping[str, FabricaMotor]] = {
    "nativo": fabrica_nativo,
    # Fase 2 (task 2.7): "n8n": fabrica_n8n,
}
"""Motores registrados: nome → fábrica. Cada invariante roda uma vez por entrada."""

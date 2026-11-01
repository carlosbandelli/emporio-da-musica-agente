"""Fixture `motor_factory`: parametriza todo teste de contrato pelos motores registrados."""

import pytest

from tests.contrato.fabricas import MOTORES
from tests.contrato.kit import FabricaMotor


@pytest.fixture(params=sorted(MOTORES), ids=lambda nome: f"motor={nome}")
def motor_factory(request: pytest.FixtureRequest) -> FabricaMotor:
    """Fábrica de um dos motores registrados em `fabricas.MOTORES` (uma execução por motor)."""
    fabrica: FabricaMotor = MOTORES[request.param]
    return fabrica

"""Fixture `kit`: parametriza todo teste de contrato de pagamento pelos gateways registrados."""

import pytest

from tests.contrato.pagamento.fabricas import GATEWAYS
from tests.contrato.pagamento.kit import KitGateway


@pytest.fixture(params=sorted(GATEWAYS), ids=lambda nome: f"gateway={nome}")
def kit(request: pytest.FixtureRequest) -> KitGateway:
    """Gateway novo (sem estado compartilhado) de um dos registrados em `fabricas.GATEWAYS`."""
    return GATEWAYS[request.param]()


@pytest.fixture
def kit_com_falhas(kit: KitGateway) -> KitGateway:
    """Como `kit`, mas pula o gateway que não permite injetar falhas (ex.: sandbox real)."""
    if not kit.controle.suporta_falhas:
        pytest.skip("gateway sem injeção de falhas")
    return kit

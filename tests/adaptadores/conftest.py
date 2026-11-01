from collections.abc import Mapping
from pathlib import Path

import pytest

from emporio.configuracao.tenants import carregar_tenants
from emporio.portas.tenant import ConfigTenant

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def tenants() -> Mapping[str, ConfigTenant]:
    return carregar_tenants(RAIZ / "config" / "tenants.yaml")

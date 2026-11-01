"""Configuração: Settings (ambiente) e carregador de tenants (YAML). Lido só pelo composition root."""

from emporio.configuracao.settings import PRESETS_BASE_URL, ConfigLLMCompativel, Settings
from emporio.configuracao.tenants import ErroConfiguracao, carregar_tenant, carregar_tenants, obter_tenant

__all__ = [
    "PRESETS_BASE_URL",
    "ConfigLLMCompativel",
    "ErroConfiguracao",
    "Settings",
    "carregar_tenant",
    "carregar_tenants",
    "obter_tenant",
]

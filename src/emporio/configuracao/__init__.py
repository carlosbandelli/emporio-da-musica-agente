"""Configuração: Settings (ambiente) e carregador de tenants (YAML). Lido só pelo composition root."""

from emporio.configuracao.settings import Settings
from emporio.configuracao.tenants import ErroConfiguracao, carregar_tenant, carregar_tenants, obter_tenant

__all__ = ["ErroConfiguracao", "Settings", "carregar_tenant", "carregar_tenants", "obter_tenant"]

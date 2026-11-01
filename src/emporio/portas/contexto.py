"""Contexto de requisição: tenant, idioma e papel de quem fala."""

from dataclasses import dataclass
from enum import StrEnum


class Idioma(StrEnum):
    """Idiomas do atendimento (arquitetura §10)."""

    PT_BR = "pt-BR"
    EN_US = "en-US"


class Papel(StrEnum):
    """Papéis de RBAC (arquitetura §8). `servico_tools` é a credencial de máquina do n8n: só `tools:usar`."""

    CLIENTE = "cliente"
    ATENDENTE = "atendente"
    GERENTE = "gerente"
    ADMIN_PLATAFORMA = "admin_plataforma"
    SERVICO_TOOLS = "servico_tools"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Contexto imutável criado por requisição e passado a toda porta que toca dados ou estado."""

    tenant_id: str
    idioma: Idioma = Idioma.PT_BR
    papel: Papel = Papel.CLIENTE

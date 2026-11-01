"""Carregador de `config/tenants.yaml`: função pura (caminho entra, modelos saem), sem cache."""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from emporio.portas.tenant import ConfigTenant


class ErroConfiguracao(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Configuração ausente, inválida ou tenant inexistente."""


def carregar_tenants(caminho: Path) -> Mapping[str, ConfigTenant]:
    """Lê o YAML e valida cada tenant. Erros citam o arquivo e o caminho do campo (ex.: `emporio.limites.sessao`)."""
    try:
        texto = caminho.read_text(encoding="utf-8")
    except OSError as exc:
        raise ErroConfiguracao(f"Não foi possível ler {caminho}: {exc.strerror or exc}") from exc
    try:
        bruto = yaml.safe_load(texto)
    except yaml.YAMLError as exc:
        raise ErroConfiguracao(f"YAML inválido em {caminho}: {exc}") from exc
    if not isinstance(bruto, dict) or not isinstance(bruto.get("tenants"), dict) or not bruto["tenants"]:
        raise ErroConfiguracao(f"{caminho}: a chave 'tenants' deve ser um mapa com ao menos um tenant")

    tenants: dict[str, ConfigTenant] = {}
    for tenant_id, dados in bruto["tenants"].items():
        try:
            tenants[str(tenant_id)] = ConfigTenant.model_validate(dados)
        except ValidationError as exc:
            detalhes = "; ".join(f"{_campo(str(tenant_id), erro['loc'])}: {erro['msg']}" for erro in exc.errors())
            raise ErroConfiguracao(f"Configuração inválida em {caminho}: {detalhes}") from exc
    return tenants


def obter_tenant(tenants: Mapping[str, ConfigTenant], tenant_id: str) -> ConfigTenant:
    """Devolve o tenant ou levanta erro claro listando os disponíveis."""
    try:
        return tenants[tenant_id]
    except KeyError:
        disponiveis = ", ".join(sorted(tenants)) or "(nenhum)"
        raise ErroConfiguracao(f"Tenant '{tenant_id}' não existe. Disponíveis: {disponiveis}") from None


def carregar_tenant(caminho: Path, tenant_id: str) -> ConfigTenant:
    """Atalho: carrega o arquivo e devolve um tenant."""
    return obter_tenant(carregar_tenants(caminho), tenant_id)


def _campo(tenant_id: str, loc: tuple[int | str, ...]) -> str:
    return ".".join([tenant_id, *(str(parte) for parte in loc)])

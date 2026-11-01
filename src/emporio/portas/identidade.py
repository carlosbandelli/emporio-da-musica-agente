"""Identidade e RBAC (arquitetura §8): porta `ProvedorIdentidade`, matriz papel × permissão e a decisão de acesso.

Tudo aqui é dado puro ou função pura (sem I/O nem SDK), testável sem FastAPI nem JWT.

Regras de tenant (dupla checagem):

- Permissões de **escopo de tenant** (chat, solicitações, config, tools) exigem `identidade.tenant_id` igual ao tenant
  resolvido da requisição (header `X-Tenant-ID` ou subdomínio). Diferente → `TENANT_DIVERGENTE` (403). Vale para todos
  os papéis, inclusive `admin_plataforma`: não há curinga de tenant.
- Permissões de **escopo de plataforma** (`tenants:criar`, `metricas:ler`) não olham o tenant da requisição.
- `admin_plataforma` só tem as permissões de plataforma (§8: "criar tenants, métricas de todos os tenants"). Não lê
  solicitações nem edita a config de uma loja (menor privilégio: solicitações têm dados pessoais de clientes).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from emporio.portas.contexto import Papel


class Permissao(StrEnum):
    """Permissões finas (nomes da arquitetura §8, mais `chat:usar` e `metricas:ler`)."""

    CHAT_USAR = "chat:usar"
    SOLICITACOES_LER = "solicitacoes:ler"
    SOLICITACOES_RESOLVER = "solicitacoes:resolver"
    CONFIG_EDITAR = "config:editar"
    TOOLS_USAR = "tools:usar"
    TENANTS_CRIAR = "tenants:criar"
    METRICAS_LER = "metricas:ler"


_ATENDENTE: Final = frozenset(
    {
        Permissao.CHAT_USAR,
        Permissao.SOLICITACOES_LER,
        Permissao.SOLICITACOES_RESOLVER,
        Permissao.TOOLS_USAR,
    }
)

PERMISSOES_POR_PAPEL: Final[Mapping[Papel, frozenset[Permissao]]] = MappingProxyType(
    {
        Papel.CLIENTE: frozenset({Permissao.CHAT_USAR}),
        Papel.ATENDENTE: _ATENDENTE,
        Papel.GERENTE: _ATENDENTE | {Permissao.CONFIG_EDITAR},
        Papel.ADMIN_PLATAFORMA: frozenset({Permissao.TENANTS_CRIAR, Permissao.METRICAS_LER}),
        Papel.SERVICO_TOOLS: frozenset({Permissao.TOOLS_USAR}),
    }
)
"""Matriz da §8. `gerente` = tudo do `atendente` + `config:editar`. `tools:usar` fica com a equipe da loja e
com `servico_tools`, a credencial de máquina do n8n (só `tools:usar`, sem acesso a solicitações com dados pessoais)."""

PERMISSOES_DE_PLATAFORMA: Final = frozenset({Permissao.TENANTS_CRIAR, Permissao.METRICAS_LER})
"""Permissões que não são de um tenant: dispensam a checagem `tenant do token == tenant da requisição`."""

PAPEIS_DE_TENANT: Final = (Papel.SERVICO_TOOLS, Papel.CLIENTE, Papel.ATENDENTE, Papel.GERENTE)
"""Papéis de quem atua dentro de uma loja, do menor para o maior privilégio."""


@dataclass(frozen=True, slots=True)
class Identidade:
    """Quem fez a requisição, já autenticado (claims `sub`, `tenant_id`, `roles` do token)."""

    sub: str
    tenant_id: str
    papeis: frozenset[Papel]


class ErroIdentidade(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Token inválido (assinatura, formato, claims) ou expirado. A mensagem nunca contém o token."""


class ErroTokenExpirado(ErroIdentidade):
    """`exp` já passou (além da tolerância de relógio)."""


class ProvedorIdentidade(Protocol):
    """Emite e valida credenciais. Hoje JWT local (HS256); evolução: Keycloak/Auth0/Cognito (arquitetura §3)."""

    def emitir(self, *, sub: str, tenant_id: str, papeis: Iterable[Papel], validade_s: int) -> str:
        """Token assinado com `sub`, `tenant_id`, `roles`, `iat` e `exp`."""
        ...

    def validar(self, token: str) -> Identidade:
        """Identidade do token; levanta `ErroIdentidade` (ou `ErroTokenExpirado`)."""
        ...


class Decisao(StrEnum):
    """Resultado de `autorizar`. A API mapeia: 401 para `NAO_AUTENTICADO`; 403 para os dois negados."""

    PERMITIDO = "permitido"
    NAO_AUTENTICADO = "nao_autenticado"
    SEM_PERMISSAO = "sem_permissao"
    TENANT_DIVERGENTE = "tenant_divergente"


def permissoes(papeis: Iterable[Papel]) -> frozenset[Permissao]:
    """União das permissões dos papéis."""
    total: frozenset[Permissao] = frozenset()
    for papel in papeis:
        total |= PERMISSOES_POR_PAPEL[papel]
    return total


def autorizar(identidade: Identidade | None, permissao: Permissao, tenant_requisicao: str) -> Decisao:
    """Decide o acesso: autenticado → tem a permissão → (se de tenant) o tenant do token é o da requisição."""
    if identidade is None:
        return Decisao.NAO_AUTENTICADO
    if permissao not in permissoes(identidade.papeis):
        return Decisao.SEM_PERMISSAO
    if permissao not in PERMISSOES_DE_PLATAFORMA and identidade.tenant_id != tenant_requisicao:
        return Decisao.TENANT_DIVERGENTE
    return Decisao.PERMITIDO


def papel_no_tenant(papeis: Iterable[Papel]) -> Papel:
    """Maior papel de loja entre os do token (o que vai no `TenantContext` do chat); sem nenhum → `cliente`."""
    presentes = frozenset(papeis)
    escolhido = Papel.CLIENTE
    for papel in PAPEIS_DE_TENANT:
        if papel in presentes:
            escolhido = papel
    return escolhido

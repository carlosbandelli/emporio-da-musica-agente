"""Portas de estado: sessão, rate limit, estado do circuito e relógio."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from emporio.portas.contexto import Idioma, TenantContext
from emporio.portas.llm import MensagemLLM


@dataclass(frozen=True, slots=True)
class EstadoSessao:
    """Estado de uma conversa. `idioma` é `None` até ser detectado/definido."""

    historico: tuple[MensagemLLM, ...] = ()
    idioma: Idioma | None = None


class SessaoStore(Protocol):
    """Guarda o estado da conversa, isolado por tenant + sessão."""

    async def obter(self, ctx: TenantContext, sessao_id: str) -> EstadoSessao | None:
        """Estado da sessão, ou `None` se não existe ou expirou."""
        ...

    async def salvar(self, ctx: TenantContext, sessao_id: str, estado: EstadoSessao, ttl_s: int) -> None:
        """Grava o estado com expiração em `ttl_s` segundos."""
        ...

    async def apagar(self, ctx: TenantContext, sessao_id: str) -> None:
        """Remove a sessão (não falha se não existir)."""
        ...


@dataclass(frozen=True, slots=True)
class ResultadoLimite:
    """Resultado do consumo de um token. `retry_after_s` só quando negado."""

    permitido: bool
    retry_after_s: float | None = None


class RateLimiter(Protocol):
    """Token bucket por camada (sessao, usuario, tenant, provedor; arquitetura §5.1)."""

    async def consumir(self, ctx: TenantContext, camada: str, chave: str) -> ResultadoLimite:
        """Tenta consumir 1 token do balde `(tenant, camada, chave)`."""
        ...


class EstadoCircuito(StrEnum):
    """Estados do circuit breaker."""

    FECHADO = "fechado"
    MEIO_ABERTO = "meio_aberto"
    ABERTO = "aberto"


class CircuitoStore(Protocol):
    """Guarda o estado do circuito por provedor. A política (limiares, tempos) fica no adapter de LLM (1.8)."""

    async def estado(self, provedor: str) -> EstadoCircuito:
        """Estado atual do circuito do provedor."""
        ...

    async def registrar_sucesso(self, provedor: str) -> None:
        """Registra uma chamada bem-sucedida."""
        ...

    async def registrar_falha(self, provedor: str, *, permanente: bool) -> None:
        """Registra uma falha; `permanente=True` (401/403) abre o circuito na hora."""
        ...


class Relogio(Protocol):
    """Fonte de tempo injetável (permite simular `DATA_REFERENCIA`)."""

    def agora(self) -> datetime:
        """Instante atual, sempre com fuso (timezone-aware)."""
        ...

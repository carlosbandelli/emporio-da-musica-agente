"""Porta de telemetria (arquitetura §7): métricas e traces como eventos semânticos, sem expor Prometheus nem OTel.

Motor, LLM e API chamam estes métodos; o adapter `observabilidade` os traduz em métricas/spans. A implementação
nula (`TelemetriaNula`) é o padrão dos testes e do modo local. Regra de privacidade: nenhum método recebe texto de
mensagem, `session_id` ou dado pessoal; só identificadores de baixa cardinalidade (tenant, provedor, nível, tool).
Ponto de extensão do banco: o adapter de Postgres pode abrir `span("db.consulta", tabela=...)` por esta porta.
"""

from contextlib import AbstractContextManager, nullcontext
from typing import Protocol

from emporio.portas.estado import EstadoCircuito
from emporio.portas.llm import UsoTokens

AtributoSpan = str | int | float | bool

CONTENT_TYPE_TEXTO = "text/plain; charset=utf-8"


class Telemetria(Protocol):
    """Contrato de emissão de telemetria. Nunca deve lançar nem bloquear."""

    def http_requisicao(self, tenant_id: str, rota: str, metodo: str, status: int, duracao_s: float) -> None:
        """Uma requisição HTTP concluída; `rota` é o template (ex.: `/v1/chat`)."""
        ...

    def rate_limit_negado(self, tenant_id: str, camada: str) -> None:
        """429 da app por camada (`sessao`, `usuario`, `tenant`, `llm`)."""
        ...

    def roteamento(self, tenant_id: str, nivel: str) -> None:
        """Mensagem classificada em `simples`, `media` ou `complexa`."""
        ...

    def llm_chamada(
        self,
        tenant_id: str,
        provedor: str,
        nivel: str,
        resultado: str,
        *,
        duracao_s: float,
        uso: UsoTokens | None = None,
    ) -> None:
        """Chamada ao LLM: `resultado` em `ok`, `transitorio`, `permanente`, `circuito_aberto`."""
        ...

    def llm_fallback(self, tenant_id: str, de: str, para: str) -> None:
        """Troca de provedor pela cadeia de fallback."""
        ...

    def circuito_estado(self, provedor: str, estado: EstadoCircuito) -> None:
        """Estado observado do circuit breaker do provedor."""
        ...

    def prompt_cache(self, tenant_id: str, provedor: str, hit: bool) -> None:
        """Cache do prompt (CAG): acerto ou perda."""
        ...

    def tool_executada(self, tenant_id: str, tool: str, ok: bool, duracao_s: float) -> None:
        """Uma execução de tool."""
        ...

    def span(self, nome: str, **atributos: AtributoSpan) -> AbstractContextManager[None]:
        """Abre um span filho do atual (ou raiz). Atributo `tenant_id` também entra no contexto dos logs."""
        ...

    def exportar_metricas(self) -> tuple[bytes, str]:
        """Corpo e content-type do `GET /metrics`. Vazio na implementação nula."""
        ...


class TelemetriaNula:
    """Não faz nada. Padrão quando nenhuma telemetria é injetada."""

    def http_requisicao(self, tenant_id: str, rota: str, metodo: str, status: int, duracao_s: float) -> None:
        return None

    def rate_limit_negado(self, tenant_id: str, camada: str) -> None:
        return None

    def roteamento(self, tenant_id: str, nivel: str) -> None:
        return None

    def llm_chamada(
        self,
        tenant_id: str,
        provedor: str,
        nivel: str,
        resultado: str,
        *,
        duracao_s: float,
        uso: UsoTokens | None = None,
    ) -> None:
        return None

    def llm_fallback(self, tenant_id: str, de: str, para: str) -> None:
        return None

    def circuito_estado(self, provedor: str, estado: EstadoCircuito) -> None:
        return None

    def prompt_cache(self, tenant_id: str, provedor: str, hit: bool) -> None:
        return None

    def tool_executada(self, tenant_id: str, tool: str, ok: bool, duracao_s: float) -> None:
        return None

    def span(self, nome: str, **atributos: AtributoSpan) -> AbstractContextManager[None]:
        return nullcontext()

    def exportar_metricas(self) -> tuple[bytes, str]:
        return b"", CONTENT_TYPE_TEXTO

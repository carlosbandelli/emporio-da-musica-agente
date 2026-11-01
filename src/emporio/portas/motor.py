"""Porta `MotorDeAtendimento` (arquitetura §3.2) e diagnóstico para a CLI."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from emporio.portas.contexto import Idioma, TenantContext


@dataclass(frozen=True, slots=True)
class MensagemCliente:
    """Mensagem de texto recebida do cliente."""

    texto: str


@dataclass(frozen=True, slots=True)
class RespostaAtendimento:
    """Resposta do motor.

    `nivel` e `provedor` são `None` se nenhum LLM foi chamado (ou o motor não usa o roteador).
    `degradada` indica mensagem fixa por falha, limite ou deadline.
    """

    texto: str
    idioma: Idioma
    nivel: str | None = None
    provedor: str | None = None
    tools_chamadas: tuple[str, ...] = ()
    degradada: bool = False


class MotorDeAtendimento(Protocol):
    """Única porta que os adapters de entrada usam para conversar (invariantes I1-I8 na arquitetura §3.2)."""

    async def responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        """Responde a uma mensagem do cliente na sessão informada."""
        ...


@dataclass(frozen=True, slots=True)
class DiagnosticoInfo:
    """Estado do sistema para o comando `diagnostico`. Só nomes: nunca valores de chave."""

    provedores_com_chave: tuple[str, ...]
    motor_ativo: str
    tenant_id: str
    dados_carregados: bool
    data_referencia: datetime | None = None
    avisos: tuple[str, ...] = ()


class Diagnostico(Protocol):
    """Porta de leitura do estado do sistema, injetada na CLI (e na API, na Fase 2)."""

    async def diagnosticar(self, ctx: TenantContext) -> DiagnosticoInfo:
        """Devolve o diagnóstico para o tenant."""
        ...

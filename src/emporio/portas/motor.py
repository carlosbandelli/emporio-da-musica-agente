"""Porta `MotorDeAtendimento` (arquitetura §3.2) e diagnóstico para a CLI."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from emporio.portas.contexto import Idioma, TenantContext


@dataclass(frozen=True, slots=True)
class MensagemCliente:
    """Mensagem de texto recebida do cliente."""

    texto: str


class CodigoResposta(StrEnum):
    """Por que o motor devolveu uma mensagem fixa (`None` em `RespostaAtendimento.codigo` = resposta normal).

    Existe para os adapters de entrada distinguirem situações sem ler o texto (ex.: a API mapeia `LIMITE` em 429).
    """

    LIMITE = "limite"
    """Rate limit negou a mensagem; `retry_after_s` diz quando tentar de novo."""
    DEADLINE = "deadline"
    INDISPONIVEL = "indisponivel"
    """Nenhum provedor de LLM utilizável agora."""
    ENTRADA_INVALIDA = "entrada_invalida"
    """Mensagem vazia ou acima do tamanho máximo (não é degradada: o cliente é que precisa reenviar)."""
    ERRO = "erro"
    """Qualquer outra falha (tenant desconhecido, exceção, guarda de valores, limite de iterações)."""


@dataclass(frozen=True, slots=True)
class RespostaAtendimento:
    """Resposta do motor.

    `nivel` e `provedor` são `None` se nenhum LLM foi chamado (ou o motor não usa o roteador).
    `degradada` indica mensagem fixa por falha, limite ou deadline. `codigo` e `retry_after_s` são aditivos
    (padrão `None`): `codigo` classifica a mensagem fixa; `retry_after_s` só vem com `LIMITE`.
    """

    texto: str
    idioma: Idioma
    nivel: str | None = None
    provedor: str | None = None
    tools_chamadas: tuple[str, ...] = ()
    degradada: bool = False
    codigo: CodigoResposta | None = None
    retry_after_s: float | None = None


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

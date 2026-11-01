"""Regras de domínio do atendimento: Solicitacao e o ciclo de vida do seu status."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class TipoSolicitacao(StrEnum):
    """Tipos de solicitação que o agente encaminha para a equipe."""

    RECLAMACAO = "reclamacao"
    COTACAO_FRETE = "cotacao_frete"
    OUTROS = "outros"


class StatusSolicitacao(StrEnum):
    """Estado de atendimento de uma solicitação pela equipe."""

    ABERTA = "aberta"
    EM_ATENDIMENTO = "em_atendimento"
    RESOLVIDA = "resolvida"


TRANSICOES: Final[Mapping[StatusSolicitacao, frozenset[StatusSolicitacao]]] = MappingProxyType(
    {
        StatusSolicitacao.ABERTA: frozenset({StatusSolicitacao.EM_ATENDIMENTO, StatusSolicitacao.RESOLVIDA}),
        StatusSolicitacao.EM_ATENDIMENTO: frozenset({StatusSolicitacao.ABERTA, StatusSolicitacao.RESOLVIDA}),
        StatusSolicitacao.RESOLVIDA: frozenset({StatusSolicitacao.EM_ATENDIMENTO}),
    }
)
"""Transições permitidas (além de manter o mesmo status, que é idempotente). Resolvida só volta reaberta para
`em_atendimento` (alguém assume de novo); nunca volta direto para a fila (`aberta`)."""


class ErroTransicaoStatus(ValueError):  # noqa: N818 - nome em pt-BR do projeto
    """Mudança de status fora de `TRANSICOES`."""

    def __init__(self, atual: StatusSolicitacao, novo: StatusSolicitacao) -> None:
        super().__init__(f"transição de status inválida: {atual.value} -> {novo.value}")
        self.atual = atual
        self.novo = novo


def transicao_permitida(atual: StatusSolicitacao, novo: StatusSolicitacao) -> bool:
    """`True` se a solicitação pode ir de `atual` para `novo` (mesmo status é permitido: idempotente)."""
    return novo == atual or novo in TRANSICOES[atual]


def origens_permitidas(novo: StatusSolicitacao) -> frozenset[StatusSolicitacao]:
    """Status de onde se pode chegar a `novo` (inclui o próprio). Útil para um `UPDATE ... WHERE status IN (...)`."""
    return frozenset(s for s in StatusSolicitacao if transicao_permitida(s, novo))


@dataclass(frozen=True, slots=True)
class Solicitacao:
    """Pedido registrado para a equipe. `chave_idempotencia` evita duplicar em retries.

    `status`, `atualizada_em` e `atualizada_por` (o `sub` de quem mudou o status) formam a trilha mínima de auditoria.
    """

    id: str
    tipo: TipoSolicitacao
    descricao: str
    sessao_id: str
    chave_idempotencia: str
    criada_em: datetime
    contato: str | None = None
    status: StatusSolicitacao = StatusSolicitacao.ABERTA
    atualizada_em: datetime | None = None
    atualizada_por: str | None = None

    def com_status(self, novo: StatusSolicitacao, *, por: str, em: datetime) -> "Solicitacao":
        """Cópia com o novo status; levanta `ErroTransicaoStatus` se a transição não é permitida."""
        if not transicao_permitida(self.status, novo):
            raise ErroTransicaoStatus(self.status, novo)
        return Solicitacao(
            id=self.id,
            tipo=self.tipo,
            descricao=self.descricao,
            sessao_id=self.sessao_id,
            chave_idempotencia=self.chave_idempotencia,
            criada_em=self.criada_em,
            contato=self.contato,
            status=novo,
            atualizada_em=em,
            atualizada_por=por,
        )

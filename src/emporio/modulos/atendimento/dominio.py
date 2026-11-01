"""Regras de domínio do atendimento: Solicitacao.

Por enquanto só a forma dos dados (sem comportamento).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TipoSolicitacao(StrEnum):
    """Tipos de solicitação que o agente encaminha para a equipe."""

    RECLAMACAO = "reclamacao"
    COTACAO_FRETE = "cotacao_frete"
    OUTROS = "outros"


@dataclass(frozen=True, slots=True)
class Solicitacao:
    """Pedido registrado para a equipe. `chave_idempotencia` evita duplicar em retries."""

    id: str
    tipo: TipoSolicitacao
    descricao: str
    sessao_id: str
    chave_idempotencia: str
    criada_em: datetime
    contato: str | None = None

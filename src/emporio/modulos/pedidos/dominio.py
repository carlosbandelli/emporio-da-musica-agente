"""Regras de domínio de pedidos: Pedido, StatusPedido e verificação do cliente.

Por enquanto só formas de dados (sem comportamento); a verificação de identidade vem na tarefa 1.6.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class StatusPedido(StrEnum):
    """Situação do pedido (coluna `status` do CSV)."""

    PENDENTE = "pending"
    CONFIRMADO = "confirmed"
    ENVIADO = "shipped"
    ENTREGUE = "delivered"
    CANCELADO = "cancelled"


class FormaPagamento(StrEnum):
    """Forma de pagamento já normalizada (`credit_Nx` vira `CREDITO` com N parcelas)."""

    PIX = "pix"
    DEBITO = "debito"
    BOLETO = "boleto"
    CREDITO = "credito"


@dataclass(frozen=True, slots=True)
class MetodoPagamento:
    """Forma de pagamento e número de parcelas (1 quando não parcelado)."""

    forma: FormaPagamento
    parcelas: int = 1


@dataclass(frozen=True, slots=True)
class ItemPedido:
    """Item do pedido. O CSV não traz preço unitário."""

    produto_id: int
    quantidade: int


@dataclass(frozen=True, slots=True)
class Pedido:
    """Pedido com seus itens. Rastreio e previsão existem só depois do envio."""

    numero: int
    cliente_id: int
    status: StatusPedido
    pagamento: MetodoPagamento
    itens: tuple[ItemPedido, ...]
    total_centavos: int | None = None
    data_pedido: date | None = None
    codigo_rastreio: str | None = None
    previsao_entrega: date | None = None
    observacoes: str = ""


@dataclass(frozen=True, slots=True)
class Cliente:
    """Cliente (dados pessoais, LGPD): nunca exibir sem verificação de identidade."""

    id: int
    nome: str
    telefone: str
    email: str
    cidade: str

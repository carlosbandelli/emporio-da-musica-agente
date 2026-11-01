"""Regras de domínio do catálogo: Produto, Categoria e status de produto.

Por enquanto só formas de dados (sem comportamento). Dinheiro é inteiro em centavos até `Dinheiro` chegar em
`modulos.comum` (tarefa 1.3).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class StatusProduto(StrEnum):
    """Situação de venda do produto (coluna `status` do CSV)."""

    ATIVO = "active"
    DESCONTINUADO = "discontinued"
    EM_BREVE = "coming_soon"


@dataclass(frozen=True, slots=True)
class Categoria:
    """Categoria do catálogo (algumas não têm produtos)."""

    id: int
    nome: str
    descricao: str = ""


@dataclass(frozen=True, slots=True)
class Produto:
    """Produto do catálogo. `preco_centavos` é o preço de tabela, em centavos de real."""

    id: int
    nome: str
    categoria_id: int
    descricao: str
    preco_centavos: int
    estoque: int
    status: StatusProduto
    specs: Mapping[str, object]
    criado_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class Promocao:
    """Registro de promoção. Só as com `ativa=True` valem; promoções não acumulam."""

    produto_id: int
    desconto_percentual: int
    descricao: str
    ativa: bool

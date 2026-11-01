"""Portas de dados: catálogo, pedidos, clientes e solicitações. Todo método recebe o `TenantContext` primeiro."""

from dataclasses import dataclass
from typing import Protocol

from emporio.modulos.atendimento.dominio import Solicitacao
from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao
from emporio.modulos.pedidos.dominio import Cliente, Pedido
from emporio.portas.contexto import TenantContext


@dataclass(frozen=True, slots=True)
class FiltroProdutos:
    """Filtros de busca de produtos. Por padrão só produtos ativos."""

    texto: str | None = None
    categoria_id: int | None = None
    preco_min_centavos: int | None = None
    preco_max_centavos: int | None = None
    incluir_inativos: bool = False


class Catalogo(Protocol):
    """Leitura do catálogo e das promoções."""

    async def listar_categorias(self, ctx: TenantContext) -> tuple[Categoria, ...]:
        """Todas as categorias, inclusive as sem produtos."""
        ...

    async def listar_produtos(self, ctx: TenantContext, filtro: FiltroProdutos) -> tuple[Produto, ...]:
        """Produtos que atendem ao filtro."""
        ...

    async def obter_produto(self, ctx: TenantContext, produto_id: int) -> Produto | None:
        """Produto pelo id, ou `None`."""
        ...

    async def listar_promocoes(self, ctx: TenantContext, produto_id: int) -> tuple[Promocao, ...]:
        """Todos os registros de promoção do produto (ativos ou não)."""
        ...


class Pedidos(Protocol):
    """Leitura de pedidos."""

    async def obter_pedido(self, ctx: TenantContext, numero: int) -> Pedido | None:
        """Pedido pelo número, ou `None`. A verificação de identidade é feita pelo caso de uso."""
        ...


class Clientes(Protocol):
    """Leitura de clientes (dados pessoais)."""

    async def obter_cliente(self, ctx: TenantContext, cliente_id: int) -> Cliente | None:
        """Cliente pelo id, ou `None`."""
        ...


class Solicitacoes(Protocol):
    """Registro de solicitações para a equipe."""

    async def registrar(self, ctx: TenantContext, solicitacao: Solicitacao) -> Solicitacao:
        """Grava a solicitação; idempotente por `chave_idempotencia` (devolve a já existente)."""
        ...

    async def listar(self, ctx: TenantContext) -> tuple[Solicitacao, ...]:
        """Solicitações registradas do tenant."""
        ...

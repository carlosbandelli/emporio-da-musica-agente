"""`CatalogoCsv`, `PedidosCsv` e `ClientesCsv`: portas de leitura sobre os CSVs do tenant."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from emporio.adaptadores.csv import _leitura
from emporio.adaptadores.csv._base import CargaPorTenant, ResolvedorDados
from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao, StatusProduto
from emporio.modulos.pedidos.dominio import Cliente, Pedido
from emporio.portas.contexto import TenantContext
from emporio.portas.repositorios import FiltroProdutos
from emporio.portas.tenant import ConfigTenant


@dataclass(frozen=True, slots=True)
class _DadosCatalogo:
    categorias: tuple[Categoria, ...]
    produtos: tuple[Produto, ...]
    por_id: Mapping[int, Produto]
    promocoes: Mapping[int, tuple[Promocao, ...]]
    texto: Mapping[int, str]  # texto normalizado (nome + descrição) para busca


def _carregar_catalogo(dados_dir: Path) -> _DadosCatalogo:
    produtos = _leitura.ler_produtos(dados_dir)
    promocoes: dict[int, list[Promocao]] = defaultdict(list)
    for promocao in _leitura.ler_promocoes(dados_dir):
        promocoes[promocao.produto_id].append(promocao)
    return _DadosCatalogo(
        categorias=_leitura.ler_categorias(dados_dir),
        produtos=produtos,
        por_id={p.id: p for p in produtos},
        promocoes={k: tuple(v) for k, v in promocoes.items()},
        texto={p.id: _leitura.normalizar_texto(f"{p.nome} {p.descricao}") for p in produtos},
    )


class CatalogoCsv:
    """Implementa `Catalogo`."""

    def __init__(self, tenants: Mapping[str, ConfigTenant], raiz: Path | None = None) -> None:
        self._carga = CargaPorTenant(ResolvedorDados(tenants, raiz), _carregar_catalogo)

    async def listar_categorias(self, ctx: TenantContext) -> tuple[Categoria, ...]:
        """Todas as categorias, inclusive as sem produtos."""
        return (await self._carga.obter(ctx.tenant_id)).categorias

    async def listar_produtos(self, ctx: TenantContext, filtro: FiltroProdutos) -> tuple[Produto, ...]:
        """Produtos do filtro: texto (todos os termos, sem acento/caixa), categoria, faixa de preço e status."""
        dados = await self._carga.obter(ctx.tenant_id)
        termos = _leitura.normalizar_texto(filtro.texto).split() if filtro.texto else []
        resultado = []
        for p in dados.produtos:
            if not filtro.incluir_inativos and p.status is not StatusProduto.ATIVO:
                continue
            if filtro.categoria_id is not None and p.categoria_id != filtro.categoria_id:
                continue
            if filtro.preco_min_centavos is not None and p.preco_centavos < filtro.preco_min_centavos:
                continue
            if filtro.preco_max_centavos is not None and p.preco_centavos > filtro.preco_max_centavos:
                continue
            if termos and not all(t in dados.texto[p.id] for t in termos):
                continue
            resultado.append(p)
        return tuple(resultado)

    async def obter_produto(self, ctx: TenantContext, produto_id: int) -> Produto | None:
        """Produto pelo id (qualquer status), ou `None`."""
        return (await self._carga.obter(ctx.tenant_id)).por_id.get(produto_id)

    async def listar_promocoes(self, ctx: TenantContext, produto_id: int) -> tuple[Promocao, ...]:
        """Todos os registros de promoção do produto (ativos ou não)."""
        return (await self._carga.obter(ctx.tenant_id)).promocoes.get(produto_id, ())


class PedidosCsv:
    """Implementa `Pedidos`."""

    def __init__(self, tenants: Mapping[str, ConfigTenant], raiz: Path | None = None) -> None:
        self._carga = CargaPorTenant(ResolvedorDados(tenants, raiz), self._carregar)

    @staticmethod
    def _carregar(dados_dir: Path) -> Mapping[int, Pedido]:
        return {p.numero: p for p in _leitura.ler_pedidos(dados_dir)}

    async def obter_pedido(self, ctx: TenantContext, numero: int) -> Pedido | None:
        """Pedido pelo número, ou `None`."""
        return (await self._carga.obter(ctx.tenant_id)).get(numero)


class ClientesCsv:
    """Implementa `Clientes`."""

    def __init__(self, tenants: Mapping[str, ConfigTenant], raiz: Path | None = None) -> None:
        self._carga = CargaPorTenant(ResolvedorDados(tenants, raiz), self._carregar)

    @staticmethod
    def _carregar(dados_dir: Path) -> Mapping[int, Cliente]:
        return {c.id: c for c in _leitura.ler_clientes(dados_dir)}

    async def obter_cliente(self, ctx: TenantContext, cliente_id: int) -> Cliente | None:
        """Cliente pelo id, ou `None`."""
        return (await self._carga.obter(ctx.tenant_id)).get(cliente_id)

"""Dublês em memória das portas e dados mínimos para os testes de casos de uso."""

import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

from emporio.configuracao.tenants import carregar_tenants
from emporio.modulos.atendimento.dominio import Solicitacao
from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao, StatusProduto
from emporio.modulos.pedidos.dominio import (
    Cliente,
    FormaPagamento,
    ItemPedido,
    MetodoPagamento,
    Pedido,
    StatusPedido,
)
from emporio.portas import FiltroProdutos, Idioma, TenantContext
from emporio.portas.tenant import ConfigTenant

RAIZ = Path(__file__).resolve().parents[2]
CTX = TenantContext(tenant_id="emporio")
CTX_EN = TenantContext(tenant_id="emporio", idioma=Idioma.EN_US)


def config_emporio() -> ConfigTenant:
    return carregar_tenants(RAIZ / "config" / "tenants.yaml")["emporio"]


def resolver_config(tenant_id: str) -> ConfigTenant:
    assert tenant_id == "emporio"
    return config_emporio()


class RelogioFixo:
    def __init__(self, instante: datetime) -> None:
        self.instante = instante

    def agora(self) -> datetime:
        return self.instante


def local(ano: int, mes: int, dia: int, hora: int = 10, minuto: int = 0) -> datetime:
    """Instante em hora de Campo Grande (UTC-4, sem horário de verão) devolvido em UTC."""
    return datetime(ano, mes, dia, hora + 4, minuto, tzinfo=UTC)


def produto(
    id: int, nome: str, preco: int, *, estoque: int = 3, categoria: int = 5, status: StatusProduto = StatusProduto.ATIVO
) -> Produto:
    return Produto(id, nome, categoria, f"{nome} descrição", preco * 100, estoque, status, {"cor": "natural"})


PRODUTOS = (
    produto(1, "Violão Alfa", 500),
    produto(2, "Violão Beta", 900, estoque=0),
    produto(3, "Violão Gama", 950),
    produto(4, "Violão Delta", 1500),
    produto(5, "Violão Épsilon", 800, estoque=0, status=StatusProduto.DESCONTINUADO),
    produto(6, "Violão Zeta", 700, status=StatusProduto.EM_BREVE),
    produto(7, "Guitarra Eta", 1200, categoria=1),
    produto(8, "Violão Teta", 1100),  # promoção ativa de 20% -> R$ 880,00
    produto(9, "Violão Iota", 700),  # só promoção encerrada
    produto(10, "Takamine GD20 Dreadnought Natural", 2199),
    produto(11, "Takamine GC5CE Elétrico Nylon Natural", 3299),
    produto(12, "Violão Kappa Zero", 1000, estoque=0, status=StatusProduto.DESCONTINUADO),
    produto(13, "Piano Lambda", 4000, categoria=4),
)
PROMOCOES = {
    7: (Promocao(7, 10, "Oferta", True),),
    8: (Promocao(8, 20, "Black Friday", True), Promocao(8, 5, "Antiga", False)),
    9: (Promocao(9, 15, "Páscoa", False),),
}
CATEGORIAS = (
    Categoria(1, "Guitarras", "Guitarras elétricas"),
    Categoria(2, "Baixos", "Baixos"),
    Categoria(3, "Baterias e Percussão", "Baterias"),
    Categoria(4, "Teclados e Pianos", "Pianos digitais"),
    Categoria(5, "Violões", "Violões clássicos, folk e eletroacústicos"),
    Categoria(6, "Instrumentos de Sopro (Madeiras)", "Flautas, clarinetes, saxofones"),
    Categoria(7, "Instrumentos de Sopro (Metais)", "Trompetes, trombones"),
    Categoria(8, "Cordas Orquestrais", "Violinos, violas, violoncelos"),
    Categoria(9, "Ukuleles", "Ukuleles soprano"),
)


def _norm(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


class CatalogoFalso:
    def __init__(self, produtos: tuple[Produto, ...] = PRODUTOS) -> None:
        self.produtos = {p.id: p for p in produtos}

    async def listar_categorias(self, ctx: TenantContext) -> tuple[Categoria, ...]:
        return CATEGORIAS

    async def listar_produtos(self, ctx: TenantContext, filtro: FiltroProdutos) -> tuple[Produto, ...]:
        termos = _norm(filtro.texto or "").split()
        saida = []
        for p in self.produtos.values():
            if not filtro.incluir_inativos and p.status is not StatusProduto.ATIVO:
                continue
            if filtro.categoria_id is not None and p.categoria_id != filtro.categoria_id:
                continue
            if filtro.preco_min_centavos is not None and p.preco_centavos < filtro.preco_min_centavos:
                continue
            if filtro.preco_max_centavos is not None and p.preco_centavos > filtro.preco_max_centavos:
                continue
            texto = _norm(f"{p.nome} {p.descricao}")
            if all(t in texto for t in termos):
                saida.append(p)
        return tuple(saida)

    async def obter_produto(self, ctx: TenantContext, produto_id: int) -> Produto | None:
        return self.produtos.get(produto_id)

    async def listar_promocoes(self, ctx: TenantContext, produto_id: int) -> tuple[Promocao, ...]:
        return PROMOCOES.get(produto_id, ())


CLIENTES = {
    1: Cliente(1, "Maria Souza Lima", "(67) 99999-1234", "Maria.Souza@Exemplo.com", "Campo Grande"),
    2: Cliente(2, "João Pereira", "(67) 3333-4444", "joao@exemplo.com", "Dourados"),
}
PEDIDOS = {
    100: Pedido(
        100,
        1,
        StatusPedido.ENTREGUE,
        MetodoPagamento(FormaPagamento.CREDITO, 6),
        (ItemPedido(1, 2), ItemPedido(7, 1)),
        total_centavos=220_000,
        data_pedido=date(2026, 3, 1),
        codigo_rastreio="BR123",
        previsao_entrega=date(2026, 3, 5),
    ),
    101: Pedido(
        101,
        2,
        StatusPedido.PENDENTE,
        MetodoPagamento(FormaPagamento.PIX),
        (ItemPedido(3, 1),),
        95_000,
        date(2026, 3, 2),
    ),
    102: Pedido(
        102,
        2,
        StatusPedido.CANCELADO,
        MetodoPagamento(FormaPagamento.BOLETO),
        (ItemPedido(4, 1),),
        150_000,
        date(2026, 3, 2),
        observacoes="Cancelado a pedido do cliente",
    ),
}


class PedidosFalso:
    async def obter_pedido(self, ctx: TenantContext, numero: int) -> Pedido | None:
        return PEDIDOS.get(numero)


class ClientesFalso:
    async def obter_cliente(self, ctx: TenantContext, cliente_id: int) -> Cliente | None:
        return CLIENTES.get(cliente_id)


class SolicitacoesFalso:
    def __init__(self) -> None:
        self.itens: dict[str, Solicitacao] = {}

    async def registrar(self, ctx: TenantContext, solicitacao: Solicitacao) -> Solicitacao:
        return self.itens.setdefault(solicitacao.chave_idempotencia, solicitacao)

    async def listar(self, ctx: TenantContext) -> tuple[Solicitacao, ...]:
        return tuple(self.itens.values())

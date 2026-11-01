"""Repositórios Postgres: `CatalogoPostgres`, `PedidosPostgres`, `ClientesPostgres`, `SolicitacoesPostgres`.

Mesmo contrato dos adaptadores CSV. Todo método recebe o `TenantContext` e passa por `AcessoTenant`, que fixa o
`search_path`; as tabelas são referenciadas sem schema.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from emporio.adaptadores.db._acesso import AcessoTenant
from emporio.adaptadores.db._leitura_csv import normalizar_texto
from emporio.modulos.atendimento.dominio import Solicitacao, StatusSolicitacao, TipoSolicitacao
from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao, StatusProduto
from emporio.modulos.pedidos.dominio import (
    Cliente,
    FormaPagamento,
    ItemPedido,
    MetodoPagamento,
    Pedido,
    StatusPedido,
)
from emporio.portas.contexto import TenantContext
from emporio.portas.repositorios import FiltroProdutos

_COLUNAS_PRODUTO = "id, nome, categoria_id, descricao, preco_centavos, estoque, status, specs, criado_em"
_SQL_PRODUTO_POR_ID = text(f"SELECT {_COLUNAS_PRODUTO} FROM produtos WHERE id = :id")
_COLUNAS_SOLICITACAO = (
    "id, tipo, descricao, sessao_id, chave_idempotencia, criada_em, contato, status, atualizada_em, atualizada_por"
)


def _produto(r: Any) -> Produto:
    specs = r.specs if isinstance(r.specs, dict) else {}
    return Produto(
        id=r.id,
        nome=r.nome,
        categoria_id=r.categoria_id,
        descricao=r.descricao,
        preco_centavos=r.preco_centavos,
        estoque=r.estoque,
        status=StatusProduto(r.status),
        specs=specs,
        criado_em=r.criado_em,
    )


class CatalogoPostgres:
    """Implementa `Catalogo`."""

    def __init__(self, acesso: AcessoTenant) -> None:
        self._acesso = acesso

    async def listar_categorias(self, ctx: TenantContext) -> tuple[Categoria, ...]:
        """Todas as categorias, inclusive as sem produtos."""

        def consulta(c: Connection) -> tuple[Categoria, ...]:
            linhas = c.execute(text("SELECT id, nome, descricao FROM categorias ORDER BY id"))
            return tuple(Categoria(id=r.id, nome=r.nome, descricao=r.descricao) for r in linhas)

        return await self._acesso.ler(ctx, consulta)

    async def listar_produtos(self, ctx: TenantContext, filtro: FiltroProdutos) -> tuple[Produto, ...]:
        """Produtos do filtro: texto (todos os termos, sem acento/caixa), categoria, faixa de preço e status."""
        condicoes: list[str] = []
        params: dict[str, object] = {}
        if not filtro.incluir_inativos:
            condicoes.append("status = 'active'")
        if filtro.categoria_id is not None:
            condicoes.append("categoria_id = :categoria")
            params["categoria"] = filtro.categoria_id
        if filtro.preco_min_centavos is not None:
            condicoes.append("preco_centavos >= :preco_min")
            params["preco_min"] = filtro.preco_min_centavos
        if filtro.preco_max_centavos is not None:
            condicoes.append("preco_centavos <= :preco_max")
            params["preco_max"] = filtro.preco_max_centavos
        termos = normalizar_texto(filtro.texto).split() if filtro.texto else []
        for i, termo in enumerate(termos):
            condicoes.append(f"strpos(texto_busca, :termo{i}) > 0")
            params[f"termo{i}"] = termo
        where = f" WHERE {' AND '.join(condicoes)}" if condicoes else ""
        # Só fragmentos fixos entram no SQL; todos os valores vão como parâmetros.
        sql = text(f"SELECT {_COLUNAS_PRODUTO} FROM produtos{where} ORDER BY id")

        def consulta(c: Connection) -> tuple[Produto, ...]:
            return tuple(_produto(r) for r in c.execute(sql, params))

        return await self._acesso.ler(ctx, consulta)

    async def obter_produto(self, ctx: TenantContext, produto_id: int) -> Produto | None:
        """Produto pelo id (qualquer status), ou `None`."""

        def consulta(c: Connection) -> Produto | None:
            r = c.execute(_SQL_PRODUTO_POR_ID, {"id": produto_id}).first()
            return _produto(r) if r is not None else None

        return await self._acesso.ler(ctx, consulta)

    async def listar_promocoes(self, ctx: TenantContext, produto_id: int) -> tuple[Promocao, ...]:
        """Todos os registros de promoção do produto (ativos ou não)."""

        def consulta(c: Connection) -> tuple[Promocao, ...]:
            linhas = c.execute(
                text(
                    "SELECT produto_id, desconto_percentual, descricao, ativa FROM promocoes "
                    "WHERE produto_id = :id ORDER BY id"
                ),
                {"id": produto_id},
            )
            return tuple(
                Promocao(
                    produto_id=r.produto_id,
                    desconto_percentual=r.desconto_percentual,
                    descricao=r.descricao,
                    ativa=r.ativa,
                )
                for r in linhas
            )

        return await self._acesso.ler(ctx, consulta)


class PedidosPostgres:
    """Implementa `Pedidos`."""

    def __init__(self, acesso: AcessoTenant) -> None:
        self._acesso = acesso

    async def obter_pedido(self, ctx: TenantContext, numero: int) -> Pedido | None:
        """Pedido pelo número, ou `None`."""

        def consulta(c: Connection) -> Pedido | None:
            r = c.execute(
                text(
                    "SELECT numero, cliente_id, status, pagamento_forma, pagamento_parcelas, total_centavos, "
                    "data_pedido, codigo_rastreio, previsao_entrega, observacoes FROM pedidos WHERE numero = :n"
                ),
                {"n": numero},
            ).first()
            if r is None:
                return None
            itens = c.execute(
                text("SELECT produto_id, quantidade FROM itens_pedido WHERE pedido_numero = :n ORDER BY seq"),
                {"n": numero},
            )
            return Pedido(
                numero=r.numero,
                cliente_id=r.cliente_id,
                status=StatusPedido(r.status),
                pagamento=MetodoPagamento(FormaPagamento(r.pagamento_forma), r.pagamento_parcelas),
                itens=tuple(ItemPedido(produto_id=i.produto_id, quantidade=i.quantidade) for i in itens),
                total_centavos=r.total_centavos,
                data_pedido=r.data_pedido,
                codigo_rastreio=r.codigo_rastreio,
                previsao_entrega=r.previsao_entrega,
                observacoes=r.observacoes,
            )

        return await self._acesso.ler(ctx, consulta)


class ClientesPostgres:
    """Implementa `Clientes`."""

    def __init__(self, acesso: AcessoTenant) -> None:
        self._acesso = acesso

    async def obter_cliente(self, ctx: TenantContext, cliente_id: int) -> Cliente | None:
        """Cliente pelo id, ou `None`."""

        def consulta(c: Connection) -> Cliente | None:
            r = c.execute(
                text("SELECT id, nome, telefone, email, cidade FROM clientes WHERE id = :id"), {"id": cliente_id}
            ).first()
            if r is None:
                return None
            return Cliente(id=r.id, nome=r.nome, telefone=r.telefone, email=r.email, cidade=r.cidade)

        return await self._acesso.ler(ctx, consulta)


def _solicitacao(r: Any) -> Solicitacao:
    return Solicitacao(
        id=r.id,
        tipo=TipoSolicitacao(r.tipo),
        descricao=r.descricao,
        sessao_id=r.sessao_id,
        chave_idempotencia=r.chave_idempotencia,
        criada_em=r.criada_em,
        contato=r.contato,
        status=StatusSolicitacao(r.status),
        atualizada_em=r.atualizada_em,
        atualizada_por=r.atualizada_por,
    )


class SolicitacoesPostgres:
    """Implementa `Solicitacoes`. Idempotência pela restrição única `chave_idempotencia` (vale entre réplicas)."""

    def __init__(self, acesso: AcessoTenant) -> None:
        self._acesso = acesso

    async def registrar(self, ctx: TenantContext, solicitacao: Solicitacao) -> Solicitacao:
        """Grava a solicitação; mesma `chave_idempotencia` devolve a já gravada, sem nova linha."""
        valores: Mapping[str, object] = {
            "id": solicitacao.id,
            "tipo": solicitacao.tipo.value,
            "descricao": solicitacao.descricao,
            "sessao_id": solicitacao.sessao_id,
            "chave": solicitacao.chave_idempotencia,
            "criada_em": solicitacao.criada_em,
            "contato": solicitacao.contato,
        }

        def gravar(c: Connection) -> Solicitacao:
            c.execute(
                text(
                    "INSERT INTO solicitacoes (id, tipo, descricao, sessao_id, chave_idempotencia, criada_em, contato) "
                    "VALUES (:id, :tipo, :descricao, :sessao_id, :chave, :criada_em, :contato) "
                    "ON CONFLICT DO NOTHING"
                ),
                valores,
            )
            r = c.execute(
                text(f"SELECT {_COLUNAS_SOLICITACAO} FROM solicitacoes WHERE chave_idempotencia = :chave"),
                {"chave": solicitacao.chave_idempotencia},
            ).one()
            return _solicitacao(r)

        return await self._acesso.escrever(ctx, gravar)

    async def listar(
        self,
        ctx: TenantContext,
        *,
        status: StatusSolicitacao | None = None,
        tipo: TipoSolicitacao | None = None,
    ) -> tuple[Solicitacao, ...]:
        """Solicitações do tenant, na ordem de gravação, com filtros opcionais (valores sempre como parâmetros)."""
        condicoes: list[str] = []
        params: dict[str, object] = {}
        if status is not None:
            condicoes.append("status = :status")
            params["status"] = status.value
        if tipo is not None:
            condicoes.append("tipo = :tipo")
            params["tipo"] = tipo.value
        where = f" WHERE {' AND '.join(condicoes)}" if condicoes else ""
        sql = text(f"SELECT {_COLUNAS_SOLICITACAO} FROM solicitacoes{where} ORDER BY ordem")

        def consulta(c: Connection) -> tuple[Solicitacao, ...]:
            return tuple(_solicitacao(r) for r in c.execute(sql, params))

        return await self._acesso.ler(ctx, consulta)

    async def atualizar_status(
        self, ctx: TenantContext, solicitacao_id: str, status: StatusSolicitacao, *, por: str, em: datetime
    ) -> Solicitacao | None:
        """Muda o status sob `SELECT ... FOR UPDATE` (réplicas concorrentes não pulam a regra de transição)."""

        def gravar(c: Connection) -> Solicitacao | None:
            r = c.execute(
                text(f"SELECT {_COLUNAS_SOLICITACAO} FROM solicitacoes WHERE id = :id FOR UPDATE"),
                {"id": solicitacao_id},
            ).first()
            if r is None:
                return None
            atual = _solicitacao(r)
            if atual.status == status:
                return atual  # idempotente: não muda a auditoria
            nova = atual.com_status(status, por=por, em=em)  # levanta ErroTransicaoStatus
            c.execute(
                text(
                    "UPDATE solicitacoes SET status = :status, atualizada_em = :em, atualizada_por = :por "
                    "WHERE id = :id"
                ),
                {"status": status.value, "em": em, "por": por, "id": solicitacao_id},
            )
            return nova

        return await self._acesso.escrever(ctx, gravar)

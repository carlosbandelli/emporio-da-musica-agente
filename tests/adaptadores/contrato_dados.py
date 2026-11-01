"""Verificações de contrato das portas de dados, sobre os CSVs reais de `data/`.

Cada função recebe o(s) repositório(s) e levanta `AssertionError` se o contrato for violado. Os adaptadores CSV
(`tests/adaptadores/test_csv.py`) e Postgres (`tests/adaptadores/db/test_contrato.py`) chamam as mesmas funções.
"""

import asyncio
import dataclasses
from datetime import UTC, date, datetime

from emporio.modulos.atendimento.dominio import ErroTransicaoStatus, Solicitacao, StatusSolicitacao, TipoSolicitacao
from emporio.modulos.catalogo.dominio import StatusProduto
from emporio.modulos.pedidos.dominio import FormaPagamento, MetodoPagamento, StatusPedido
from emporio.portas import FiltroProdutos, TenantContext
from emporio.portas.repositorios import Catalogo, Clientes, Pedidos, Solicitacoes

CTX = TenantContext(tenant_id="emporio")


async def verificar_catalogo_real(cat: Catalogo) -> None:
    todos = await cat.listar_produtos(CTX, FiltroProdutos(incluir_inativos=True))
    assert len(todos) == 65
    assert min(p.id for p in todos) == 81
    assert all(p.preco_centavos > 0 for p in todos)
    assert any(p.specs for p in todos)
    categorias = await cat.listar_categorias(CTX)
    assert {c.id for c in categorias} >= {6, 7, 8}
    assert not any(p.categoria_id in {6, 7, 8} for p in todos)
    p81 = await cat.obter_produto(CTX, 81)
    assert p81 is not None
    assert p81.preco_centavos == 59990
    assert p81.specs["strings"] == "nylon"
    assert await cat.obter_produto(CTX, 1) is None


async def verificar_filtros(cat: Catalogo) -> None:
    ativos = await cat.listar_produtos(CTX, FiltroProdutos())
    assert all(p.status is StatusProduto.ATIVO for p in ativos)
    assert len(ativos) == 62
    violoes = await cat.listar_produtos(CTX, FiltroProdutos(categoria_id=5, preco_max_centavos=100000))
    assert violoes
    assert all(p.categoria_id == 5 and p.preco_centavos <= 100000 for p in violoes)
    faixa = await cat.listar_produtos(CTX, FiltroProdutos(preco_min_centavos=100000, preco_max_centavos=200000))
    assert faixa
    assert all(100000 <= p.preco_centavos <= 200000 for p in faixa)
    a = await cat.listar_produtos(CTX, FiltroProdutos(texto="YAMAHA c40"))
    assert [p.id for p in a] == [81]
    b = await cat.listar_produtos(CTX, FiltroProdutos(texto="violao classico"))  # sem acento
    assert 81 in {p.id for p in b}
    todos = await cat.listar_produtos(CTX, FiltroProdutos(incluir_inativos=True))
    assert sum(1 for p in todos if p.status is not StatusProduto.ATIVO) == 3


async def verificar_promocoes_todas_as_linhas(cat: Catalogo) -> None:
    todas = [p for pid in range(81, 146) for p in await cat.listar_promocoes(CTX, pid)]
    assert len(todas) == 25
    assert sum(1 for p in todas if p.ativa) == 4
    assert [len(await cat.listar_promocoes(CTX, pid)) > 1 for pid in (93, 96, 99)].count(True) >= 1


async def verificar_pedidos_real(ped: Pedidos) -> None:
    p8 = await ped.obter_pedido(CTX, 8)
    assert p8 is not None
    assert p8.status is StatusPedido.ENVIADO
    assert p8.codigo_rastreio
    assert p8.previsao_entrega == date(2026, 2, 28)
    assert len(p8.itens) >= 1
    p1 = await ped.obter_pedido(CTX, 1)
    assert p1 is not None
    assert p1.total_centavos == 1149900  # R$ 11.499,00 (premissa #22)
    p4 = await ped.obter_pedido(CTX, 4)
    assert p4 is not None
    assert p4.total_centavos == 79990
    p2 = await ped.obter_pedido(CTX, 2)
    assert p2 is not None
    assert p2.pagamento == MetodoPagamento(FormaPagamento.CREDITO, 6)
    p9 = await ped.obter_pedido(CTX, 9)
    assert p9 is not None
    assert p9.codigo_rastreio is None
    assert p9.previsao_entrega is None
    p17 = await ped.obter_pedido(CTX, 17)
    assert p17 is not None
    assert p17.status is StatusPedido.CANCELADO
    assert p17.observacoes
    assert await ped.obter_pedido(CTX, 999) is None


async def verificar_total_confere_com_itens(ped: Pedidos, cat: Catalogo) -> None:
    p = await ped.obter_pedido(CTX, 1)
    assert p is not None
    soma = 0
    for item in p.itens:
        produto = await cat.obter_produto(CTX, item.produto_id)
        assert produto is not None
        soma += produto.preco_centavos * item.quantidade
    assert soma == p.total_centavos


async def verificar_clientes(cli: Clientes) -> None:
    c = await cli.obter_cliente(CTX, 3)
    assert c is not None
    assert c.id == 3
    assert "@" in c.email
    assert await cli.obter_cliente(CTX, 99999) is None


def solicitacao_exemplo(chave: str, desc: str = "d") -> Solicitacao:
    return Solicitacao(
        id=f"id-{chave}",
        tipo=TipoSolicitacao.RECLAMACAO,
        descricao=desc,
        sessao_id="s1",
        chave_idempotencia=chave,
        criada_em=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        contato="x",
    )


async def verificar_solicitacoes_idempotente(repo: Solicitacoes, outra_instancia: Solicitacoes) -> None:
    """`outra_instancia` aponta para o mesmo armazenamento (prova que a leitura vem do armazenamento)."""
    a = await repo.registrar(CTX, solicitacao_exemplo("k1"))
    b = await repo.registrar(CTX, solicitacao_exemplo("k1", "outra descricao"))
    assert a == b
    await repo.registrar(CTX, solicitacao_exemplo("k2"))
    listadas = await outra_instancia.listar(CTX)
    assert [s.chave_idempotencia for s in listadas] == ["k1", "k2"]
    assert listadas[0] == a


async def verificar_solicitacoes_concorrentes(repo: Solicitacoes) -> None:
    await asyncio.gather(*(repo.registrar(CTX, solicitacao_exemplo("k")) for _ in range(8)))
    assert len(await repo.listar(CTX)) == 1


# --- status das solicitações (task 2.5) ------------------------------------------------------------------------------
QUANDO = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)


async def verificar_solicitacoes_status(repo: Solicitacoes, outra_instancia: Solicitacoes) -> None:
    """Fluxo aberta → em_atendimento → resolvida, auditoria gravada, idempotência e transição inválida."""
    await repo.registrar(CTX, solicitacao_exemplo("k1"))
    await repo.registrar(CTX, solicitacao_exemplo("k2"))
    assert (await repo.listar(CTX))[0].status is StatusSolicitacao.ABERTA

    em_atendimento = await repo.atualizar_status(CTX, "id-k1", StatusSolicitacao.EM_ATENDIMENTO, por="ana", em=QUANDO)
    assert em_atendimento is not None
    assert (em_atendimento.status, em_atendimento.atualizada_por) == (StatusSolicitacao.EM_ATENDIMENTO, "ana")
    assert em_atendimento.atualizada_em == QUANDO

    depois = QUANDO.replace(hour=16)
    resolvida = await repo.atualizar_status(CTX, "id-k1", StatusSolicitacao.RESOLVIDA, por="bia", em=depois)
    assert resolvida is not None and resolvida.status is StatusSolicitacao.RESOLVIDA
    # Mesmo status: idempotente, sem mudar a auditoria.
    de_novo = await repo.atualizar_status(CTX, "id-k1", StatusSolicitacao.RESOLVIDA, por="caio", em=QUANDO)
    assert de_novo == resolvida

    try:
        await repo.atualizar_status(CTX, "id-k1", StatusSolicitacao.ABERTA, por="ana", em=QUANDO)
    except ErroTransicaoStatus:
        pass
    else:
        raise AssertionError("resolvida -> aberta deveria ser recusada")

    assert await repo.atualizar_status(CTX, "nao-existe", StatusSolicitacao.RESOLVIDA, por="ana", em=QUANDO) is None

    listadas = await outra_instancia.listar(CTX)  # persistido no armazenamento
    assert [(s.id, s.status) for s in listadas] == [
        ("id-k1", StatusSolicitacao.RESOLVIDA),
        ("id-k2", StatusSolicitacao.ABERTA),
    ]
    assert dataclasses.replace(listadas[0]) == resolvida
    # Registrar de novo a mesma chave devolve a já existente com o status atual (não reabre).
    assert (await repo.registrar(CTX, solicitacao_exemplo("k1"))).status is StatusSolicitacao.RESOLVIDA


async def verificar_solicitacoes_filtros(repo: Solicitacoes) -> None:
    await repo.registrar(CTX, solicitacao_exemplo("k1"))
    await repo.registrar(CTX, dataclasses.replace(solicitacao_exemplo("k2"), id="id-k2", tipo=TipoSolicitacao.OUTROS))
    await repo.registrar(CTX, solicitacao_exemplo("k3"))
    await repo.atualizar_status(CTX, "id-k3", StatusSolicitacao.EM_ATENDIMENTO, por="ana", em=QUANDO)

    def ids(itens: tuple[Solicitacao, ...]) -> list[str]:
        return [s.id for s in itens]

    assert ids(await repo.listar(CTX)) == ["id-k1", "id-k2", "id-k3"]
    assert ids(await repo.listar(CTX, status=StatusSolicitacao.ABERTA)) == ["id-k1", "id-k2"]
    assert ids(await repo.listar(CTX, tipo=TipoSolicitacao.OUTROS)) == ["id-k2"]
    assert ids(await repo.listar(CTX, status=StatusSolicitacao.EM_ATENDIMENTO, tipo=TipoSolicitacao.RECLAMACAO)) == [
        "id-k3"
    ]
    assert await repo.listar(CTX, status=StatusSolicitacao.RESOLVIDA) == ()

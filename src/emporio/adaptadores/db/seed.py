"""Seed dos CSVs de `data/` no schema do tenant (idempotente: `ON CONFLICT DO NOTHING` nas chaves primárias).

A limpeza é a mesma do adaptador CSV (premissas #22 a #27). Dados já existentes não são sobrescritos: o seed só
completa o que falta, então edições feitas no banco sobrevivem a novas execuções de `migrar`.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, text

from emporio.adaptadores.db import _leitura_csv as leitura

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResultadoSeed:
    """Linhas realmente inseridas nesta execução (0 em reexecução)."""

    inseridas: int
    ignorado: bool = False


def tem_dados(dados_dir: Path) -> bool:
    """O diretório tem os CSVs de produtos (critério mínimo para semear)."""
    return leitura.caminho_tabela(dados_dir, "products").is_file()


def _inserir(conexao: Connection, sql: str, linhas: list[dict[str, object]]) -> int:
    if not linhas:
        return 0
    total = 0
    for linha in linhas:
        total += max(conexao.execute(text(sql), linha).rowcount, 0)
    return total


def semear(conexao: Connection, dados_dir: Path) -> ResultadoSeed:
    """Insere categorias, produtos, promoções, clientes, pedidos e itens. Exige `search_path` já fixado."""
    if not tem_dados(dados_dir):
        return ResultadoSeed(0, ignorado=True)

    n = _inserir(
        conexao,
        "INSERT INTO categorias (id, nome, descricao) VALUES (:id, :nome, :descricao) ON CONFLICT (id) DO NOTHING",
        [{"id": c.id, "nome": c.nome, "descricao": c.descricao} for c in leitura.ler_categorias(dados_dir)],
    )
    n += _inserir(
        conexao,
        "INSERT INTO produtos (id, nome, categoria_id, descricao, preco_centavos, estoque, status, specs, criado_em, "
        "texto_busca) VALUES (:id, :nome, :categoria_id, :descricao, :preco, :estoque, :status, "
        "CAST(:specs AS jsonb), :criado_em, :busca) ON CONFLICT (id) DO NOTHING",
        [
            {
                "id": p.id,
                "nome": p.nome,
                "categoria_id": p.categoria_id,
                "descricao": p.descricao,
                "preco": p.preco_centavos,
                "estoque": p.estoque,
                "status": p.status.value,
                "specs": json.dumps(p.specs, ensure_ascii=False),
                "criado_em": p.criado_em,
                "busca": leitura.normalizar_texto(f"{p.nome} {p.descricao}"),
            }
            for p in leitura.ler_produtos(dados_dir)
        ],
    )
    n += _inserir(
        conexao,
        "INSERT INTO promocoes (id, produto_id, desconto_percentual, descricao, ativa) "
        "VALUES (:id, :produto_id, :desconto, :descricao, :ativa) ON CONFLICT (id) DO NOTHING",
        [
            {
                "id": identificador,
                "produto_id": p.produto_id,
                "desconto": p.desconto_percentual,
                "descricao": p.descricao,
                "ativa": p.ativa,
            }
            for identificador, p in leitura.ler_promocoes(dados_dir)
        ],
    )
    n += _inserir(
        conexao,
        "INSERT INTO clientes (id, nome, telefone, email, cidade) VALUES (:id, :nome, :telefone, :email, :cidade) "
        "ON CONFLICT (id) DO NOTHING",
        [
            {"id": c.id, "nome": c.nome, "telefone": c.telefone, "email": c.email, "cidade": c.cidade}
            for c in leitura.ler_clientes(dados_dir)
        ],
    )
    for pedido in leitura.ler_pedidos(dados_dir):
        inseriu = _inserir(
            conexao,
            "INSERT INTO pedidos (numero, cliente_id, status, pagamento_forma, pagamento_parcelas, total_centavos, "
            "data_pedido, codigo_rastreio, previsao_entrega, observacoes) VALUES (:numero, :cliente_id, :status, "
            ":forma, :parcelas, :total, :data_pedido, :rastreio, :previsao, :observacoes) "
            "ON CONFLICT (numero) DO NOTHING",
            [
                {
                    "numero": pedido.numero,
                    "cliente_id": pedido.cliente_id,
                    "status": pedido.status.value,
                    "forma": pedido.pagamento.forma.value,
                    "parcelas": pedido.pagamento.parcelas,
                    "total": pedido.total_centavos,
                    "data_pedido": pedido.data_pedido,
                    "rastreio": pedido.codigo_rastreio,
                    "previsao": pedido.previsao_entrega,
                    "observacoes": pedido.observacoes,
                }
            ],
        )
        n += inseriu
        if inseriu:  # itens só entram junto com o pedido novo
            n += _inserir(
                conexao,
                "INSERT INTO itens_pedido (pedido_numero, seq, produto_id, quantidade) "
                "VALUES (:numero, :seq, :produto_id, :quantidade) ON CONFLICT DO NOTHING",
                [
                    {"numero": pedido.numero, "seq": i, "produto_id": item.produto_id, "quantidade": item.quantidade}
                    for i, item in enumerate(pedido.itens)
                ],
            )
    return ResultadoSeed(n)

"""Leitura e limpeza dos CSVs para o seed (cópia deliberada de `adaptadores/csv/_leitura.py`).

A regra C5 proíbe um adaptador de importar outro; por isso a leitura é duplicada aqui. Os testes de contrato
comparam o resultado do seed com o do adaptador CSV para detectar divergência.

Política: linha com campo estrutural inválido (id, preço, status, forma de pagamento) é descartada com `warning`
(número da linha e id, nunca dados pessoais); campo acessório inválido (specs, datas, total) vira vazio/`None` e a
linha é mantida. Ver premissas #22 a #27.
"""

import csv
import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao, StatusProduto
from emporio.modulos.pedidos.dominio import (
    Cliente,
    FormaPagamento,
    ItemPedido,
    MetodoPagamento,
    Pedido,
    StatusPedido,
)

log = logging.getLogger(__name__)

PREFIXO_ARQUIVO = "desafio_tecnico_ai_eng - "
DESCONTO_MAXIMO = 100


def caminho_tabela(dados_dir: Path, tabela: str) -> Path:
    """Caminho de `desafio_tecnico_ai_eng - <tabela>.csv` dentro do diretório de dados."""
    return dados_dir / f"{PREFIXO_ARQUIVO}{tabela}.csv"


def _linhas(caminho: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Linhas de dados com o número da linha no arquivo (cabeçalho = linha 1). Células ausentes viram vazio."""
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo)
        for r in leitor:
            yield leitor.line_num, {k: (v or "").strip() for k, v in r.items() if isinstance(k, str)}


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sem acentos: base da busca por texto."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).casefold()


def para_centavos(valor: str) -> int:
    """Converte reais (ponto ou vírgula decimal) em centavos inteiros, `ROUND_HALF_UP`.

    Levanta `ValueError` se não for número finito não negativo.
    """
    try:
        numero = Decimal(valor.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"valor monetário inválido: {valor!r}") from exc
    if not numero.is_finite() or numero < 0:
        raise ValueError(f"valor monetário inválido: {valor!r}")
    return int((numero * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _data(valor: str) -> date | None:
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _booleano(valor: str) -> bool:
    return valor.strip().lower() in {"1", "true", "t", "yes", "y", "sim"}


_PARCELADO = re.compile(r"^credit_(\d{1,2})x$")


def interpretar_pagamento(bruto: str) -> MetodoPagamento:
    """`pix`, `debit`, `boleto`, `credit_Nx` para `MetodoPagamento`. Levanta `ValueError` se desconhecido."""
    valor = bruto.strip().lower()
    simples = {
        "pix": FormaPagamento.PIX,
        "debit": FormaPagamento.DEBITO,
        "boleto": FormaPagamento.BOLETO,
    }
    if valor in simples:
        return MetodoPagamento(simples[valor])
    correspondencia = _PARCELADO.match(valor)
    if correspondencia and int(correspondencia.group(1)) >= 1:
        return MetodoPagamento(FormaPagamento.CREDITO, int(correspondencia.group(1)))
    raise ValueError(f"forma de pagamento desconhecida: {bruto!r}")


def _specs(valor: str, linha: int, produto_id: int) -> dict[str, object]:
    if not valor:
        return {}
    try:
        convertido = json.loads(valor)
    except json.JSONDecodeError:
        log.warning(
            "products.csv linha %d (produto %d): specs com JSON inválido; mantido com specs vazias", linha, produto_id
        )
        return {}
    if not isinstance(convertido, dict):
        log.warning(
            "products.csv linha %d (produto %d): specs não é objeto JSON; mantido com specs vazias", linha, produto_id
        )
        return {}
    return convertido


def ler_categorias(dados_dir: Path) -> tuple[Categoria, ...]:
    """Todas as categorias (inclusive as sem produtos)."""
    resultado: list[Categoria] = []
    for linha, r in _linhas(caminho_tabela(dados_dir, "categories")):
        try:
            resultado.append(Categoria(id=int(r["category_id"]), nome=r["name"], descricao=r.get("description", "")))
        except (ValueError, KeyError):
            log.warning("categories.csv linha %d: linha malformada descartada", linha)
    return tuple(resultado)


def ler_produtos(dados_dir: Path) -> tuple[Produto, ...]:
    """Produtos limpos. Linhas com id/preço/categoria/estoque/status inválidos são descartadas com warning."""
    resultado: list[Produto] = []
    vistos: set[int] = set()
    for linha, r in _linhas(caminho_tabela(dados_dir, "products")):
        try:
            produto_id = int(r["product_id"])
            preco = para_centavos(r["price_brl"])
            categoria_id = int(r["category_id"])
            estoque = int(r["stock_quantity"])
            status = StatusProduto(r["status"].lower())
            if estoque < 0 or not r["name"]:
                raise ValueError("estoque negativo ou nome vazio")
        except (ValueError, KeyError) as exc:
            log.warning("products.csv linha %d: linha malformada descartada (%s)", linha, exc)
            continue
        if produto_id in vistos:
            log.warning("products.csv linha %d: id %d duplicado; mantida a primeira ocorrência", linha, produto_id)
            continue
        vistos.add(produto_id)
        try:
            criado_em = datetime.fromisoformat(r["created_at"]) if r["created_at"] else None
        except ValueError:
            criado_em = None
        resultado.append(
            Produto(
                id=produto_id,
                nome=r["name"],
                categoria_id=categoria_id,
                descricao=r["description"],
                preco_centavos=preco,
                estoque=estoque,
                status=status,
                specs=_specs(r["specs"], linha, produto_id),
                criado_em=criado_em,
            )
        )
    return tuple(resultado)


def ler_promocoes(dados_dir: Path) -> tuple[tuple[int, Promocao], ...]:
    """Linhas de promoção com o `promotion_id` (ou o número da linha, se a coluna faltar)."""
    resultado: list[tuple[int, Promocao]] = []
    for linha, r in _linhas(caminho_tabela(dados_dir, "promotions")):
        try:
            desconto = int(r["discount_percent"])
            if not 0 < desconto <= DESCONTO_MAXIMO:
                raise ValueError("desconto fora de 1..100")
            identificador = int(r["promotion_id"]) if r.get("promotion_id") else linha
            resultado.append(
                (
                    identificador,
                    Promocao(
                        produto_id=int(r["product_id"]),
                        desconto_percentual=desconto,
                        descricao=r["description"],
                        ativa=_booleano(r["is_active"]),
                    ),
                )
            )
        except (ValueError, KeyError):
            log.warning("promotions.csv linha %d: linha malformada descartada", linha)
    return tuple(resultado)


def ler_pedidos(dados_dir: Path) -> tuple[Pedido, ...]:
    """Pedidos com itens de `order_items.csv`. Total em reais (decimal) vira centavos (premissa #22)."""
    itens: dict[int, list[ItemPedido]] = {}
    for linha, r in _linhas(caminho_tabela(dados_dir, "order_items")):
        try:
            quantidade = int(r["quantity"])
            if quantidade < 1:
                raise ValueError("quantidade < 1")
            itens.setdefault(int(r["order_id"]), []).append(
                ItemPedido(produto_id=int(r["product_id"]), quantidade=quantidade)
            )
        except (ValueError, KeyError):
            log.warning("order_items.csv linha %d: item malformado descartado", linha)

    resultado: list[Pedido] = []
    for linha, r in _linhas(caminho_tabela(dados_dir, "orders")):
        try:
            numero = int(r["order_id"])
            cliente_id = int(r["customer_id"])
            status = StatusPedido(r["status"].lower())
            pagamento = interpretar_pagamento(r["payment_method"])
        except (ValueError, KeyError) as exc:
            log.warning("orders.csv linha %d: linha malformada descartada (%s)", linha, exc)
            continue
        try:
            total = para_centavos(r["total_brl"]) if r["total_brl"] else None
        except ValueError:
            log.warning("orders.csv linha %d (pedido %d): total inválido; mantido sem total", linha, numero)
            total = None
        resultado.append(
            Pedido(
                numero=numero,
                cliente_id=cliente_id,
                status=status,
                pagamento=pagamento,
                itens=tuple(itens.get(numero, ())),
                total_centavos=total,
                data_pedido=_data(r["order_date"]),
                codigo_rastreio=r["tracking_code"] or None,
                previsao_entrega=_data(r["estimated_delivery"]),
                observacoes=r.get("notes", ""),
            )
        )
    return tuple(resultado)


def ler_clientes(dados_dir: Path) -> tuple[Cliente, ...]:
    """Clientes (PII). Os logs citam só o número da linha."""
    resultado: list[Cliente] = []
    for linha, r in _linhas(caminho_tabela(dados_dir, "customers")):
        try:
            resultado.append(
                Cliente(
                    id=int(r["customer_id"]),
                    nome=r["name"],
                    telefone=r["phone"],
                    email=r["email"],
                    cidade=r["city"],
                )
            )
        except (ValueError, KeyError):
            log.warning("customers.csv linha %d: linha malformada descartada", linha)
    return tuple(resultado)

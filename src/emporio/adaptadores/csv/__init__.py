"""Adaptadores de dados lidos dos CSVs em data/ e solicitações em arquivo JSONL."""

from emporio.adaptadores.csv._base import ErroTenantDesconhecido
from emporio.adaptadores.csv.catalogo import CatalogoCsv, ClientesCsv, PedidosCsv
from emporio.adaptadores.csv.solicitacoes import SolicitacoesJsonl

__all__ = ["CatalogoCsv", "ClientesCsv", "ErroTenantDesconhecido", "PedidosCsv", "SolicitacoesJsonl"]

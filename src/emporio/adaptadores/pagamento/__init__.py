"""Adapters da porta `GatewayPagamento` (`docs/pagamento.md`): hoje só o gateway simulado."""

from emporio.adaptadores.pagamento.fake import CABECALHO_ASSINATURA, Cenario, FakeGatewayPagamento, Falha

__all__ = ["CABECALHO_ASSINATURA", "Cenario", "FakeGatewayPagamento", "Falha"]

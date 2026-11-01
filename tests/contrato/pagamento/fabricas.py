"""Fábricas dos gateways testados e o registro `GATEWAYS`. **Único arquivo que muda para um gateway novo.**"""

from __future__ import annotations

from typing import cast

from emporio.adaptadores.pagamento import FakeGatewayPagamento, Falha
from emporio.adaptadores.pagamento.fake import Operacao
from emporio.portas.pagamento import StatusPagamento, TenantId
from tests.contrato.pagamento.kit import TENANT_A, TENANT_B, FabricaGateway, KitGateway, RelogioFixo, TipoFalha

_FALHAS: dict[TipoFalha, Falha] = {
    "transitoria": Falha.TRANSITORIA,
    "timeout_ambiguo": Falha.TIMEOUT_AMBIGUO,
    "permanente": Falha.PERMANENTE,
}


class ControleFake:
    """`ControleGateway` do fake: delega aos métodos de simulação do próprio adapter."""

    suporta_falhas = True

    def __init__(self, fake: FakeGatewayPagamento) -> None:
        self._fake = fake

    async def levar_a(self, tenant_id: TenantId, pagamento_id: str, status: StatusPagamento) -> None:
        self._fake.simular_evento(tenant_id, pagamento_id, status)

    async def webhook(
        self, tenant_id: TenantId, pagamento_id: str, status: StatusPagamento | None
    ) -> tuple[dict[str, str], bytes]:
        return self._fake.assinar_evento(tenant_id, pagamento_id, status)

    async def cobrancas_emitidas(self, tenant_id: TenantId) -> int:
        return self._fake.cobrancas_emitidas(tenant_id)

    def injetar_falha(self, tipo: TipoFalha, operacao: str, *, vezes: int = 1) -> None:
        self._fake.injetar_falha(_FALHAS[tipo], vezes=vezes, operacao=cast("Operacao", operacao))


def fabrica_fake() -> KitGateway:
    """`FakeGatewayPagamento` novo, com relógio parado e um segredo de webhook por tenant."""
    relogio = RelogioFixo()
    fake = FakeGatewayPagamento(
        relogio=relogio,
        segredos_webhook={TENANT_A: ("segredo-a",), TENANT_B: ("segredo-b",)},
    )
    return KitGateway(gateway=fake, controle=ControleFake(fake), relogio=relogio)


GATEWAYS: dict[str, FabricaGateway] = {
    "fake": fabrica_fake,
}
"""Gateways cobertos pela suíte. Um adapter real entra aqui (ver README)."""

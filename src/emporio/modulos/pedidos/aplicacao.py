"""Casos de uso de pedidos: consulta com verificação de identidade (LGPD, premissa #15).

Nenhum dado do pedido é revelado sem número + e-mail ou telefone que correspondam ao cliente do pedido. Pedido
inexistente e prova errada produzem exatamente o mesmo resultado genérico.
"""

from __future__ import annotations

import hmac
import re
from datetime import date

from pydantic import BaseModel, ConfigDict

from emporio.modulos.comum import Dinheiro, Montante, montante
from emporio.modulos.pedidos.dominio import Cliente, StatusPedido
from emporio.modulos.politicas.aplicacao import AvaliacaoDevolucao, ServicoPoliticas
from emporio.portas import Catalogo, Clientes, Pedidos, Relogio, TenantContext

MENSAGEM_NAO_ENCONTRADO = (
    "Não encontrei um pedido com esses dados. Confira o número do pedido e o e-mail ou telefone do cadastro."
)
_TELEFONE_MIN_DIGITOS = 8
_TAMANHO_COM_PAIS = 12  # 55 + DDD + número (mín. 8 dígitos)


class _Dto(BaseModel):
    model_config = ConfigDict(frozen=True)


class ItemDoPedido(_Dto):
    nome: str
    quantidade: int


class ResultadoPedido(_Dto):
    """Resultado de `consultar_pedido`. Sem identidade comprovada, só `encontrado=False` e a mensagem genérica."""

    encontrado: bool
    mensagem: str | None = None
    numero: int | None = None
    status: str | None = None
    cliente_primeiro_nome: str | None = None
    data_pedido: date | None = None
    itens: tuple[ItemDoPedido, ...] = ()
    total: Montante | None = None
    pagamento: str | None = None
    parcelas: int | None = None
    rastreio: str | None = None
    previsao_entrega: date | None = None
    observacoes: str | None = None
    recebimento_estimado: bool = False
    devolucao: AvaliacaoDevolucao | None = None


NAO_ENCONTRADO = ResultadoPedido(encontrado=False, mensagem=MENSAGEM_NAO_ENCONTRADO)


def _digitos(texto: str) -> str:
    digitos = re.sub(r"\D", "", texto)
    return digitos[2:] if digitos.startswith("55") and len(digitos) >= _TAMANHO_COM_PAIS else digitos


def _iguais(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def identidade_confere(cliente: Cliente, prova: str) -> bool:
    """E-mail (sem diferenciar caixa) ou telefone (só dígitos; aceita sem país/DDD, mínimo 8 dígitos)."""
    prova = prova.strip()
    if "@" in prova:
        return _iguais(prova.casefold(), cliente.email.strip().casefold())
    informado = _digitos(prova)
    cadastrado = _digitos(cliente.telefone)
    if len(informado) < _TELEFONE_MIN_DIGITOS or not cadastrado:
        return False
    return _iguais(informado, cadastrado) or _iguais(informado, cadastrado[-len(informado) :])


class ServicoPedidos:
    """Casos de uso de pedidos."""

    def __init__(
        self, pedidos: Pedidos, clientes: Clientes, catalogo: Catalogo, relogio: Relogio, politicas: ServicoPoliticas
    ) -> None:
        self._pedidos = pedidos
        self._clientes = clientes
        self._catalogo = catalogo
        self._relogio = relogio
        self._politicas = politicas

    async def consultar_pedido(self, ctx: TenantContext, numero: int, email_ou_telefone: str) -> ResultadoPedido:
        """Status, itens, total, rastreio e janelas de devolução, só se a prova de identidade conferir."""
        pedido = await self._pedidos.obter_pedido(ctx, numero)
        if pedido is None:
            return NAO_ENCONTRADO
        cliente = await self._clientes.obter_cliente(ctx, pedido.cliente_id)
        if cliente is None or not identidade_confere(cliente, email_ou_telefone):
            return NAO_ENCONTRADO

        itens = []
        for item in pedido.itens:
            produto = await self._catalogo.obter_produto(ctx, item.produto_id)
            itens.append(
                ItemDoPedido(nome=produto.nome if produto else f"Produto {item.produto_id}", quantidade=item.quantidade)
            )

        entregue = pedido.status is StatusPedido.ENTREGUE
        devolucao: AvaliacaoDevolucao | None = None
        if pedido.data_pedido is not None:
            devolucao = self._politicas.avaliar_devolucao(
                data_compra=pedido.data_pedido,
                status=pedido.status.value,
                data_recebimento=pedido.previsao_entrega if entregue else None,
            )
        total = None if pedido.total_centavos is None else montante(Dinheiro(pedido.total_centavos), ctx.idioma.value)
        return ResultadoPedido(
            encontrado=True,
            numero=pedido.numero,
            status=pedido.status.value,
            cliente_primeiro_nome=cliente.nome.split()[0] if cliente.nome.split() else None,
            data_pedido=pedido.data_pedido,
            itens=tuple(itens),
            total=total,
            pagamento=pedido.pagamento.forma.value,
            parcelas=pedido.pagamento.parcelas if pedido.pagamento.parcelas > 1 else None,
            rastreio=pedido.codigo_rastreio,
            previsao_entrega=pedido.previsao_entrega,
            observacoes=pedido.observacoes or None,
            recebimento_estimado=entregue and pedido.previsao_entrega is not None,
            devolucao=devolucao,
        )

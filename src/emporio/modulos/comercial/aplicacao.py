"""Casos de uso comerciais: preço vigente de um produto e simulação de pagamento.

Tudo é recalculado do catálogo; nenhum valor vindo do LLM é aceito. `precificar` é a API que o módulo de catálogo usa
para mostrar preço de tabela, vigente, PIX e parcelamento.

Decisão (premissa nova): o desconto PIX é aplicado **por item** (só nos itens sem promoção ativa); o frete grátis e o
limite de pagamento combinado usam o total do preço **vigente** (antes do desconto PIX).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from emporio.modulos.comercial.dominio import (
    LIMITE_COMBINACAO,
    ErroComercial,
    FormaPagamento,
    Parcelamento,
    PrecoVigente,
    Promocao,
)
from emporio.modulos.comum import Dinheiro, Montante, montante
from emporio.modulos.politicas.aplicacao import FreteResultado, ServicoPoliticas
from emporio.portas import Catalogo, Idioma, TenantContext

__all__ = [
    "FormaPagamento",
    "ItemSimulacao",
    "PlanoParcelas",
    "PrecoProduto",
    "PromocaoEntrada",
    "ResultadoSimulacao",
    "ServicoComercial",
    "precificar",
]


class _Dto(BaseModel):
    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True, slots=True)
class PromocaoEntrada:
    """Promoção de um produto, como o chamador a conhece (percentual inteiro, ativa ou não)."""

    percentual: int
    ativa: bool
    descricao: str = ""


class PlanoParcelas(_Dto):
    """Parcelamento sem juros: a sobra da divisão vai para a 1ª parcela (premissa #17)."""

    parcelas: int
    valor_primeira_parcela: Montante
    valor_demais_parcelas: Montante | None = None
    total: Montante


class PrecoProduto(_Dto):
    """Preços de um produto: tabela, vigente (com promoção ativa), PIX e parcelamento máximo no crédito."""

    preco_tabela: Montante
    preco_vigente: Montante
    desconto_percentual: int | None = None
    descricao_promocao: str | None = None
    em_promocao: bool
    promocao_encerrada: bool
    preco_pix: Montante
    pix_tem_desconto: bool
    parcelas_max: int | None = None
    parcelamento_max: PlanoParcelas | None = None


def _plano(total: Dinheiro, parcelas: int, idioma: Idioma) -> PlanoParcelas:
    valores = Parcelamento.simular(total, parcelas)
    loc = idioma.value
    return PlanoParcelas(
        parcelas=parcelas,
        valor_primeira_parcela=montante(valores[0], loc),
        valor_demais_parcelas=montante(valores[1], loc) if parcelas > 1 else None,
        total=montante(total, loc),
    )


def precificar(preco_centavos: int, promocoes: Sequence[PromocaoEntrada], idioma: Idioma) -> PrecoProduto:
    """Preço vigente, PIX e parcelamento máximo de um produto a partir da tabela e das linhas de promoção.

    Só promoções ativas valem; não acumulam (vence a de maior percentual); PIX não soma com promoção.
    `promocao_encerrada` é verdadeiro quando há linhas de promoção, mas nenhuma vale hoje.
    """
    preco = PrecoVigente(Dinheiro(preco_centavos), [Promocao(p.percentual, p.ativa, p.descricao) for p in promocoes])
    loc = idioma.value
    aplicada = preco.promocao_aplicada
    vigente = preco.preco_vigente
    maximo = Parcelamento.parcelas_max(vigente) if vigente.centavos > 0 else None
    return PrecoProduto(
        preco_tabela=montante(preco.preco_tabela, loc),
        preco_vigente=montante(vigente, loc),
        desconto_percentual=None if aplicada is None else int(aplicada.percentual),
        descricao_promocao=None if aplicada is None or not aplicada.descricao else aplicada.descricao,
        em_promocao=preco.em_promocao,
        promocao_encerrada=bool(promocoes) and not preco.em_promocao,
        preco_pix=montante(preco.preco_pix, loc),
        pix_tem_desconto=preco.pix_tem_desconto,
        parcelas_max=maximo,
        parcelamento_max=None if maximo is None else _plano(vigente, maximo, idioma),
    )


# --- simulação de pagamento -----------------------------------------------------------------------------------------
class ItemSimulacao(BaseModel):
    """Item a simular: só o id e a quantidade. Preço nunca é aceito de fora."""

    produto_id: int
    quantidade: int = Field(ge=1, le=999)


class ItemSimulado(_Dto):
    produto_id: int
    nome: str
    quantidade: int
    preco_unitario: Montante
    subtotal: Montante
    desconto_percentual: int | None = None
    preco_pix_unitario: Montante
    estoque_suficiente: bool


class Simulacao(_Dto):
    """Forma de pagamento escolhida e quanto o cliente paga."""

    forma: str
    parcelas: int
    total: Montante
    valor_primeira_parcela: Montante | None = None
    valor_demais_parcelas: Montante | None = None
    desconto_pix_aplicado: bool = False


class ResultadoSimulacao(_Dto):
    """Resultado da simulação; em erro (`ok=False`) `erro` explica e os demais campos trazem o que foi possível."""

    ok: bool
    erro: str | None = None
    itens: tuple[ItemSimulado, ...] = ()
    total_vigente: Montante | None = None
    total_pix: Montante | None = None
    economia_pix: Montante | None = None
    parcelas_max: int | None = None
    parcelamento_max: PlanoParcelas | None = None
    simulacao: Simulacao | None = None
    pagamento_combinado_permitido: bool | None = None
    frete: FreteResultado | None = None
    avisos: tuple[str, ...] = ()


def _erro(mensagem: str, **extra: object) -> ResultadoSimulacao:
    return ResultadoSimulacao(ok=False, erro=mensagem, **extra)  # type: ignore[arg-type]


class ServicoComercial:
    """Casos de uso comerciais sobre o catálogo."""

    def __init__(self, catalogo: Catalogo, politicas: ServicoPoliticas) -> None:
        self._catalogo = catalogo
        self._politicas = politicas

    async def simular_pagamento(
        self,
        ctx: TenantContext,
        itens: Sequence[ItemSimulacao],
        forma: FormaPagamento | str | None = None,
        parcelas: int | None = None,
        cidade: str | None = None,
    ) -> ResultadoSimulacao:
        """Total, PIX, parcelamento e frete de uma compra, recalculados do catálogo (ignora qualquer preço externo)."""
        if not itens:
            return _erro("Nenhum item informado para simular.")
        try:
            forma_escolhida = None if forma is None else FormaPagamento(forma)
        except ValueError:
            validas = ", ".join(f.value for f in FormaPagamento)
            return _erro(f"Forma de pagamento desconhecida: use uma de {validas}.")

        quantidades: dict[int, int] = {}
        for item in itens:
            quantidades[item.produto_id] = quantidades.get(item.produto_id, 0) + item.quantidade

        loc = ctx.idioma.value
        simulados: list[ItemSimulado] = []
        problemas: list[str] = []
        avisos: list[str] = []
        total_vigente = Dinheiro.zero()
        total_pix = Dinheiro.zero()
        for produto_id, quantidade in quantidades.items():
            produto = await self._catalogo.obter_produto(ctx, produto_id)
            if produto is None:
                problemas.append(f"Produto {produto_id} não encontrado.")
                continue
            if produto.status != "active":
                problemas.append(f"Produto {produto_id} ({produto.nome}) não está à venda no momento.")
                continue
            promocoes = [
                PromocaoEntrada(p.desconto_percentual, p.ativa, p.descricao)
                for p in await self._catalogo.listar_promocoes(ctx, produto_id)
            ]
            preco = precificar(produto.preco_centavos, promocoes, ctx.idioma)
            vigente = Dinheiro(preco.preco_vigente.centavos)
            pix = Dinheiro(preco.preco_pix.centavos)
            total_vigente += vigente * quantidade
            total_pix += pix * quantidade
            suficiente = produto.estoque >= quantidade
            if not suficiente:
                avisos.append(f"Estoque insuficiente de '{produto.nome}': restam {produto.estoque}.")
            simulados.append(
                ItemSimulado(
                    produto_id=produto_id,
                    nome=produto.nome,
                    quantidade=quantidade,
                    preco_unitario=preco.preco_vigente,
                    subtotal=montante(vigente * quantidade, loc),
                    desconto_percentual=preco.desconto_percentual,
                    preco_pix_unitario=preco.preco_pix,
                    estoque_suficiente=suficiente,
                )
            )
        if problemas:
            return _erro(" ".join(problemas))

        maximo = Parcelamento.parcelas_max(total_vigente)
        base: dict[str, object] = {
            "itens": tuple(simulados),
            "total_vigente": montante(total_vigente, loc),
            "total_pix": montante(total_pix, loc),
            "economia_pix": montante(total_vigente - total_pix, loc),
            "parcelas_max": maximo,
            "parcelamento_max": _plano(total_vigente, maximo, ctx.idioma),
            "pagamento_combinado_permitido": total_vigente > LIMITE_COMBINACAO,
            "avisos": tuple(avisos),
        }
        if cidade is not None:
            base["frete"] = self._politicas.cotar_frete(ctx, cidade, total_vigente.centavos)

        try:
            simulacao = self._simular_forma(
                ctx, forma_escolhida, parcelas, total_vigente=total_vigente, total_pix=total_pix, maximo=maximo
            )
        except ErroComercial as erro:
            return _erro(str(erro), **base)
        return ResultadoSimulacao(ok=True, simulacao=simulacao, **base)  # type: ignore[arg-type]

    @staticmethod
    def _simular_forma(
        ctx: TenantContext,
        forma: FormaPagamento | None,
        parcelas: int | None,
        *,
        total_vigente: Dinheiro,
        total_pix: Dinheiro,
        maximo: int,
    ) -> Simulacao | None:
        loc = ctx.idioma.value
        if forma is None and parcelas is None:
            return None
        if forma is None:
            forma = FormaPagamento.CREDITO
        if forma is not FormaPagamento.CREDITO:
            if parcelas not in (None, 1):
                raise ErroComercial("Só o cartão de crédito pode ser parcelado; PIX, débito e boleto são à vista.")
            total = total_pix if forma is FormaPagamento.PIX else total_vigente
            return Simulacao(
                forma=forma.value,
                parcelas=1,
                total=montante(total, loc),
                desconto_pix_aplicado=forma is FormaPagamento.PIX and total_pix < total_vigente,
            )
        n = maximo if parcelas is None else parcelas
        plano = _plano(total_vigente, n, ctx.idioma)
        return Simulacao(
            forma=forma.value,
            parcelas=n,
            total=plano.total,
            valor_primeira_parcela=plano.valor_primeira_parcela,
            valor_demais_parcelas=plano.valor_demais_parcelas,
        )

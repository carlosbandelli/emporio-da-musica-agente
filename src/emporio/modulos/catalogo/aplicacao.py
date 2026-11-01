"""Casos de uso do catálogo: busca e detalhe de produtos (preço vigente via `comercial.aplicacao`).

Estoque é sempre lido do repositório a cada chamada (nunca guardado). Resultados são compactos: sem despejo de specs
nem catálogo completo. Termos de acessórios (fora de escopo) vêm da configuração do tenant.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Final

from pydantic import BaseModel, ConfigDict

from emporio.modulos.catalogo.dominio import Categoria, Produto, StatusProduto
from emporio.modulos.comercial.aplicacao import PlanoParcelas, PrecoProduto, PromocaoEntrada, precificar
from emporio.modulos.comum import Dinheiro, Montante
from emporio.portas import Catalogo, FiltroProdutos, ResolvedorConfig, TenantContext

MAX_RESULTADOS: Final = 10
MAX_ALTERNATIVAS_POR_PRODUTO: Final = 3
MAX_ALTERNATIVAS: Final = 4
MAX_CANDIDATOS: Final = 5
MAX_SPECS: Final = 6
LIMITE_SIMILARIDADE: Final = 0.78
_TAMANHO_MIN_RADICAL: Final = 3

_PALAVRAS_VAZIAS: Final = frozenset({"e", "de", "do", "da", "dos", "das", "para", "com", "a", "o", "and", "of"})


class _Dto(BaseModel):
    model_config = ConfigDict(frozen=True)


class ItemProduto(_Dto):
    """Produto resumido para listas."""

    id: int
    nome: str
    categoria: str
    preco_tabela: Montante
    preco_vigente: Montante
    desconto_percentual: int | None = None
    em_estoque: bool
    substitui_id: int | None = None


class ResultadoBusca(_Dto):
    """Resultado de `buscar_produtos`."""

    produtos: tuple[ItemProduto, ...] = ()
    total_encontrado: int = 0
    alternativas: tuple[ItemProduto, ...] = ()
    fora_de_escopo: bool = False
    motivo: str | None = None
    sem_itens_na_categoria: bool = False
    categoria_nao_encontrada: bool = False
    categorias_validas: tuple[str, ...] = ()
    observacao: str | None = None


class DetalheProduto(_Dto):
    """Resultado de `detalhar_produto`."""

    encontrado: bool
    ambiguo: bool = False
    candidatos: tuple[ItemProduto, ...] = ()
    fora_de_escopo: bool = False
    motivo: str | None = None
    id: int | None = None
    nome: str | None = None
    categoria: str | None = None
    descricao: str | None = None
    status: str | None = None
    disponibilidade: str | None = None
    em_estoque: bool | None = None
    estoque: int | None = None
    preco_tabela: Montante | None = None
    preco_vigente: Montante | None = None
    desconto_percentual: int | None = None
    descricao_promocao: str | None = None
    promocao_encerrada: bool | None = None
    preco_pix: Montante | None = None
    pix_tem_desconto: bool | None = None
    parcelas_max: int | None = None
    parcelamento_max: PlanoParcelas | None = None
    specs: Mapping[str, str] | None = None
    equivalentes: tuple[ItemProduto, ...] = ()
    alternativas: tuple[ItemProduto, ...] = ()
    observacao: str | None = None


# --- texto -----------------------------------------------------------------------------------------------------------
def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).casefold()


def _radical(token: str) -> str:
    """Tira o plural simples (violões -> violao, guitarras -> guitarra) para casar singular e plural."""
    t = _normalizar(token)
    if len(t) > _TAMANHO_MIN_RADICAL and t.endswith(("oes", "aes")):
        return t[:-3] + "ao"
    if len(t) > _TAMANHO_MIN_RADICAL and t.endswith("ns"):
        return t[:-2] + "m"
    if len(t) > _TAMANHO_MIN_RADICAL and t.endswith("s"):
        return t[:-1]
    return t


def _tokens(texto: str) -> list[str]:
    return re.findall(r"\w+", _normalizar(texto))


def _radicais(texto: str) -> list[str]:
    return [_radical(t) for t in _tokens(texto) if t not in _PALAVRAS_VAZIAS]


def _termos_detectados(texto: str, termos: tuple[str, ...]) -> list[str]:
    """Termos de acessório presentes no texto (palavra inteira; singular ou plural)."""
    corpo = f" {' '.join(_tokens(texto))} "
    achados = []
    for termo in termos:
        norm = " ".join(_tokens(termo))
        if any(f" {variante} " in corpo for variante in (norm, _radical(norm))):
            achados.append(termo)
    return achados


_PALAVRAS_INSTRUMENTO_EXTRAS = ("violao", "instrumento")
_TAMANHO_MIN_PALAVRA_INSTRUMENTO: Final = 4  # ignora "e", "de" etc. nos nomes de categoria


def _menciona_instrumento(texto: str, categorias: Sequence[Categoria], termos_acessorio: tuple[str, ...]) -> bool:
    """O pedido é de um instrumento com uma característica, e não de um acessório?

    Verdadeiro quando uma palavra de instrumento (nome de categoria) aparece ANTES do primeiro termo de
    acessório: "violão com cordas de aço" → instrumento; "cordas para violão" → acessório.
    Palavras que também são termos de acessório (ex.: "cordas" em "Cordas Orquestrais") não contam.
    """
    acessorio = {tok for termo in termos_acessorio for tok in _tokens(termo)}
    acessorio |= {_radical(tok) for tok in acessorio}
    instrumento = {tok for c in categorias for tok in _tokens(c.nome) if len(tok) >= _TAMANHO_MIN_PALAVRA_INSTRUMENTO}
    instrumento.update(_PALAVRAS_INSTRUMENTO_EXTRAS)
    instrumento -= acessorio
    instrumento |= {_radical(tok) for tok in instrumento}
    for tok in _tokens(texto):
        if tok in acessorio or _radical(tok) in acessorio:
            return False
        if tok in instrumento or _radical(tok) in instrumento:
            return True
    return False


def _sem_termos(texto: str, termos: list[str]) -> str:
    """Remove do texto as palavras dos termos de acessório detectados (e suas variantes de plural)."""
    remover = {tok for termo in termos for tok in _tokens(termo)}
    remover |= {_radical(tok) for tok in remover}
    return " ".join(t for t in _tokens(texto) if t not in remover and _radical(t) not in remover)


def _resumo_spec(valor: object) -> str:
    texto = ", ".join(str(v) for v in valor) if isinstance(valor, list | tuple) else str(valor)
    return texto[:60]


class ServicoCatalogo:
    """Casos de uso de busca e detalhe de produtos."""

    def __init__(self, catalogo: Catalogo, resolver_config: ResolvedorConfig) -> None:
        self._catalogo = catalogo
        self._config = resolver_config

    # --- helpers de preço --------------------------------------------------------------------------------------------
    async def _precificar(self, ctx: TenantContext, produto: Produto) -> PrecoProduto:
        promocoes = [
            PromocaoEntrada(p.desconto_percentual, p.ativa, p.descricao)
            for p in await self._catalogo.listar_promocoes(ctx, produto.id)
        ]
        return precificar(produto.preco_centavos, promocoes, ctx.idioma)

    def _item(
        self, produto: Produto, preco: PrecoProduto, nomes: Mapping[int, str], substitui_id: int | None = None
    ) -> ItemProduto:
        return ItemProduto(
            id=produto.id,
            nome=produto.nome,
            categoria=nomes.get(produto.categoria_id, str(produto.categoria_id)),
            preco_tabela=preco.preco_tabela,
            preco_vigente=preco.preco_vigente,
            desconto_percentual=preco.desconto_percentual,
            em_estoque=produto.estoque > 0,
            substitui_id=substitui_id,
        )

    async def _nomes_categorias(self, ctx: TenantContext) -> dict[int, str]:
        return {c.id: c.nome for c in await self._catalogo.listar_categorias(ctx)}

    async def _similares(
        self,
        ctx: TenantContext,
        produto: Produto,
        referencia: Dinheiro,
        nomes: Mapping[int, str],
        *,
        excluir: set[int],
        limite: int = MAX_ALTERNATIVAS_POR_PRODUTO,
    ) -> tuple[ItemProduto, ...]:
        """Produtos ativos, em estoque, da mesma categoria, do preço vigente mais próximo de `referencia`."""
        candidatos = await self._catalogo.listar_produtos(ctx, FiltroProdutos(categoria_id=produto.categoria_id))
        ranqueados = []
        for c in candidatos:
            if c.id == produto.id or c.id in excluir or c.estoque <= 0 or c.status is not StatusProduto.ATIVO:
                continue
            preco = await self._precificar(ctx, c)
            distancia = abs(preco.preco_vigente.centavos - referencia.centavos)
            ranqueados.append((distancia, c.id, c, preco))
        ranqueados.sort(key=lambda r: (r[0], r[1]))
        return tuple(self._item(c, preco, nomes, substitui_id=produto.id) for _, _, c, preco in ranqueados[:limite])

    # --- categoria ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _resolver_categoria(categoria: int | str, categorias: tuple[Categoria, ...]) -> Categoria | None:
        if isinstance(categoria, int) or str(categoria).strip().isdigit():
            alvo = int(categoria)
            return next((c for c in categorias if c.id == alvo), None)
        pedidos = set(_radicais(str(categoria)))
        if not pedidos:
            return None
        for campo in (lambda c: c.nome, lambda c: f"{c.nome} {c.descricao}"):
            for c in categorias:
                if pedidos <= set(_radicais(campo(c))):
                    return c
        return None

    # --- buscar_produtos ---------------------------------------------------------------------------------------------
    async def buscar_produtos(
        self,
        ctx: TenantContext,
        *,
        texto: str | None = None,
        categoria: int | str | None = None,
        preco_min_reais: Decimal | None = None,
        preco_max_reais: Decimal | None = None,
    ) -> ResultadoBusca:
        """Busca produtos ativos por texto, categoria (id ou nome) e faixa de preço vigente (em reais)."""
        config = self._config(ctx.tenant_id)
        categorias = await self._catalogo.listar_categorias(ctx)
        if texto:
            termos = (*config.termos_fora_de_escopo.pt_br, *config.termos_fora_de_escopo.en_us)
            achados = _termos_detectados(texto, termos)
            if achados and _menciona_instrumento(texto, categorias, termos):
                # "violão com cordas de aço": o cliente quer um instrumento; o acessório é só característica.
                texto = _sem_termos(texto, achados) or None
            elif achados:
                return ResultadoBusca(
                    fora_de_escopo=True,
                    motivo=(
                        "A loja vende apenas instrumentos musicais, sem acessórios "
                        f"(detectado: {', '.join(achados)}). Redirecione com educação, sem inventar lojas parceiras."
                    ),
                    observacao="Se o cliente quer um instrumento com essa característica, busque sem o termo.",
                )

        nomes = {c.id: c.nome for c in categorias}
        categoria_id: int | None = None
        if categoria is not None and str(categoria).strip():
            achada = self._resolver_categoria(categoria, categorias)
            if achada is None:
                return ResultadoBusca(
                    categoria_nao_encontrada=True,
                    categorias_validas=tuple(c.nome for c in categorias),
                    observacao="Categoria não reconhecida; use uma das categorias válidas.",
                )
            categoria_id = achada.id
            if not await self._catalogo.listar_produtos(
                ctx, FiltroProdutos(categoria_id=achada.id, incluir_inativos=True)
            ):
                return ResultadoBusca(
                    sem_itens_na_categoria=True,
                    observacao=(
                        f"No momento não há itens da categoria '{achada.nome}' no catálogo "
                        "(não afirme que a loja não vende esse tipo de instrumento; "
                        "ofereça o que existe ou registre o interesse)."
                    ),
                )

        piso = None if preco_min_reais is None else Dinheiro.de_reais(preco_min_reais).centavos
        teto = None if preco_max_reais is None else Dinheiro.de_reais(preco_max_reais).centavos
        filtro = FiltroProdutos(
            texto=" ".join(_radicais(texto)) if texto else None,
            categoria_id=categoria_id,
            preco_min_centavos=piso,  # vigente <= tabela: tabela >= piso é condição necessária
        )
        encontrados = []
        for produto in await self._catalogo.listar_produtos(ctx, filtro):
            preco = await self._precificar(ctx, produto)
            vigente = preco.preco_vigente.centavos
            if (piso is not None and vigente < piso) or (teto is not None and vigente > teto):
                continue
            encontrados.append((produto, preco))
        encontrados.sort(key=lambda par: (par[0].estoque <= 0, par[1].preco_vigente.centavos, par[0].id))
        mostrados = encontrados[:MAX_RESULTADOS]
        itens = tuple(self._item(p, preco, nomes) for p, preco in mostrados)

        alternativas: list[ItemProduto] = []
        ja_listados = {p.id for p, _ in mostrados}
        for produto, preco in mostrados:
            if produto.estoque > 0 or len(alternativas) >= MAX_ALTERNATIVAS:
                continue
            for alt in await self._similares(
                ctx, produto, Dinheiro(preco.preco_vigente.centavos), nomes, excluir=ja_listados
            ):
                if len(alternativas) < MAX_ALTERNATIVAS and alt.id not in {a.id for a in alternativas}:
                    alternativas.append(alt)
        return ResultadoBusca(
            produtos=itens,
            total_encontrado=len(encontrados),
            alternativas=tuple(alternativas),
            observacao=None if itens else "Nenhum produto ativo encontrado com esses filtros.",
        )

    # --- detalhar_produto --------------------------------------------------------------------------------------------
    async def _resolver_por_nome(self, ctx: TenantContext, nome: str) -> list[Produto]:
        radicais = _radicais(nome)
        if not radicais:
            return []
        todos = await self._catalogo.listar_produtos(
            ctx, FiltroProdutos(texto=" ".join(radicais), incluir_inativos=True)
        )
        no_nome = [p for p in todos if all(r in _normalizar(p.nome) for r in radicais)]
        achados = no_nome or list(todos)
        if achados:
            exatos = [p for p in achados if _normalizar(p.nome) == _normalizar(nome).strip()]
            return exatos or achados
        # Aproximação (erro de digitação): cada termo do pedido precisa parecer com algum termo do nome.
        pedido = " ".join(radicais)
        pontuados = []
        for p in await self._catalogo.listar_produtos(ctx, FiltroProdutos(incluir_inativos=True)):
            palavras = _radicais(p.nome)
            if not palavras:
                continue
            parecidos = [max(SequenceMatcher(None, r, w).ratio() for w in palavras) for r in radicais]
            if min(parecidos) >= LIMITE_SIMILARIDADE:
                pontuados.append(
                    (-sum(parecidos) / len(parecidos), SequenceMatcher(None, pedido, " ".join(palavras)).ratio(), p)
                )
        pontuados.sort(key=lambda t: (t[0], -t[1], t[2].id))
        if not pontuados:
            return []
        melhor = pontuados[0][0]
        return [p for score, _, p in pontuados if score == melhor]

    async def detalhar_produto(
        self, ctx: TenantContext, *, produto_id: int | None = None, nome: str | None = None
    ) -> DetalheProduto:
        """Detalhes de um produto (por id ou nome): estoque lido na hora, preços, PIX, parcelas e alternativas."""
        if produto_id is None and not (nome and nome.strip()):
            return DetalheProduto(encontrado=False, observacao="Informe o id ou o nome do produto.")
        nomes = await self._nomes_categorias(ctx)
        if produto_id is not None:
            produto = await self._catalogo.obter_produto(ctx, produto_id)
            if produto is None:
                return DetalheProduto(encontrado=False, observacao="Produto não encontrado pelo id informado.")
        else:
            assert nome is not None
            candidatos = await self._resolver_por_nome(ctx, nome)
            if not candidatos:
                return self._nao_encontrado(ctx, nome)
            if len(candidatos) > 1:
                itens = []
                for c in candidatos[:MAX_CANDIDATOS]:
                    itens.append(self._item(c, await self._precificar(ctx, c), nomes))
                return DetalheProduto(
                    encontrado=False,
                    ambiguo=True,
                    candidatos=tuple(itens),
                    observacao="Mais de um produto combina; peça ao cliente para escolher.",
                )
            produto = candidatos[0]
        return await self._detalhe(ctx, produto, nomes)

    def _nao_encontrado(self, ctx: TenantContext, nome: str) -> DetalheProduto:
        config = self._config(ctx.tenant_id)
        termos = (*config.termos_fora_de_escopo.pt_br, *config.termos_fora_de_escopo.en_us)
        achados = _termos_detectados(nome, termos)
        if achados:
            return DetalheProduto(
                encontrado=False,
                fora_de_escopo=True,
                motivo="A loja vende apenas instrumentos musicais, sem acessórios. Redirecione com educação.",
            )
        return DetalheProduto(encontrado=False, observacao="Nenhum produto encontrado com esse nome.")

    async def _detalhe(self, ctx: TenantContext, produto: Produto, nomes: Mapping[int, str]) -> DetalheProduto:
        preco = await self._precificar(ctx, produto)
        em_estoque = produto.estoque > 0
        ativo = produto.status is StatusProduto.ATIVO
        referencia = Dinheiro(preco.preco_vigente.centavos)
        equivalentes: tuple[ItemProduto, ...] = ()
        alternativas: tuple[ItemProduto, ...] = ()
        observacao: str | None = None
        if produto.status is StatusProduto.DESCONTINUADO:
            disponibilidade = "descontinuado"
            equivalentes = await self._similares(ctx, produto, referencia, nomes, excluir=set())
            observacao = "Produto descontinuado: não é mais vendido; ofereça os equivalentes."
        elif produto.status is StatusProduto.EM_BREVE:
            disponibilidade = "em_breve"
            observacao = "Produto ainda não está disponível para compra (em breve)."
        elif not em_estoque:
            disponibilidade = "sem_estoque"
            alternativas = await self._similares(ctx, produto, referencia, nomes, excluir=set())
            observacao = "Sem estoque agora; ofereça as alternativas."
        else:
            disponibilidade = "disponivel"
        if preco.promocao_encerrada:
            aviso = "A promoção deste produto já terminou; vale o preço atual."
            observacao = f"{observacao} {aviso}" if observacao else aviso
        return DetalheProduto(
            encontrado=True,
            id=produto.id,
            nome=produto.nome,
            categoria=nomes.get(produto.categoria_id, str(produto.categoria_id)),
            descricao=produto.descricao[:300],
            status=produto.status.value,
            disponibilidade=disponibilidade,
            em_estoque=em_estoque,
            estoque=produto.estoque,
            preco_tabela=preco.preco_tabela,
            preco_vigente=preco.preco_vigente,
            desconto_percentual=preco.desconto_percentual,
            descricao_promocao=preco.descricao_promocao,
            promocao_encerrada=preco.promocao_encerrada,
            preco_pix=preco.preco_pix if ativo else None,
            pix_tem_desconto=preco.pix_tem_desconto if ativo else None,
            parcelas_max=preco.parcelas_max if ativo else None,
            parcelamento_max=preco.parcelamento_max if ativo else None,
            specs={str(k): _resumo_spec(v) for k, v in list(produto.specs.items())[:MAX_SPECS]},
            equivalentes=equivalentes,
            alternativas=alternativas,
            observacao=observacao,
        )

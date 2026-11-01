"""Atendimento: registro de solicitações e a fachada de tools (`FerramentasLoja`) entregue ao agente.

A fachada reúne os casos de uso dos demais módulos (só via `aplicacao`), valida os argumentos com Pydantic, filtra as
tools pelo papel (arquitetura §8) e devolve resultados JSON compactos. Nenhuma dependência de LLM.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from emporio.modulos.atendimento.dominio import Solicitacao, TipoSolicitacao
from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from emporio.modulos.comercial.aplicacao import ItemSimulacao, ServicoComercial
from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from emporio.portas import DefinicaoTool, Papel, Relogio, Solicitacoes, TenantContext

logger = logging.getLogger(__name__)

PRAZO_RETORNO_HORAS_UTEIS: Final = 24
SESSAO_DESCONHECIDA: Final = "sem-sessao"
_DESCRICAO_MAX: Final = 1000


# --- registrar_solicitacao -------------------------------------------------------------------------------------------
class _Dto(BaseModel):
    model_config = ConfigDict(frozen=True)


class ResultadoSolicitacao(_Dto):
    """Resultado de `registrar_solicitacao`: protocolo e prazo de retorno (premissa #16)."""

    ok: bool
    erro: str | None = None
    protocolo: str | None = None
    tipo: str | None = None
    prazo_retorno_horas_uteis: int | None = None
    mensagem: str | None = None
    contato_informado: bool | None = None
    aviso: str | None = None


def _normalizar(descricao: str) -> str:
    return " ".join(descricao.casefold().split())


class ServicoAtendimento:
    """Registro de solicitações para a equipe."""

    def __init__(self, solicitacoes: Solicitacoes, relogio: Relogio) -> None:
        self._solicitacoes = solicitacoes
        self._relogio = relogio

    @staticmethod
    def chave_derivada(ctx: TenantContext, sessao_id: str, tipo: TipoSolicitacao, descricao: str) -> str:
        """Chave determinística: tenant + sessão + tipo + descrição normalizada (caixa e espaços ignorados)."""
        bruto = "\x1f".join((ctx.tenant_id, sessao_id, tipo.value, _normalizar(descricao)))
        return hashlib.sha256(bruto.encode()).hexdigest()

    async def registrar_solicitacao(
        self,
        ctx: TenantContext,
        *,
        tipo: TipoSolicitacao | str,
        descricao: str,
        sessao_id: str,
        contato: str | None = None,
        chave_idempotencia: str | None = None,
    ) -> ResultadoSolicitacao:
        """Grava a solicitação (idempotente) e devolve o protocolo; o retorno é em até 24h úteis."""
        try:
            tipo_ok = TipoSolicitacao(tipo)
        except ValueError:
            validos = ", ".join(t.value for t in TipoSolicitacao)
            return ResultadoSolicitacao(ok=False, erro=f"Tipo inválido; use um de: {validos}.")
        texto = " ".join(descricao.split())
        if not texto:
            return ResultadoSolicitacao(ok=False, erro="Descreva a solicitação para registrar.")
        contato_limpo = contato.strip() if contato and contato.strip() else None
        chave = chave_idempotencia or self.chave_derivada(ctx, sessao_id, tipo_ok, texto)
        protocolo = "EMP-" + hashlib.sha256(f"{ctx.tenant_id}|{chave}".encode()).hexdigest()[:10].upper()
        gravada = await self._solicitacoes.registrar(
            ctx,
            Solicitacao(
                id=protocolo,
                tipo=tipo_ok,
                descricao=texto[:_DESCRICAO_MAX],
                sessao_id=sessao_id,
                chave_idempotencia=chave,
                criada_em=self._relogio.agora(),
                contato=contato_limpo,
            ),
        )
        return ResultadoSolicitacao(
            ok=True,
            protocolo=gravada.id,
            tipo=gravada.tipo.value,
            prazo_retorno_horas_uteis=PRAZO_RETORNO_HORAS_UTEIS,
            mensagem=f"Solicitação registrada. A equipe retorna em até {PRAZO_RETORNO_HORAS_UTEIS} horas úteis.",
            contato_informado=gravada.contato is not None,
            aviso=None if gravada.contato else "Nenhum contato informado: peça WhatsApp ou e-mail ao cliente.",
        )


# --- fachada de tools ------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ResultadoTool:
    """Resultado de uma tool. Sempre serializável em JSON; `nome` permite ao motor registrar a tool chamada (I6)."""

    nome: str
    ok: bool
    dados: Mapping[str, Any] = field(default_factory=dict)
    erro: str | None = None
    codigo_erro: str | None = None

    def para_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {"nome": self.nome, "ok": self.ok}
        if self.ok:
            base["dados"] = dict(self.dados)
        else:
            base["erro"] = self.erro
            base["codigo_erro"] = self.codigo_erro
        return base

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False, separators=(",", ":"))


ExecutorTool = Callable[[TenantContext, Any, str | None], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class Tool:
    """Tool registrada: argumentos (modelo Pydantic), papéis autorizados e função que a executa."""

    nome: str
    descricao: str
    argumentos: type[BaseModel]
    papeis: frozenset[Papel]
    funcao: ExecutorTool


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArgsBuscarProdutos(_Args):
    texto: str | None = Field(None, description="Palavras da busca: marca, modelo ou tipo (ex.: 'Yamaha', 'nylon').")
    categoria: str | None = Field(
        None, description="Categoria por nome (Guitarras, Baixos, Violões, Ukuleles, Teclados e Pianos...) ou id."
    )
    preco_min: float | None = Field(None, ge=0, description="Preço mínimo em reais (compara com o preço vigente).")
    preco_max: float | None = Field(None, ge=0, description="Preço máximo em reais (compara com o preço vigente).")

    @field_validator("categoria", mode="before")
    @classmethod
    def _categoria_texto(cls, valor: object) -> object:
        return str(valor) if isinstance(valor, int) and not isinstance(valor, bool) else valor


class ArgsDetalharProduto(_Args):
    produto_id: int | None = Field(None, description="Id do produto, se já conhecido de uma busca anterior.")
    nome: str | None = Field(None, description="Nome ou parte do nome do produto (ex.: 'Takamine GD20').")

    @model_validator(mode="after")
    def _um_dos_dois(self) -> ArgsDetalharProduto:
        if self.produto_id is None and not (self.nome and self.nome.strip()):
            raise ValueError("informe produto_id ou nome")
        return self


class ArgsSimularPagamento(_Args):
    itens: list[ItemSimulacao] = Field(min_length=1, description="Produtos e quantidades (só ids; nunca preços).")
    forma: Literal["pix", "credito", "debito", "boleto"] | None = Field(None, description="Forma de pagamento.")
    parcelas: int | None = Field(None, ge=1, description="Parcelas no crédito (1 a 12, sem juros).")
    cidade: str | None = Field(None, description="Cidade de entrega, para calcular o frete.")


class ArgsConsultarPedido(_Args):
    numero: int = Field(description="Número do pedido.")
    email_ou_telefone: str = Field(description="E-mail ou telefone do cadastro do cliente (prova de identidade).")


class ArgsStatusLoja(_Args):
    pass


class ArgsRegistrarSolicitacao(_Args):
    tipo: Literal["reclamacao", "cotacao_frete", "outros"] = Field(description="Tipo da solicitação.")
    descricao: str = Field(min_length=3, max_length=_DESCRICAO_MAX, description="Resumo do pedido do cliente.")
    contato: str | None = Field(None, description="WhatsApp ou e-mail para a equipe retornar.")


def _inline(no: Any, defs: Mapping[str, Any], dentro_de_properties: bool = False) -> Any:
    """Simplifica o JSON Schema do Pydantic: resolve `$ref`, tira `title` e vira `anyOf` com null em tipo simples."""
    if isinstance(no, list):
        return [_inline(x, defs) for x in no]
    if not isinstance(no, dict):
        return no
    if "$ref" in no:
        alvo = defs[no["$ref"].rsplit("/", 1)[-1]]
        return _inline({**alvo, **{k: v for k, v in no.items() if k != "$ref"}}, defs)
    if "anyOf" in no:
        opcoes = [o for o in no["anyOf"] if o.get("type") != "null"]
        if len(opcoes) == 1:
            return _inline({**{k: v for k, v in no.items() if k != "anyOf"}, **opcoes[0]}, defs)
    saida: dict[str, Any] = {}
    for chave, valor in no.items():
        if (
            chave == "$defs"
            or (chave == "title" and not dentro_de_properties)
            or (chave == "default" and valor is None)
        ):
            continue
        saida[chave] = _inline(valor, defs, dentro_de_properties=chave == "properties")
    return saida


def _compactar(no: Any) -> Any:
    """Remove listas e objetos vazios (economiza tokens do LLM); números e booleanos ficam."""
    if isinstance(no, dict):
        compactado = {k: _compactar(v) for k, v in no.items()}
        return {k: v for k, v in compactado.items() if not (isinstance(v, list | dict) and not v)}
    if isinstance(no, list):
        return [_compactar(v) for v in no]
    return no


def _schema(modelo: type[BaseModel]) -> dict[str, Any]:
    bruto = modelo.model_json_schema()
    schema: dict[str, Any] = _inline(bruto, bruto.get("$defs", {}))
    schema.setdefault("properties", {})
    return schema


_TODOS_OS_PAPEIS: Final = frozenset(Papel)


class FerramentasLoja:
    """Fachada das tools da loja. Construída com os casos de uso; sem I/O próprio nem estado."""

    def __init__(
        self,
        *,
        catalogo: ServicoCatalogo,
        comercial: ServicoComercial,
        pedidos: ServicoPedidos,
        politicas: ServicoPoliticas,
        atendimento: ServicoAtendimento,
    ) -> None:
        self._catalogo = catalogo
        self._comercial = comercial
        self._pedidos = pedidos
        self._politicas = politicas
        self._atendimento = atendimento
        self._tools: dict[str, Tool] = {}
        for tool in self._tools_padrao():
            self.registrar(tool)

    def registrar(self, tool: Tool) -> None:
        """Acrescenta uma tool (ex.: administrativa). O filtro por papel vale para todas."""
        self._tools[tool.nome] = tool

    def definicoes(self, papel: Papel) -> tuple[DefinicaoTool, ...]:
        """Tools que o papel pode usar, com JSON Schema dos argumentos."""
        return tuple(
            DefinicaoTool(nome=t.nome, descricao=t.descricao, parametros=_schema(t.argumentos))
            for t in self._tools.values()
            if papel in t.papeis
        )

    async def executar(
        self, ctx: TenantContext, nome: str, argumentos: Mapping[str, object], *, sessao_id: str | None = None
    ) -> ResultadoTool:
        """Executa uma tool. Nunca levanta: qualquer erro vira `ResultadoTool(ok=False)` com `codigo_erro`."""
        tool = self._tools.get(nome)
        if tool is None:
            return ResultadoTool(nome, False, erro=f"Tool desconhecida: {nome}.", codigo_erro="tool_desconhecida")
        if ctx.papel not in tool.papeis:
            return ResultadoTool(
                nome, False, erro="Esta tool não está disponível para o seu papel.", codigo_erro="tool_nao_permitida"
            )
        try:
            args = tool.argumentos.model_validate(dict(argumentos))
        except ValidationError as erro:
            detalhes = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'argumentos'}: {e['msg']}" for e in erro.errors()
            )
            return ResultadoTool(
                nome, False, erro=f"Argumentos inválidos ({detalhes}).", codigo_erro="argumentos_invalidos"
            )
        try:
            resultado = await tool.funcao(ctx, args, sessao_id)
            dados = _compactar(resultado.model_dump(mode="json", exclude_none=True))
        except Exception:
            logger.exception("Falha inesperada na tool %s", nome)
            return ResultadoTool(nome, False, erro="Falha interna ao executar a consulta.", codigo_erro="erro_interno")
        return ResultadoTool(nome, True, dados=dados)

    # --- tools padrão ------------------------------------------------------------------------------------------------
    def _tools_padrao(self) -> tuple[Tool, ...]:
        async def buscar(ctx: TenantContext, a: ArgsBuscarProdutos, _: str | None) -> BaseModel:
            return await self._catalogo.buscar_produtos(
                ctx,
                texto=a.texto,
                categoria=a.categoria,
                preco_min_reais=None if a.preco_min is None else Decimal(str(a.preco_min)),
                preco_max_reais=None if a.preco_max is None else Decimal(str(a.preco_max)),
            )

        async def detalhar(ctx: TenantContext, a: ArgsDetalharProduto, _: str | None) -> BaseModel:
            return await self._catalogo.detalhar_produto(ctx, produto_id=a.produto_id, nome=a.nome)

        async def simular(ctx: TenantContext, a: ArgsSimularPagamento, _: str | None) -> BaseModel:
            return await self._comercial.simular_pagamento(
                ctx, a.itens, forma=a.forma, parcelas=a.parcelas, cidade=a.cidade
            )

        async def pedido(ctx: TenantContext, a: ArgsConsultarPedido, _: str | None) -> BaseModel:
            return await self._pedidos.consultar_pedido(ctx, a.numero, a.email_ou_telefone)

        async def loja(ctx: TenantContext, a: ArgsStatusLoja, _: str | None) -> BaseModel:
            return self._politicas.status_loja(ctx)

        async def solicitar(ctx: TenantContext, a: ArgsRegistrarSolicitacao, sessao_id: str | None) -> BaseModel:
            return await self._atendimento.registrar_solicitacao(
                ctx,
                tipo=a.tipo,
                descricao=a.descricao,
                sessao_id=sessao_id or SESSAO_DESCONHECIDA,
                contato=a.contato,
            )

        def tool(nome: str, descricao: str, args: type[BaseModel], funcao: ExecutorTool) -> Tool:
            return Tool(nome, descricao, args, _TODOS_OS_PAPEIS, funcao)

        return (
            tool(
                "buscar_produtos",
                "Lista instrumentos do catálogo por texto, categoria e/ou faixa de preço. Use para perguntas como "
                "'violões até R$ 1.000' ou 'tem guitarra Fender?'. Compara a faixa com o preço vigente (com promoção). "
                "Mostra só produtos ativos e sugere alternativas quando o item está sem estoque. Se o pedido for de "
                "acessório (cordas, palhetas, cabos, cases, pedais, amplificadores), devolve fora_de_escopo: "
                "a loja só vende instrumentos. Nunca cite preço ou estoque sem chamar esta tool ou detalhar_produto.",
                ArgsBuscarProdutos,
                buscar,
            ),
            tool(
                "detalhar_produto",
                "Detalhes de UM produto pelo nome ou id: estoque atual, preço de tabela, preço vigente, promoção, "
                "preço no PIX, parcelamento máximo, specs e disponibilidade. Use para 'quanto custa o Takamine GD20?' "
                "ou 'tem em estoque?'. Produto descontinuado vem com equivalentes; sem estoque vem com alternativas; "
                "promoção encerrada é sinalizada. Se o nome for ambíguo, devolve candidatos para o cliente escolher.",
                ArgsDetalharProduto,
                detalhar,
            ),
            tool(
                "simular_pagamento",
                "Calcula total, preço no PIX (5% só em itens sem promoção), parcelamento sem juros (até 12x com "
                "parcela mínima), se pagamento combinado é permitido (acima de R$ 2.000) e o frete se informar a "
                "cidade. Passe apenas ids e quantidades: os valores são sempre recalculados do catálogo. Use para "
                "'quanto fica no PIX?', 'dá para parcelar em 10x?', 'qual o frete para Campo Grande?'.",
                ArgsSimularPagamento,
                simular,
            ),
            tool(
                "consultar_pedido",
                "Consulta status, itens, total, rastreio, previsão de entrega e prazos de devolução/troca/garantia de "
                "um pedido. Exige o número do pedido E o e-mail ou telefone do cadastro; sem os dois, peça ao "
                "cliente. Se não encontrar, diga apenas que não localizou com esses dados (nunca revele se o "
                "pedido existe).",
                ArgsConsultarPedido,
                pedido,
            ),
            tool(
                "status_loja",
                "Informa se a loja está aberta agora, quando reabre, o horário semanal e os contatos (telefone, "
                "WhatsApp, e-mail). Use para perguntas de horário ou quando o cliente fala fora do expediente.",
                ArgsStatusLoja,
                loja,
            ),
            tool(
                "registrar_solicitacao",
                "Registra uma solicitação para a equipe humana (reclamacao, cotacao_frete para cidades fora de Campo "
                "Grande ou instrumentos grandes, ou outros) e devolve o protocolo. Retorno em até 24 horas úteis. "
                "Peça um contato (WhatsApp ou e-mail) ao cliente antes, se não tiver.",
                ArgsRegistrarSolicitacao,
                solicitar,
            ),
        )

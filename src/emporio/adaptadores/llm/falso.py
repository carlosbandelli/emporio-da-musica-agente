"""`LLMFalso`: provedor roteirizável para testes do motor, do roteador e da CLI (sem rede).

Exemplo::

    falso = LLMFalso([chamar_tool("detalhar_produto", {"produto_id": 93}), responder_texto("Custa R$ 999,00")])
    ...  # o motor chama falso.gerar(...) duas vezes
    assert falso.requisicoes[1].mensagens[-1].role is PapelMensagem.TOOL
"""

import dataclasses
import inspect
import itertools
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Self

from emporio.portas.llm import ChamadaTool, RequisicaoLLM, RespostaLLM, UsoTokens

Respondedor = Callable[[RequisicaoLLM], RespostaLLM | Awaitable[RespostaLLM]]
ItemRoteiro = RespostaLLM | BaseException | Respondedor
"""Um passo do roteiro: a resposta, a exceção a levantar, ou um callable (sync ou async) que recebe a requisição."""

_contador_ids = itertools.count(1)


class RoteiroEsgotado(AssertionError):  # noqa: N818 - nome em pt-BR do projeto
    """O código chamou o LLM mais vezes do que o roteiro previa.

    Herda de `AssertionError` (não de `ErroLLM`) para o roteador/motor não tratarem um bug de teste como falha de
    provedor e caírem no fallback silenciosamente.
    """


def responder_texto(texto: str, *, provedor: str = "", modelo: str = "", uso: UsoTokens | None = None) -> RespostaLLM:
    """Resposta final só com texto. `provedor`/`modelo` vazios são preenchidos pelo `LLMFalso`."""
    return RespostaLLM(texto=texto, provedor=provedor, modelo=modelo, uso=uso or UsoTokens())


def chamar_tool(
    nome: str, argumentos: Mapping[str, object] | None = None, *, id: str | None = None, texto: str | None = None
) -> RespostaLLM:
    """Resposta pedindo uma tool. `id` é gerado (`call_falso_N`) se omitido."""
    return chamar_tools(ChamadaTool(id or _novo_id(), nome, dict(argumentos or {})), texto=texto)


def chamar_tools(*chamadas: ChamadaTool | tuple[str, Mapping[str, object]], texto: str | None = None) -> RespostaLLM:
    """Resposta pedindo várias tools; aceita `ChamadaTool` ou `(nome, argumentos)` (id gerado)."""
    normalizadas = tuple(
        c if isinstance(c, ChamadaTool) else ChamadaTool(_novo_id(), c[0], dict(c[1])) for c in chamadas
    )
    return RespostaLLM(texto=texto, provedor="", modelo="", chamadas_tool=normalizadas)


def _novo_id() -> str:
    return f"call_falso_{next(_contador_ids)}"


class LLMFalso:
    """Implementa `LLMProvider` seguindo um roteiro; grava cada `RequisicaoLLM` recebida em `requisicoes`."""

    def __init__(self, roteiro: Iterable[ItemRoteiro] = (), *, nome: str = "falso", modelo: str = "falso-1") -> None:
        self._nome = nome
        self.modelo = modelo
        self._roteiro: deque[ItemRoteiro] = deque(roteiro)
        self.requisicoes: list[RequisicaoLLM] = []

    @property
    def nome(self) -> str:
        """Nome configurado (ex.: `groq` para simular um provedor específico)."""
        return self._nome

    @property
    def restantes(self) -> int:
        """Itens do roteiro ainda não consumidos."""
        return len(self._roteiro)

    @property
    def ultima_requisicao(self) -> RequisicaoLLM:
        """Última requisição recebida (falha se nenhuma)."""
        if not self.requisicoes:
            raise AssertionError(f"LLMFalso({self._nome!r}) ainda não recebeu requisições")
        return self.requisicoes[-1]

    def roteirizar(self, *itens: ItemRoteiro) -> Self:
        """Acrescenta itens ao fim do roteiro; devolve o próprio falso (encadeável)."""
        self._roteiro.extend(itens)
        return self

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Consome o próximo item do roteiro."""
        self.requisicoes.append(req)
        if not self._roteiro:
            ultima = req.mensagens[-1] if req.mensagens else None
            raise RoteiroEsgotado(
                f"LLMFalso({self._nome!r}): roteiro esgotado na chamada nº {len(self.requisicoes)}"
                f" (última mensagem: {ultima!r})"
            )
        item = self._roteiro.popleft()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, RespostaLLM):
            return self._completar(item)
        resultado = item(req)
        resposta = await resultado if inspect.isawaitable(resultado) else resultado
        return self._completar(resposta)

    def _completar(self, resposta: RespostaLLM) -> RespostaLLM:
        return dataclasses.replace(
            resposta, provedor=resposta.provedor or self._nome, modelo=resposta.modelo or self.modelo
        )

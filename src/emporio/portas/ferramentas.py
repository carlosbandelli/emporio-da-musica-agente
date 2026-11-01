"""Porta da fachada de tools: o que um canal de entrada (API HTTP, motor externo como o n8n) precisa para listar e
executar as tools da loja, sem conhecer os módulos que as implementam."""

from collections.abc import Mapping
from typing import Any, Protocol

from emporio.portas.contexto import Papel, TenantContext
from emporio.portas.llm import DefinicaoTool


class ResultadoFerramenta(Protocol):
    """Resultado de uma tool, como o LLM o recebe (`para_dict`). `codigo_erro` só existe quando `ok` é falso."""

    @property
    def nome(self) -> str: ...

    @property
    def ok(self) -> bool: ...

    @property
    def codigo_erro(self) -> str | None: ...

    def para_dict(self) -> dict[str, Any]: ...


class FachadaTools(Protocol):
    """Lista as tools do papel e executa uma tool. `executar` nunca levanta: erro vira `ok=False` com `codigo_erro`."""

    def definicoes(self, papel: Papel) -> tuple[DefinicaoTool, ...]: ...

    async def executar(
        self, ctx: TenantContext, nome: str, argumentos: Mapping[str, object], *, sessao_id: str | None = None
    ) -> ResultadoFerramenta: ...

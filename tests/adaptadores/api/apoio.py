"""Apoio dos testes da API: monta a app com `LLMFalso` e fornece clientes ASGI em memória (sem rede, sem porta)."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
import yaml
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.llm import LLMFalso
from emporio.composicao import Aplicacao, montar
from emporio.configuracao import Settings

RAIZ: Final = Path(__file__).resolve().parents[3]
REF: Final = datetime(2026, 3, 10, 15, tzinfo=UTC)
BASE: Final = "http://testserver"


def settings(tenants_config: Path | None = None) -> Settings:
    """`Settings` sem ler o arquivo de ambiente; data de referência fixa."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None, tenants_config=tenants_config or RAIZ / "config" / "tenants.yaml", data_referencia=REF
    )


def aplicacao_com(llm: LLMFalso | None, dir_estado: Path, tenants_config: Path | None = None) -> Aplicacao:
    """Aplicação real (`composicao.montar`) com o LLM falso injetado; `None` = sem provedor (levanta sem chave)."""
    provedores = {"falso": llm} if llm is not None else {}
    return montar(settings(tenants_config), provedores=provedores, raiz=RAIZ, dir_estado=dir_estado)


def app_com(llm: LLMFalso | None, dir_estado: Path, tenants_config: Path | None = None) -> FastAPI:
    """App FastAPI cujo `lifespan` monta `aplicacao_com(...)` uma só vez (outro lifespan reaproveita)."""
    montada: list[Aplicacao] = []

    def fabrica() -> Aplicacao:
        if not montada:
            montada.append(aplicacao_com(llm, dir_estado, tenants_config))
        return montada[0]

    return criar_app(fabrica)


def tenants_com_limite_sessao(destino: Path, capacidade: int) -> Path:
    """Cópia do `tenants.yaml` com o balde da sessão minúsculo, para provocar o rate limit."""
    bruto: dict[str, Any] = yaml.safe_load((RAIZ / "config" / "tenants.yaml").read_text(encoding="utf-8"))
    bruto["tenants"]["emporio"]["limites"]["sessao"] = {"capacidade": capacidade, "reposicao_por_s": 0.01}
    caminho = destino / "tenants-limite.yaml"
    caminho.write_text(yaml.safe_dump(bruto, allow_unicode=True), encoding="utf-8")
    return caminho


class TransporteComLifespan(httpx.AsyncBaseTransport):
    """Transporte ASGI que roda o `lifespan` da app em volta de cada requisição.

    Serve onde o chamador não controla o ciclo de vida (CLI `--api`, adapter `MotorViaApi` da suíte de contrato).
    Como as fábricas dos testes devolvem sempre a mesma `Aplicacao`, repetir o lifespan não perde estado.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._asgi = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with LifespanManager(self._app):
            resposta = await self._asgi.handle_async_request(request)
            conteudo = await resposta.aread()  # lê o corpo ainda dentro do lifespan
        return httpx.Response(
            resposta.status_code, headers=resposta.headers, content=conteudo, request=request, extensions={}
        )


def cabecalhos(tenant: str | None = "emporio", **extras: str) -> Mapping[str, str]:
    """`X-Tenant-ID` (se houver tenant) mais cabeçalhos extras."""
    base = {"X-Tenant-ID": tenant} if tenant else {}
    return {**base, **extras}

"""API FastAPI do agente (`emporio.adaptadores.api.app:app`, nome exigido pelo `Dockerfile`).

Endpoints:

- `POST /v1/chat`: `{session_id, mensagem, idioma?}` → `RespostaChat`. Só usa a porta `MotorDeAtendimento` da
  `Aplicacao` montada pela composição.
- `GET /health`: liveness. 200 sempre, sem dependências.
- `GET /ready`: 200 quando a aplicação está montada (há motor com provedor de LLM); 503 com o motivo caso contrário.

Tenant: header `X-Tenant-ID` (vence) ou subdomínio do `Host` (ver `_resolucao.tenant_do_host`). Sem nenhum → 400;
desconhecido → 404. Idioma: campo `idioma` do corpo, senão `Accept-Language`, senão o padrão do tenant; as mensagens
de erro de borda (400/404/422) usam só o `Accept-Language` (a loja ainda não é conhecida).

Mapeamento de `RespostaAtendimento.codigo`: `limite` → 429 com `Retry-After`. Os demais (`deadline`, `indisponivel`,
`erro`, `entrada_invalida`) continuam 200 com `degradada`: o corpo é uma mensagem de atendimento que o canal (widget,
WhatsApp, CLI) deve mostrar ao cliente, e um 5xx faria proxies e clientes repetirem a chamada (mais gasto de LLM)
ou esconderem o texto. A degradação segue visível em `degradada` e `codigo`. Só quando nada foi montado (sem chave de
LLM, premissa #19) o `/v1/chat` devolve 503, pois não há motor para responder.

Sem estado global: a `Aplicacao` montada fica em `app.state`, preenchida no `lifespan`.
"""

import logging
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from emporio.adaptadores.api._esquemas import CorpoErro, PedidoChat, RespostaChat
from emporio.adaptadores.api._mensagens import CodigoErro, mensagem_erro
from emporio.adaptadores.api._resolucao import idioma_do_accept_language, tenant_do_host
from emporio.composicao import Aplicacao, ErroConfiguracao, ErroSemChaveLLM, carregar_settings, montar
from emporio.portas import CodigoResposta, Idioma, MensagemCliente, Papel, TenantContext

logger = logging.getLogger(__name__)

FabricaAplicacao = Callable[[], Aplicacao]

CABECALHO_TENANT: Final = "X-Tenant-ID"
RETRY_AFTER_PADRAO_S: Final = 1


def _montar_padrao() -> Aplicacao:
    return montar(carregar_settings())


@dataclass(frozen=True, slots=True)
class _Entrada:
    """Tenant já validado e idioma do canal (header), resolvidos antes de ler o corpo."""

    aplicacao: Aplicacao
    tenant_id: str
    idioma_header: Idioma | None


class _ErroApi(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Erro de borda com status, código e idioma já decididos; o handler monta o corpo."""

    def __init__(self, status: int, codigo: CodigoErro, idioma: Idioma) -> None:
        super().__init__(codigo.value)
        self.status = status
        self.codigo = codigo
        self.idioma = idioma


def _idioma_de_erro(request: Request) -> Idioma:
    return idioma_do_accept_language(request.headers.get("accept-language")) or Idioma.PT_BR


def _corpo_erro(codigo: CodigoErro, idioma: Idioma) -> dict[str, object]:
    return CorpoErro(codigo=codigo.value, mensagem=mensagem_erro(codigo, idioma)).model_dump()


def _entrada(request: Request) -> _Entrada:
    """Dependência: valida que há aplicação montada e resolve o tenant (header, depois subdomínio)."""
    idioma_header = idioma_do_accept_language(request.headers.get("accept-language"))
    aplicacao: Aplicacao | None = request.app.state.aplicacao
    if aplicacao is None:
        raise _ErroApi(503, CodigoErro.INDISPONIVEL, idioma_header or Idioma.PT_BR)
    tenant_id = (request.headers.get(CABECALHO_TENANT) or "").strip() or tenant_do_host(request.headers.get("host"))
    erro_idioma = idioma_header or Idioma.PT_BR
    if tenant_id is None:
        raise _ErroApi(400, CodigoErro.TENANT_AUSENTE, erro_idioma)
    if tenant_id not in aplicacao.tenants:
        raise _ErroApi(404, CodigoErro.TENANT_DESCONHECIDO, erro_idioma)
    return _Entrada(aplicacao, tenant_id, idioma_header)


def _retry_after(segundos: float | None) -> str:
    return str(max(RETRY_AFTER_PADRAO_S, math.ceil(segundos))) if segundos is not None else str(RETRY_AFTER_PADRAO_S)


def criar_app(montar_aplicacao: FabricaAplicacao | None = None) -> FastAPI:
    """Cria a aplicação FastAPI. A `Aplicacao` é montada no `lifespan` (nada é montado no import).

    Se a montagem falhar por configuração (ex.: sem chave de LLM), a API sobe mesmo assim: `/health` 200, `/ready` 503
    e `/v1/chat` 503 com mensagem fixa. Outros erros de montagem (bugs) derrubam a inicialização.
    """
    fabrica = montar_aplicacao or _montar_padrao

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.aplicacao = None
        app.state.motivo_indisponivel = None
        try:
            app.state.aplicacao = fabrica()
        except ErroSemChaveLLM as erro:
            logger.error("api: sem chave de LLM: %s", erro)
            app.state.motivo_indisponivel = ("sem_chave_llm", str(erro))
        except ErroConfiguracao as erro:
            logger.error("api: configuração inválida: %s", erro)
            app.state.motivo_indisponivel = ("configuracao_invalida", None)  # detalhe só no log (pode citar caminhos)
        yield

    app = FastAPI(
        title="Empório da Música - atendimento",
        version="0.1.0",
        description="API de atendimento por texto da Sônia (tenant por `X-Tenant-ID` ou subdomínio).",
        lifespan=lifespan,
    )
    # Valores iniciais para quem usa o app sem executar o lifespan: tudo "não montado".
    app.state.aplicacao = None
    app.state.motivo_indisponivel = ("nao_iniciada", None)

    @app.exception_handler(_ErroApi)
    async def _tratar_erro_api(_: Request, erro: _ErroApi) -> JSONResponse:
        if erro.codigo is CodigoErro.INDISPONIVEL:
            corpo = RespostaChat(
                texto=mensagem_erro(erro.codigo, erro.idioma),
                idioma=erro.idioma,
                degradada=True,
                codigo=CodigoResposta.INDISPONIVEL,
            )
            return JSONResponse(corpo.model_dump(mode="json"), status_code=erro.status)
        return JSONResponse(_corpo_erro(erro.codigo, erro.idioma), status_code=erro.status)

    @app.exception_handler(RequestValidationError)
    async def _tratar_validacao(request: Request, _: RequestValidationError) -> JSONResponse:
        return JSONResponse(_corpo_erro(CodigoErro.REQUISICAO_INVALIDA, _idioma_de_erro(request)), status_code=422)

    @app.get("/health", summary="Liveness: 200 sempre, sem dependências")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/ready",
        summary="Readiness: aplicação montada e com provedor de LLM",
        responses={503: {"description": "Aplicação não montada (ex.: sem chave de LLM)."}},
    )
    async def ready(request: Request) -> JSONResponse:
        aplicacao: Aplicacao | None = request.app.state.aplicacao
        if aplicacao is None:
            motivo, detalhe = request.app.state.motivo_indisponivel or ("nao_iniciada", None)
            corpo: dict[str, object] = {"status": "indisponivel", "motivo": motivo}
            if detalhe:
                corpo["detalhe"] = detalhe
            return JSONResponse(corpo, status_code=503)
        return JSONResponse({"status": "ok", "motor": aplicacao.motor_ativo, "provedores": list(aplicacao.provedores)})

    @app.post(
        "/v1/chat",
        response_model=RespostaChat,
        summary="Uma mensagem do cliente, uma resposta da atendente",
        responses={
            400: {"model": CorpoErro, "description": "Tenant não informado (nem header, nem subdomínio)."},
            404: {"model": CorpoErro, "description": "Tenant desconhecido."},
            422: {"model": CorpoErro, "description": "Corpo inválido."},
            429: {
                "model": RespostaChat,
                "description": "Rate limit. Cabeçalho `Retry-After` em segundos; o corpo traz a mensagem fixa.",
                "headers": {"Retry-After": {"schema": {"type": "integer"}}},
            },
            503: {"model": RespostaChat, "description": "Aplicação não montada (ex.: sem chave de LLM)."},
        },
    )
    async def chat(pedido: PedidoChat, entrada: Annotated[_Entrada, Depends(_entrada)]) -> JSONResponse:
        config = entrada.aplicacao.tenants[entrada.tenant_id]
        idioma = pedido.idioma or entrada.idioma_header or config.idioma_padrao
        ctx = TenantContext(tenant_id=entrada.tenant_id, idioma=idioma, papel=Papel.CLIENTE)
        resposta = await entrada.aplicacao.motor.responder(ctx, pedido.session_id, MensagemCliente(pedido.mensagem))
        corpo = RespostaChat.de(resposta, config.persona.nome).model_dump(mode="json")
        if resposta.codigo is CodigoResposta.LIMITE:
            return JSONResponse(corpo, status_code=429, headers={"Retry-After": _retry_after(resposta.retry_after_s)})
        return JSONResponse(corpo)

    return app


app = criar_app()
"""Instância ASGI (`uvicorn emporio.adaptadores.api.app:app`). Não monta nada no import: o `lifespan` monta."""

"""API FastAPI do agente (`emporio.adaptadores.api.app:app`, nome exigido pelo `Dockerfile`).

Endpoints:

- `POST /v1/chat`: `{session_id, mensagem, idioma?}` → `RespostaChat`. Só usa a porta `MotorDeAtendimento` da
  `Aplicacao` montada pela composição.
- `GET /health`: liveness. 200 sempre, sem dependências.
- `GET /metrics`: métricas Prometheus da `Telemetria` da aplicação (contrato em `docs/deploy.md` §6).
- `GET /ready`: 200 quando a aplicação está montada (há motor com provedor de LLM); 503 com o motivo caso contrário.

Tenant: header `X-Tenant-ID` (vence) ou subdomínio do `Host` (ver `_resolucao.tenant_do_host`). Sem nenhum → 400;
desconhecido → 404. Idioma: campo `idioma` do corpo, senão `Accept-Language`, senão o padrão do tenant; as mensagens
de erro de borda (400/404/422) usam só o `Accept-Language` (a loja ainda não é conhecida).

Mapeamento de `RespostaAtendimento.codigo`: `limite` → 429 com `Retry-After`. Os demais (`deadline`, `indisponivel`,
`erro`, `entrada_invalida`) continuam 200 com `degradada`: o corpo é uma mensagem de atendimento que o canal (widget,
WhatsApp, CLI) deve mostrar ao cliente, e um 5xx faria proxies e clientes repetirem a chamada (mais gasto de LLM)
ou esconderem o texto. A degradação segue visível em `degradada` e `codigo`. Só quando nada foi montado (sem chave de
LLM, premissa #19) o `/v1/chat` devolve 503, pois não há motor para responder.

RBAC (arquitetura §8, task 2.5): token `Authorization: Bearer <JWT>` validado pela porta `ProvedorIdentidade`.

- `POST /v1/chat` é público: sem token = `cliente` anônimo. Com token, ele precisa ser válido, do mesmo tenant e ter
  `chat:usar`; o papel do `TenantContext` vem do token.
- Admin (`Depends(exige(permissao))`): `GET /v1/admin/solicitacoes?status=&tipo=` (`solicitacoes:ler`),
  `PATCH /v1/admin/solicitacoes/{id}` (`solicitacoes:resolver`; 404 inexistente, 409 transição inválida),
  `GET`/`PUT /v1/admin/config` (`config:editar`; edição parcial em memória do processo).
- Ordem das checagens: aplicação montada (503) → tenant (400/404) → identidade configurada (503
  `autenticacao_indisponivel`, sem `JWT_SECRET` válido) → token presente e válido (401 + `WWW-Authenticate: Bearer`)
  → permissão (403 `sem_permissao`) → tenant do token igual ao da requisição (403 `tenant_divergente`) → corpo (422).
- O token nunca é ecoado nem registrado; respostas admin vão com `Cache-Control: no-store` (dados pessoais).

Sem estado global: a `Aplicacao` montada fica em `app.state`, preenchida no `lifespan`.
"""

import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from emporio.adaptadores.api._esquemas import (
    ConfigEditavel,
    CorpoErro,
    ListaSolicitacoes,
    MudancaStatus,
    PedidoChat,
    RespostaChat,
    SolicitacaoAdmin,
)
from emporio.adaptadores.api._mensagens import CodigoErro, mensagem_erro
from emporio.adaptadores.api._resolucao import idioma_do_accept_language, tenant_do_host
from emporio.adaptadores.api._seguranca import (
    CABECALHO_401_AUSENTE,
    CABECALHO_401_INVALIDO,
    CabecalhoInvalido,
    token_bearer,
)
from emporio.composicao import (
    Aplicacao,
    ErroConfiguracao,
    ErroSemChaveLLM,
    carregar_settings,
    iniciar_observabilidade,
    montar,
)
from emporio.portas import (
    AlteracaoConfig,
    CodigoResposta,
    Decisao,
    ErroIdentidade,
    ErroTransicaoStatus,
    Identidade,
    Idioma,
    MensagemCliente,
    Papel,
    Permissao,
    StatusSolicitacao,
    TenantContext,
    TipoSolicitacao,
    autorizar,
    papel_no_tenant,
)

logger = logging.getLogger(__name__)

FabricaAplicacao = Callable[[], Aplicacao]

CABECALHO_TENANT: Final = "X-Tenant-ID"
RETRY_AFTER_PADRAO_S: Final = 1
ROTA_DESCONHECIDA: Final = "desconhecida"
TENANT_DESCONHECIDO: Final = "desconhecido"
ROTA_METRICAS: Final = "/metrics"
PREFIXO_ADMIN: Final = "/v1/admin/"


def _montar_padrao() -> Aplicacao:
    settings = carregar_settings()
    return montar(settings, telemetria=iniciar_observabilidade(settings))


@dataclass(frozen=True, slots=True)
class _Entrada:
    """Tenant já validado e idioma do canal (header), resolvidos antes de ler o corpo."""

    aplicacao: Aplicacao
    tenant_id: str
    idioma_header: Idioma | None


class _ErroApi(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Erro de borda com status, código e idioma já decididos; o handler monta o corpo."""

    def __init__(
        self, status: int, codigo: CodigoErro, idioma: Idioma, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(codigo.value)
        self.status = status
        self.codigo = codigo
        self.idioma = idioma
        self.headers = dict(headers or {})


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


@dataclass(frozen=True, slots=True)
class _Acesso:
    """Requisição autenticada e autorizada: tenant resolvido, identidade do token e contexto para as portas."""

    entrada: _Entrada
    identidade: Identidade
    ctx: TenantContext

    @property
    def idioma(self) -> Idioma:
        """Idioma das mensagens fixas desta requisição."""
        return self.ctx.idioma


def _identidade_do_token(request: Request, entrada: _Entrada, *, obrigatoria: bool) -> Identidade | None:
    """Valida o `Authorization: Bearer`. Sem cabeçalho: `None` (ou 401 se `obrigatoria`).

    Ordem: provedor configurado (senão 503) → cabeçalho presente (senão 401) → token válido (senão 401). O token
    nunca é ecoado nem registrado em log.
    """
    idioma = entrada.idioma_header or entrada.aplicacao.tenants[entrada.tenant_id].idioma_padrao
    cabecalho = request.headers.get("authorization")
    provedor = entrada.aplicacao.identidade
    if provedor is None:
        if obrigatoria or cabecalho:
            raise _ErroApi(503, CodigoErro.AUTENTICACAO_INDISPONIVEL, idioma)
        return None
    try:
        token = token_bearer(cabecalho)
    except CabecalhoInvalido:
        raise _ErroApi(401, CodigoErro.TOKEN_INVALIDO, idioma, CABECALHO_401_INVALIDO) from None
    if token is None:
        if obrigatoria:
            raise _ErroApi(401, CodigoErro.NAO_AUTENTICADO, idioma, CABECALHO_401_AUSENTE)
        return None
    try:
        return provedor.validar(token)
    except ErroIdentidade as erro:
        logger.info("api: token recusado (%s)", type(erro).__name__)
        raise _ErroApi(401, CodigoErro.TOKEN_INVALIDO, idioma, CABECALHO_401_INVALIDO) from None


def _checar(identidade: Identidade, permissao: Permissao, entrada: _Entrada, idioma: Idioma) -> None:
    """Aplica a matriz da §8 e a dupla checagem de tenant (403 nos dois casos)."""
    decisao = autorizar(identidade, permissao, entrada.tenant_id)
    if decisao is Decisao.SEM_PERMISSAO:
        raise _ErroApi(403, CodigoErro.SEM_PERMISSAO, idioma)
    if decisao is Decisao.TENANT_DIVERGENTE:
        logger.warning("api: token de outro tenant recusado (rota do tenant %s)", entrada.tenant_id)
        raise _ErroApi(403, CodigoErro.TENANT_DIVERGENTE, idioma)
    if decisao is not Decisao.PERMITIDO:  # pragma: no cover - identidade nunca é None aqui
        raise _ErroApi(401, CodigoErro.NAO_AUTENTICADO, idioma, CABECALHO_401_AUSENTE)


def exige(permissao: Permissao) -> Callable[..., _Acesso]:
    """Dependência FastAPI: `Depends(exige(Permissao.SOLICITACOES_LER))` → token válido, permissão e mesmo tenant."""

    def dependencia(request: Request, entrada: Annotated[_Entrada, Depends(_entrada)]) -> _Acesso:
        identidade = _identidade_do_token(request, entrada, obrigatoria=True)
        if identidade is None:  # pragma: no cover - `obrigatoria=True` já levanta 401
            raise _ErroApi(401, CodigoErro.NAO_AUTENTICADO, Idioma.PT_BR, CABECALHO_401_AUSENTE)
        config = entrada.aplicacao.tenants[entrada.tenant_id]
        idioma = entrada.idioma_header or config.idioma_padrao
        _checar(identidade, permissao, entrada, idioma)
        ctx = TenantContext(tenant_id=entrada.tenant_id, idioma=idioma, papel=papel_no_tenant(identidade.papeis))
        return _Acesso(entrada, identidade, ctx)

    return dependencia


def _papel_do_chat(request: Request, entrada: Annotated[_Entrada, Depends(_entrada)]) -> Papel:
    """`/v1/chat` é público: sem token = `cliente` anônimo; com token, ele precisa ser válido, do tenant e ter
    `chat:usar`, e o papel vem dele."""
    identidade = _identidade_do_token(request, entrada, obrigatoria=False)
    if identidade is None:
        return Papel.CLIENTE
    idioma = entrada.idioma_header or entrada.aplicacao.tenants[entrada.tenant_id].idioma_padrao
    _checar(identidade, Permissao.CHAT_USAR, entrada, idioma)
    return papel_no_tenant(identidade.papeis)


def _sem_cache(corpo: object, status: int = 200) -> JSONResponse:
    """Respostas admin trazem dados pessoais: nunca em cache de proxy ou navegador."""
    return JSONResponse(corpo, status_code=status, headers={"Cache-Control": "no-store"})


def _retry_after(segundos: float | None) -> str:
    return str(max(RETRY_AFTER_PADRAO_S, math.ceil(segundos))) if segundos is not None else str(RETRY_AFTER_PADRAO_S)


def _instalar_metricas_http(app: FastAPI) -> None:
    """Middleware que emite `emporio_http_*` pela porta de telemetria da `Aplicacao` montada."""

    @app.middleware("http")
    async def _metricas_http(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        aplicacao: Aplicacao | None = request.app.state.aplicacao
        if aplicacao is None or request.url.path == ROTA_METRICAS:
            return await call_next(request)
        inicio = time.perf_counter()
        status = 500
        try:
            resposta = await call_next(request)
            status = resposta.status_code
            return resposta
        finally:
            rota = request.scope.get("route")
            tenant = (request.headers.get(CABECALHO_TENANT) or "").strip() or tenant_do_host(
                request.headers.get("host")
            )
            aplicacao.telemetria.http_requisicao(
                # Tenant só vira rótulo se existir: valor do cliente nunca cria série nova (cardinalidade).
                tenant if tenant is not None and tenant in aplicacao.tenants else TENANT_DESCONHECIDO,
                getattr(rota, "path", ROTA_DESCONHECIDA),
                request.method,
                status,
                time.perf_counter() - inicio,
            )


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

    _instalar_metricas_http(app)

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
        return JSONResponse(_corpo_erro(erro.codigo, erro.idioma), status_code=erro.status, headers=erro.headers)

    @app.exception_handler(RequestValidationError)
    async def _tratar_validacao(request: Request, _: RequestValidationError) -> JSONResponse:
        # Nunca ecoa a entrada (o corpo padrão do FastAPI repete os valores recebidos).
        codigo = (
            CodigoErro.DADOS_INVALIDOS if request.url.path.startswith(PREFIXO_ADMIN) else CodigoErro.REQUISICAO_INVALIDA
        )
        return JSONResponse(_corpo_erro(codigo, _idioma_de_erro(request)), status_code=422)

    @app.get("/health", summary="Liveness: 200 sempre, sem dependências")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", summary="Métricas Prometheus (porta 8000; não exposta pelo Ingress)", include_in_schema=False)
    async def metricas(request: Request) -> Response:
        aplicacao: Aplicacao | None = request.app.state.aplicacao
        if aplicacao is None:
            return Response(b"", media_type="text/plain")
        corpo, tipo = aplicacao.telemetria.exportar_metricas()
        return Response(corpo, headers={"content-type": tipo})

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
    async def chat(
        pedido: PedidoChat,
        entrada: Annotated[_Entrada, Depends(_entrada)],
        papel: Annotated[Papel, Depends(_papel_do_chat)],
    ) -> JSONResponse:
        config = entrada.aplicacao.tenants[entrada.tenant_id]
        idioma = pedido.idioma or entrada.idioma_header or config.idioma_padrao
        ctx = TenantContext(tenant_id=entrada.tenant_id, idioma=idioma, papel=papel)
        resposta = await entrada.aplicacao.motor.responder(ctx, pedido.session_id, MensagemCliente(pedido.mensagem))
        corpo = RespostaChat.de(resposta, config.persona.nome).model_dump(mode="json")
        if resposta.codigo is CodigoResposta.LIMITE:
            return JSONResponse(corpo, status_code=429, headers={"Retry-After": _retry_after(resposta.retry_after_s)})
        return JSONResponse(corpo)

    _instalar_admin(app)
    return app


def _respostas_auth(**extras: dict[str, object]) -> dict[int | str, dict[str, object]]:
    """Respostas documentadas (OpenAPI) dos endpoints protegidos; `extras` por status em texto (ex.: `_409=...`)."""
    base: dict[int | str, dict[str, object]] = {
        400: {"model": CorpoErro, "description": "Tenant não informado."},
        401: {"model": CorpoErro, "description": "Sem token, token inválido ou expirado (`WWW-Authenticate: Bearer`)."},
        403: {"model": CorpoErro, "description": "Papel sem a permissão, ou token de outro tenant."},
        404: {"model": CorpoErro, "description": "Tenant (ou recurso) desconhecido."},
        422: {"model": CorpoErro, "description": "Dados inválidos."},
        503: {"model": CorpoErro, "description": "Autenticação não configurada (sem `JWT_SECRET` válido)."},
    }
    base.update({int(chave.lstrip("_")): valor for chave, valor in extras.items()})
    return base


def _instalar_admin(app: FastAPI) -> None:
    """Endpoints da equipe da loja (arquitetura §8). Todos exigem token do tenant com a permissão da rota."""

    @app.get(
        "/v1/admin/solicitacoes",
        response_model=ListaSolicitacoes,
        summary="Solicitações do tenant (permissão `solicitacoes:ler`)",
        responses=_respostas_auth(),
    )
    async def listar_solicitacoes(
        acesso: Annotated[_Acesso, Depends(exige(Permissao.SOLICITACOES_LER))],
        status: Annotated[StatusSolicitacao | None, Query(description="Filtra pelo status.")] = None,
        tipo: Annotated[TipoSolicitacao | None, Query(description="Filtra pelo tipo.")] = None,
    ) -> JSONResponse:
        repo = acesso.entrada.aplicacao.solicitacoes
        if repo is None:  # pragma: no cover - `montar` sempre preenche
            raise _ErroApi(503, CodigoErro.INDISPONIVEL, acesso.idioma)
        itens = await repo.listar(acesso.ctx, status=status, tipo=tipo)
        corpo = ListaSolicitacoes(solicitacoes=[SolicitacaoAdmin.de(s) for s in itens])
        return _sem_cache(corpo.model_dump(mode="json"))

    @app.patch(
        "/v1/admin/solicitacoes/{solicitacao_id}",
        response_model=SolicitacaoAdmin,
        summary="Muda o status de uma solicitação (permissão `solicitacoes:resolver`)",
        responses=_respostas_auth(_409={"model": CorpoErro, "description": "Transição de status não permitida."}),
    )
    async def mudar_status(
        mudanca: MudancaStatus,
        acesso: Annotated[_Acesso, Depends(exige(Permissao.SOLICITACOES_RESOLVER))],
        solicitacao_id: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")],
    ) -> JSONResponse:
        repo = acesso.entrada.aplicacao.solicitacoes
        if repo is None:  # pragma: no cover - `montar` sempre preenche
            raise _ErroApi(503, CodigoErro.INDISPONIVEL, acesso.idioma)
        try:
            nova = await repo.atualizar_status(
                acesso.ctx, solicitacao_id, mudanca.status, por=acesso.identidade.sub, em=datetime.now(UTC)
            )
        except ErroTransicaoStatus:
            raise _ErroApi(409, CodigoErro.TRANSICAO_INVALIDA, acesso.idioma) from None
        if nova is None:
            raise _ErroApi(404, CodigoErro.SOLICITACAO_NAO_ENCONTRADA, acesso.idioma)
        logger.info("admin: solicitação %s -> %s por %s", solicitacao_id, mudanca.status.value, acesso.identidade.sub)
        return _sem_cache(SolicitacaoAdmin.de(nova).model_dump(mode="json"))

    @app.get(
        "/v1/admin/config",
        response_model=ConfigEditavel,
        summary="Configuração editável do tenant (permissão `config:editar`)",
        responses=_respostas_auth(),
    )
    async def ler_config(acesso: Annotated[_Acesso, Depends(exige(Permissao.CONFIG_EDITAR))]) -> JSONResponse:
        loja = acesso.entrada.aplicacao.config_tenants
        if loja is None:  # pragma: no cover - `montar` sempre preenche
            raise _ErroApi(503, CodigoErro.INDISPONIVEL, acesso.idioma)
        return _sem_cache(ConfigEditavel.de(await loja.obter(acesso.ctx)).model_dump(mode="json"))

    @app.put(
        "/v1/admin/config",
        response_model=ConfigEditavel,
        summary="Atualiza a configuração editável do tenant em memória do processo (permissão `config:editar`)",
        responses=_respostas_auth(),
    )
    async def atualizar_config(
        alteracao: AlteracaoConfig,
        acesso: Annotated[_Acesso, Depends(exige(Permissao.CONFIG_EDITAR))],
    ) -> JSONResponse:
        loja = acesso.entrada.aplicacao.config_tenants
        if loja is None:  # pragma: no cover - `montar` sempre preenche
            raise _ErroApi(503, CodigoErro.INDISPONIVEL, acesso.idioma)
        try:
            nova = await loja.atualizar(acesso.ctx, alteracao)
        except ValidationError:
            raise _ErroApi(422, CodigoErro.DADOS_INVALIDOS, acesso.idioma) from None
        logger.info(
            "admin: config do tenant %s alterada por %s (campos: %s)",
            acesso.ctx.tenant_id,
            acesso.identidade.sub,
            ", ".join(sorted(alteracao.model_dump(exclude_none=True))),
        )
        return _sem_cache(ConfigEditavel.de(nova).model_dump(mode="json"))


app = criar_app()
"""Instância ASGI (`uvicorn emporio.adaptadores.api.app:app`). Não monta nada no import: o `lifespan` monta."""

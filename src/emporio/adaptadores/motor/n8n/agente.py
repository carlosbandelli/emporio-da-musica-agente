"""`AgenteN8n`: implementa `MotorDeAtendimento` (arquitetura §3.1/§3.2) delegando a orquestração a um workflow n8n.

O n8n decide "quem chama o quê" (LLM e tools); as regras de negócio continuam nos módulos, alcançadas por
`POST /v1/tools/{nome}` da API. O que não pode depender do workflow fica aqui, no Python:

1. tenant → config e manual (desconhecido: mensagem fixa);
2. sessão (`SessaoStore`): o histórico mora no Python e vai inteiro no webhook (I2; o workflow é stateless);
3. validação (I1): vazia ou acima de `tamanho_max_mensagem` → mensagem fixa, sem chamar o n8n;
4. rate limit nas camadas `sessao` e `tenant`, como no nativo;
5. idioma da sessão (I7) e system prompt CAG montados aqui (`motor.comum`), enviados no corpo;
6. deadline do tenant (I5): `asyncio.timeout` no turno inteiro e timeout HTTP = tempo restante;
7. resiliência (§5.2): circuit breaker (`CircuitoStore`, chave `n8n`) e retry só em erro transitório de transporte;
8. guarda de valores (I6) sobre os resultados de tools que o workflow devolve, com uma rodada corretiva;
9. grava o turno (append-only) no formato do nativo, então trocar de motor não perde a conversa.

Credencial das tools: a cada turno o motor emite um JWT de `atendente` só do tenant do turno, válido pelo deadline
mais uma margem (`ProvedorIdentidade.emitir`); o workflow o repassa em `Authorization` ao chamar `/v1/tools/*` (I3).

Nunca lança (I4): toda falha vira mensagem fixa traduzida com `degradada=True` e `codigo`. Sem estado global (I8).
"""

import asyncio
import dataclasses
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import httpx
from pydantic import ValidationError
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, wait_random_exponential

from emporio.adaptadores.motor.comum.guarda import (
    eh_nota_interna,
    nota_correcao,
    valores_nao_verificados,
    valores_permitidos,
)
from emporio.adaptadores.motor.comum.historico import cortar_historico
from emporio.adaptadores.motor.comum.idioma import resolver_idioma
from emporio.adaptadores.motor.comum.mensagens import ChaveMensagem, mensagem_fixa
from emporio.adaptadores.motor.comum.prompt import PromptSistema, montar_prompt
from emporio.adaptadores.motor.n8n.contrato import (
    ChamadaToolWebhook,
    MensagemHistorico,
    PedidoWebhook,
    RespostaWebhook,
    SistemaWebhook,
    ToolsWebhook,
)
from emporio.modulos.politicas.dominio import FUSO_LOJA
from emporio.portas.contexto import Idioma, Papel, TenantContext
from emporio.portas.estado import CircuitoStore, EstadoCircuito, EstadoSessao, RateLimiter, Relogio, SessaoStore
from emporio.portas.identidade import ProvedorIdentidade
from emporio.portas.llm import ChamadaTool, MensagemLLM, PapelMensagem
from emporio.portas.motor import CodigoResposta, MensagemCliente, RespostaAtendimento
from emporio.portas.telemetria import Telemetria, TelemetriaNula
from emporio.portas.tenant import ConfigTenant

logger = logging.getLogger(__name__)

NOME_CIRCUITO: Final = "n8n"
"""Chave do circuito no `CircuitoStore` (um n8n por processo, compartilhado pelos tenants)."""
TENTATIVAS_PADRAO: Final = 2
"""Chamadas ao webhook por rodada: a original e um retry, só em falha de transporte (o workflow não rodou)."""
SUB_SERVICO: Final = "n8n"
"""`sub` do token de serviço emitido por turno (aparece no log de `/v1/tools`)."""
MARGEM_TOKEN_S: Final = 30
"""Validade do token além do deadline (tolerância de relógio entre os processos)."""
TTL_SESSAO_S: Final = 1800
MAX_MENSAGENS_HISTORICO: Final = 40
CHAVE_RATE_LIMIT_TENANT: Final = "global"
PREFIXO_ID_TOOL: Final = "n8n-"

STATUS_RETENTAVEIS: Final = frozenset({502, 503, 504})
"""Gateway/fila do n8n fora: o workflow não executou, repetir não duplica LLM nem tools."""
STATUS_PERMANENTES: Final = frozenset({401, 403, 404})
"""Segredo errado ou webhook não registrado (workflow inativo): abre o circuito na hora."""
_HTTP_OK: Final = 200
_HTTP_ERRO_SERVIDOR: Final = 500

RelogioMonotonic = Callable[[], float]
Sono = Callable[[float], Awaitable[None]]

_CODIGO_DA_FALHA: Final = {
    ChaveMensagem.INDISPONIVEL: CodigoResposta.INDISPONIVEL,
    ChaveMensagem.DEADLINE: CodigoResposta.DEADLINE,
}


class _FalhaN8n(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Falha de uma chamada ao webhook, já classificada (mensagem fixa e se vale repetir)."""

    def __init__(self, motivo: str, chave: ChaveMensagem, *, retentavel: bool = False) -> None:
        super().__init__(motivo)
        self.chave = chave
        self.retentavel = retentavel


@dataclass(slots=True)
class _Turno:
    """Estado mutável de um turno (só vive durante `responder`)."""

    nivel: str | None = None
    provedor: str | None = None
    chamadas: list[ChamadaToolWebhook] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Desfecho:
    texto: str | None = None
    falha: ChaveMensagem | None = None


class AgenteN8n:
    """Motor de atendimento que chama o webhook do n8n. Implementa `MotorDeAtendimento`.

    `http` é injetável (testes usam `httpx.MockTransport`); `circuito` é a mesma porta usada pelos LLMs.
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        url_tools: str,
        tenants: Mapping[str, ConfigTenant],
        politicas: Mapping[str, str],
        http: httpx.AsyncClient,
        identidade: ProvedorIdentidade,
        circuito: CircuitoStore,
        sessoes: SessaoStore,
        limitador: RateLimiter,
        relogio: Relogio,
        segredo_webhook: str | None = None,
        relogio_monotonic: RelogioMonotonic = time.monotonic,
        sleep: Sono = asyncio.sleep,
        tentativas: int = TENTATIVAS_PADRAO,
        ttl_sessao_s: int = TTL_SESSAO_S,
        max_mensagens_historico: int = MAX_MENSAGENS_HISTORICO,
        telemetria: Telemetria | None = None,
    ) -> None:
        if tentativas < 1:
            raise ValueError("tentativas deve ser pelo menos 1")
        if not webhook_url.strip():
            raise ValueError("webhook_url vazio")
        self._url = webhook_url
        self._url_tools = url_tools.rstrip("/")
        self._tenants = tenants  # referência viva, como no nativo (edições do admin valem no turno seguinte)
        self._politicas = dict(politicas)
        self._http = http
        self._identidade = identidade
        self._circuito = circuito
        self._sessoes = sessoes
        self._limitador = limitador
        self._relogio = relogio
        self._segredo = segredo_webhook
        self._agora = relogio_monotonic
        self._sleep = sleep
        self._tentativas = tentativas
        self._ttl_sessao_s = ttl_sessao_s
        self._max_historico = max_mensagens_historico
        self._telemetria: Telemetria = telemetria if telemetria is not None else TelemetriaNula()
        self._espera = wait_random_exponential(multiplier=0.2, max=1.0)

    async def responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        """Responde a uma mensagem do cliente. Nunca lança exceção (I4)."""
        try:
            with self._telemetria.span("chat", tenant_id=ctx.tenant_id, motor="n8n"):
                return await self._responder(ctx, sessao_id, mensagem)
        except Exception as erro:  # rede de segurança: falha fora do turno (sessão, rate limit, config)
            logger.error("motor n8n: falha inesperada tenant=%s tipo=%s", ctx.tenant_id, type(erro).__name__)
            config = self._tenants.get(ctx.tenant_id)
            return RespostaAtendimento(
                texto=mensagem_fixa(ChaveMensagem.ERRO, ctx.idioma, contatos=config.contatos if config else None),
                idioma=ctx.idioma,
                degradada=True,
                codigo=CodigoResposta.ERRO,
            )

    # --- turno ------------------------------------------------------------------------------------------------------
    async def _responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        config = self._tenants.get(ctx.tenant_id)
        manual = self._politicas.get(ctx.tenant_id)
        if config is None or manual is None:
            logger.warning("motor n8n: tenant desconhecido tenant=%s", ctx.tenant_id)
            return RespostaAtendimento(
                mensagem_fixa(ChaveMensagem.ERRO, ctx.idioma), ctx.idioma, degradada=True, codigo=CodigoResposta.ERRO
            )

        estado = await self._sessoes.obter(ctx, sessao_id) or EstadoSessao()
        idioma_atual = estado.idioma or self._idioma_do_canal(ctx, config)
        texto = mensagem.texto

        if not texto.strip():
            return RespostaAtendimento(
                mensagem_fixa(ChaveMensagem.VAZIA, idioma_atual), idioma_atual, codigo=CodigoResposta.ENTRADA_INVALIDA
            )
        limite = config.limites.tamanho_max_mensagem
        if len(texto) > limite:
            fixa = mensagem_fixa(ChaveMensagem.LONGA, idioma_atual, limite_caracteres=limite)
            return RespostaAtendimento(fixa, idioma_atual, codigo=CodigoResposta.ENTRADA_INVALIDA)

        for camada, chave in (("sessao", sessao_id), ("tenant", CHAVE_RATE_LIMIT_TENANT)):
            consumo = await self._limitador.consumir(ctx, camada, chave)
            if not consumo.permitido:
                self._telemetria.rate_limit_negado(ctx.tenant_id, camada)
                return RespostaAtendimento(
                    mensagem_fixa(ChaveMensagem.LIMITE, idioma_atual),
                    idioma_atual,
                    degradada=True,
                    codigo=CodigoResposta.LIMITE,
                    retry_after_s=consumo.retry_after_s,
                )

        idioma_sessao = resolver_idioma(texto, estado.idioma, config.idiomas_suportados)
        idioma = idioma_sessao or self._idioma_do_canal(ctx, config)
        ctx_turno = dataclasses.replace(ctx, idioma=idioma)
        turno = _Turno()
        prazo = self._agora() + config.deadline_mensagem_s

        falha: ChaveMensagem
        try:
            async with asyncio.timeout(config.deadline_mensagem_s):
                desfecho = await self._executar_turno(
                    ctx_turno,
                    sessao_id=sessao_id,
                    texto=texto,
                    estado=estado,
                    config=config,
                    manual=manual,
                    prazo=prazo,
                    turno=turno,
                )
        except TimeoutError:
            logger.warning("motor n8n: deadline esgotado tenant=%s", ctx.tenant_id)
            falha = ChaveMensagem.DEADLINE
        except _FalhaN8n as erro:
            logger.warning("motor n8n: falha tenant=%s motivo=%s", ctx.tenant_id, erro)
            falha = erro.chave
        except Exception as erro:
            logger.error("motor n8n: falha no turno tenant=%s tipo=%s", ctx.tenant_id, type(erro).__name__)
            falha = ChaveMensagem.ERRO
        else:
            if desfecho.texto is not None:
                tools = _mensagens_de_tools(turno.chamadas, prefixo=f"{PREFIXO_ID_TOOL}{len(estado.historico)}-")
                novas = (MensagemLLM(PapelMensagem.USER, texto), *tools)
                historico = (*estado.historico, *novas, MensagemLLM(PapelMensagem.ASSISTANT, desfecho.texto))
                await self._gravar(ctx, sessao_id, historico, idioma_sessao)
                return self._resposta(desfecho.texto, idioma, turno, degradada=False, codigo=None)
            falha = desfecho.falha or ChaveMensagem.ERRO
            logger.warning("motor n8n: resposta degradada tenant=%s motivo=%s", ctx.tenant_id, falha.value)

        fixa = mensagem_fixa(falha, idioma, contatos=config.contatos)
        historico = (
            *estado.historico,
            MensagemLLM(PapelMensagem.USER, texto),
            MensagemLLM(PapelMensagem.ASSISTANT, fixa),
        )
        await self._gravar(ctx, sessao_id, historico, idioma_sessao)
        return self._resposta(
            fixa, idioma, turno, degradada=True, codigo=_CODIGO_DA_FALHA.get(falha, CodigoResposta.ERRO)
        )

    async def _executar_turno(
        self,
        ctx: TenantContext,
        *,
        sessao_id: str,
        texto: str,
        estado: EstadoSessao,
        config: ConfigTenant,
        manual: str,
        prazo: float,
        turno: _Turno,
    ) -> _Desfecho:
        prompt = montar_prompt(config, manual, ctx.idioma, self._relogio.agora().astimezone(FUSO_LOJA).date())
        token = self._identidade.emitir(
            sub=SUB_SERVICO,
            tenant_id=ctx.tenant_id,
            papeis=(Papel.ATENDENTE,),
            validade_s=math.ceil(config.deadline_mensagem_s) + MARGEM_TOKEN_S,
        )
        conversa = _conversa(estado.historico)
        resposta = await self._rodada(
            ctx,
            sessao_id=sessao_id,
            mensagem=texto,
            conversa=conversa,
            prompt=prompt,
            token=token,
            prazo=prazo,
            turno=turno,
        )
        suspeitos = self._suspeitos(resposta.texto, estado.historico, texto, turno.chamadas, manual)
        if not suspeitos:
            return _Desfecho(texto=resposta.texto)

        # Rodada corretiva (como no nativo): a resposta anterior e a nota interna vão no histórico desta chamada.
        logger.info("motor n8n: rodada corretiva de valores tenant=%s qtd=%d", ctx.tenant_id, len(suspeitos))
        conversa_corrigida = (
            *conversa,
            MensagemHistorico(papel="user", texto=texto),
            MensagemHistorico(papel="assistant", texto=resposta.texto),
        )
        resposta = await self._rodada(
            ctx,
            sessao_id=sessao_id,
            mensagem=nota_correcao(suspeitos),
            conversa=conversa_corrigida,
            prompt=prompt,
            token=token,
            prazo=prazo,
            turno=turno,
        )
        if self._suspeitos(resposta.texto, estado.historico, texto, turno.chamadas, manual):
            return _Desfecho(falha=ChaveMensagem.VALOR_NAO_VERIFICADO)
        return _Desfecho(texto=resposta.texto)

    @staticmethod
    def _suspeitos(
        resposta: str,
        historico: Sequence[MensagemLLM],
        texto_cliente: str,
        chamadas: Sequence[ChamadaToolWebhook],
        manual: str,
    ) -> tuple[str, ...]:
        """Guarda de valores (I6): mesma regra do nativo, com os resultados de tools devolvidos pelo workflow."""
        fontes = (*historico, MensagemLLM(PapelMensagem.USER, texto_cliente), *_mensagens_de_tools(chamadas))
        return valores_nao_verificados(resposta, valores_permitidos(fontes, manual))

    async def _rodada(
        self,
        ctx: TenantContext,
        *,
        sessao_id: str,
        mensagem: str,
        conversa: Sequence[MensagemHistorico],
        prompt: PromptSistema,
        token: str,
        prazo: float,
        turno: _Turno,
    ) -> RespostaWebhook:
        pedido = PedidoWebhook(
            tenant_id=ctx.tenant_id,
            sessao_id=sessao_id,
            mensagem=mensagem,
            idioma=ctx.idioma.value,
            historico=list(conversa),
            sistema=SistemaWebhook(cacheavel=prompt.cacheavel, dinamico=prompt.dinamico),
            tools=ToolsWebhook(url_base=f"{self._url_tools}/v1/tools", token=token),
            prazo_s=max(prazo - self._agora(), 0.0),
        )
        resposta = await self._chamar(pedido.model_dump(mode="json"), prazo)
        turno.chamadas.extend(resposta.tools_chamadas)
        turno.nivel = resposta.nivel or turno.nivel
        turno.provedor = resposta.provedor or turno.provedor
        return resposta

    # --- HTTP + resiliência -----------------------------------------------------------------------------------------
    async def _chamar(self, corpo: Mapping[str, object], prazo: float) -> RespostaWebhook:
        tentativas = AsyncRetrying(
            stop=lambda estado: self._parar(estado, prazo),
            wait=self._espera,
            retry=retry_if_exception(lambda e: isinstance(e, _FalhaN8n) and e.retentavel),
            sleep=self._sleep,
            reraise=True,
        )
        resposta: RespostaWebhook = await tentativas(self._tentativa, corpo, prazo)
        return resposta

    def _parar(self, estado: RetryCallState, prazo: float) -> bool:
        if estado.attempt_number >= self._tentativas:
            return True
        return prazo - self._agora() - estado.upcoming_sleep <= 0

    async def _tentativa(self, corpo: Mapping[str, object], prazo: float) -> RespostaWebhook:
        estado = await self._circuito.estado(NOME_CIRCUITO)
        self._telemetria.circuito_estado(NOME_CIRCUITO, estado)
        if estado is EstadoCircuito.ABERTO:
            raise _FalhaN8n("circuito aberto", ChaveMensagem.INDISPONIVEL)
        restante = prazo - self._agora()
        if restante <= 0:
            raise TimeoutError
        cabecalhos = {"Authorization": f"Bearer {self._segredo}"} if self._segredo else {}
        try:
            r = await self._http.post(self._url, json=corpo, headers=cabecalhos, timeout=restante)
        except httpx.ConnectTimeout:
            await self._falha(permanente=False)
            raise _FalhaN8n("timeout de conexão", ChaveMensagem.INDISPONIVEL, retentavel=True) from None
        except httpx.TimeoutException:
            raise TimeoutError from None  # o timeout HTTP é o restante do deadline: não conta contra o n8n
        except httpx.TransportError as erro:
            await self._falha(permanente=False)
            motivo = f"transporte: {type(erro).__name__}"
            raise _FalhaN8n(motivo, ChaveMensagem.INDISPONIVEL, retentavel=True) from None
        return await self._interpretar(r)

    async def _interpretar(self, r: httpx.Response) -> RespostaWebhook:
        status = r.status_code
        if status in STATUS_RETENTAVEIS:
            await self._falha(permanente=False)
            raise _FalhaN8n(f"HTTP {status}", ChaveMensagem.INDISPONIVEL, retentavel=True)
        if status in STATUS_PERMANENTES:
            await self._falha(permanente=True)
            raise _FalhaN8n(f"HTTP {status} (segredo ou webhook)", ChaveMensagem.INDISPONIVEL)
        if status >= _HTTP_ERRO_SERVIDOR:
            # O workflow executou e falhou (LLM, tool...): não repete, para não gastar LLM nem repetir tools.
            await self._falha(permanente=False)
            raise _FalhaN8n(f"HTTP {status} (erro no workflow)", ChaveMensagem.INDISPONIVEL)
        if status != _HTTP_OK:
            await self._sucesso()  # o n8n respondeu: o problema é o pedido (contrato divergente)
            raise _FalhaN8n(f"HTTP {status} inesperado", ChaveMensagem.ERRO)
        try:
            resposta = RespostaWebhook.model_validate(json.loads(r.content))
        except (ValueError, ValidationError):
            await self._falha(permanente=False)
            raise _FalhaN8n("resposta malformada", ChaveMensagem.ERRO) from None
        await self._sucesso()
        return resposta

    async def _sucesso(self) -> None:
        await self._circuito.registrar_sucesso(NOME_CIRCUITO)

    async def _falha(self, *, permanente: bool) -> None:
        await self._circuito.registrar_falha(NOME_CIRCUITO, permanente=permanente)

    # --- apoio ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _idioma_do_canal(ctx: TenantContext, config: ConfigTenant) -> Idioma:
        return ctx.idioma if ctx.idioma in config.idiomas_suportados else config.idioma_padrao

    async def _gravar(
        self, ctx: TenantContext, sessao_id: str, historico: tuple[MensagemLLM, ...], idioma: Idioma | None
    ) -> None:
        estado = EstadoSessao(historico=cortar_historico(historico, self._max_historico), idioma=idioma)
        await self._sessoes.salvar(ctx, sessao_id, estado, self._ttl_sessao_s)

    @staticmethod
    def _resposta(
        texto: str, idioma: Idioma, turno: _Turno, *, degradada: bool, codigo: CodigoResposta | None
    ) -> RespostaAtendimento:
        return RespostaAtendimento(
            texto=texto,
            idioma=idioma,
            nivel=turno.nivel,
            provedor=turno.provedor,
            tools_chamadas=tuple(c.nome for c in turno.chamadas),
            degradada=degradada,
            codigo=codigo,
        )


def _conversa(historico: Iterable[MensagemLLM]) -> tuple[MensagemHistorico, ...]:
    """O que o workflow recebe da sessão: falas do cliente e respostas finais (sem tools e sem notas internas)."""
    saida: list[MensagemHistorico] = []
    for m in historico:
        if m.role is PapelMensagem.USER and not eh_nota_interna(m) and m.content:
            saida.append(MensagemHistorico(papel="user", texto=m.content))
        elif m.role is PapelMensagem.ASSISTANT and not m.tool_calls and m.content:
            saida.append(MensagemHistorico(papel="assistant", texto=m.content))
    return tuple(saida)


def _mensagens_de_tools(
    chamadas: Sequence[ChamadaToolWebhook], *, prefixo: str = PREFIXO_ID_TOOL
) -> tuple[MensagemLLM, ...]:
    """Tools do turno no formato do nativo (um `ASSISTANT` com `tool_calls` + um `TOOL` por resultado).

    Assim o histórico gravado continua válido se o motor voltar a ser o nativo, e a guarda dos turnos seguintes
    enxerga os resultados (como no nativo). `prefixo` deixa os ids únicos na sessão.
    """
    if not chamadas:
        return ()
    ids = [f"{prefixo}{i}" for i in range(len(chamadas))]
    pedido = MensagemLLM(
        PapelMensagem.ASSISTANT,
        "",
        tool_calls=tuple(ChamadaTool(id_, c.nome, c.argumentos) for id_, c in zip(ids, chamadas, strict=True)),
    )
    resultados = (
        MensagemLLM(PapelMensagem.TOOL, c.resultado or "", tool_call_id=id_)
        for id_, c in zip(ids, chamadas, strict=True)
    )
    return (pedido, *resultados)

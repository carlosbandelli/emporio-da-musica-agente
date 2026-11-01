"""`AgenteNativo`: implementa a porta `MotorDeAtendimento` (arquitetura §3.2) com roteador + prompt CAG + tools.

Fluxo de `responder` (uma mensagem do cliente = um turno):

1. tenant → config e manual (desconhecido: mensagem fixa genérica);
2. sessão (`SessaoStore`): histórico e idioma;
3. validação (I1): vazia ou acima de `tamanho_max_mensagem` → mensagem fixa, sem LLM e sem gravar;
4. rate limit nas camadas `sessao` e `tenant` → negado: mensagem fixa, sem gravar;
5. idioma (I7): pedido explícito de troca, idioma da sessão ou detecção; ambíguo → idioma do canal (`ctx.idioma`);
6. deadline (I5) do tenant: `prazo_monotonic` em toda requisição + `asyncio.timeout` de reserva no turno inteiro;
7. classifica uma vez e roda o loop agentic (no máximo `max_iteracoes` chamadas ao LLM);
8. guarda de valores (I6) com uma rodada corretiva;
9. grava o turno (append-only) e corta o histórico longo por turnos inteiros.

Nunca lança (I4): toda falha vira mensagem fixa com `degradada=True`. Em falha no meio do turno, a sessão recebe só
a mensagem do cliente e a resposta fixa em texto, nunca uma rodada de tools incompleta. Sem estado global: tudo é
atributo da instância (I8).
"""

import asyncio
import dataclasses
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from emporio.adaptadores.motor.nativo.guarda import nota_correcao, valores_nao_verificados, valores_permitidos
from emporio.adaptadores.motor.nativo.historico import cortar_historico
from emporio.adaptadores.motor.nativo.idioma import resolver_idioma
from emporio.adaptadores.motor.nativo.mensagens import ChaveMensagem, mensagem_fixa
from emporio.adaptadores.motor.nativo.prompt import montar_prompt
from emporio.adaptadores.motor.nativo.roteador import ErroSemProvedor, Nivel, Roteador
from emporio.modulos.atendimento.aplicacao import ResultadoTool
from emporio.modulos.politicas.dominio import FUSO_LOJA
from emporio.portas.contexto import Idioma, Papel, TenantContext
from emporio.portas.estado import EstadoSessao, RateLimiter, Relogio, SessaoStore
from emporio.portas.llm import DefinicaoTool, ErroPrazoEsgotado, MensagemLLM, PapelMensagem, RequisicaoLLM
from emporio.portas.motor import CodigoResposta, MensagemCliente, RespostaAtendimento
from emporio.portas.tenant import ConfigTenant

logger = logging.getLogger(__name__)

MAX_ITERACOES_PADRAO: Final = 6
"""Chamadas ao LLM por mensagem do cliente (rodadas de tools + resposta final + rodada corretiva)."""
TTL_SESSAO_S: Final = 1800
"""Sessão expira após 30 min sem mensagens."""
MAX_MENSAGENS_HISTORICO: Final = 40
"""Acima disso, os turnos mais antigos saem do histórico."""
CHAVE_RATE_LIMIT_TENANT: Final = "global"

RelogioMonotonic = Callable[[], float]

_CODIGO_DA_FALHA: Final = {
    ChaveMensagem.INDISPONIVEL: CodigoResposta.INDISPONIVEL,
    ChaveMensagem.DEADLINE: CodigoResposta.DEADLINE,
}
"""Falhas do turno com código próprio; as demais viram `CodigoResposta.ERRO`."""


class FachadaTools(Protocol):
    """O que o motor usa da fachada de tools (`FerramentasLoja`)."""

    def definicoes(self, papel: Papel) -> tuple[DefinicaoTool, ...]:
        """Tools que o papel pode usar, em ordem determinística."""
        ...

    async def executar(
        self, ctx: TenantContext, nome: str, argumentos: Mapping[str, object], *, sessao_id: str | None = None
    ) -> ResultadoTool:
        """Executa a tool; erros de negócio voltam como `ResultadoTool(ok=False)`."""
        ...


@dataclass(slots=True)
class _Turno:
    """Estado mutável de um turno (só vive durante `responder`)."""

    nivel: Nivel | None = None
    provedor: str | None = None
    novas: list[MensagemLLM] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Desfecho:
    """Resultado do loop: texto final ou a chave da mensagem fixa (degradada)."""

    texto: str | None = None
    falha: ChaveMensagem | None = None


class AgenteNativo:
    """Motor de atendimento nativo. Implementa `MotorDeAtendimento`.

    `tenants` e `politicas` (texto do manual já carregado) são indexados por `tenant_id`; o bloco cacheável do prompt
    é montado uma vez por tenant e guardado na instância, para ser byte a byte igual em todos os turnos.
    """

    def __init__(
        self,
        *,
        tenants: Mapping[str, ConfigTenant],
        politicas: Mapping[str, str],
        roteador: Roteador,
        ferramentas: FachadaTools,
        sessoes: SessaoStore,
        limitador: RateLimiter,
        relogio: Relogio,
        relogio_monotonic: RelogioMonotonic = time.monotonic,
        max_iteracoes: int = MAX_ITERACOES_PADRAO,
        ttl_sessao_s: int = TTL_SESSAO_S,
        max_mensagens_historico: int = MAX_MENSAGENS_HISTORICO,
    ) -> None:
        if max_iteracoes < 1:
            raise ValueError("max_iteracoes deve ser pelo menos 1")
        self._tenants = dict(tenants)
        self._politicas = dict(politicas)
        self._roteador = roteador
        self._ferramentas = ferramentas
        self._sessoes = sessoes
        self._limitador = limitador
        self._relogio = relogio
        self._agora = relogio_monotonic
        self._max_iteracoes = max_iteracoes
        self._ttl_sessao_s = ttl_sessao_s
        self._max_historico = max_mensagens_historico
        self._cacheavel: dict[str, str] = {}

    async def responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        """Responde a uma mensagem do cliente. Nunca lança exceção (I4)."""
        try:
            return await self._responder(ctx, sessao_id, mensagem)
        except Exception as erro:  # rede de segurança: falha fora do turno (sessão, rate limit, config)
            logger.error("motor nativo: falha inesperada tenant=%s tipo=%s", ctx.tenant_id, type(erro).__name__)
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
            logger.warning("motor nativo: tenant desconhecido tenant=%s", ctx.tenant_id)
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
                logger.info("motor nativo: rate limit tenant=%s camada=%s", ctx.tenant_id, camada)
                return RespostaAtendimento(
                    mensagem_fixa(ChaveMensagem.LIMITE, idioma_atual),
                    idioma_atual,
                    degradada=True,
                    codigo=CodigoResposta.LIMITE,
                    retry_after_s=consumo.retry_after_s,
                )

        idioma_sessao = resolver_idioma(texto, estado.idioma, config.idiomas_suportados)
        idioma = idioma_sessao or self._idioma_do_canal(ctx, config)
        ctx_turno = dataclasses.replace(ctx, idioma=idioma)  # as tools formatam dinheiro no idioma da sessão
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
        except ErroSemProvedor as erro:
            logger.warning("motor nativo: sem provedor tenant=%s erro=%s", ctx.tenant_id, erro)
            falha = ChaveMensagem.INDISPONIVEL
        except (ErroPrazoEsgotado, TimeoutError):
            logger.warning("motor nativo: deadline esgotado tenant=%s", ctx.tenant_id)
            falha = ChaveMensagem.DEADLINE
        except Exception as erro:
            logger.error("motor nativo: falha no turno tenant=%s tipo=%s", ctx.tenant_id, type(erro).__name__)
            falha = ChaveMensagem.ERRO
        else:
            if desfecho.texto is not None:
                historico = (*estado.historico, *turno.novas)
                await self._gravar(ctx, sessao_id, historico, idioma_sessao)
                return self._resposta(desfecho.texto, idioma, turno, degradada=False, codigo=None)
            falha = desfecho.falha or ChaveMensagem.ERRO
            logger.warning("motor nativo: resposta degradada tenant=%s motivo=%s", ctx.tenant_id, falha.value)

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
        historico = estado.historico
        turno.nivel = await self._roteador.classificar(texto, historico, prazo_monotonic=prazo)
        prompt = montar_prompt(config, manual, ctx.idioma, self._relogio.agora().astimezone(FUSO_LOJA).date())
        cacheavel = self._cacheavel.setdefault(ctx.tenant_id, prompt.cacheavel)
        tools = self._ferramentas.definicoes(ctx.papel)
        turno.novas.append(MensagemLLM(PapelMensagem.USER, texto))
        corrigiu = False
        resposta_vazia = False

        for _ in range(self._max_iteracoes):
            req = RequisicaoLLM(
                system_cacheavel=cacheavel,
                system_dinamico=prompt.dinamico,
                mensagens=(*historico, *turno.novas),
                tools=tools,
                prazo_monotonic=prazo,
            )
            gerado = await self._roteador.gerar(turno.nivel, req, preferido=turno.provedor)
            turno.provedor = gerado.provedor
            resposta = gerado.resposta

            if resposta.chamadas_tool:
                turno.novas.append(
                    MensagemLLM(
                        PapelMensagem.ASSISTANT,
                        resposta.texto or "",
                        tool_calls=resposta.chamadas_tool,
                        bruto=resposta.bruto,
                    )
                )
                resultados = await asyncio.gather(
                    *(
                        self._ferramentas.executar(ctx, c.nome, c.argumentos, sessao_id=sessao_id)
                        for c in resposta.chamadas_tool
                    )
                )
                for chamada, resultado in zip(resposta.chamadas_tool, resultados, strict=True):
                    turno.tools.append(chamada.nome)
                    turno.novas.append(
                        MensagemLLM(
                            PapelMensagem.TOOL,
                            resultado.para_json(),
                            tool_call_id=chamada.id,
                            erro_tool=not resultado.ok,
                        )
                    )
                continue

            final = (resposta.texto or "").strip()
            if not final:
                # Raciocínio consumiu a saída ou o modelo devolveu vazio: tenta mais uma vez, sem mexer no histórico.
                if resposta_vazia:
                    return _Desfecho(falha=ChaveMensagem.ERRO)
                resposta_vazia = True
                continue

            turno.novas.append(MensagemLLM(PapelMensagem.ASSISTANT, resposta.texto or "", bruto=resposta.bruto))
            suspeitos = valores_nao_verificados(final, valores_permitidos((*historico, *turno.novas), manual))
            if not suspeitos:
                return _Desfecho(texto=final)
            if corrigiu:
                return _Desfecho(falha=ChaveMensagem.VALOR_NAO_VERIFICADO)
            corrigiu = True
            logger.info("motor nativo: rodada corretiva de valores tenant=%s qtd=%d", ctx.tenant_id, len(suspeitos))
            turno.novas.append(MensagemLLM(PapelMensagem.USER, nota_correcao(suspeitos)))

        return _Desfecho(falha=ChaveMensagem.LIMITE_ITERACOES)

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
            nivel=str(turno.nivel) if turno.nivel is not None else None,
            provedor=turno.provedor,
            tools_chamadas=tuple(turno.tools),
            degradada=degradada,
            codigo=codigo,
        )

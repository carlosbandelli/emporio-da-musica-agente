"""`AgenteN8n` (task 2.7): contrato do webhook, timeout, retry, circuito, resposta malformada e guarda de valores.

O webhook é um `httpx.MockTransport` roteirizado (sem n8n, sem rede). Os invariantes I1-I8 ficam na suíte de
contrato (`tests/contrato`, fábrica `n8n`); aqui ficam os detalhes de resiliência e do contrato HTTP.
"""

import asyncio
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from pydantic import SecretStr

from emporio.adaptadores.identidade import JwtLocal
from emporio.adaptadores.memoria import CircuitoStoreMemoria, RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.comum.guarda import NOTA_INTERNA_PREFIXO
from emporio.adaptadores.motor.comum.prompt import carregar_politicas
from emporio.adaptadores.motor.n8n import NOME_CIRCUITO, VERSAO_CONTRATO, AgenteN8n
from emporio.configuracao import carregar_tenants
from emporio.portas import (
    CodigoResposta,
    EstadoCircuito,
    Idioma,
    MensagemCliente,
    Papel,
    PapelMensagem,
    TenantContext,
)
from emporio.portas.tenant import ConfigTenant
from tests.contrato.kit import DATA_REFERENCIA, RAIZ
from tests.modulos.fakes import RelogioFixo

URL: Final = "http://n8n.teste/webhook/emporio-atendimento"
SEGREDO_JWT: Final = "segredo-jwt-dos-testes-do-agente-n8n-com-32-bytes-ou-mais"
SEGREDO_WEBHOOK: Final = "segredo-webhook"
CTX: Final = TenantContext("emporio")
PRECO: Final = "R$ 2.199,00"
RESULTADO_TOOL: Final = json.dumps(
    {"nome": "detalhar_produto", "ok": True, "dados": {"preco_vigente": {"texto": PRECO, "centavos": 219900}}},
    ensure_ascii=False,
)

Resposta = httpx.Response | BaseException | Callable[[httpx.Request], Any]


class Webhook:
    """Webhook roteirizado: cada chamada consome um item (resposta, exceção ou função async/sync)."""

    def __init__(self, *itens: Resposta) -> None:
        self._itens = list(itens)
        self.pedidos: list[httpx.Request] = []

    def corpos(self) -> list[dict[str, Any]]:
        return [json.loads(p.content) for p in self.pedidos]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        if not self._itens:
            raise AssertionError("webhook chamado além do roteiro")
        item = self._itens.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            resultado = item(request)
            return await resultado if asyncio.iscoroutine(resultado) else resultado
        return item


class CircuitoEspiao(CircuitoStoreMemoria):
    """Circuito real que registra sucessos e falhas (com `permanente`)."""

    def __init__(self, estado_fixo: EstadoCircuito | None = None) -> None:
        super().__init__()
        self.eventos: list[str] = []
        self._fixo = estado_fixo

    async def estado(self, provedor: str) -> EstadoCircuito:
        return self._fixo if self._fixo is not None else await super().estado(provedor)

    async def registrar_sucesso(self, provedor: str) -> None:
        assert provedor == NOME_CIRCUITO
        self.eventos.append("sucesso")
        await super().registrar_sucesso(provedor)

    async def registrar_falha(self, provedor: str, *, permanente: bool) -> None:
        assert provedor == NOME_CIRCUITO
        self.eventos.append("falha_permanente" if permanente else "falha")
        await super().registrar_falha(provedor, permanente=permanente)


def _tenants(**ajustes: object) -> dict[str, ConfigTenant]:
    config = carregar_tenants(RAIZ / "config" / "tenants.yaml")["emporio"]
    return {"emporio": config.model_copy(update=ajustes)}


def _agente(
    webhook: Webhook,
    *,
    circuito: CircuitoEspiao | None = None,
    sessoes: SessaoStoreMemoria | None = None,
    tenants: dict[str, ConfigTenant] | None = None,
    segredo: str | None = SEGREDO_WEBHOOK,
) -> AgenteN8n:
    tenants = tenants or _tenants()

    async def sem_espera(_: float) -> None:
        return None

    return AgenteN8n(
        webhook_url=URL,
        segredo_webhook=segredo,
        url_tools="http://api.teste/",
        tenants=tenants,
        politicas={tid: carregar_politicas(RAIZ / c.politicas_arquivo) for tid, c in tenants.items()},
        http=httpx.AsyncClient(transport=httpx.MockTransport(webhook)),
        identidade=JwtLocal(SecretStr(SEGREDO_JWT)),
        circuito=circuito or CircuitoEspiao(),
        sessoes=sessoes or SessaoStoreMemoria(),
        limitador=RateLimiterMemoria(tenants),
        relogio=RelogioFixo(DATA_REFERENCIA),
        sleep=sem_espera,
    )


def ok(texto: str = "Oi! Sou a Sônia.", tools: Iterable[dict[str, Any]] = (), **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"texto": texto, "tools_chamadas": list(tools), **extra})


async def _perguntar(agente: AgenteN8n, texto: str = "Quanto custa o Takamine GD20?", sessao: str = "s1"):
    return await agente.responder(CTX, sessao, MensagemCliente(texto))


# --- contrato do webhook ---------------------------------------------------------------------------------------------
async def test_corpo_do_webhook_segue_o_contrato_e_token_e_do_tenant() -> None:
    webhook = Webhook(ok("Oi! Sou a Sônia.", provedor="gemini"), ok("Você disse oi."))
    agente = _agente(webhook)
    primeira = await _perguntar(agente, "oi, tudo bem?")
    await _perguntar(agente, "o que eu disse?")

    assert primeira.texto == "Oi! Sou a Sônia." and primeira.provedor == "gemini" and not primeira.degradada
    pedido = webhook.pedidos[0]
    assert pedido.headers["authorization"] == f"Bearer {SEGREDO_WEBHOOK}"
    corpo = webhook.corpos()[0]
    assert corpo["versao"] == VERSAO_CONTRATO
    assert (corpo["tenant_id"], corpo["sessao_id"], corpo["mensagem"], corpo["idioma"]) == (
        "emporio",
        "s1",
        "oi, tudo bem?",
        "pt-BR",
    )
    assert corpo["historico"] == []
    assert "Empório da Música" in corpo["sistema"]["cacheavel"] and corpo["sistema"]["dinamico"]
    assert corpo["tools"]["url_base"] == "http://api.teste/v1/tools"
    assert 0 < corpo["prazo_s"] <= _tenants()["emporio"].deadline_mensagem_s
    identidade = JwtLocal(SecretStr(SEGREDO_JWT)).validar(corpo["tools"]["token"])
    assert identidade.tenant_id == "emporio" and identidade.papeis == frozenset({Papel.ATENDENTE})

    # I2: o histórico vai do Python para o workflow (stateless).
    assert webhook.corpos()[1]["historico"] == [
        {"papel": "user", "texto": "oi, tudo bem?"},
        {"papel": "assistant", "texto": "Oi! Sou a Sônia."},
    ]


async def test_sem_segredo_nao_manda_authorization() -> None:
    webhook = Webhook(ok())
    await _perguntar(_agente(webhook, segredo=None))
    assert "authorization" not in webhook.pedidos[0].headers


async def test_mensagem_invalida_nao_chama_o_webhook() -> None:
    webhook = Webhook()
    resposta = await _perguntar(_agente(webhook), "   ")
    assert resposta.codigo is CodigoResposta.ENTRADA_INVALIDA and webhook.pedidos == []


async def test_turno_com_tools_grava_historico_no_formato_do_nativo() -> None:
    sessoes = SessaoStoreMemoria()
    tool = {"nome": "detalhar_produto", "argumentos": {"nome": "Takamine GD20"}, "resultado": RESULTADO_TOOL}
    webhook = Webhook(ok(f"Sai por {PRECO}.", [tool]), ok(f"Continua {PRECO}."))
    agente = _agente(webhook, sessoes=sessoes)

    resposta = await _perguntar(agente)
    assert resposta.texto == f"Sai por {PRECO}." and resposta.tools_chamadas == ("detalhar_produto",)
    estado = await sessoes.obter(CTX, "s1")
    assert estado is not None
    papeis = [m.role for m in estado.historico]
    assert papeis == [PapelMensagem.USER, PapelMensagem.ASSISTANT, PapelMensagem.TOOL, PapelMensagem.ASSISTANT]
    pedido_tool, resultado = estado.historico[1], estado.historico[2]
    assert pedido_tool.tool_calls[0].nome == "detalhar_produto"
    assert resultado.tool_call_id == pedido_tool.tool_calls[0].id and resultado.content == RESULTADO_TOOL

    # 2º turno: o preço da tool do turno anterior continua permitido (como no nativo) e as tools não vão ao n8n.
    segunda = await _perguntar(agente, "e agora?")
    assert segunda.texto == f"Continua {PRECO}." and not segunda.degradada
    assert [m["papel"] for m in webhook.corpos()[1]["historico"]] == ["user", "assistant"]


# --- guarda de valores (I6) ------------------------------------------------------------------------------------------
async def test_preco_sem_tool_faz_rodada_corretiva_e_depois_degrada() -> None:
    webhook = Webhook(ok("Custa R$ 999,00."), ok("Custa R$ 999,00 mesmo."))
    resposta = await _perguntar(_agente(webhook))

    assert resposta.degradada and resposta.codigo is CodigoResposta.ERRO and "999" not in resposta.texto
    correcao = webhook.corpos()[1]
    assert correcao["mensagem"].startswith(NOTA_INTERNA_PREFIXO) and "R$ 999,00" in correcao["mensagem"]
    assert correcao["historico"][-1] == {"papel": "assistant", "texto": "Custa R$ 999,00."}


async def test_rodada_corretiva_que_consulta_a_tool_e_aceita() -> None:
    tool = {"nome": "detalhar_produto", "resultado": RESULTADO_TOOL}
    webhook = Webhook(ok("Custa R$ 999,00."), ok(f"Corrigindo: {PRECO}.", [tool]))
    resposta = await _perguntar(_agente(webhook))
    assert resposta.texto == f"Corrigindo: {PRECO}." and not resposta.degradada
    assert resposta.tools_chamadas == ("detalhar_produto",)


async def test_tool_so_com_nome_nao_libera_valores() -> None:
    webhook = Webhook(ok(f"Custa {PRECO}.", ["detalhar_produto"]), ok(f"Custa {PRECO}.", ["detalhar_produto"]))
    resposta = await _perguntar(_agente(webhook))
    assert resposta.degradada and PRECO not in resposta.texto


# --- deadline (I5) ---------------------------------------------------------------------------------------------------
async def test_timeout_vira_degradada_deadline_sem_contar_contra_o_n8n() -> None:
    async def lento(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return ok()

    circuito = CircuitoEspiao()
    webhook = Webhook(lento)
    resposta = await _perguntar(_agente(webhook, circuito=circuito, tenants=_tenants(deadline_mensagem_s=0.05)))
    assert resposta.degradada and resposta.codigo is CodigoResposta.DEADLINE
    assert circuito.eventos == []


async def test_read_timeout_do_httpx_vira_deadline() -> None:
    circuito = CircuitoEspiao()
    webhook = Webhook(httpx.ReadTimeout("lento"))
    resposta = await _perguntar(_agente(webhook, circuito=circuito))
    assert resposta.codigo is CodigoResposta.DEADLINE and len(webhook.pedidos) == 1 and circuito.eventos == []


# --- retry e circuito ------------------------------------------------------------------------------------------------
async def test_503_tem_retry_e_a_segunda_tentativa_responde() -> None:
    circuito = CircuitoEspiao()
    webhook = Webhook(httpx.Response(503), ok("Voltei!"))
    resposta = await _perguntar(_agente(webhook, circuito=circuito), "oi")
    assert resposta.texto == "Voltei!" and not resposta.degradada
    assert len(webhook.pedidos) == 2 and circuito.eventos == ["falha", "sucesso"]


@pytest.mark.parametrize("falha", [httpx.Response(502), httpx.Response(504), httpx.ConnectError("recusada")])
async def test_falha_transitoria_persistente_para_no_limite_de_tentativas(falha: Resposta) -> None:
    circuito = CircuitoEspiao()
    webhook = Webhook(falha, falha)
    resposta = await _perguntar(_agente(webhook, circuito=circuito), "oi")
    assert resposta.degradada and resposta.codigo is CodigoResposta.INDISPONIVEL
    assert len(webhook.pedidos) == 2 and circuito.eventos == ["falha", "falha"]
    assert "(67) 3321-4500" in resposta.texto  # mensagem fixa com o WhatsApp do tenant


async def test_500_do_workflow_nao_tem_retry() -> None:
    circuito = CircuitoEspiao()
    webhook = Webhook(httpx.Response(500, json={"message": "Error in workflow"}))
    resposta = await _perguntar(_agente(webhook, circuito=circuito), "oi")
    assert resposta.codigo is CodigoResposta.INDISPONIVEL and len(webhook.pedidos) == 1
    assert circuito.eventos == ["falha"] and "Error in workflow" not in resposta.texto


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_segredo_errado_ou_webhook_inativo_abre_o_circuito_na_hora(status: int) -> None:
    circuito = CircuitoEspiao()
    webhook = Webhook(httpx.Response(status))
    agente = _agente(webhook, circuito=circuito)
    resposta = await _perguntar(agente, "oi")
    assert resposta.codigo is CodigoResposta.INDISPONIVEL and circuito.eventos == ["falha_permanente"]
    # Circuito aberto: a próxima mensagem nem chega ao webhook.
    seguinte = await _perguntar(agente, "oi de novo", sessao="s2")
    assert seguinte.codigo is CodigoResposta.INDISPONIVEL and len(webhook.pedidos) == 1


async def test_circuito_aberto_nao_chama_o_webhook() -> None:
    webhook = Webhook()
    resposta = await _perguntar(_agente(webhook, circuito=CircuitoEspiao(EstadoCircuito.ABERTO)), "oi")
    assert resposta.degradada and resposta.codigo is CodigoResposta.INDISPONIVEL and webhook.pedidos == []


async def test_status_inesperado_e_erro_sem_contar_falha() -> None:
    circuito = CircuitoEspiao()
    resposta = await _perguntar(_agente(Webhook(httpx.Response(422)), circuito=circuito), "oi")
    assert resposta.codigo is CodigoResposta.ERRO and circuito.eventos == ["sucesso"]


# --- resposta malformada ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "resposta_n8n",
    [
        httpx.Response(200, text="<html>não é json</html>"),
        httpx.Response(200, json={"output": "campo errado"}),
        httpx.Response(200, json={"texto": "   "}),
        httpx.Response(200, json={"texto": "ok", "tools_chamadas": [{"nome": "../admin"}]}),
        httpx.Response(200, json=["lista"]),
    ],
    ids=["nao-json", "sem-texto", "texto-vazio", "nome-de-tool-invalido", "lista"],
)
async def test_resposta_malformada_degrada_com_erro(resposta_n8n: httpx.Response) -> None:
    circuito = CircuitoEspiao()
    resposta = await _perguntar(_agente(Webhook(resposta_n8n), circuito=circuito), "oi")
    assert resposta.degradada and resposta.codigo is CodigoResposta.ERRO
    assert resposta.idioma is Idioma.PT_BR and circuito.eventos == ["falha"]


async def test_falha_grava_so_mensagem_do_cliente_e_a_fixa() -> None:
    sessoes = SessaoStoreMemoria()
    resposta = await _perguntar(_agente(Webhook(httpx.Response(500)), sessoes=sessoes), "oi")
    estado = await sessoes.obter(CTX, "s1")
    assert estado is not None
    assert [(m.role, m.content) for m in estado.historico] == [
        (PapelMensagem.USER, "oi"),
        (PapelMensagem.ASSISTANT, resposta.texto),
    ]


async def test_idioma_en_us_vai_no_corpo_e_na_mensagem_fixa() -> None:
    webhook = Webhook(ok("Hi!"), httpx.Response(500))
    agente = _agente(webhook)
    await _perguntar(agente, "Hi, how much is the Takamine GD20?", sessao="en")
    falha = await _perguntar(agente, "ok", sessao="en")
    assert [c["idioma"] for c in webhook.corpos()] == ["en-US", "en-US"]
    assert falha.idioma is Idioma.EN_US and "try again" in falha.texto


def test_construtor_valida_parametros(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tentativas"):
        AgenteN8n(
            webhook_url=URL,
            url_tools="http://api",
            tenants={},
            politicas={},
            http=httpx.AsyncClient(),
            identidade=JwtLocal(SecretStr(SEGREDO_JWT)),
            circuito=CircuitoEspiao(),
            sessoes=SessaoStoreMemoria(),
            limitador=RateLimiterMemoria({}),
            relogio=RelogioFixo(DATA_REFERENCIA),
            tentativas=0,
        )

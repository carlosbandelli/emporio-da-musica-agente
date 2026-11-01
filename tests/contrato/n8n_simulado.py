"""`N8nSimulado`: faz o papel do workflow de `deploy/n8n/` dentro de um `httpx.MockTransport` (sem n8n de verdade).

Webhook → "AI Agent" (o `LLMFalso` do kit decide) → "HTTP Request tools" (`POST {url_base}/{nome}` na API FastAPI
montada em memória, com o token e o tenant que o motor mandou) → "Respond to Webhook" (`texto` + `tools_chamadas`).
Como no n8n, nenhuma regra de negócio mora aqui: preço, estoque e políticas vêm das tools reais, e o espião de tools
do kit as enxerga do lado servidor.

Falha no "workflow" (LLM levantou, roteiro esgotado, limite de iterações) vira HTTP 500, como o n8n responde quando
uma execução quebra antes do "Respond to Webhook". Erro de tool não quebra o workflow: o corpo volta ao LLM como
resultado com erro (é o que o HTTP Request tool do n8n faz).
"""

import json
from typing import Any, Final

import httpx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from emporio.adaptadores.llm import LLMFalso
from emporio.portas import DefinicaoTool, MensagemLLM, PapelMensagem, RequisicaoLLM

MAX_ITERACOES_AGENTE: Final = 6
"""O `maxIterations` do nó AI Agent do workflow de exemplo."""
_PAPEIS: Final = {"user": PapelMensagem.USER, "assistant": PapelMensagem.ASSISTANT}


class _TransporteApi(httpx.AsyncBaseTransport):
    """ASGI em memória com o `lifespan` da app em volta de cada requisição; exceção da app vira 500 (não propaga)."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._asgi = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with LifespanManager(self._app):
            resposta = await self._asgi.handle_async_request(request)
            conteudo = await resposta.aread()
        return httpx.Response(resposta.status_code, headers=resposta.headers, content=conteudo, request=request)


class N8nSimulado:
    """Workflow n8n simulado. `webhooks` guarda os corpos recebidos (para os testes inspecionarem o contrato)."""

    def __init__(self, llm: LLMFalso, *, segredo: str | None = None) -> None:
        self._llm = llm
        self._segredo = segredo
        self._api: FastAPI | None = None
        self.webhooks: list[dict[str, Any]] = []

    def conectar_api(self, app: FastAPI) -> None:
        """App FastAPI que as "HTTP Request tools" chamam (montada depois do motor, que ela mesma contém)."""
        self._api = app

    def transporte(self) -> httpx.MockTransport:
        """Transporte para o `httpx.AsyncClient` do `AgenteN8n`."""
        return httpx.MockTransport(self.tratar)

    async def tratar(self, request: httpx.Request) -> httpx.Response:
        """Nó Webhook (Header Auth) + execução do workflow."""
        if self._segredo is not None and request.headers.get("authorization") != f"Bearer {self._segredo}":
            return httpx.Response(403, text="Authorization data is wrong!")
        corpo: dict[str, Any] = json.loads(request.content)
        self.webhooks.append(corpo)
        try:
            saida = await self._executar(corpo)
        except Exception as erro:
            return httpx.Response(500, json={"message": "Error in workflow", "tipo": type(erro).__name__})
        return httpx.Response(200, json=saida)

    async def _executar(self, corpo: dict[str, Any]) -> dict[str, Any]:
        assert self._api is not None, "conecte a API antes de usar o n8n simulado"
        tools = corpo["tools"]
        cabecalhos = {
            "Authorization": f"Bearer {tools['token']}",
            tools["cabecalho_tenant"]: corpo["tenant_id"],
            "Accept-Language": corpo["idioma"],
        }
        async with httpx.AsyncClient(transport=_TransporteApi(self._api)) as api:
            lista = await api.get(tools["url_base"], headers=cabecalhos)
            lista.raise_for_status()
            definicoes = tuple(DefinicaoTool(t["nome"], t["descricao"], t["parametros"]) for t in lista.json()["tools"])
            mensagens = [MensagemLLM(_PAPEIS[m["papel"]], m["texto"]) for m in corpo["historico"]]
            mensagens.append(MensagemLLM(PapelMensagem.USER, corpo["mensagem"]))
            chamadas: list[dict[str, Any]] = []
            for _ in range(MAX_ITERACOES_AGENTE):
                resposta = await self._llm.gerar(
                    RequisicaoLLM(
                        system_cacheavel=corpo["sistema"]["cacheavel"],
                        system_dinamico=corpo["sistema"]["dinamico"],
                        mensagens=tuple(mensagens),
                        tools=definicoes,
                    )
                )
                if not resposta.chamadas_tool:
                    return {"texto": resposta.texto or "", "tools_chamadas": chamadas, "provedor": self._llm.nome}
                mensagens.append(MensagemLLM(PapelMensagem.ASSISTANT, "", tool_calls=resposta.chamadas_tool))
                for chamada in resposta.chamadas_tool:
                    r = await api.post(
                        f"{tools['url_base']}/{chamada.nome}",
                        json={"argumentos": dict(chamada.argumentos), "session_id": corpo["sessao_id"]},
                        headers=cabecalhos,
                    )
                    mensagens.append(
                        MensagemLLM(PapelMensagem.TOOL, r.text, tool_call_id=chamada.id, erro_tool=r.is_error)
                    )
                    chamadas.append({"nome": chamada.nome, "argumentos": dict(chamada.argumentos), "resultado": r.text})
        raise RuntimeError("AI Agent: limite de iterações")

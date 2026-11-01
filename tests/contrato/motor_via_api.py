"""`MotorViaApi`: implementa `MotorDeAtendimento` chamando `POST /v1/chat` de uma app FastAPI por ASGI em memória.

Serve para rodar os invariantes I1-I8 também através da API (task 2.1): a porta que os adapters de entrada usam
continua sendo a mesma, só que com HTTP no meio (tenant por `X-Tenant-ID`, idioma do canal no corpo).
"""

import httpx
from fastapi import FastAPI

from emporio.portas import CodigoResposta, Idioma, MensagemCliente, RespostaAtendimento, TenantContext
from tests.adaptadores.api.apoio import BASE, TransporteComLifespan


class MotorViaApi:
    """Cliente da API com a forma da porta. Uma resposta sem `texto` (400/404/422) vira `RuntimeError`.

    Isso não viola I4 (a porta nunca lança): erros de borda não existem para um tenant válido e uma mensagem
    bem-formada, que é tudo que a suíte envia; se aparecerem, o teste deve falhar alto, não degradar em silêncio.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def responder(self, ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento:
        """`POST /v1/chat` e reconstrói a `RespostaAtendimento` do corpo (inclusive em 429 e 503)."""
        async with httpx.AsyncClient(transport=TransporteComLifespan(self._app), base_url=BASE) as http:
            r = await http.post(
                "/v1/chat",
                json={"session_id": sessao_id, "mensagem": mensagem.texto, "idioma": ctx.idioma.value},
                headers={"X-Tenant-ID": ctx.tenant_id},
            )
        dados = r.json()
        if "texto" not in dados:
            raise RuntimeError(f"a API recusou a requisição (HTTP {r.status_code}): {dados}")
        return RespostaAtendimento(
            texto=dados["texto"],
            idioma=Idioma(dados["idioma"]),
            nivel=dados["nivel"],
            provedor=dados["provedor"],
            tools_chamadas=tuple(dados["tools_chamadas"]),
            degradada=dados["degradada"],
            codigo=CodigoResposta(dados["codigo"]) if dados["codigo"] else None,
            retry_after_s=dados["retry_after_s"],
        )

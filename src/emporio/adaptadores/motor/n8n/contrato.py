"""Contrato do webhook do n8n (versão 1), documentado em `deploy/n8n/README.md`.

Request (`POST N8N_WEBHOOK_URL`, `Authorization: Bearer <N8N_WEBHOOK_SEGREDO>` quando configurado):

```json
{"versao": 1, "tenant_id": "emporio", "sessao_id": "s1", "mensagem": "...", "idioma": "pt-BR",
 "historico": [{"papel": "user", "texto": "..."}, {"papel": "assistant", "texto": "..."}],
 "sistema": {"cacheavel": "...", "dinamico": "..."},
 "tools": {"url_base": "http://app:8000/v1/tools", "token": "<JWT curto>", "cabecalho_tenant": "X-Tenant-ID"},
 "prazo_s": 29.9}
```

Response 200:

```json
{"texto": "...", "tools_chamadas": [{"nome": "detalhar_produto", "argumentos": {}, "resultado": "<corpo da tool>"}],
 "nivel": null, "provedor": "gemini"}
```

O histórico e o prompt vêm do Python a cada chamada (o workflow não guarda estado). `resultado` é o corpo devolvido
por `POST /v1/tools/{nome}` (texto ou JSON): é dele que a guarda de valores (I6) tira os preços permitidos.
"""

import json
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

VERSAO_CONTRATO: Final = 1
_NOME_TOOL: Final = r"^[A-Za-z0-9_-]+$"


class MensagemHistorico(BaseModel):
    """Fala anterior da conversa (só texto do cliente e respostas finais; nada de tools nem notas internas)."""

    papel: Literal["user", "assistant"]
    texto: str


class SistemaWebhook(BaseModel):
    """System prompt CAG em dois blocos (o mesmo do motor nativo): manual + persona, e idioma/data."""

    cacheavel: str
    dinamico: str


class ToolsWebhook(BaseModel):
    """Como o workflow chama as tools: `POST {url_base}/{nome}` com o token e o tenant no cabeçalho."""

    url_base: str
    token: str
    """JWT de `atendente` só deste tenant, válido pelo deadline do turno mais uma margem."""
    cabecalho_tenant: str = "X-Tenant-ID"


class PedidoWebhook(BaseModel):
    """Corpo enviado ao webhook."""

    versao: int = VERSAO_CONTRATO
    tenant_id: str
    sessao_id: str
    mensagem: str
    idioma: str
    historico: list[MensagemHistorico]
    sistema: SistemaWebhook
    tools: ToolsWebhook
    prazo_s: float
    """Tempo que resta do deadline do tenant; o workflow pode usá-lo como timeout dos próprios nós."""


class ChamadaToolWebhook(BaseModel):
    """Uma tool executada pelo workflow (no n8n: `intermediateSteps` do AI Agent)."""

    model_config = ConfigDict(extra="ignore")

    nome: Annotated[str, Field(min_length=1, max_length=64, pattern=_NOME_TOOL)]
    argumentos: dict[str, object] = Field(default_factory=dict)
    resultado: str | None = None

    @field_validator("argumentos", mode="before")
    @classmethod
    def _argumentos(cls, valor: object) -> object:
        """O n8n pode mandar os argumentos como texto JSON (`toolInput`)."""
        if isinstance(valor, str):
            try:
                carregado = json.loads(valor) if valor.strip() else {}
            except ValueError:
                return {"entrada": valor}
            return carregado if isinstance(carregado, dict) else {"entrada": carregado}
        return {} if valor is None else valor

    @field_validator("resultado", mode="before")
    @classmethod
    def _resultado(cls, valor: object) -> object:
        """Resultado em JSON (objeto) vira texto, como o LLM o recebeu."""
        if valor is None or isinstance(valor, str):
            return valor
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


class RespostaWebhook(BaseModel):
    """Corpo esperado do webhook. Campo desconhecido é ignorado; `texto` vazio é resposta malformada."""

    model_config = ConfigDict(extra="ignore")

    texto: Annotated[str, Field(min_length=1)]
    tools_chamadas: list[ChamadaToolWebhook] = Field(default_factory=list)
    nivel: Annotated[str, Field(max_length=32)] | None = None
    provedor: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("texto", mode="before")
    @classmethod
    def _texto(cls, valor: object) -> object:
        return valor.strip() if isinstance(valor, str) else valor

    @field_validator("tools_chamadas", mode="before")
    @classmethod
    def _tools(cls, valor: object) -> object:
        """Aceita também só os nomes (`["status_loja"]`): sem resultado, nenhum valor dela fica permitido."""
        if valor is None:
            return []
        if isinstance(valor, list):
            return [{"nome": v} if isinstance(v, str) else v for v in valor]
        return valor

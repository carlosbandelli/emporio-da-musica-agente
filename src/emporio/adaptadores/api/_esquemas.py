"""Corpo de entrada e de saída do `POST /v1/chat` (Pydantic v2)."""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from emporio.portas import CodigoResposta, Idioma, RespostaAtendimento

TAMANHO_MAX_CORPO: Final = 20_000
"""Teto de caracteres aceitos na borda (proteção de memória). O limite de negócio é `tamanho_max_mensagem` do tenant,
aplicado pelo motor com mensagem fixa; vazio também passa para o motor (I1)."""


class PedidoChat(BaseModel):
    """Corpo de `POST /v1/chat`."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        min_length=1, max_length=128, description="Identificador da conversa, escolhido pelo canal."
    )
    mensagem: str = Field(max_length=TAMANHO_MAX_CORPO, description="Texto do cliente.")
    idioma: Idioma | None = Field(
        default=None, description="Idioma do canal (pt-BR ou en-US). Vence o `Accept-Language`; opcional."
    )


class RespostaChat(BaseModel):
    """Resposta de `POST /v1/chat`: os campos de `RespostaAtendimento` mais `persona`."""

    texto: str
    idioma: Idioma
    nivel: str | None = None
    provedor: str | None = None
    tools_chamadas: list[str] = Field(default_factory=list)
    degradada: bool = False
    codigo: CodigoResposta | None = None
    retry_after_s: float | None = None
    persona: str | None = Field(default=None, description="Nome da atendente do tenant (para o canal exibir).")

    @classmethod
    def de(cls, resposta: RespostaAtendimento, persona: str | None) -> "RespostaChat":
        """Converte a resposta do motor."""
        return cls(
            texto=resposta.texto,
            idioma=resposta.idioma,
            nivel=resposta.nivel,
            provedor=resposta.provedor,
            tools_chamadas=list(resposta.tools_chamadas),
            degradada=resposta.degradada,
            codigo=resposta.codigo,
            retry_after_s=resposta.retry_after_s,
            persona=persona,
        )


class CorpoErro(BaseModel):
    """Corpo dos erros de borda (400, 404, 422)."""

    codigo: str
    mensagem: str

"""Corpos de entrada e de saída da API (`POST /v1/chat` e endpoints admin), em Pydantic v2."""

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from emporio.portas import (
    CodigoResposta,
    ConfigTenant,
    Idioma,
    RespostaAtendimento,
    Solicitacao,
    StatusSolicitacao,
    TipoSolicitacao,
)
from emporio.portas.tenant import Limites, Persona

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


# --- admin (RBAC, task 2.5) ------------------------------------------------------------------------------------------
class SolicitacaoAdmin(BaseModel):
    """Uma solicitação vista pela equipe. Sem `chave_idempotencia` (detalhe interno)."""

    id: str
    tipo: TipoSolicitacao
    descricao: str
    sessao_id: str
    criada_em: datetime
    contato: str | None = None
    status: StatusSolicitacao
    atualizada_em: datetime | None = None
    atualizada_por: str | None = None

    @classmethod
    def de(cls, s: Solicitacao) -> "SolicitacaoAdmin":
        """Converte a entidade."""
        return cls(
            id=s.id,
            tipo=s.tipo,
            descricao=s.descricao,
            sessao_id=s.sessao_id,
            criada_em=s.criada_em,
            contato=s.contato,
            status=s.status,
            atualizada_em=s.atualizada_em,
            atualizada_por=s.atualizada_por,
        )


class ListaSolicitacoes(BaseModel):
    """Corpo de `GET /v1/admin/solicitacoes`."""

    solicitacoes: list[SolicitacaoAdmin]


class MudancaStatus(BaseModel):
    """Corpo de `PATCH /v1/admin/solicitacoes/{id}`."""

    model_config = ConfigDict(extra="forbid")

    status: StatusSolicitacao


class ConfigEditavel(BaseModel):
    """Os campos editáveis da configuração do tenant (resposta de `GET`/`PUT /v1/admin/config`)."""

    persona: Persona
    termos_fora_de_escopo: dict[str, list[str]]
    limites: Limites
    deadline_mensagem_s: float

    @classmethod
    def de(cls, config: ConfigTenant) -> "ConfigEditavel":
        """Extrai os campos editáveis (com os nomes de idioma `pt-BR`/`en-US`)."""
        termos = config.termos_fora_de_escopo.model_dump(by_alias=True)
        return cls(
            persona=config.persona,
            termos_fora_de_escopo={k: list(v) for k, v in termos.items()},
            limites=config.limites,
            deadline_mensagem_s=config.deadline_mensagem_s,
        )

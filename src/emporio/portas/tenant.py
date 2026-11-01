"""Modelo da configuração de um tenant (`config/tenants.yaml`).

Fica em `portas/` porque é um tipo compartilhado: o composition root e os adapters o recebem pronto, sem conhecer o
YAML. Só o carregador (`emporio.configuracao`) sabe ler o arquivo.
"""

from collections.abc import Callable
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emporio.portas.contexto import Idioma, TenantContext


class _Modelo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Persona(_Modelo):
    """Persona do assistente (arquitetura §9.2)."""

    nome: str
    resumo: str


class Contatos(_Modelo):
    """Contatos oficiais da loja (premissas #1 e #2)."""

    telefone: str
    whatsapp: str
    email: str


class LimiteBalde(_Modelo):
    """Token bucket de uma camada: `capacidade` tokens, repostos a `reposicao_por_s` tokens por segundo."""

    capacidade: int = Field(gt=0)
    reposicao_por_s: float = Field(gt=0)


class Limites(_Modelo):
    """Limites do tenant (arquitetura §5.1: configuráveis por tenant)."""

    sessao: LimiteBalde
    usuario: LimiteBalde
    tenant: LimiteBalde
    provedor: LimiteBalde
    tamanho_max_mensagem: int = Field(gt=0)


class TermosForaDeEscopo(_Modelo):
    """Termos de itens que a loja não vende, por idioma (acessórios)."""

    pt_br: tuple[str, ...] = Field(alias="pt-BR")
    en_us: tuple[str, ...] = Field(alias="en-US")


class ConfigTenant(_Modelo):
    """Configuração completa de um tenant."""

    nome: str
    schema_db: str = Field(alias="schema")
    idioma_padrao: Idioma
    idiomas_suportados: tuple[Idioma, ...] = Field(min_length=1)
    persona: Persona
    contatos: Contatos
    politicas_arquivo: str
    dados_dir: str
    deadline_mensagem_s: float = Field(gt=0)
    limites: Limites
    termos_fora_de_escopo: TermosForaDeEscopo


ResolvedorConfig = Callable[[str], ConfigTenant]
"""Dado o `tenant_id`, devolve a `ConfigTenant`. Injetado nos casos de uso (o composition root fornece)."""


# --- configuração editável (PUT /v1/admin/config) ---------------------------------------------------------------------
NOME_PERSONA_MAX: Final = 60
RESUMO_PERSONA_MAX: Final = 2000
TERMO_MAX: Final = 60
TERMOS_MAX: Final = 300
DEADLINE_MAX_S: Final = 120.0


class AlteracaoConfig(_Modelo):
    """Campos que o `gerente` pode mudar. Atualização parcial: campo omitido mantém o valor atual.

    Ficam de fora, de propósito, os campos que tocam isolamento ou o sistema de arquivos (`schema`, `dados_dir`,
    `politicas_arquivo`), contatos oficiais e idiomas: mudar esses exige deploy (revisão humana).
    """

    persona: Persona | None = None
    termos_fora_de_escopo: TermosForaDeEscopo | None = None
    limites: Limites | None = None
    deadline_mensagem_s: float | None = Field(default=None, gt=0, le=DEADLINE_MAX_S)

    @model_validator(mode="after")
    def _validar(self) -> "AlteracaoConfig":
        if not any(getattr(self, campo) is not None for campo in type(self).model_fields):
            raise ValueError("informe ao menos um campo")
        if self.persona is not None:
            nome, resumo = self.persona.nome.strip(), self.persona.resumo.strip()
            if not nome or len(nome) > NOME_PERSONA_MAX:
                raise ValueError(f"persona.nome deve ter de 1 a {NOME_PERSONA_MAX} caracteres")
            if not resumo or len(resumo) > RESUMO_PERSONA_MAX:
                raise ValueError(f"persona.resumo deve ter de 1 a {RESUMO_PERSONA_MAX} caracteres")
        if self.termos_fora_de_escopo is not None:
            for lista in (self.termos_fora_de_escopo.pt_br, self.termos_fora_de_escopo.en_us):
                if len(lista) > TERMOS_MAX or any(not t.strip() or len(t) > TERMO_MAX for t in lista):
                    raise ValueError(f"até {TERMOS_MAX} termos por idioma, cada um com 1 a {TERMO_MAX} caracteres")
        return self


def aplicar_alteracao(atual: ConfigTenant, alteracao: AlteracaoConfig) -> ConfigTenant:
    """Nova `ConfigTenant` com a alteração aplicada e **revalidada** por inteiro (função pura)."""
    dados: dict[str, Any] = atual.model_dump(by_alias=True)
    dados.update(alteracao.model_dump(by_alias=True, exclude_none=True))
    return ConfigTenant.model_validate(dados)


class ConfigTenants(Protocol):
    """Leitura e edição da configuração do tenant (protótipo: em memória do processo)."""

    async def obter(self, ctx: TenantContext) -> ConfigTenant:
        """Configuração vigente do tenant do contexto."""
        ...

    async def atualizar(self, ctx: TenantContext, alteracao: AlteracaoConfig) -> ConfigTenant:
        """Aplica a alteração validada e devolve a configuração nova."""
        ...

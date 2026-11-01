"""Modelo da configuração de um tenant (`config/tenants.yaml`).

Fica em `portas/` porque é um tipo compartilhado: o composition root e os adapters o recebem pronto, sem conhecer o
YAML. Só o carregador (`emporio.configuracao`) sabe ler o arquivo.
"""

from pydantic import BaseModel, ConfigDict, Field

from emporio.portas.contexto import Idioma


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

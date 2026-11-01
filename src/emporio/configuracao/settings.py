"""Settings do processo (variáveis de ambiente e arquivo de ambiente local).

O arquivo de ambiente só é lido quando `Settings()` é instanciado, e isso acontece apenas no composition root.
Testes constroem `Settings(_env_file=None, ...)`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVEDORES_NATIVOS: tuple[str, ...] = ("claude", "gemini", "groq")
"""Provedores com adapter próprio (chaves `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`)."""

PRESETS_BASE_URL: Mapping[str, str | None] = MappingProxyType(
    {
        "openai": None,  # endpoint padrão do SDK (https://api.openai.com/v1)
        "deepseek": "https://api.deepseek.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "mistral": "https://api.mistral.ai/v1",
        "together": "https://api.together.xyz/v1",
        "ollama": "http://localhost:11434/v1",
    }
)
"""URL base por nome de `LLM_PROVEDOR` quando `LLM_BASE_URL` está vazio (`None` = padrão do SDK da OpenAI)."""

_RE_NOME_PROVEDOR = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class ConfigLLMCompativel:
    """Provedor compatível com a API da OpenAI já resolvido (sem a chave). `base_url=None` = padrão do SDK."""

    nome: str
    modelo: str
    base_url: str | None


class Settings(BaseSettings):
    """Configuração de ambiente. Chaves são `SecretStr`: `repr`/`str` nunca mostram o valor."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    google_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    motor_atendimento: Literal["nativo", "n8n"] = "nativo"
    data_referencia: datetime | None = None
    tenants_config: Path = Path("config/tenants.yaml")
    tenant_padrao: str = "emporio"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # Modelos por provedor (premissas propostas na task 1.8; validação com chave real nas tasks 1.14/1.16).
    modelo_claude: str = "claude-opus-5-5"
    esforco_claude: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    claude_fallback_servidor: bool = True
    modelo_gemini: str = "gemini-2.5-flash"
    modelo_groq: str = "openai/gpt-oss-120b"
    # `reasoning_effort` do Groq; só modelos que raciocinam aceitam (gpt-oss, qwen3). `None` = não envia.
    esforco_groq: Literal["low", "medium", "high"] | None = "low"
    # Fase 2 (docs/deploy.md). Ausentes = modo local (CSV e memória). URLs podem conter senha: SecretStr.
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    sentry_dsn: SecretStr | None = None
    otel_exporter_otlp_endpoint: str | None = None
    jwt_secret: SecretStr | None = None
    # Motor n8n (task 2.7, `MOTOR_ATENDIMENTO=n8n`; contrato em deploy/n8n/README.md). A URL do webhook pode levar
    # segredo no caminho: SecretStr. `n8n_webhook_segredo` vai no `Authorization: Bearer` (Header Auth do Webhook).
    # `n8n_url_api` é a base da API vista de dentro do n8n, para ele chamar `/v1/tools/*` de volta.
    n8n_webhook_url: SecretStr | None = None
    n8n_webhook_segredo: SecretStr | None = None
    n8n_url_api: str = "http://app:8000"
    # Provedor genérico compatível com a API da OpenAI (OpenAI, DeepSeek, OpenRouter, Mistral, Together, Ollama...).
    # `LLM_PROVEDOR` é um nome livre (vira o nome na cadeia e nas métricas); `LLM_BASE_URL` vazio usa o preset do
    # nome (`PRESETS_BASE_URL`). Ativo só com `LLM_API_KEY` e `LLM_MODELO`; Ollama local aceita qualquer chave.
    llm_provedor: str = "openai"
    llm_modelo: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None

    @field_validator("llm_provedor", mode="before")
    @classmethod
    def _validar_llm_provedor(cls, valor: object) -> object:
        if not isinstance(valor, str):
            return valor
        nome = valor.strip().lower() or "openai"
        if not _RE_NOME_PROVEDOR.fullmatch(nome):
            raise ValueError(f"'{valor}' é inválido: use só letras minúsculas, números, '_' e '-' (ex.: deepseek)")
        if nome in PROVEDORES_NATIVOS:
            raise ValueError(
                f"'{nome}' já é um provedor nativo (use a variável de chave dele) ou escolha outro nome "
                f"(ex.: {nome}-openai) e informe LLM_BASE_URL"
            )
        return nome

    @field_validator("llm_modelo", "llm_base_url", mode="before")
    @classmethod
    def _vazio_vira_none(cls, valor: object) -> object:
        if isinstance(valor, str):
            return valor.strip() or None
        return valor

    @field_validator("llm_base_url")
    @classmethod
    def _validar_llm_base_url(cls, valor: str | None) -> str | None:
        if valor is not None and not valor.startswith(("http://", "https://")):
            raise ValueError(f"'{valor}' deve começar com http:// ou https://")
        return valor

    @model_validator(mode="after")
    def _validar_llm_compativel(self) -> Self:
        if not _tem_valor(self.llm_api_key):
            return self
        if self.llm_modelo is None:
            raise ValueError("LLM_MODELO é obrigatório quando LLM_API_KEY está definida (ex.: gpt-4o-mini)")
        if self.llm_base_url is None and self.llm_provedor not in PRESETS_BASE_URL:
            conhecidos = ", ".join(PRESETS_BASE_URL)
            raise ValueError(
                f"LLM_PROVEDOR='{self.llm_provedor}' não tem URL padrão: defina LLM_BASE_URL "
                f"(nomes com URL pronta: {conhecidos})"
            )
        return self

    def llm_compativel(self) -> ConfigLLMCompativel | None:
        """Provedor compatível com OpenAI configurado (`LLM_API_KEY` e `LLM_MODELO`), ou `None`. Sem a chave."""
        if not _tem_valor(self.llm_api_key) or self.llm_modelo is None:
            return None
        base_url = self.llm_base_url or PRESETS_BASE_URL.get(self.llm_provedor)
        return ConfigLLMCompativel(self.llm_provedor, self.llm_modelo, base_url)

    def provedores_com_chave(self) -> tuple[str, ...]:
        """Nomes dos provedores de LLM com chave configurada (nunca os valores); o compatível com OpenAI por último."""
        candidatos = (
            ("claude", self.anthropic_api_key),
            ("gemini", self.google_api_key),
            ("groq", self.groq_api_key),
        )
        nativos = tuple(nome for nome, chave in candidatos if _tem_valor(chave))
        compativel = self.llm_compativel()
        return (*nativos, compativel.nome) if compativel is not None else nativos


def _tem_valor(chave: SecretStr | None) -> bool:
    return chave is not None and bool(chave.get_secret_value().strip())

"""Settings do processo (variáveis de ambiente e arquivo de ambiente local).

O arquivo de ambiente só é lido quando `Settings()` é instanciado, e isso acontece apenas no composition root.
Testes constroem `Settings(_env_file=None, ...)`.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    def provedores_com_chave(self) -> tuple[str, ...]:
        """Nomes dos provedores de LLM com chave configurada (nunca os valores)."""
        candidatos = (
            ("claude", self.anthropic_api_key),
            ("gemini", self.google_api_key),
            ("groq", self.groq_api_key),
        )
        return tuple(nome for nome, chave in candidatos if chave is not None and chave.get_secret_value().strip())

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

    def provedores_com_chave(self) -> tuple[str, ...]:
        """Nomes dos provedores de LLM com chave configurada (nunca os valores)."""
        candidatos = (
            ("claude", self.anthropic_api_key),
            ("gemini", self.google_api_key),
            ("groq", self.groq_api_key),
        )
        return tuple(nome for nome, chave in candidatos if chave is not None and chave.get_secret_value().strip())

"""Acesso ao Postgres com `search_path` por tenant, timeout e retry leve (arquitetura §4 e §5.2).

Desenho:
- Engine **síncrono** (psycopg 3) executado com `asyncio.to_thread`: o psycopg assíncrono não roda no
  `ProactorEventLoop` do Windows, e o volume de consultas do atendimento não justifica o custo.
- Única porta para uma conexão: `AcessoTenant.ler` / `AcessoTenant.escrever`, que exigem o `TenantContext`. Eles
  resolvem o schema na configuração (nunca no texto do usuário) e fixam `search_path` com `set_config(..., true)`
  (escopo da transação: ao devolver a conexão ao pool, nada vaza para o próximo tenant). As tabelas são sempre usadas
  sem qualificação, então uma consulta sem tenant não encontra tabela alguma (o schema `public` fica vazio).
- Timeout de 2 s (conexão, consulta e espera no pool). Retry (2 tentativas, backoff exponencial com jitter, base 0,1 s,
  teto 0,5 s) só para leituras e para a abertura da conexão; escritas nunca são repetidas depois de iniciadas.
"""

import asyncio
import re
from collections.abc import Callable, Mapping
from typing import TypeVar

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from emporio.portas.contexto import TenantContext
from emporio.portas.tenant import ConfigTenant

T = TypeVar("T")

TIMEOUT_S = 2
TENTATIVAS = 2
BACKOFF_BASE_S = 0.1
BACKOFF_TETO_S = 0.5
_SCHEMA_VALIDO = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class ErroTenantDesconhecido(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """O tenant do contexto não existe na configuração do adapter."""


class ErroConfiguracaoBanco(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """`DATABASE_URL` ausente ou schema de tenant inválido."""


def schemas_dos_tenants(tenants: Mapping[str, ConfigTenant]) -> dict[str, str]:
    """`tenant_id` para schema, validando o nome (identificador simples em minúsculas)."""
    resultado: dict[str, str] = {}
    for tenant_id, config in tenants.items():
        if not _SCHEMA_VALIDO.match(config.schema_db):
            raise ErroConfiguracaoBanco(f"Tenant '{tenant_id}': nome de schema inválido (use [a-z_][a-z0-9_]*).")
        resultado[tenant_id] = config.schema_db
    return resultado


def url_do_banco(url: SecretStr | None) -> str:
    """Valor da URL, exigindo `postgresql+psycopg://` (nunca mostrado em mensagens)."""
    valor = url.get_secret_value().strip() if url is not None else ""
    if not valor:
        raise ErroConfiguracaoBanco(
            "DATABASE_URL não definida (ex.: postgresql+psycopg://usuario:senha@host:5432/banco)."
        )
    if not valor.startswith("postgresql+psycopg://"):
        raise ErroConfiguracaoBanco("DATABASE_URL deve começar com 'postgresql+psycopg://'.")
    return valor


def criar_engine(url: str, *, com_timeout: bool = True) -> Engine:
    """Engine do pool de atendimento (com timeout) ou de migração (`com_timeout=False`, conexões não reaproveitadas)."""
    if not com_timeout:
        from sqlalchemy.pool import NullPool  # noqa: PLC0415 - só a migração usa

        return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})
    return create_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_timeout=TIMEOUT_S,
        pool_pre_ping=True,
        connect_args={"connect_timeout": TIMEOUT_S, "options": f"-c statement_timeout={TIMEOUT_S * 1000}"},
    )


class AcessoTenant:
    """Executa funções síncronas numa transação com o `search_path` do tenant do contexto."""

    def __init__(self, engine: Engine, tenants: Mapping[str, ConfigTenant]) -> None:
        self._engine = engine
        self._schemas = schemas_dos_tenants(tenants)

    def schema(self, ctx: TenantContext) -> str:
        """Schema do tenant; levanta `ErroTenantDesconhecido` se não existe."""
        schema = self._schemas.get(ctx.tenant_id)
        if schema is None:
            disponiveis = ", ".join(sorted(self._schemas)) or "(nenhum)"
            raise ErroTenantDesconhecido(f"Tenant '{ctx.tenant_id}' desconhecido. Disponíveis: {disponiveis}")
        return schema

    async def ler(self, ctx: TenantContext, funcao: Callable[[Connection], T]) -> T:
        """Leitura: a transação inteira é repetida em erro operacional (conexão, timeout)."""
        schema = self.schema(ctx)
        return await asyncio.to_thread(self._rodar, schema, funcao, True)

    async def escrever(self, ctx: TenantContext, funcao: Callable[[Connection], T]) -> T:
        """Escrita: só a abertura da conexão é repetida; erro depois disso sobe para o chamador."""
        schema = self.schema(ctx)
        return await asyncio.to_thread(self._rodar, schema, funcao, False)

    def descartar(self) -> None:
        """Fecha o pool."""
        self._engine.dispose()

    @staticmethod
    def _tentar(retry_leitura: bool) -> Retrying:
        return Retrying(
            stop=stop_after_attempt(TENTATIVAS if retry_leitura else 1),
            wait=wait_random_exponential(multiplier=BACKOFF_BASE_S, max=BACKOFF_TETO_S),
            retry=retry_if_exception_type(OperationalError),
            reraise=True,
        )

    def _transacao(self, schema: str, funcao: Callable[[Connection], T], conexao: Connection) -> T:
        with conexao.begin():
            conexao.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
            return funcao(conexao)

    def _rodar(self, schema: str, funcao: Callable[[Connection], T], leitura: bool) -> T:
        if leitura:
            for tentativa in self._tentar(True):
                with tentativa, self._engine.connect() as conexao:
                    return self._transacao(schema, funcao, conexao)
            raise AssertionError("inalcançável")  # pragma: no cover
        for tentativa in self._tentar(True):  # abrir a conexão pode ser repetido
            with tentativa:
                conexao = self._engine.connect()
        with conexao:
            return self._transacao(schema, funcao, conexao)

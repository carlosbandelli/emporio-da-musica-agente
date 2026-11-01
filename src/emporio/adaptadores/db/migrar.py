"""Migração e seed em loop por schema de tenant: `python -m emporio.adaptadores.db.migrar`.

- Lê `DATABASE_URL` (Settings) e `config/tenants.yaml`; nunca imprime a URL.
- Um **advisory lock** do Postgres (sessão, chave fixa) serializa os processos: o segundo espera o primeiro terminar,
  encontra tudo na revisão `head` e não aplica nada (compose, Job do Kubernetes e CI podem rodar em paralelo).
- Para cada tenant: `CREATE SCHEMA IF NOT EXISTS`, `alembic upgrade head` (tabela `alembic_version` dentro do schema) e
  seed idempotente dos CSVs de `dados_dir`.
- Códigos de saída: 0 ok; 2 configuração inválida (sem `DATABASE_URL`, YAML); 1 falha de banco.
"""

import logging
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from emporio.adaptadores.db import seed
from emporio.adaptadores.db._acesso import (
    ErroConfiguracaoBanco,
    criar_engine,
    schemas_dos_tenants,
    url_do_banco,
)
from emporio.configuracao import ErroConfiguracao, Settings, carregar_tenants
from emporio.portas.tenant import ConfigTenant

log = logging.getLogger(__name__)

CHAVE_LOCK = 7_300_511_283_906_001  # bigint fixo e arbitrário: "emporio.migracoes"
TIMEOUT_LOCK_S = 300.0
_SCRIPTS = Path(__file__).resolve().parent / "migracoes"


class ErroMigracao(Exception):  # noqa: N818 - nome em pt-BR do projeto
    """Não foi possível obter o lock ou migrar (mensagem sem credenciais)."""


@dataclass(frozen=True, slots=True)
class ResultadoTenant:
    """O que `migrar` fez num tenant."""

    tenant_id: str
    schema: str
    revisao_anterior: str | None
    revisao_atual: str | None
    seed: seed.ResultadoSeed

    @property
    def migrou(self) -> bool:
        """Alguma revisão foi aplicada por esta execução."""
        return self.revisao_anterior != self.revisao_atual


def _config_alembic(conexao: Connection, schema: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPTS).replace("%", "%%"))
    cfg.attributes["connection"] = conexao
    cfg.attributes["schema"] = schema
    return cfg


def _revisao(conexao: Connection, schema: str) -> str | None:
    return MigrationContext.configure(conexao, opts={"version_table_schema": schema}).get_current_revision()


def revisao_head() -> str | None:
    """Revisão mais recente dos scripts empacotados."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPTS).replace("%", "%%"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _fixar_schema(conexao: Connection, schema: str) -> None:
    conexao.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})


def _migrar_tenant(
    engine: Engine, tenant_id: str, config: ConfigTenant, schema: str, raiz: Path, *, com_seed: bool
) -> ResultadoTenant:
    quoted = engine.dialect.identifier_preparer.quote_schema(schema)
    with engine.connect() as conexao:
        conexao.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))
        _fixar_schema(conexao, schema)
        antes = _revisao(conexao, schema)
        command.upgrade(_config_alembic(conexao, schema), "head")
        depois = _revisao(conexao, schema)
        resultado_seed = seed.ResultadoSeed(0, ignorado=True)
        if com_seed:
            resultado_seed = seed.semear(conexao, raiz / config.dados_dir)
        conexao.commit()
    return ResultadoTenant(tenant_id, schema, antes, depois, resultado_seed)


def _adquirir_lock(conexao: Connection, timeout_s: float) -> None:
    limite = time.monotonic() + timeout_s
    while not conexao.execute(text("SELECT pg_try_advisory_lock(:chave)"), {"chave": CHAVE_LOCK}).scalar_one():
        if time.monotonic() > limite:
            raise ErroMigracao(f"Tempo esgotado ({timeout_s:.0f} s) esperando outra migração em andamento.")
        time.sleep(0.2)


def migrar(
    url: SecretStr | str,
    tenants: Mapping[str, ConfigTenant],
    raiz: Path | None = None,
    *,
    com_seed: bool = True,
    timeout_lock_s: float = TIMEOUT_LOCK_S,
) -> tuple[ResultadoTenant, ...]:
    """Migra (e semeia) todos os tenants sob o advisory lock; idempotente e seguro com processos concorrentes."""
    valor = url_do_banco(url if isinstance(url, SecretStr) else SecretStr(url))
    schemas = schemas_dos_tenants(tenants)
    base = raiz if raiz is not None else Path.cwd()
    engine = criar_engine(valor, com_timeout=False)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as trava:
            _adquirir_lock(trava, timeout_lock_s)
            try:
                return tuple(
                    _migrar_tenant(engine, tid, cfg, schemas[tid], base, com_seed=com_seed)
                    for tid, cfg in tenants.items()
                )
            finally:
                trava.execute(text("SELECT pg_advisory_unlock(:chave)"), {"chave": CHAVE_LOCK})
    finally:
        engine.dispose()


def _descrever(r: ResultadoTenant) -> str:
    estado = f"migrado {r.revisao_anterior or '-'} -> {r.revisao_atual}" if r.migrou else f"em dia ({r.revisao_atual})"
    dados = "sem dados para semear" if r.seed.ignorado else f"seed: {r.seed.inseridas} linhas novas"
    return f"tenant {r.tenant_id} (schema {r.schema}): {estado}; {dados}"


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada de `python -m emporio.adaptadores.db.migrar`."""
    del argv
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    raiz = Path.cwd()
    try:
        settings = Settings()
        tenants = carregar_tenants(raiz / settings.tenants_config)
        url_do_banco(settings.database_url)
    except (ErroConfiguracao, ErroConfiguracaoBanco, ValueError) as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2
    try:
        resultados = migrar(settings.database_url or "", tenants, raiz)
    except (ErroMigracao, SQLAlchemyError) as exc:
        # SQLAlchemyError pode embutir o SQL mas não a URL; ainda assim mostramos só o tipo e a primeira linha.
        primeira = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        print(f"Falha na migração ({type(exc).__name__}): {primeira}", file=sys.stderr)
        return 1
    for r in resultados:
        print(_descrever(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())

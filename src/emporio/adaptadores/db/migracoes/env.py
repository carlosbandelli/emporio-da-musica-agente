"""Ambiente Alembic: usa a conexão aberta por `migrar` (com `search_path` do tenant) e a versão por schema."""

from alembic import context
from sqlalchemy import Connection


def _executar(conexao: Connection, schema: str) -> None:
    context.configure(
        connection=conexao,
        target_metadata=None,
        version_table_schema=schema,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


conexao = context.config.attributes.get("connection")
schema = context.config.attributes.get("schema")
if conexao is None or schema is None:
    raise RuntimeError("Alembic só roda via `python -m emporio.adaptadores.db.migrar` (sem conexão/schema informados).")
_executar(conexao, schema)

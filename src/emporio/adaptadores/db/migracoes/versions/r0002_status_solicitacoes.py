"""Status das solicitações e trilha mínima de auditoria (task 2.5: `PATCH /v1/admin/solicitacoes/{id}`).

Aditiva: linhas existentes ficam `aberta`. Roda no schema corrente (o `search_path` do tenant, definido por `migrar`).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Acrescenta `status`, `atualizada_em`, `atualizada_por` e um índice por status."""
    op.execute(
        "ALTER TABLE solicitacoes "
        "ADD COLUMN status text NOT NULL DEFAULT 'aberta' "
        "CONSTRAINT ck_solicitacoes_status CHECK (status IN ('aberta', 'em_atendimento', 'resolvida')), "
        "ADD COLUMN atualizada_em timestamptz, "
        "ADD COLUMN atualizada_por text"
    )
    op.execute("CREATE INDEX ix_solicitacoes_status ON solicitacoes (status)")


def downgrade() -> None:
    """Remove as colunas (o índice cai junto)."""
    op.execute(
        "ALTER TABLE solicitacoes DROP COLUMN IF EXISTS atualizada_por, "
        "DROP COLUMN IF EXISTS atualizada_em, DROP COLUMN IF EXISTS status"
    )

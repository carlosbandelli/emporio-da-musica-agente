"""Esquema inicial de um tenant: catálogo, promoções, clientes, pedidos e solicitações.

As tabelas são criadas sem qualificar o schema: o `search_path` da conexão (definido por `migrar`) aponta para o
schema do tenant. Valores em centavos (inteiros); `texto_busca` guarda nome + descrição sem acento/caixa.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TABELAS = (
    """
    CREATE TABLE categorias (
        id integer PRIMARY KEY,
        nome text NOT NULL,
        descricao text NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE produtos (
        id integer PRIMARY KEY,
        nome text NOT NULL,
        categoria_id integer NOT NULL,
        descricao text NOT NULL DEFAULT '',
        preco_centavos bigint NOT NULL CHECK (preco_centavos >= 0),
        estoque integer NOT NULL CHECK (estoque >= 0),
        status text NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
        specs jsonb NOT NULL DEFAULT '{}'::jsonb,
        criado_em timestamp,
        texto_busca text NOT NULL
    )
    """,
    "CREATE INDEX ix_produtos_categoria ON produtos (categoria_id)",
    "CREATE INDEX ix_produtos_preco ON produtos (preco_centavos)",
    """
    CREATE TABLE promocoes (
        id integer PRIMARY KEY,
        produto_id integer NOT NULL,
        desconto_percentual smallint NOT NULL CHECK (desconto_percentual BETWEEN 1 AND 100),
        descricao text NOT NULL DEFAULT '',
        ativa boolean NOT NULL
    )
    """,
    "CREATE INDEX ix_promocoes_produto ON promocoes (produto_id)",
    """
    CREATE TABLE clientes (
        id integer PRIMARY KEY,
        nome text NOT NULL,
        telefone text NOT NULL DEFAULT '',
        email text NOT NULL DEFAULT '',
        cidade text NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE pedidos (
        numero integer PRIMARY KEY,
        cliente_id integer NOT NULL,
        status text NOT NULL,
        pagamento_forma text NOT NULL,
        pagamento_parcelas smallint NOT NULL DEFAULT 1,
        total_centavos bigint,
        data_pedido date,
        codigo_rastreio text,
        previsao_entrega date,
        observacoes text NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE itens_pedido (
        pedido_numero integer NOT NULL REFERENCES pedidos (numero) ON DELETE CASCADE,
        seq smallint NOT NULL,
        produto_id integer NOT NULL,
        quantidade integer NOT NULL CHECK (quantidade > 0),
        PRIMARY KEY (pedido_numero, seq)
    )
    """,
    """
    CREATE TABLE solicitacoes (
        ordem bigint GENERATED ALWAYS AS IDENTITY,
        id text PRIMARY KEY,
        tipo text NOT NULL,
        descricao text NOT NULL,
        sessao_id text NOT NULL,
        chave_idempotencia text NOT NULL UNIQUE,
        criada_em timestamptz NOT NULL,
        contato text
    )
    """,
)


def upgrade() -> None:
    """Cria as tabelas no schema corrente."""
    for ddl in _TABELAS:
        op.execute(ddl)


def downgrade() -> None:
    """Remove as tabelas (ordem inversa das dependências)."""
    for tabela in ("solicitacoes", "itens_pedido", "pedidos", "clientes", "promocoes", "produtos", "categorias"):
        op.execute(f"DROP TABLE IF EXISTS {tabela}")

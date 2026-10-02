"""rls por household

Revision ID: b3c1f0a7d2e4
Revises: 9808f27666c4
Create Date: 2026-10-02 15:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c1f0a7d2e4"
down_revision: str | Sequence[str] | None = "9808f27666c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mesmo nome de `app.db.session.APP_ROLE`. A migration não importa o app de propósito:
# o histórico não pode mudar se a constante mudar.
APP_ROLE = "slate_app"

# Tabela -> coluna que identifica a household da linha.
TENANT_TABLES = {
    "household": "id",
    "member": "household_id",
    "external_holder": "household_id",
    "account": "household_id",
    "category": "household_id",
    "commitment": "household_id",
    "entry": "household_id",
}

COMMANDS = ("select", "insert", "update", "delete")

CREATE_ROLE = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$
"""

# Household do JWT que a API setta em `request.jwt.claims` (app/db/session.py). Sem
# claims, NULL: nenhuma linha passa nas policies.
CURRENT_HOUSEHOLD_FUNCTION = """
CREATE FUNCTION current_household_id() RETURNS uuid
LANGUAGE sql STABLE
AS $$
    SELECT (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb
            ->> 'household_id')::uuid
$$
"""


def _policy_sql(table: str, column: str, command: str) -> str:
    # `(SELECT ...)` vira initplan: a função roda uma vez por query, não por linha.
    predicate = f"{column} = (SELECT current_household_id())"
    clauses = {
        "select": f"USING ({predicate})",
        "insert": f"WITH CHECK ({predicate})",
        "update": f"USING ({predicate}) WITH CHECK ({predicate})",
        "delete": f"USING ({predicate})",
    }
    return (
        f"CREATE POLICY {table}_{command}_own_household ON {table} "
        f"FOR {command.upper()} TO {APP_ROLE} {clauses[command]}"
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CREATE_ROLE)
    # A API conecta como dono das tabelas e faz `SET LOCAL ROLE` para a role da RLS.
    op.execute(f"GRANT {APP_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    op.execute(CURRENT_HOUSEHOLD_FUNCTION)
    # Fora do PUBLIC para não virar RPC na Data API do Supabase.
    op.execute("REVOKE EXECUTE ON FUNCTION current_household_id() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION current_household_id() TO {APP_ROLE}")

    for table, column in TENANT_TABLES.items():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for command in COMMANDS:
            op.execute(_policy_sql(table, column, command))

    # Sem policy nem grant: no Supabase as tabelas de `public` ficam expostas a anon e
    # authenticated pela Data API, e RLS sem policy fecha esse acesso. O dono (Alembic)
    # continua lendo.
    op.execute("ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY")
    for table in reversed(TENANT_TABLES):
        for command in COMMANDS:
            op.execute(f"DROP POLICY {table}_{command}_own_household ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE}")
    op.execute("DROP FUNCTION current_household_id()")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"DROP ROLE {APP_ROLE}")

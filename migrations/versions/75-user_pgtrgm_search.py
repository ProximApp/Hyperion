"""empty message

Create Date: 2026-10-05 03:15:59.094451
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d31d25f0133"
down_revision: str | None = "bdd48a894b7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable trigram search and accent-insensitive search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
    # Immutable wrapper around unaccent, required to use it in an index expression
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text AS $$
        SELECT public.unaccent('public.unaccent', $1);
        $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;
        """,
    )

    # Must be strictly identical to the expression used in the model
    expr_str = "f_unaccent(firstname || ' ' || name || ' ' || COALESCE(nickname, ''))"
    op.create_index(
        "ix_user_full_name_trgm",
        "core_user",
        [sa.literal_column(expr_str)],
        postgresql_using="gin",
        postgresql_ops={expr_str: "gin_trgm_ops"},
    )


def downgrade() -> None:
    # Fixed table name: it was "core_users" instead of "core_user"
    op.drop_index("ix_user_full_name_trgm", table_name="core_user")
    op.execute("DROP FUNCTION IF EXISTS f_unaccent(text);")


def pre_test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    pass


def test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    pass

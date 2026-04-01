"""empty message

Create Date: 2026-03-01 11:41:22.994301
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "146db8dcb23e"
down_revision: str | None = "de94c373f94a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    store_table = sa.table(
        "mypayment_store",
        sa.column("id", sa.String),
    )

    conn = op.get_bind()
    stores = conn.execute(
        sa.select(store_table.c.id),
    ).fetchall()

    if len(stores) > 0:
        raise Exception(  # noqa: TRY002, TRY003
            "There are already stores in database, we cannot safely migrate to add association_id to store",
        )

    op.add_column(
        "mypayment_store",
        sa.Column(
            "association_id",
            sa.Uuid(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        None,
        "mypayment_store",
        "associations_associations",
        ["association_id"],
        ["id"],
    )
    op.create_unique_constraint(None, "mypayment_store", ["association_id"])


def downgrade() -> None:
    op.drop_constraint(
        "mypayment_store_association_id_fkey",
        "mypayment_store",
        type_="foreignkey",
    )
    op.drop_column("mypayment_store", "association_id")


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

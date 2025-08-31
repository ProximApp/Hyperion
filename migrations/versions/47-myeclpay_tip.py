"""empty message

Create Date: 2025-08-31 16:21:26.108997
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e39b96af2ca0"
down_revision: str | None = "63ed74985b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checkout_checkout_payment",
        sa.Column("tip_amount", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "checkout_checkout_payment",
        "tip_amount",
    )


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

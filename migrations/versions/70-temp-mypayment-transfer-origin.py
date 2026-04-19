"""add transfer origin

Create Date: 2026-04-18 00:00:00.000000
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d2aa4e6f1b"
down_revision: str | None = "de94c373f94a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class TransferOrigin(StrEnum):
    HELLO_ASSO = "hello_asso"


wallet_id = uuid4()
transfer_id = uuid4()


def upgrade() -> None:
    op.alter_column(
        "mypayment_transfer",
        "type",
        new_column_name="origin",
    )
    op.execute("ALTER TYPE transfertype RENAME TO transferorigin")


def downgrade() -> None:
    op.execute("ALTER TYPE transferorigin RENAME TO transfertype")
    op.alter_column(
        "mypayment_transfer",
        "origin",
        new_column_name="type",
    )


def pre_test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    alembic_runner.insert_into(
        "mypayment_wallet",
        {
            "id": wallet_id,
            "type": "USER",
            "balance": 0,
        },
    )
    alembic_runner.insert_into(
        "mypayment_transfer",
        {
            "id": transfer_id,
            "type": "HELLO_ASSO",
            "transfer_identifier": "test-transfer-identifier",
            "approver_user_id": None,
            "wallet_id": wallet_id,
            "total": 100,
            "creation": "2026-04-18T00:00:00+00:00",
            "confirmed": False,
            "module": None,
            "object_id": None,
        },
    )


def test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    has_old_column = alembic_connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_name = 'mypayment_transfer' AND column_name = 'type'
            """,
        ),
    ).scalar_one()
    assert has_old_column == 0

    has_new_column = alembic_connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_name = 'mypayment_transfer' AND column_name = 'origin'
            """,
        ),
    ).scalar_one()
    assert has_new_column == 1

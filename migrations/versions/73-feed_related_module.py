"""empty message

Create Date: 2026-09-09 11:27:48.371396
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dafecdb477c8"
down_revision: str | None = "af6920fed071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feed_news",
        sa.Column("news_related_module_root", sa.String(), nullable=True),
    )
    op.add_column(
        "feed_news",
        sa.Column("news_related_module_object_id", sa.Uuid(), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE feed_news
            SET
                news_related_module_root = module,
                news_related_module_object_id = module_object_id
            """,
        ),
    )

    op.alter_column(
        "feed_news",
        "news_related_module_root",
        existing_type=sa.String(),
        nullable=False,
    )
    op.alter_column(
        "feed_news",
        "news_related_module_object_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("feed_news", "news_related_module_root")
    op.drop_column("feed_news", "news_related_module_object_id")


news_id = str(uuid.uuid4())
module_object_id = str(uuid.uuid4())
image_id = str(uuid.uuid4())


def pre_test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    alembic_runner.insert_into(
        "feed_news",
        [
            {
                "id": news_id,
                "title": "Test news",
                "start": datetime.now(UTC),
                "entity": "calendar",
                "module": "calendar",
                "module_object_id": module_object_id,
                "image_directory": "test",
                "image_id": image_id,
                "status": "PUBLISHED",
            },
        ],
    )


def test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    result = alembic_connection.execute(
        sa.text(
            """
            SELECT
                news_related_module_root,
                news_related_module_object_id
            FROM feed_news
            WHERE id = :news_id
            """,
        ),
        {"news_id": news_id},
    ).one()

    assert result.news_related_module_root == "calendar"
    assert str(result.news_related_module_object_id) == module_object_id

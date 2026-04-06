"""empty message

Create Date: 2026-03-27 23:23:07.797594
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

from app.types.sqlalchemy import TZDateTime

# revision identifiers, used by Alembic.
revision: str = "c052cfbe6d75"
down_revision: str | None = "146db8dcb23e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickets_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("open_datetime", TZDateTime(), nullable=False),
        sa.Column("close_datetime", TZDateTime(), nullable=True),
        sa.Column("quota", sa.Integer(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["mypayment_store.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tickets_category",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("quota", sa.Integer(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("required_membership", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["tickets_event.id"],
        ),
        sa.ForeignKeyConstraint(
            ["required_membership"],
            ["core_association_membership.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tickets_question",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column(
            "answer_type",
            sa.Enum("TEXT", "NUMBER", "BOOLEAN", name="answertype"),
            nullable=False,
        ),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["tickets_event.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tickets_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("start_datetime", TZDateTime(), nullable=False),
        sa.Column("quota", sa.Integer(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["tickets_event.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tickets_checkout",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("expiration", TZDateTime(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False),
        sa.Column("scanned", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["tickets_category.id"],
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["tickets_event.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tickets_session.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tickets_answer",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("checkout_id", sa.Uuid(), nullable=False),
        sa.Column("answer", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["checkout_id"],
            ["tickets_checkout.id"],
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["tickets_question.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "calendar_events",
        sa.Column("ticket_event_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "calendar_events_ticket_event_id_fkey",
        "calendar_events",
        "tickets_event",
        ["ticket_event_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "calendar_events_ticket_event_id_fkey",
        "calendar_events",
        type_="foreignkey",
    )
    op.drop_table("tickets_answer")
    op.drop_table("tickets_question")
    op.drop_table("tickets_checkout")
    op.drop_table("tickets_session")
    op.drop_table("tickets_category")
    op.drop_table("tickets_event")
    sa.Enum("TEXT", "NUMBER", "BOOLEAN", name="answertype").drop(op.get_bind())


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

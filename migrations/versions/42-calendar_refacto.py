"""empty message

Create Date: 2025-08-15 19:56:01.211562
"""

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.core.groups.groups_type import GroupType
from app.types.sqlalchemy import TZDateTime

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f56c14eefc4c"
down_revision: str | None = "ca44192be52b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


associations_table = sa.Table(
    "associations_associations",
    sa.MetaData(),
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("group_id", sa.String(), nullable=False),
    sa.ForeignKeyConstraint(
        ["group_id"],
        ["core_group.id"],
    ),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("name"),
)


def upgrade() -> None:
    """
    In this migration we will attribute all existing calendar events to the BDE association.
    """

    # Select existing core_associations
    conn = op.get_bind()
    bde_association = conn.execute(
        sa.select(
            associations_table,
        ).where(
            associations_table.c.name == "BDE",
        ),
    ).fetchone()

    if bde_association is None:
        bde_id = uuid.uuid4()
        bde_db = {
            "id": bde_id,
            "name": "BDE",
            "group_id": GroupType.BDE.value,
        }
        conn.execute(associations_table.insert().values(bde_db))
    else:
        bde_id = bde_association.id

    op.create_table(
        "calendar_ical_secret",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("secret", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_user.id"],
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.drop_column("calendar_events", "type")
    sa.Enum(
        "eventAE",
        "eventUSE",
        "independentAssociation",
        "happyHour",
        "direction",
        "nightParty",
        "other",
        name="calendareventtype",
    ).drop(op.get_bind())

    op.alter_column(
        "calendar_events",
        "id",
        existing_type=sa.VARCHAR(),
        type_=sa.Uuid(),
        existing_nullable=False,
        postgresql_using="id::uuid",
    )
    op.drop_index("ix_calendar_events_id", table_name="calendar_events")

    op.add_column(
        "calendar_events",
        sa.Column("association_id", sa.Uuid(), nullable=True),
    )
    conn.execute(
        sa.text("""
            UPDATE calendar_events
            SET association_id = :default_value
        """),
        {"default_value": bde_id},
    )
    op.alter_column("calendar_events", "association_id", nullable=False)
    op.create_foreign_key(
        None,
        "calendar_events",
        "associations_associations",
        ["association_id"],
        ["id"],
    )

    op.alter_column(
        "calendar_events",
        "decision",
        existing_type=sa.VARCHAR(),
        type_=sa.Enum("approved", "declined", "pending", name="decision"),
        existing_nullable=False,
        postgresql_using="decision::decision",
    )

    op.drop_column("calendar_events", "organizer")

    op.add_column(
        "calendar_events",
        sa.Column("ticket_url", sa.String(), nullable=True),
    )
    op.add_column(
        "calendar_events",
        sa.Column("ticket_url_opening", TZDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_events", "ticket_url")
    op.drop_column("calendar_events", "ticket_url_opening")

    op.add_column(
        "calendar_events",
        sa.Column("organizer", sa.String(), nullable=False),
    )

    op.alter_column(
        "calendar_events",
        "decision",
        type_=sa.VARCHAR(),
    )

    op.drop_column("calendar_events", "association_id")

    op.create_index("ix_calendar_events_id", "calendar_events", ["id"], unique=False)
    op.alter_column(
        "calendar_events",
        "id",
        type_=sa.String(),
    )

    calendareventtype = sa.Enum(
        "eventAE",
        "eventUSE",
        "independentAssociation",
        "happyHour",
        "direction",
        "nightParty",
        "other",
        name="calendareventtype",
    )
    calendareventtype.create(op.get_bind())
    op.add_column(
        "calendar_events",
        sa.Column(
            "type",
            calendareventtype,
            nullable=False,
        ),
    )

    op.drop_table("calendar_ical_secret")


def pre_test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    user_id = str(uuid.uuid4())
    school_id = str(uuid.uuid4())

    alembic_runner.insert_into(
        "core_school",
        {
            "id": school_id,
            "name": "Test School",
            "email_regex": ".*@example\\.com",
        },
    )

    alembic_runner.insert_into(
        "core_user",
        {
            "id": user_id,
            "email": "test@example.com",
            "school_id": school_id,
            "password_hash": "hashed_password",
            "account_type": "student",
            "name": "Test User",
            "firstname": "Test",
            "nickname": "Tester",
            "birthday": "2000-01-01",
            "promo": 2023,
            "phone": "1234567890",
            "floor": None,
            "created_on": "2025-08-15 19:56:01.211562",
        },
    )

    alembic_runner.insert_into(
        "calendar_events",
        {
            "id": uuid.uuid4(),
            "name": "test",
            "organizer": "test_organizer",
            "applicant_id": user_id,
            "start": "2025-08-15 19:56:01.211562",
            "end": "2025-08-15 19:56:01.211562",
            "all_day": False,
            "location": "test_location",
            "type": "eventAE",
            "description": "test_description",
            "decision": "approved",
            "recurrence_rule": None,
        },
    )


def test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    pass

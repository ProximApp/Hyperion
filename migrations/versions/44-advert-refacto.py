"""empty message

Create Date: 2025-08-16 17:33:13.993854
"""

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.core.groups.groups_type import GroupType

if TYPE_CHECKING:
    from pytest_alembic import MigrationContext

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "44216abab3e1"
down_revision: str | None = "234caa383828"
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

advertiser_table = sa.Table(
    "advert_advertisers",
    sa.MetaData(),
    sa.Column("id", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("group_manager_id", sa.String(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("name"),
)


def upgrade() -> None:
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

    op.drop_constraint(
        "advert_adverts_advertiser_id_fkey",
        "advert_adverts",
        type_="foreignkey",
    )

    conn.execute(
        sa.text("""
            UPDATE advert_adverts
            SET advertiser_id = :default_value
        """),
        {"default_value": bde_id},
    )

    op.alter_column(
        "advert_adverts",
        "advertiser_id",
        existing_type=sa.VARCHAR(),
        type_=sa.Uuid(),
        existing_nullable=False,
        postgresql_using="advertiser_id::uuid",
    )

    op.create_foreign_key(
        None,
        "advert_adverts",
        "associations_associations",
        ["advertiser_id"],
        ["id"],
    )

    op.alter_column(
        "advert_adverts",
        "id",
        existing_type=sa.VARCHAR(),
        type_=sa.Uuid(),
        existing_nullable=False,
        postgresql_using="id::uuid",
    )

    op.drop_index("ix_advert_advertisers_id", table_name="advert_advertisers")
    op.drop_table("advert_advertisers")


def downgrade() -> None:
    op.create_table(
        "advert_advertisers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("group_manager_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        op.f("ix_advert_advertisers_id"),
        "advert_advertisers",
        ["id"],
        unique=False,
    )

    op.drop_constraint(
        "advert_adverts_advertiser_id_fkey",
        "advert_adverts",
        type_="foreignkey",
    )

    op.alter_column(
        "advert_adverts",
        "advertiser_id",
        type_=sa.String(),
    )

    advertiser_id = str(uuid.uuid4())
    bde_advertiser = {
        "id": advertiser_id,
        "name": "BDE",
        "group_manager_id": GroupType.BDE.value,
    }
    conn = op.get_bind()
    conn.execute(advertiser_table.insert().values(bde_advertiser))
    conn.execute(
        sa.text("""
            UPDATE advert_adverts
            SET advertiser_id = :default_value
        """),
        {"default_value": advertiser_id},
    )

    op.create_foreign_key(
        None,
        "advert_adverts",
        "advert_advertisers",
        ["advertiser_id"],
        ["id"],
    )


def pre_test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    advertiser_id = str(uuid.uuid4())
    alembic_runner.insert_into(
        "advert_advertisers",
        {
            "id": advertiser_id,
            "name": "test",
            "group_manager_id": GroupType.BDE.value,
        },
    )
    alembic_runner.insert_into(
        "advert_adverts",
        {
            "id": uuid.uuid4(),
            "advertiser_id": advertiser_id,
            "title": "test_title",
            "content": "test_content",
            "date": "2025-08-15 19:56:01.211562",
        },
    )


def test_upgrade(
    alembic_runner: "MigrationContext",
    alembic_connection: sa.Connection,
) -> None:
    pass

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.advert import models_advert, schemas_advert


async def get_adverts(db: AsyncSession) -> Sequence[models_advert.Advert]:
    result = await db.execute(select(models_advert.Advert))
    return result.scalars().all()


async def get_advert_by_id(
    db: AsyncSession,
    advert_id: UUID,
) -> models_advert.Advert | None:
    result = await db.execute(
        select(models_advert.Advert).where(models_advert.Advert.id == advert_id),
    )
    return result.scalars().first()


async def get_adverts_by_advertisers(
    db: AsyncSession,
    advertisers: list[UUID],
) -> Sequence[models_advert.Advert]:
    result = await db.execute(
        select(models_advert.Advert).where(
            models_advert.Advert.advertiser_id.in_(advertisers),
        ),
    )
    return result.scalars().all()


async def create_advert(
    db_advert: models_advert.Advert,
    db: AsyncSession,
) -> None:
    db.add(db_advert)


async def update_advert(
    advert_id: UUID,
    advert_update: schemas_advert.AdvertUpdate,
    db: AsyncSession,
):
    await db.execute(
        update(models_advert.Advert)
        .where(models_advert.Advert.id == advert_id)
        .values(
            **advert_update.model_dump(exclude_unset=True),
        ),
    )


async def delete_advert(
    advert_id: UUID,
    db: AsyncSession,
):
    await db.execute(
        delete(models_advert.Advert).where(
            models_advert.Advert.id == advert_id,
        ),
    )
    await db.flush()

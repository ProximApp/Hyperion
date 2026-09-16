from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feed import models_feed, schemas_feed
from app.core.feed.types_feed import NewsStatus, OrderBy


async def create_news(
    news: models_feed.News,
    db: AsyncSession,
) -> None:
    """
    Create a news
    """

    db.add(news)


async def get_news(
    status: list[NewsStatus],
    db: AsyncSession,
    limit: int,
    offset: int = 0,
    order: OrderBy = OrderBy.ASC,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
) -> Sequence[models_feed.News]:
    sign = "" if order == OrderBy.ASC else "desc"
    result = await db.execute(
        select(models_feed.News)
        .where(
            models_feed.News.status.in_(status),
            # Inclusive date-window filters: `start >= start_after` if given,
            # and `start <= start_before` if given. Combined with the
            # deterministic (start, end, id) ordering, they let clients fetch
            # the pages before/after a given date (e.g. "around today")
            # without an extra count or search query. Both bounds are
            # inclusive so that paging from the previous page's last item
            # never skips events sharing its start date; clients dedupe the
            # re-included boundary item by id.
            models_feed.News.start >= start_after if start_after else true(),
            models_feed.News.start <= start_before if start_before else true(),
        )
        .order_by(
            getattr(models_feed.News.start, f"{sign}")(),
            getattr(models_feed.News.end, f"{sign}")(),
            getattr(models_feed.News.id, f"{sign}")(),
        )
        .offset(offset)
        .limit(limit),
    )
    return result.scalars().all()


async def get_all_news(
    db: AsyncSession,
) -> Sequence[models_feed.News]:
    result = await db.execute(select(models_feed.News))
    return result.scalars().all()


async def get_news_by_id(
    news_id: UUID,
    db: AsyncSession,
) -> models_feed.News | None:
    result = await db.execute(
        select(models_feed.News).where(
            models_feed.News.id == news_id,
        ),
    )
    return result.scalars().first()


async def update_news_module_and_object_id_by_id(
    news_id: UUID,
    new_module: str,
    new_module_object_id: UUID,
    db: AsyncSession,
) -> None:
    """
    Change the module and module_object_id of a news in the feed
    """
    await db.execute(
        update(models_feed.News)
        .where(
            models_feed.News.id == news_id,
        )
        .values(
            module=new_module,
            module_object_id=new_module_object_id,
        ),
    )


async def change_news_status_by_id(
    news_id: UUID,
    status: NewsStatus,
    db: AsyncSession,
) -> None:
    await db.execute(
        update(models_feed.News)
        .where(
            models_feed.News.id == news_id,
        )
        .values(status=status),
    )


async def delete_news_by_id(
    news_id: UUID,
    db: AsyncSession,
) -> None:
    await db.execute(
        delete(models_feed.News).where(
            models_feed.News.id == news_id,
        ),
    )


async def edit_news_by_id(
    news_id: UUID,
    news_edit: schemas_feed.NewsEdit,
    db: AsyncSession,
) -> None:
    await db.execute(
        update(models_feed.News)
        .where(models_feed.News.id == news_id)
        .values(**news_edit.model_dump(exclude_unset=True)),
    )


async def get_news_by_news_related_module_root_and_news_related_module_object_id(
    news_related_module_root: str,
    news_related_module_object_id: UUID,
    db: AsyncSession,
) -> models_feed.News | None:
    result = await db.execute(
        select(models_feed.News).where(
            models_feed.News.news_related_module_root == news_related_module_root,
            models_feed.News.news_related_module_object_id
            == news_related_module_object_id,
        ),
    )
    return result.scalars().first()


async def get_news_by_module_and_module_object_id(
    module: str,
    module_object_id: UUID,
    db: AsyncSession,
) -> models_feed.News | None:
    result = await db.execute(
        select(models_feed.News).where(
            models_feed.News.module == module,
            models_feed.News.module_object_id == module_object_id,
        ),
    )
    return result.scalars().first()

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feed import cruds_feed, schemas_feed
from app.core.feed.permissions_feed import FeedPermissions
from app.core.feed.types_feed import NewsStatus, OrderBy
from app.core.users import models_users
from app.dependencies import (
    get_db,
    is_user_allowed_to,
)
from app.types.module import CoreModule
from app.types.upload import FILE_RESPONSE
from app.utils.tools import get_file_from_data

router = APIRouter(tags=["Feed"])


core_module = CoreModule(
    root="feed",
    tag="Feed",
    router=router,
    factory=None,
    permissions=FeedPermissions,
)

hyperion_error_logger = logging.getLogger("hyperion.error")


@router.get(
    "/feed/news",
    response_model=list[schemas_feed.News],
    status_code=200,
)
async def get_published_news(
    limit: int = Query(default=50, gt=0, le=200),
    offset: int = Query(default=0, ge=0),
    order: OrderBy = Query(
        default=OrderBy.ASC,
        description="Sort order on (start, end, id): ascending (oldest first) or descending (newest first)",
    ),
    start_after: datetime | None = Query(
        # TODO: temporary default to a 5 days before today restriction
        default=datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=5),
        description="Only return news starting at or after this datetime (inclusive window filters for paging before/after a date)",
    ),
    start_before: datetime | None = Query(
        default=None,
        description="Only return news starting at or before this datetime (inclusive window filters for paging before/after a date)",
    ),
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(
        is_user_allowed_to([FeedPermissions.access_feed]),
    ),
):
    """
    Return published news from the feed, paginated.

    The results are ordered on (start, end, id) — ascending by default, or
    newest first with `order=desc` — so that consecutive pages are stable and
    can be fetched with limit/offset.

    The optional `start_after` / `start_before` bounds window the query on the
    news start date (`start >= start_after`, `start <= start_before`), letting
    clients page backwards and forwards around a given datetime (e.g. "today")
    with `limit`/`offset`.
    """

    return await cruds_feed.get_news(
        status=[NewsStatus.PUBLISHED],
        db=db,
        limit=limit,
        offset=offset,
        order=order,
        start_after=start_after,
        start_before=start_before,
    )


@router.get(
    "/feed/news/{news_id}/image",
    response_class=FileResponse,
    responses=FILE_RESPONSE,
    status_code=200,
)
async def get_news_image(
    news_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(
        is_user_allowed_to([FeedPermissions.access_feed]),
    ),
):
    """
    Return the image of a news
    """

    news = await cruds_feed.get_news_by_id(news_id=news_id, db=db)
    if news is None:
        raise HTTPException(
            status_code=404,
            detail="The news does not exist",
        )

    return await get_file_from_data(
        directory=news.image_directory,
        filename=news.image_id,
        raise_http_exception=True,
    )


@router.get(
    "/feed/admin/news",
    response_model=list[schemas_feed.News],
    status_code=200,
)
async def get_admin_news(
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(
        is_user_allowed_to([FeedPermissions.manage_feed]),
    ),
):
    """
    Return news from the feed

    **This endpoint is only usable by feed administrators**
    """

    return await cruds_feed.get_all_news(db=db)


@router.post(
    "/feed/admin/news/{news_id}/approve",
    status_code=204,
)
async def approve_news(
    news_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(
        is_user_allowed_to([FeedPermissions.manage_feed]),
    ),
):
    """
    Approve a news

    **This endpoint is only usable by feed administrators**
    """

    return await cruds_feed.change_news_status_by_id(
        news_id=news_id,
        status=NewsStatus.PUBLISHED,
        db=db,
    )


@router.post(
    "/feed/admin/news/{news_id}/reject",
    status_code=204,
)
async def reject_news(
    news_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(
        is_user_allowed_to([FeedPermissions.manage_feed]),
    ),
):
    """
    Reject a news

    **This endpoint is only usable by feed administrators**
    """

    await cruds_feed.change_news_status_by_id(
        news_id=news_id,
        status=NewsStatus.REJECTED,
        db=db,
    )

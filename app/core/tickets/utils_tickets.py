from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tickets import cruds_tickets


async def is_event_sold_out(
    event_id: UUID,
    quota: int | None,
    db: AsyncSession,
) -> bool:
    if quota is None:
        return False

    nb_valid_checkout_for_event = await cruds_tickets.count_valid_checkouts_by_event_id(
        event_id=event_id,
        db=db,
    )

    nb_tickets_sold_for_event = await cruds_tickets.count_tickets_by_event_id(
        event_id=event_id,
        db=db,
    )

    tickets = await cruds_tickets.get_tickets_by_event_id(
        event_id=event_id,
        db=db,
    )

    return (nb_valid_checkout_for_event + nb_tickets_sold_for_event) >= quota


async def is_category_sold_out(
    category_id: UUID,
    quota: int | None,
    db: AsyncSession,
) -> bool:
    if quota is None:
        return False

    nb_valid_checkout_for_category = (
        await cruds_tickets.count_valid_checkouts_by_category_id(
            category_id=category_id,
            db=db,
        )
    )

    nb_tickets_sold_for_category = await cruds_tickets.count_tickets_by_category_id(
        category_id=category_id,
        db=db,
    )

    return (nb_valid_checkout_for_category + nb_tickets_sold_for_category) >= quota


async def is_session_sold_out(
    session_id: UUID,
    quota: int | None,
    db: AsyncSession,
) -> bool:
    if quota is None:
        return False

    nb_valid_checkout_for_session = (
        await cruds_tickets.count_valid_checkouts_by_session_id(
            session_id=session_id,
            db=db,
        )
    )

    nb_tickets_sold_for_session = await cruds_tickets.count_tickets_by_session_id(
        session_id=session_id,
        db=db,
    )

    return (nb_valid_checkout_for_session + nb_tickets_sold_for_session) >= quota

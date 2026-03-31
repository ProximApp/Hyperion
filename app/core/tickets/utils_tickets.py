from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tickets import cruds_tickets, schemas_tickets


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


async def convert_to_event_admin(
    event: schemas_tickets.EventComplete,
    db: AsyncSession,
):
    return schemas_tickets.EventAdmin(
        id=event.id,
        name=event.name,
        store_id=event.store_id,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        sessions=[
            schemas_tickets.SessionAdmin(
                id=session.id,
                event_id=session.event_id,
                name=session.name,
                start_datetime=session.start_datetime,
                quota=session.quota,
                tickets_in_checkout=await cruds_tickets.count_valid_checkouts_by_event_id(
                    event_id=event.id,
                    db=db,
                ),
                tickets_sold=await cruds_tickets.count_tickets_by_event_id(
                    event_id=event.id,
                    db=db,
                ),
            )
            for session in event.sessions
        ],
        categories=[
            schemas_tickets.CategoryAdmin(
                id=category.id,
                event_id=category.event_id,
                name=category.name,
                price=category.price,
                required_membership=category.required_membership,
                quota=category.quota,
                tickets_in_checkout=await cruds_tickets.count_valid_checkouts_by_category_id(
                    category_id=category.id,
                    db=db,
                ),
                tickets_sold=await cruds_tickets.count_tickets_by_category_id(
                    category_id=category.id,
                    db=db,
                ),
            )
            for category in event.categories
        ],
        quota=event.quota,
        tickets_in_checkout=await cruds_tickets.count_valid_checkouts_by_event_id(
            event_id=event.id,
            db=db,
        ),
        tickets_sold=await cruds_tickets.count_tickets_by_event_id(
            event_id=event.id,
            db=db,
        ),
    )

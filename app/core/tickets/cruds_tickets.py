import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import select

from app.core.tickets import models_tickets, schemas_tickets


async def get_tickets_by_user_id(
    user_id: str,
    db: AsyncSession,
) -> Sequence[schemas_tickets.Ticket]:
    result = await db.execute(
        select(models_tickets.Ticket)
        .where(models_tickets.Ticket.user_id == user_id)
        .options(
            selectinload(models_tickets.Ticket.category),
            selectinload(models_tickets.Ticket.session),
        ),
    )
    return [
        schemas_tickets.Ticket(
            id=ticket.id,
            category_id=ticket.category_id,
            session_id=ticket.session_id,
            scanned=ticket.scanned,
            category=schemas_tickets.Category(
                id=ticket.category.id,
                name=ticket.category.name,
                price=ticket.category.price,
                required_membership=ticket.category.required_membership,
                event_id=ticket.category.event_id,
            ),
            session=schemas_tickets.Session(
                name=ticket.session.name,
                start_time=ticket.session.start_time,
                end_time=ticket.session.end_time,
                event_id=ticket.session.event_id,
            ),
            user_id=ticket.user_id,
            price=ticket.price,
        )
        for ticket in result.scalars().all()
    ]


async def get_tickets_by_event_id(
    event_id: UUID,
    db: AsyncSession,
) -> Sequence[schemas_tickets.Ticket]:
    result = await db.execute(
        select(models_tickets.Ticket)
        .where(models_tickets.Ticket.category.event_id == event_id)
        .options(
            selectinload(models_tickets.Ticket.category),
            selectinload(models_tickets.Ticket.session),
        ),
    )
    return [
        schemas_tickets.Ticket(
            id=ticket.id,
            category_id=ticket.category_id,
            session_id=ticket.session_id,
            scanned=ticket.scanned,
            category=schemas_tickets.Category(
                id=ticket.category.id,
                name=ticket.category.name,
                price=ticket.category.price,
                required_membership=ticket.category.required_membership,
                event_id=ticket.category.event_id,
            ),
            session=schemas_tickets.Session(
                name=ticket.session.name,
                start_time=ticket.session.start_time,
                end_time=ticket.session.end_time,
                event_id=ticket.session.event_id,
            ),
            user_id=ticket.user_id,
            price=ticket.price,
        )
        for ticket in result.scalars().all()
    ]


async def get_open_events(
    db: AsyncSession,
) -> Sequence[schemas_tickets.EventSimple]:
    """Return all open events from database"""

    time = datetime.now(UTC)

    result = await db.execute(
        select(models_tickets.TicketEvent).where(
            models_tickets.TicketEvent.open_datetime <= time,
            or_(
                models_tickets.TicketEvent.close_datetime.is_(None),
                models_tickets.TicketEvent.close_datetime > time,
            ),
        ),
    )
    return [
        schemas_tickets.EventSimple(
            id=association.id,
            name=association.name,
            store_id=association.store_id,
        )
        for association in result.scalars().all()
    ]


async def get_events_by_store_id(
    store_id: UUID,
    db: AsyncSession,
) -> Sequence[schemas_tickets.EventSimple]:
    """Return all open events from database"""

    result = await db.execute(
        select(models_tickets.TicketEvent).where(
            models_tickets.TicketEvent.store_id == store_id,
        ),
    )
    return [
        schemas_tickets.EventSimple(
            id=association.id,
            name=association.name,
            store_id=association.store_id,
        )
        for association in result.scalars().all()
    ]


async def get_event_by_id(
    event_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.EventAdmin | None:
    """Return one open event with public details from database"""

    result = await db.execute(
        select(models_tickets.TicketEvent)
        .where(
            models_tickets.TicketEvent.id == event_id,
        )
        .options(
            selectinload(models_tickets.TicketEvent.sessions),
            selectinload(models_tickets.TicketEvent.categories),
        ),
    )

    event = result.scalars().first()
    if event is None:
        return None

    return schemas_tickets.EventAdmin(
        id=event.id,
        name=event.name,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        quota=event.quota,
        quota_per_checkout=event.quota_per_checkout,
        quota_per_user=event.quota_per_user,
        store_id=event.store_id,
        sessions=[
            schemas_tickets.SessionComplete(
                id=session.id,
                name=session.name,
                start_time=session.start_time,
                end_time=session.end_time,
                event_id=session.event_id,
            )
            for session in sorted(event.sessions, key=lambda item: item.start_time)
        ],
        categories=[
            schemas_tickets.CategoryComplete(
                id=category.id,
                name=category.name,
                price=category.price,
                required_membership=category.required_membership,
                event_id=category.event_id,
            )
            for category in sorted(event.categories, key=lambda item: item.name)
        ],
    )


async def create_event(
    event_id: UUID,
    event: schemas_tickets.EventCreate,
    db: AsyncSession,
):
    db_event = models_tickets.TicketEvent(
        id=event_id,
        store_id=event.store_id,
        name=event.name,
        quota_per_user=event.quota_per_user,
        quota_per_checkout=event.quota_per_checkout,
        quota=event.quota,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        sessions=[
            models_tickets.Session(
                id=uuid.uuid4(),
                event_id=event_id,
                name=session.name,
                start_time=session.start_time,
                end_time=session.end_time,
                quota=session.quota,
            )
            for session in event.sessions
        ],
        categories=[
            models_tickets.Category(
                id=uuid.uuid4(),
                event_id=event_id,
                name=category.name,
                quota=category.quota,
                price=category.price,
                required_membership=category.required_membership,
            )
            for category in event.categories
        ],
    )
    db.add(db_event)


async def get_category_by_id(
    category_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.CategoryComplete | None:
    """Return one category from database"""

    result = await db.execute(
        select(models_tickets.Category).where(
            models_tickets.Category.id == category_id,
        ),
    )

    category = result.scalars().first()
    if category is None:
        return None

    return schemas_tickets.CategoryComplete(
        id=category.id,
        name=category.name,
        price=category.price,
        required_membership=category.required_membership,
        event_id=category.event_id,
    )


async def get_session_by_id(
    session_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.SessionComplete | None:
    """Return one session from database"""

    result = await db.execute(
        select(models_tickets.Session).where(
            models_tickets.Session.id == session_id,
        ),
    )

    session = result.scalars().first()
    if session is None:
        return None

    return schemas_tickets.SessionComplete(
        id=session.id,
        name=session.name,
        start_time=session.start_time,
        end_time=session.end_time,
        event_id=session.event_id,
    )


async def create_checkout(
    checkout_id: UUID,
    user_id: str,
    category_id: UUID,
    session_id: UUID,
    price: int,
    expiration: datetime,
    db: AsyncSession,
):
    db_checkout = models_tickets.Checkout(
        id=checkout_id,
        user_id=user_id,
        category_id=category_id,
        session_id=session_id,
        price=price,
        expiration=expiration,
    )
    db.add(db_checkout)

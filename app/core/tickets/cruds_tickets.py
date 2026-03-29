import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import select

from app.core.tickets import models_tickets, schemas_tickets
from app.core.users import schemas_users


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
            open_datetime=association.open_datetime,
            close_datetime=association.close_datetime,
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
            open_datetime=association.open_datetime,
            close_datetime=association.close_datetime,
        )
        for association in result.scalars().all()
    ]


async def get_event_by_id(
    event_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.EventComplete | None:
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

    return schemas_tickets.EventComplete(
        id=event.id,
        name=event.name,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        quota=event.quota,
        store_id=event.store_id,
        sessions=[
            schemas_tickets.SessionComplete(
                id=session.id,
                name=session.name,
                start_datetime=session.start_datetime,
                event_id=session.event_id,
                quota=session.quota,
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
                quota=category.quota,
            )
            for category in sorted(event.categories, key=lambda item: item.name)
        ],
    )


async def acquire_event_lock_for_update(
    event_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.EventWithoutSessionsAndCategories | None:
    """
    Acquire a lock FOR UPDATE on the event row.
    Until the end of the transaction, other:
    - update
    - delete
    - and select FOR UPDATE
    queries on the same row will be blocked until the lock is released.

    > FOR UPDATE causes the rows retrieved by the SELECT statement to be locked as though for update. This prevents them from being locked, modified or deleted by other transactions until the current transaction ends.

    By putting this lock on the beginning of an endpoint,
    we unsure that all endpoint trying to acquire the same lock
    will wait for the first lock to be released
    """
    result = await db.execute(
        select(models_tickets.TicketEvent)
        .where(
            models_tickets.TicketEvent.id == event_id,
        )
        .with_for_update(),
    )

    event = result.scalars().first()
    if event is None:
        return None

    return schemas_tickets.EventWithoutSessionsAndCategories(
        id=event.id,
        name=event.name,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        quota=event.quota,
        store_id=event.store_id,
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
        quota=event.quota,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        sessions=[
            models_tickets.EventSession(
                id=uuid.uuid4(),
                event_id=event_id,
                name=session.name,
                start_datetime=session.start_datetime,
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
        quota=category.quota,
    )


async def get_session_by_id(
    session_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.SessionComplete | None:
    """Return one session from database"""

    result = await db.execute(
        select(models_tickets.EventSession).where(
            models_tickets.EventSession.id == session_id,
        ),
    )

    session = result.scalars().first()
    if session is None:
        return None

    return schemas_tickets.SessionComplete(
        id=session.id,
        name=session.name,
        start_datetime=session.start_datetime,
        event_id=session.event_id,
        quota=session.quota,
    )


async def create_checkout(
    checkout_id: UUID,
    user_id: str,
    event_id: UUID,
    category_id: UUID,
    session_id: UUID,
    price: int,
    expiration: datetime,
    db: AsyncSession,
):
    db_checkout = models_tickets.Checkout(
        id=checkout_id,
        event_id=event_id,
        user_id=user_id,
        category_id=category_id,
        session_id=session_id,
        price=price,
        expiration=expiration,
    )
    db.add(db_checkout)


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
            event_id=ticket.event_id,
            scanned=ticket.scanned,
            category=schemas_tickets.Category(
                id=ticket.category.id,
                name=ticket.category.name,
                price=ticket.category.price,
                required_membership=ticket.category.required_membership,
                event_id=ticket.category.event_id,
            ),
            session=schemas_tickets.Session(
                id=ticket.session.id,
                name=ticket.session.name,
                start_datetime=ticket.session.start_datetime,
                event_id=ticket.session.event_id,
            ),
            user_id=ticket.user_id,
            user=schemas_users.CoreUserSimple(
                id=ticket.user.id,
                name=ticket.user.name,
                firstname=ticket.user.firstname,
                account_type=ticket.user.account_type,
                school_id=ticket.user.school_id,
            ),
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
            selectinload(models_tickets.Ticket.user),
        ),
    )
    return [
        schemas_tickets.Ticket(
            id=ticket.id,
            category_id=ticket.category_id,
            session_id=ticket.session_id,
            event_id=ticket.event_id,
            scanned=ticket.scanned,
            category=schemas_tickets.Category(
                id=ticket.category.id,
                name=ticket.category.name,
                price=ticket.category.price,
                required_membership=ticket.category.required_membership,
                event_id=ticket.category.event_id,
            ),
            session=schemas_tickets.Session(
                id=ticket.session.id,
                name=ticket.session.name,
                start_datetime=ticket.session.start_datetime,
                event_id=ticket.session.event_id,
            ),
            user_id=ticket.user_id,
            user=schemas_users.CoreUserSimple(
                id=ticket.user.id,
                name=ticket.user.name,
                firstname=ticket.user.firstname,
                account_type=ticket.user.account_type,
                school_id=ticket.user.school_id,
            ),
            price=ticket.price,
        )
        for ticket in result.scalars().all()
    ]


async def get_ticket_by_id(
    ticket_id: UUID,
    db: AsyncSession,
) -> schemas_tickets.Ticket | None:
    result = await db.execute(
        select(models_tickets.Ticket)
        .where(models_tickets.Ticket.id == ticket_id)
        .options(
            selectinload(models_tickets.Ticket.category),
            selectinload(models_tickets.Ticket.session),
        ),
    )
    ticket = result.scalars().first()
    if ticket is None:
        return None

    return schemas_tickets.Ticket(
        id=ticket.id,
        category_id=ticket.category_id,
        session_id=ticket.session_id,
        event_id=ticket.event_id,
        scanned=ticket.scanned,
        category=schemas_tickets.Category(
            id=ticket.category.id,
            name=ticket.category.name,
            price=ticket.category.price,
            required_membership=ticket.category.required_membership,
            event_id=ticket.category.event_id,
        ),
        session=schemas_tickets.Session(
            id=ticket.session.id,
            name=ticket.session.name,
            start_datetime=ticket.session.start_datetime,
            event_id=ticket.session.event_id,
        ),
        user_id=ticket.user_id,
        user=schemas_users.CoreUserSimple(
            id=ticket.user.id,
            name=ticket.user.name,
            firstname=ticket.user.firstname,
            account_type=ticket.user.account_type,
            school_id=ticket.user.school_id,
        ),
        price=ticket.price,
    )


async def mark_ticket_as_scanned(
    ticket_id: UUID,
    db: AsyncSession,
):
    await db.execute(
        update(models_tickets.Ticket)
        .where(models_tickets.Ticket.id == ticket_id)
        .values(scanned=True),
    )


async def count_tickets_by_event_id(
    event_id: UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count()).where(
            models_tickets.Ticket.event_id == event_id,
        ),
    )

    return result.scalar() or 0


async def count_tickets_by_category_id(
    category_id: UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count()).where(
            models_tickets.Ticket.category_id == category_id,
        ),
    )

    return result.scalar() or 0


async def count_tickets_by_session_id(
    session_id: UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count()).where(
            models_tickets.Ticket.session_id == session_id,
        ),
    )

    return result.scalar() or 0


async def count_valid_checkouts_by_event_id(
    event_id: UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count()).where(
            models_tickets.Checkout.event_id == event_id,
            models_tickets.Checkout.expiration >= datetime.now(UTC),
        ),
    )

    return result.scalar() or 0


async def count_valid_checkouts_by_category_id(
    category_id: UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count()).where(
            models_tickets.Checkout.category_id == category_id,
            models_tickets.Checkout.expiration >= datetime.now(UTC),
        ),
    )

    return result.scalar() or 0


async def count_valid_checkouts_by_session_id(
    session_id: UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count()).where(
            models_tickets.Checkout.session_id == session_id,
            models_tickets.Checkout.expiration >= datetime.now(UTC),
        ),
    )

    return result.scalar() or 0

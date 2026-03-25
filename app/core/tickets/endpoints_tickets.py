import csv
import logging
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mypayment import cruds_mypayment
from app.core.permissions.type_permissions import ModulePermissions
from app.core.tickets import cruds_tickets, schemas_tickets
from app.core.tickets.factory_tickets import TicketsFactory
from app.core.users.models_users import CoreUser
from app.dependencies import (
    get_db,
    is_user,
    is_user_allowed_to,
)
from app.types.module import CoreModule

router = APIRouter(tags=["Tickets"])

core_module = CoreModule(
    root="ticket",
    tag="Tickets",
    router=router,
    factory=TicketsFactory(),
)

CHECKOUT_EXPIRATION_MINUTES = 15

hyperion_error_logger = logging.getLogger("hyperion.error")
hyperion_security_logger = logging.getLogger("hyperion.security")
hyperion_mypayment_logger = logging.getLogger("hyperion.mypayment")


class TicketsPermissions(ModulePermissions):
    buy_tickets = "buy_tickets"


@router.get(
    "/tickets/events",
    response_model=list[schemas_tickets.EventSimple],
    status_code=200,
)
async def get_open_events(
    user: CoreUser = Depends(
        is_user_allowed_to(
            [TicketsPermissions.buy_tickets],
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all open events
    """
    return await cruds_tickets.get_open_events(db=db)


@router.get(
    "/tickets/events/{event_id}",
    response_model=schemas_tickets.EventPublic,
    status_code=200,
)
async def get_event(
    event_id: UUID,
    user: CoreUser = Depends(
        is_user_allowed_to(
            [TicketsPermissions.buy_tickets],
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get an event public details
    """
    event = await cruds_tickets.get_event_by_id(event_id=event_id, db=db)
    # TODO: indicate if the event is sold out
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


@router.post(
    "/tickets/events/{event_id}/checkout",
    response_model=schemas_tickets.CheckoutResponse,
    status_code=200,
)
async def create_checkout(
    event_id: UUID,
    checkout: schemas_tickets.Checkout,
    user: CoreUser = Depends(
        is_user_allowed_to(
            [TicketsPermissions.buy_tickets],
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a checkout for an open event
    """
    category = await cruds_tickets.get_category_by_id(
        category_id=checkout.category_id,
        db=db,
    )
    if category is None:
        raise HTTPException(404, "Category not found")
    session = await cruds_tickets.get_session_by_id(
        session_id=checkout.session_id,
        db=db,
    )
    if session is None:
        raise HTTPException(404, "Session not found")

    if category.event_id != event_id:
        raise HTTPException(400, "Category does not belong to the event")
    if session.event_id != event_id:
        raise HTTPException(400, "Session does not belong to the event")

    price = category.price
    expiration = datetime.now(UTC) + timedelta(minutes=CHECKOUT_EXPIRATION_MINUTES)

    # TODO: indicate if the event is sold out

    await cruds_tickets.create_checkout(
        checkout_id=uuid.uuid4(),
        user_id=user.id,
        category_id=checkout.category_id,
        session_id=checkout.session_id,
        expiration=expiration,
        price=price,
        db=db,
    )

    # TODO: return the payment id
    return schemas_tickets.CheckoutResponse(
        price=price,
        expiration=expiration,
    )


@router.get(
    "/tickets/user/me/tickets",
    response_model=list[schemas_tickets.Ticket],
    status_code=200,
)
async def get_user_tickets(
    event_id: UUID,
    user: CoreUser = Depends(
        is_user_allowed_to(
            [TicketsPermissions.buy_tickets],
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all tickets of the current user
    """
    return await cruds_tickets.get_tickets_by_user_id(
        user_id=user.id,
        db=db,
    )


@router.get(
    "/tickets/admin/events/{event_id}",
    response_model=schemas_tickets.EventAdmin,
    status_code=200,
)
async def get_event_admin(
    event_id: UUID,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get one event admin details

    **The user should have the right to manage the event seller**
    """
    event = await cruds_tickets.get_event_by_id(event_id=event_id, db=db)
    if event is None:
        raise HTTPException(404, "Event not found")

    # TODO: check if user has the right to manage the seller
    return event


@router.post(
    "/tickets/admin/events/",
    response_model=schemas_tickets.EventAdmin,
    status_code=201,
)
async def create_event(
    event_create: schemas_tickets.EventCreate,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Create an event

    **The user should have the right to manage the event seller**
    """
    # TODO: check if user has the right to manage the seller
    event_id = uuid.uuid4()

    await cruds_tickets.create_event(
        event_id=event_id,
        event=event_create,
        db=db,
    )

    return await cruds_tickets.get_event_by_id(
        event_id=event_id,
        db=db,
    )


# router.patch(
#     "/tickets/admin/events/{event_id}",
#     response_model=schemas_tickets.EventComplete,
#     status_code=204,
# )
# async def edit_event(
#     event_id: UUID,
#     event_edit: schemas_tickets.EventCreate,
#     user: CoreUser = Depends(
#         is_user(),
#     ),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Edit one event for admin
#     """
#     # TODO: an open event should not be editable
#     pass


@router.get(
    "/tickets/admin/events/{event_id}/tickets",
    response_model=list[schemas_tickets.Ticket],
    status_code=200,
)
async def get_event_tickets(
    event_id: UUID,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all tickets of an event

    **The user should have the right to manage the event seller**
    """
    # TODO: check if user has the right to manage the seller

    return await cruds_tickets.get_tickets_by_event_id(event_id=event_id, db=db)


@router.get(
    "/tickets/admin/events/{event_id}/tickets/csv",
    response_model=list[schemas_tickets.Ticket],
    status_code=200,
)
async def get_event_tickets_csv(
    event_id: UUID,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all tickets of an event as csv

    **The user should have the right to manage the event seller**
    """
    # TODO: check if user has the right to manage the seller
    event = await cruds_tickets.get_event_by_id(
        event_id=event_id,
        db=db,
    )
    if event is None:
        raise HTTPException(404, "Event not found")

    csv_io = StringIO()

    writer = csv.writer(csv_io, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Write headers
    writer.writerow(
        [
            "Ticket ID",
            "Session ID",
            "Session Name",
            "Category ID",
            "Category Name",
            "Price (€)",
            "Scanned",
        ],
    )

    tickets = await cruds_tickets.get_tickets_by_event_id(event_id=event_id, db=db)
    for ticket in tickets:
        writer.writerow(
            [
                ticket.id,
                ticket.session_id,
                ticket.session.name,
                ticket.category_id,
                ticket.category.name,
                f"{ticket.price / 100:.2f}€",
                ticket.scanned,
            ],
        )

    csv_content = csv_io.getvalue()
    csv_io.close()

    filename = f"event_{event_id}_{datetime.now(UTC)}.csv"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        csv_content,
        headers=headers,
        media_type="text/csv; charset=utf-8",
    )


@router.get(
    "/tickets/admin/association/{association_id}/events",
    response_model=list[schemas_tickets.EventSimple],
    status_code=200,
)
async def get_events_by_association(
    association_id: UUID,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all events of an association

    **The user should have the right to manage the event seller**
    """
    # TODO: check if user has the right to manage the association
    store = await cruds_mypayment.get_store_by_association_id(
        association_id=association_id,
        db=db,
    )
    # TODO: maybe return an empty list
    if store is None:
        raise HTTPException(400, "No seller associated with this association")

    return await cruds_tickets.get_events_by_store_id(
        store_id=store.id,
        db=db,
    )

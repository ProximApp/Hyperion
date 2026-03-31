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
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memberships import utils_memberships
from app.core.mypayment import cruds_mypayment, utils_mypayment
from app.core.permissions.type_permissions import ModulePermissions
from app.core.tickets import cruds_tickets, schemas_tickets, utils_tickets
from app.core.tickets.factory_tickets import TicketsFactory
from app.core.users.models_users import CoreUser
from app.dependencies import (
    get_db,
    is_user,
    is_user_allowed_to,
)
from app.types.exceptions import ObjectExpectedInDbNotFoundError
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

    if event is None:
        raise HTTPException(404, "Event not found")

    return schemas_tickets.EventPublic(
        id=event.id,
        name=event.name,
        store_id=event.store_id,
        sessions=[
            schemas_tickets.SessionPublic(
                event_id=session.event_id,
                id=session.id,
                name=session.name,
                start_datetime=session.start_datetime,
                sold_out=await utils_tickets.is_session_sold_out(
                    session_id=session.id,
                    quota=session.quota,
                    db=db,
                ),
            )
            for session in event.sessions
        ],
        categories=[
            schemas_tickets.CategoryPublic(
                event_id=category.event_id,
                id=category.id,
                name=category.name,
                price=category.price,
                required_membership=category.required_membership,
                sold_out=await utils_tickets.is_category_sold_out(
                    category_id=category.id,
                    quota=category.quota,
                    db=db,
                ),
            )
            for category in event.categories
        ],
        sold_out=await utils_tickets.is_event_sold_out(
            event_id=event.id,
            quota=event.quota,
            db=db,
        ),
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
    )


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

    if category.required_membership is not None:
        membership = await utils_memberships.get_user_active_membership_to_association_membership(
            association_membership_id=category.required_membership,
            user_id=user.id,
            db=db,
        )
        if membership is None:
            raise HTTPException(
                400,
                "User does not have required membership to choose this category",
            )

    # By putting this lock:
    # - we unsure that if an other endpoint execution acquired the lock before, this one will wait.
    # - we guarantee that any other endpoint execution that tries to acquire the lock will need to wait until the end of this transaction.
    event = await cruds_tickets.acquire_event_lock_for_update(
        event_id=event_id,
        db=db,
    )

    if event is None:
        raise ObjectExpectedInDbNotFoundError(
            object_name="Event",
            object_id=event_id,
        )

    price = category.price
    expiration = datetime.now(UTC) + timedelta(minutes=CHECKOUT_EXPIRATION_MINUTES)

    if utils_tickets.is_event_sold_out(
        event_id=event_id,
        quota=event.quota,
        db=db,
    ):
        raise HTTPException(400, "Event is sold out")
    if utils_tickets.is_category_sold_out(
        category_id=category.id,
        quota=category.quota,
        db=db,
    ):
        raise HTTPException(400, "Category is sold out")
    if utils_tickets.is_session_sold_out(
        session_id=session.id,
        quota=session.quota,
        db=db,
    ):
        raise HTTPException(400, "Session is sold out")

    await cruds_tickets.create_checkout(
        checkout_id=uuid.uuid4(),
        event_id=event_id,
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

    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=event.store_id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

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
                    event_id=event_id,
                    db=db,
                ),
                tickets_sold=await cruds_tickets.count_tickets_by_event_id(
                    event_id=event_id,
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
            event_id=event_id,
            db=db,
        ),
        tickets_sold=await cruds_tickets.count_tickets_by_event_id(
            event_id=event_id,
            db=db,
        ),
    )


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
    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=event_create.store_id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

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
    event = await cruds_tickets.get_event_by_id(event_id=event_id, db=db)
    if event is None:
        raise HTTPException(404, "Event not found")

    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=event.store_id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

    return await cruds_tickets.get_tickets_by_event_id(event_id=event_id, db=db)


@router.get(
    "/tickets/admin/events/{event_id}/tickets/csv",
    response_class=FileResponse,
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
    event = await cruds_tickets.get_event_by_id(event_id=event_id, db=db)
    if event is None:
        raise HTTPException(404, "Event not found")

    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=event.store_id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

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
            "User ID",
            "User Name",
            "User Firstname",
            "User Account Type",
            "User School ID",
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
                ticket.user_id,
                ticket.user.name,
                ticket.user.firstname,
                ticket.user.account_type,
                ticket.user.school_id,
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


@router.post(
    "/tickets/admin/tickets/{ticket_id}/check",
    response_model=schemas_tickets.Ticket,
    status_code=200,
)
async def check_ticket(
    ticket_id: UUID,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Check a ticket

    **The user should have the right to manage the event seller**
    """

    ticket = await cruds_tickets.get_ticket_by_id(ticket_id=ticket_id, db=db)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")

    event = await cruds_tickets.get_event_by_id(event_id=ticket.event_id, db=db)
    if event is None:
        raise ObjectExpectedInDbNotFoundError(
            object_name="Event",
            object_id=ticket.event_id,
        )

    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=event.store_id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

    return ticket


@router.post(
    "/tickets/admin/tickets/{ticket_id}/scan",
    status_code=204,
)
async def scan_ticket(
    ticket_id: UUID,
    user: CoreUser = Depends(
        is_user(),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a ticket as scanned

    **The user should have the right to manage the event seller**
    """

    ticket = await cruds_tickets.get_ticket_by_id(ticket_id=ticket_id, db=db)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")

    event = await cruds_tickets.get_event_by_id(event_id=ticket.event_id, db=db)
    if event is None:
        raise ObjectExpectedInDbNotFoundError(
            object_name="Event",
            object_id=ticket.event_id,
        )

    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=event.store_id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

    await cruds_tickets.mark_ticket_as_scanned(ticket_id=ticket_id, db=db)

    return ticket


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
    store = await cruds_mypayment.get_store_by_association_id(
        association_id=association_id,
        db=db,
    )
    # TODO: maybe return an empty list
    if store is None:
        raise HTTPException(400, "No seller associated with this association")

    if not await utils_mypayment.can_user_manage_events(
        user_id=user.id,
        store_id=store.id,
        db=db,
    ):
        raise HTTPException(
            status_code=403,
            detail="User is not authorized to manage store events",
        )

    return await cruds_tickets.get_events_by_store_id(
        store_id=store.id,
        db=db,
    )

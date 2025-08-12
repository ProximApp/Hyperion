import uuid

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.associations import cruds_associations
from app.core.groups.groups_type import AccountType, GroupType
from app.core.users import models_users
from app.core.utils.config import Settings
from app.core.utils.security import generate_token
from app.dependencies import (
    get_db,
    get_notification_tool,
    get_settings,
    is_user,
    is_user_a_school_member,
    is_user_in,
    is_user_in_association,
)
from app.modules.calendar import (
    cruds_calendar,
    models_calendar,
    schemas_calendar,
    utils_calendar,
)
from app.modules.calendar.factory_calendar import CalendarFactory
from app.modules.calendar.types_calendar import Decision
from app.types.exceptions import NewlyAddedObjectInDbNotFoundError
from app.types.module import Module
from app.utils.communication.notifications import NotificationTool
from app.utils.tools import (
    is_user_member_of_an_association,
    is_user_member_of_any_group,
)

module = Module(
    root=utils_calendar.root,
    tag="Calendar",
    default_allowed_account_types=[AccountType.student, AccountType.staff],
    factory=CalendarFactory(),
)


@module.router.get(
    "/calendar/events/",
    response_model=list[schemas_calendar.EventComplete],
    status_code=200,
)
async def get_events(
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_in(GroupType.BDE)),
):
    """Get all events from the database."""
    return await cruds_calendar.get_all_events(db=db)


@module.router.get(
    "/calendar/events/confirmed",
    response_model=list[schemas_calendar.EventComplete],
    status_code=200,
)
async def get_confirmed_events(
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_a_school_member),
):
    """
    Get all confirmed events.

    **Usable by every member**
    """
    return await cruds_calendar.get_confirmed_events(db=db)


@module.router.get(
    "/calendar/events/associations/{association_id}",
    response_model=list[schemas_calendar.EventComplete],
    status_code=200,
)
async def get_association_booking(
    association_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_in_association),
):
    """
    Get the booking of the association

    **Usable by members of the association**
    """

    return await cruds_calendar.get_events_by_association(
        db=db,
        association_id=association_id,
    )


@module.router.get(
    "/calendar/events/{event_id}",
    response_model=schemas_calendar.EventComplete,
    status_code=200,
)
async def get_event_by_id(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_a_school_member),
):
    """
    Get an event's information by its id.

    **Non approved events are only accessible for BDE or the event's association members**
    """

    event = await cruds_calendar.get_event(db=db, event_id=event_id)

    if event is None:
        raise HTTPException(status_code=404)

    if event.decision != Decision.approved:
        if not is_user_member_of_an_association(
            user=user,
            association=event.association,
        ) and not is_user_member_of_any_group(user, [GroupType.BDE]):
            raise HTTPException(
                status_code=403,
                detail="You are not allowed to access this event",
            )

    return event


@module.router.post(
    "/calendar/events/",
    response_model=schemas_calendar.EventComplete,
    status_code=201,
)
async def add_event(
    event: schemas_calendar.EventBase,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_a_school_member),
    settings: Settings = Depends(get_settings),
    notification_tool: NotificationTool = Depends(get_notification_tool),
):
    """
    Add an event to the calendar.
    """
    association = await cruds_associations.get_association_by_id(
        db=db,
        association_id=event.association_id,
    )
    if association is None:
        raise HTTPException(status_code=404, detail="Association not found")
    if not is_user_member_of_an_association(
        user=user,
        association=association,
    ):
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to add events to this association",
        )

    event_id = uuid.uuid4()

    decision = Decision.approved
    if settings.school.require_event_confirmation:
        decision = Decision.pending

    db_event = models_calendar.Event(
        id=event_id,
        name=event.name,
        association_id=event.association_id,
        applicant_id=user.id,
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        location=event.location,
        description=event.description,
        decision=decision,
        recurrence_rule=event.recurrence_rule,
    )

    await cruds_calendar.add_event(event=db_event, db=db)

    await db.flush()

    created_event = await cruds_calendar.get_event(db=db, event_id=event_id)
    if created_event is None:
        raise NewlyAddedObjectInDbNotFoundError("event")

    if decision == Decision.approved:
        await utils_calendar.add_event_to_feed(
            event=created_event,
            db=db,
            notification_tool=notification_tool,
        )

    return created_event


@module.router.patch(
    "/calendar/events/{event_id}",
    status_code=204,
)
async def edit_bookings_id(
    event_id: uuid.UUID,
    event_edit: schemas_calendar.EventEdit,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_a_school_member),
):
    """
    Edit an event.

    **Only usable by admins or members of the event's association**
    """
    event = await cruds_calendar.get_event(db=db, event_id=event_id)

    if event is None:
        raise HTTPException(status_code=404)

    is_user_member_of_BDE = is_user_member_of_any_group(user, [GroupType.BDE])

    if (
        not is_user_member_of_an_association(
            user=user,
            association=event.association,
        )
        and not is_user_member_of_BDE
    ):
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to edit this event",
        )

    new_decision = event.decision
    if event.decision != Decision.pending and not is_user_member_of_BDE:
        # If the event is not pending and the user is not a member of the group BDE, we will change the decision back to pending
        new_decision = Decision.pending

    await cruds_calendar.edit_event(
        event_id=event_id,
        event=event_edit,
        decision=new_decision,
        db=db,
    )


@module.router.patch(
    "/calendar/events/{event_id}/reply/{decision}",
    status_code=204,
)
async def confirm_booking(
    event_id: uuid.UUID,
    decision: Decision,
    db: AsyncSession = Depends(get_db),
    notification_tool: NotificationTool = Depends(get_notification_tool),
    user: models_users.CoreUser = Depends(is_user_in(GroupType.BDE)),
):
    """
    Give a decision to an event.

    **Only usable by admins**
    """
    event = await cruds_calendar.get_event(db=db, event_id=event_id)

    if event is None:
        raise HTTPException(status_code=404)

    await cruds_calendar.confirm_event(event_id=event_id, decision=decision, db=db)

    await utils_calendar.add_event_to_feed(
        event=event,
        db=db,
        notification_tool=notification_tool,
    )


@module.router.delete(
    "/calendar/events/{event_id}",
    status_code=204,
)
async def delete_bookings_id(
    event_id,
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_a_school_member),
):
    """
    Remove an event.

    **Only usable by admins or, if the event is pending, members of the event's association**
    """
    event = await cruds_calendar.get_event(db=db, event_id=event_id)

    if event is None:
        raise HTTPException(status_code=404)

    is_user_member_of_BDE = is_user_member_of_any_group(user, [GroupType.BDE])
    is_user_member_of_the_event_association = is_user_member_of_an_association(
        user=user,
        association=event.association,
    )

    if is_user_member_of_BDE or (
        is_user_member_of_the_event_association and event.decision == Decision.pending
    ):
        await cruds_calendar.delete_event(event_id=event_id, db=db)
    else:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to delete this event",
        )


@module.router.get(
    "/calendar/ical-url",
    response_model=schemas_calendar.IcalSecret,
    status_code=200,
)
async def get_ical_url(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: models_users.CoreUser = Depends(is_user()),
):
    """
    Generate a unique ical url for the user
    """

    secret_db = await cruds_calendar.get_ical_secret_by_user_id(user_id=user.id, db=db)

    if secret_db is None:
        secret = generate_token()
        await cruds_calendar.add_ical_secret(user_id=user.id, secret=secret, db=db)
    else:
        secret = secret_db.secret

    return schemas_calendar.IcalSecret(
        secret=f"https://{settings.CLIENT_URL}calendar/ical?secret={secret}",
    )


@module.router.post(
    "/calendar/ical/create",
    status_code=204,
)
async def recreate_ical_file(
    db: AsyncSession = Depends(get_db),
    user: models_users.CoreUser = Depends(is_user_in(GroupType.admin)),
):
    """
    Create manually the icalendar file

    **Only usable by global admins**
    """

    events = await cruds_calendar.get_all_events(db)
    await utils_calendar.create_icalendar_file(all_events=events)


@module.router.get(
    "/calendar/ical",
    response_class=FileResponse,
    status_code=200,
)
async def get_icalendar_file(
    secret: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the icalendar file corresponding to the event in the database."""

    existing_secret = await cruds_calendar.get_ical_secret_by_secret(
        secret=secret,
        db=db,
    )
    if existing_secret is None:
        raise HTTPException(status_code=403, detail="Invalid secret")

    return FileResponse(utils_calendar.calendar_file_path)

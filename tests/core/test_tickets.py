import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from fastapi.testclient import TestClient

from app.core.associations.models_associations import CoreAssociation
from app.core.groups.groups_type import GroupType
from app.core.memberships import models_memberships
from app.core.mypayment.models_mypayment import Store, Structure, Wallet
from app.core.mypayment.types_mypayment import WalletType
from app.core.tickets import models_tickets
from app.core.tickets.endpoints_tickets import TicketsPermissions
from app.core.users import models_users
from tests.commons import (
    add_object_to_db,
    create_api_access_token,
    create_groups_with_permissions,
    create_user_with_groups,
)

user: models_users.CoreUser
user_token: str

membership: models_memberships.CoreAssociationMembership
structure_manager_user: models_users.CoreUser
structure: Structure
wallet: Wallet
core_association: CoreAssociation
store: Store


global_event: models_tickets.TicketEvent
event_session: models_tickets.EventSession
event_category: models_tickets.Category
event_sold_out_category: models_tickets.Category
event_sold_out_session: models_tickets.EventSession

sold_out_event: models_tickets.TicketEvent
session_sold_out_event: models_tickets.EventSession
category_sold_out_event: models_tickets.Category


ticket: models_tickets.Ticket


@pytest_asyncio.fixture(scope="module", autouse=True)
async def init_objects() -> None:
    global user, user_token
    ticket_permission_group = await create_groups_with_permissions(
        [TicketsPermissions.buy_tickets],
        "ticket_permission_group",
    )
    user = await create_user_with_groups(groups=[ticket_permission_group.id])
    user_token = create_api_access_token(user)

    global \
        membership, \
        structure_manager_user, \
        structure, \
        wallet, \
        core_association, \
        store
    membership = models_memberships.CoreAssociationMembership(
        id=uuid.uuid4(),
        name="Test Membership",
        manager_group_id=GroupType.admin,
    )
    await add_object_to_db(membership)
    structure_manager_user = await create_user_with_groups(groups=[])
    structure = Structure(
        id=uuid.uuid4(),
        short_id="test",
        name="Test Structure",
        siege_address_street="123 Test Street",
        siege_address_city="Test City",
        siege_address_zipcode="12345",
        siege_address_country="Test Country",
        siret=None,
        iban="FR",
        bic="",
        manager_user_id=structure_manager_user.id,
        creation=datetime.now(tz=UTC),
        association_membership_id=membership.id,
    )
    await add_object_to_db(structure)
    wallet = Wallet(
        id=uuid.uuid4(),
        type=WalletType.STORE,
        balance=0,
    )
    await add_object_to_db(wallet)
    core_association = CoreAssociation(
        id=uuid.uuid4(),
        name="Test Association",
        group_id=GroupType.admin,
    )
    await add_object_to_db(core_association)
    store = Store(
        id=uuid.uuid4(),
        name="Test Store",
        structure_id=structure.id,
        wallet_id=wallet.id,
        creation=datetime.now(tz=UTC),
        association_id=core_association.id,
    )
    await add_object_to_db(store)

    global global_event, event_session, event_category
    ticket_event_id = uuid.uuid4()
    event_session = models_tickets.EventSession(
        id=uuid.uuid4(),
        event_id=ticket_event_id,
        name="Test Session",
        start_datetime=datetime.now(tz=UTC) - timedelta(days=1),
        quota=None,
    )
    event_category = models_tickets.Category(
        id=uuid.uuid4(),
        event_id=ticket_event_id,
        name="Test Category",
        quota=None,
        price=1000,
        required_membership=None,
    )
    global_event = models_tickets.TicketEvent(
        id=uuid.uuid4(),
        store_id=store.id,
        name="Test global_event",
        open_datetime=datetime.now(tz=UTC) - timedelta(days=1),
        close_datetime=datetime.now(tz=UTC) + timedelta(days=1),
        quota=10,
        sessions=[event_session],
        categories=[event_category],
    )
    await add_object_to_db(global_event)

    global event_sold_out_category, event_sold_out_session
    event_sold_out_category = models_tickets.Category(
        id=uuid.uuid4(),
        event_id=global_event.id,
        name="Test global_event Sold Out Category",
        quota=1,
        price=1000,
        required_membership=None,
    )
    await add_object_to_db(event_sold_out_category)
    ticket_sold_out_category = models_tickets.Ticket(
        id=uuid.uuid4(),
        category_id=event_sold_out_category.id,
        session_id=event_session.id,
        event_id=global_event.id,
        user_id=user.id,
        price=10,
        scanned=False,
    )
    await add_object_to_db(ticket_sold_out_category)
    event_sold_out_session = models_tickets.EventSession(
        id=uuid.uuid4(),
        event_id=global_event.id,
        name="Test global_event Sold Out Session",
        start_datetime=datetime.now(tz=UTC) - timedelta(days=1),
        quota=1,
    )
    await add_object_to_db(event_sold_out_session)
    ticket_sold_out_session = models_tickets.Ticket(
        id=uuid.uuid4(),
        category_id=event_category.id,
        session_id=event_sold_out_session.id,
        event_id=global_event.id,
        user_id=user.id,
        price=10,
        scanned=False,
    )
    await add_object_to_db(ticket_sold_out_session)

    global sold_out_event, session_sold_out_event, category_sold_out_event
    ticket_sold_out_event_id = uuid.uuid4()
    session_sold_out_event = models_tickets.EventSession(
        id=uuid.uuid4(),
        event_id=ticket_sold_out_event_id,
        name="Test Session Sold Out",
        start_datetime=datetime.now(tz=UTC) - timedelta(days=1),
        quota=1,
    )
    category_sold_out_event = models_tickets.Category(
        id=uuid.uuid4(),
        event_id=ticket_sold_out_event_id,
        name="Test Category Sold Out",
        quota=1,
        price=1000,
        required_membership=None,
    )
    sold_out_event = models_tickets.TicketEvent(
        id=ticket_sold_out_event_id,
        store_id=store.id,
        name="Test global_event Sold Out",
        open_datetime=datetime.now(tz=UTC) - timedelta(days=1),
        close_datetime=datetime.now(tz=UTC) + timedelta(days=1),
        quota=1,
        sessions=[session_sold_out_event],
        categories=[category_sold_out_event],
    )
    await add_object_to_db(sold_out_event)
    user = await create_user_with_groups(groups=[])
    ticket_sold_out_event = models_tickets.Ticket(
        id=uuid.uuid4(),
        category_id=category_sold_out_event.id,
        session_id=session_sold_out_event.id,
        event_id=ticket_sold_out_event_id,
        user_id=user.id,
        price=10,
        scanned=False,
    )
    await add_object_to_db(ticket_sold_out_event)


def test_get_open_events(client: TestClient):
    response = client.get(
        "/tickets/events",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) > 1


def test_get_event_with_non_existing_id(client: TestClient):
    response = client.get(
        f"/tickets/events/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404


def test_get_event(client: TestClient):
    response = client.get(
        f"/tickets/events/{global_event.id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event = response.json()
    assert event["id"] == str(global_event.id)
    assert len(event["sessions"]) > 0
    assert len(event["categories"]) > 0
    assert event["sold_out"] is False


def test_get_sold_out_event(client: TestClient):
    response = client.get(
        f"/tickets/events/{sold_out_event.id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event = response.json()
    assert event["id"] == str(sold_out_event.id)
    assert len(event["sessions"]) > 0
    assert len(event["categories"]) > 0
    assert event["sold_out"] is True


def test_create_checkout_with_invalid_category(client: TestClient):
    response = client.post(
        f"/tickets/events/{sold_out_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(uuid.uuid4()),
            "session_id": str(session_sold_out_event.id),
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found"


def test_create_checkout_with_invalid_session(client: TestClient):
    response = client.post(
        f"/tickets/events/{sold_out_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(category_sold_out_event.id),
            "session_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_create_checkout_with_category_from_another_event(client: TestClient):
    response = client.post(
        f"/tickets/events/{sold_out_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(event_category.id),
            "session_id": str(session_sold_out_event.id),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Category does not belong to the event"


def test_create_checkout_with_session_from_another_event(client: TestClient):
    response = client.post(
        f"/tickets/events/{sold_out_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(category_sold_out_event.id),
            "session_id": str(event_session.id),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Session does not belong to the event"


# TODO: test required membership


def test_create_checkout_with_sold_out_event(client: TestClient):
    response = client.post(
        f"/tickets/events/{sold_out_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(category_sold_out_event.id),
            "session_id": str(session_sold_out_event.id),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Event is sold out"


def test_create_checkout_with_sold_out_category(client: TestClient):
    response = client.post(
        f"/tickets/events/{global_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(event_sold_out_category.id),
            "session_id": str(event_session.id),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Category is sold out"


def test_create_checkout_with_sold_out_session(client: TestClient):
    response = client.post(
        f"/tickets/events/{global_event.id}/checkout",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "category_id": str(event_category.id),
            "session_id": str(event_sold_out_session.id),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Session is sold out"


def test_get_user_tickets(client: TestClient):
    response = client.get(
        "/tickets/user/me/tickets",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) > 1

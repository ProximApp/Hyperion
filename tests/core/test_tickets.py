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


ticket_event: models_tickets.TicketEvent
ticket_session: models_tickets.EventSession
ticket_category: models_tickets.Category

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

    global ticket_event, ticket_session, ticket_category
    ticket_event_id = uuid.uuid4()
    ticket_session = models_tickets.EventSession(
        id=uuid.uuid4(),
        event_id=ticket_event_id,
        name="Test Session",
        start_time=datetime.now(tz=UTC) - timedelta(days=1),
        end_time=datetime.now(tz=UTC) + timedelta(days=1),
        quota=None,
    )
    ticket_category = models_tickets.Category(
        id=uuid.uuid4(),
        event_id=ticket_event_id,
        name="Test Category",
        quota=None,
        price=1000,
        required_membership=None,
    )
    ticket_event = models_tickets.TicketEvent(
        id=uuid.uuid4(),
        store_id=store.id,
        name="Test Event",
        open_datetime=datetime.now(tz=UTC) - timedelta(days=1),
        close_datetime=datetime.now(tz=UTC) + timedelta(days=1),
        quota_per_user=2,
        quota_per_checkout=2,
        quota=10,
        sessions=[ticket_session],
        categories=[ticket_category],
    )
    await add_object_to_db(ticket_event)


def test_get_open_events(client: TestClient):
    response = client.get(
        "/tickets/events",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_event_with_non_existing_id(client: TestClient):
    response = client.get(
        f"/tickets/events/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404


def test_get_event(client: TestClient):
    response = client.get(
        f"/tickets/events/{ticket_event.id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200

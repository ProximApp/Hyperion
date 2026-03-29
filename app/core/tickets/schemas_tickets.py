from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
)

from app.core.users import schemas_users


class Session(BaseModel):
    id: UUID
    event_id: UUID
    name: str
    start_time: datetime
    end_time: datetime


class SessionComplete(Session):
    """
    Correspond to a Session in the database
    """

    quota: int | None


class SessionPublic(Session):
    sold_out: bool


class SessionAdmin(SessionComplete):
    sold: int
    waiting: int


class SessionCreate(BaseModel):
    name: str
    start_time: datetime
    end_time: datetime
    quota: int | None


class Category(BaseModel):
    id: UUID
    event_id: UUID
    name: str
    price: int
    required_membership: UUID | None


class CategoryComplete(Category):
    """
    Correspond to a Category in the database
    """

    quota: int | None


class CategoryPublic(Category):
    sold_out: bool


class CategoryAdmin(CategoryComplete):
    sold: int
    waiting: int


class CategoryCreate(BaseModel):
    name: str
    price: int
    quota: int | None
    required_membership: UUID | None


class EventSimple(BaseModel):
    id: UUID
    name: str

    store_id: UUID

    open_datetime: datetime
    close_datetime: datetime | None


class EventComplete(EventSimple):
    sessions: list[SessionComplete]
    categories: list[CategoryComplete]

    quota_per_user: int | None
    quota_per_checkout: int | None
    quota: int | None


class EventPublic(EventSimple):
    sessions: list[SessionPublic]
    categories: list[CategoryPublic]

    sold_out: bool


class EventAdmin(EventComplete):
    sessions: list[SessionAdmin]
    categories: list[CategoryAdmin]

    quota_per_user: int | None
    quota_per_checkout: int | None
    quota: int | None


class EventCreate(BaseModel):
    store_id: UUID
    name: str
    quota_per_user: int | None
    quota_per_checkout: int | None
    quota: int | None
    open_datetime: datetime
    close_datetime: datetime | None
    sessions: list[SessionCreate]
    categories: list[CategoryCreate]


class Ticket(BaseModel):
    id: UUID
    price: int
    user_id: UUID

    category_id: UUID
    session_id: UUID

    scanned: bool

    category: Category
    session: Session
    user: schemas_users.CoreUserSimple


class Checkout(BaseModel):
    category_id: UUID
    session_id: UUID


class CheckoutResponse(BaseModel):
    price: int
    expiration: datetime

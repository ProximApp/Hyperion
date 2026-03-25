from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
)


class Session(BaseModel):
    name: str
    start_time: datetime
    end_time: datetime
    event_id: UUID


class SessionCreate(BaseModel):
    name: str
    start_time: datetime
    end_time: datetime
    quota: int | None


class SessionComplete(Session):
    id: UUID


class Category(BaseModel):
    id: UUID
    name: str
    price: int
    required_membership: UUID | None
    event_id: UUID


class CategoryCreate(BaseModel):
    name: str
    price: int
    quota: int | None
    required_membership: UUID | None


class CategoryComplete(Category):
    id: UUID


class EventSimple(BaseModel):
    id: UUID
    name: str

    store_id: UUID


class EventPublic(EventSimple):
    sessions: list[Session]
    categories: list[Category]

    open_datetime: datetime
    close_datetime: datetime | None


class EventAdmin(EventPublic):
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


class Checkout(BaseModel):
    category_id: UUID
    session_id: UUID


class CheckoutResponse(BaseModel):
    price: int
    expiration: datetime

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.mypayment import models_mypayment
from app.core.users import models_users
from app.types.sqlalchemy import Base, PrimaryKey

# TODO: do we want to be able to disable sessions or prices?


class TicketEvent(Base):
    __tablename__ = "tickets_event"

    id: Mapped[PrimaryKey]
    name: Mapped[str]

    store_id: Mapped[UUID] = mapped_column(ForeignKey("mypayment_store.id"))

    open_datetime: Mapped[datetime]
    close_datetime: Mapped[datetime | None]

    # Total number of tickets available, None means unlimited
    quota: Mapped[int | None]

    store: Mapped[models_mypayment.Store] = relationship(init=False)

    sessions: Mapped[list["EventSession"]] = relationship(back_populates="event")
    categories: Mapped[list["Category"]] = relationship(back_populates="event")


class EventSession(Base):
    __tablename__ = "tickets_session"

    id: Mapped[PrimaryKey]
    event_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_event.id"))

    name: Mapped[str]

    start_datetime: Mapped[datetime]

    quota: Mapped[int | None] = mapped_column(default=None)

    event: Mapped["TicketEvent"] = relationship(back_populates="sessions", init=False)


class Category(Base):
    __tablename__ = "tickets_category"

    id: Mapped[PrimaryKey]
    event_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_event.id"))

    name: Mapped[str]

    quota: Mapped[int | None]

    price: Mapped[int]  # in cents
    required_membership: Mapped[UUID | None] = mapped_column(
        ForeignKey("core_association_membership.id"),
    )

    event: Mapped["TicketEvent"] = relationship(back_populates="categories", init=False)


class Ticket(Base):
    __tablename__ = "tickets_ticket"

    id: Mapped[PrimaryKey]

    category_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_category.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_session.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_event.id"))

    user_id: Mapped[str] = mapped_column(ForeignKey("core_user.id"))

    price: Mapped[int]  # in cents

    scanned: Mapped[bool]

    category: Mapped["Category"] = relationship(init=False)
    session: Mapped["EventSession"] = relationship(init=False)
    user: Mapped[models_users.CoreUser] = relationship(init=False)


class Checkout(Base):
    __tablename__ = "tickets_checkout"

    id: Mapped[PrimaryKey]

    category_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_category.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_session.id"))

    event_id: Mapped[UUID] = mapped_column(ForeignKey("tickets_event.id"))

    price: Mapped[int]  # in cents
    expiration: Mapped[datetime]

    user_id: Mapped[str] = mapped_column(ForeignKey("core_user.id"))

    # Do we need this?
    user: Mapped[models_users.CoreUser] = relationship(init=False)

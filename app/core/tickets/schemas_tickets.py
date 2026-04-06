from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
)

from app.core.tickets.types_tickets import AnswerType
from app.core.users import schemas_users


class Session(BaseModel):
    id: UUID
    event_id: UUID
    name: str
    start_datetime: datetime
    disabled: bool


class SessionComplete(Session):
    """
    Correspond to a Session in the database
    """

    quota: int | None


class SessionPublic(Session):
    sold_out: bool


class SessionAdmin(SessionComplete):
    tickets_in_checkout: int
    tickets_sold: int


class SessionCreate(BaseModel):
    name: str
    start_datetime: datetime

    quota: int | None


class SessionUpdate(BaseModel):
    name: str | None = None
    start_datetime: datetime | None = None
    quota: int | None = None
    disabled: bool | None = None


class Category(BaseModel):
    id: UUID
    event_id: UUID
    name: str
    price: int
    required_membership: UUID | None
    disabled: bool


class CategoryComplete(Category):
    """
    Correspond to a Category in the database
    """

    quota: int | None


class CategoryPublic(Category):
    sold_out: bool


class CategoryAdmin(CategoryComplete):
    tickets_in_checkout: int
    tickets_sold: int


class CategoryCreate(BaseModel):
    name: str
    price: int
    quota: int | None
    required_membership: UUID | None


class CategoryUpdate(BaseModel):
    name: str | None = None
    price: int | None = None
    quota: int | None = None
    required_membership: UUID | None = None
    disabled: bool | None = None


class Question(BaseModel):
    id: UUID
    event_id: UUID
    question: str
    answer_type: AnswerType
    price: int | None
    required: bool
    disabled: bool


class QuestionPublic(Question):
    pass


class QuestionAdmin(Question):
    pass


class QuestionCreate(BaseModel):
    question: str
    answer_type: AnswerType
    price: int | None
    required: bool


class EventSimple(BaseModel):
    id: UUID
    name: str

    store_id: UUID

    open_datetime: datetime
    close_datetime: datetime | None

    disabled: bool


class EventWithoutSessionsAndCategories(EventSimple):
    quota: int | None


class EventComplete(EventWithoutSessionsAndCategories):
    sessions: list[SessionComplete]
    categories: list[CategoryComplete]
    questions: list[Question]


class EventPublic(EventSimple):
    sessions: list[SessionPublic]
    categories: list[CategoryPublic]
    questions: list[QuestionPublic]

    sold_out: bool


class EventAdmin(EventWithoutSessionsAndCategories):
    sessions: list[SessionAdmin]
    categories: list[CategoryAdmin]
    questions: list[QuestionAdmin]

    tickets_in_checkout: int
    tickets_sold: int


class EventCreate(BaseModel):
    store_id: UUID
    name: str
    quota: int | None
    open_datetime: datetime
    close_datetime: datetime | None
    sessions: list[SessionCreate]
    categories: list[CategoryCreate]
    questions: list[QuestionCreate]


class EventUpdate(BaseModel):
    name: str | None = None
    quota: int | None = None
    open_datetime: datetime | None = None
    close_datetime: datetime | None = None


class Ticket(BaseModel):
    id: UUID
    price: int
    user_id: str

    event_id: UUID
    category_id: UUID
    session_id: UUID

    scanned: bool

    category: Category
    session: Session
    user: schemas_users.CoreUserSimple


class Answer(BaseModel):
    question_id: UUID
    answer_type: AnswerType
    answer: str | int | bool

    @property
    def answer_value(self) -> str:
        return str(self.answer)


class AnswerText(BaseModel):
    answer_type: Literal[AnswerType.TEXT]
    answer: str


class AnswerNumber(BaseModel):
    answer_type: Literal[AnswerType.NUMBER]
    answer: int


class AnswerBoolean(BaseModel):
    answer_type: Literal[AnswerType.BOOLEAN]
    answer: bool


class Checkout(BaseModel):
    category_id: UUID
    session_id: UUID
    answers: list[Answer]


class CheckoutResponse(BaseModel):
    price: int
    expiration: datetime

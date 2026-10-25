from enum import StrEnum


class AnswerType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"


class StreamAction(StrEnum):
    HOLD = "HOLD"
    CONFIRM = "CONFIRM"
    RELEASE = "RELEASE"


SOLD_OUT_MESSAGES = {
    "EVENT": "Event is sold out",
    "CATEGORY": "Category is sold out",
    "SESSION": "Session is sold out",
}

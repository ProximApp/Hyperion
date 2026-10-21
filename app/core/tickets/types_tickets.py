from enum import StrEnum


class AnswerType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"


class StreamAction(StrEnum):
    HOLD = "HOLD"
    CONFIRM = "CONFIRM"
    RELEASE = "RELEASE"


class LuaResult(StrEnum):
    RESERVED = "RESERVED"
    USER_ALREADY_HAS_RESERVATION = "USER_ALREADY_HAS_RESERVATION"
    SOLD_OUT_OR_INSUFFICIENT_STOCK = "SOLD_OUT_OR_INSUFFICIENT_STOCK"
    STOCK_NOT_INITIALIZED = "STOCK_NOT_INITIALIZED"
    CONFIRMED = "CONFIRMED"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    HOLD_NOT_FOUND = "HOLD_NOT_FOUND"
    NOT_PENDING = "NOT_PENDING"
    NOT_EXPIRED = "NOT_EXPIRED"
    RELEASED = "RELEASED"
    OK = "OK"


class EventRedisKeys(StrEnum):
    STREAM_KEY = "tickets:stream:checkouts"
    DEAD_LETTER_STREAM_KEY = "tickets:stream:checkouts:dead"
    STREAM_GROUP = "tickets-write-behind"
    EXPIRATIONS_ZSET_KEY = "tickets:holds:expirations"


SOLD_OUT_MESSAGES = {
    "EVENT": "Event is sold out",
    "CATEGORY": "Category is sold out",
    "SESSION": "Session is sold out",
}

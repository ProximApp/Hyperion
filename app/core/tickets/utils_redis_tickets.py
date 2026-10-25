from typing import Any
from uuid import UUID


def stock_key_event(event_id: UUID) -> str:
    return f"tickets:stock:event:{event_id}"


def stock_key_category(category_id: UUID) -> str:
    return f"tickets:stock:category:{category_id}"


def stock_key_session(session_id: UUID) -> str:
    return f"tickets:stock:session:{session_id}"


def hold_key(checkout_id: UUID | str) -> str:
    return f"tickets:hold:{checkout_id}"


def user_hold_key(event_id: UUID, user_id: str) -> str:
    return f"tickets:user_hold:{event_id}:{user_id}"


def snapshot_key(event_id: UUID) -> str:
    return f"tickets:event:{event_id}:snapshot"


def warmup_lock_key(event_id: UUID) -> str:
    return f"tickets:event:{event_id}:warmup_lock"


# --------------------------------------------------------------------------------------
# Result codes returned by the Lua scripts
# --------------------------------------------------------------------------------------


def to_str(value: Any) -> str:
    """Redis clients return bytes unless `decode_responses=True`; normalise both."""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)

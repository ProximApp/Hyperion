from uuid import UUID


class RedisUnavailableError(Exception):
    def __init__(self, checkout_id: UUID):
        super().__init__(
            f"Redis is unavailable while confirming paid ticket checkout {checkout_id}",
        )


class UnexpectedRedisConfirmationResultError(Exception):
    def __init__(self, checkout_id: UUID, status: str):
        super().__init__(
            f"Unexpected Redis confirmation result for checkout {checkout_id}: {status}",
        )


class TicketCheckoutNotFoundError(Exception):
    def __init__(self, checkout_id: UUID):
        super().__init__(
            f"Paid ticket checkout {checkout_id} is absent from Redis and PostgreSQL",
        )


class InvalidTTLValueError(Exception):
    def __init__(self, ttl: int):
        super().__init__(
            f"hold_ttl_seconds must be positive, but got {ttl}",
        )


class InvalidRedisStateError(Exception):
    def __init__(self, checkout_id: UUID, state: str):
        super().__init__(
            f"Redis state for checkout {checkout_id} is invalid: {state}",
        )


class RedisCheckoutConfirmationError(Exception):
    def __init__(self, checkout_id: UUID):
        super().__init__(
            f"Redis checkout confirmation failed for checkout {checkout_id}",
        )


class RedisCheckoutConfirmationUnableError(Exception):
    def __init__(self, checkout_id: UUID, status: str):
        super().__init__(f"Reservation {checkout_id} could not be confirmed: {status}")


class RedisCheckoutConfirmPublishError(Exception):
    def __init__(self, checkout_id: UUID):
        super().__init__(
            f"Could not publish CONFIRM event for checkout {checkout_id}",
        )

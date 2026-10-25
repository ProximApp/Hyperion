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

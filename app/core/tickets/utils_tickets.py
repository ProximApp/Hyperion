import logging
import uuid
from collections.abc import Sequence
from uuid import UUID

from fastapi import (
    HTTPException,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memberships import utils_memberships
from app.core.mypayment import utils_mypayment
from app.core.tickets import (
    cruds_tickets,
    schemas_tickets,
    utils_redis_tickets,
)
from app.core.tickets.redis_event_tool import RedisEventTool
from app.core.tickets.schemas_tickets import ReservationResult
from app.core.tickets.types_tickets import SOLD_OUT_MESSAGES, LuaResult

MEMBERSHIP_CACHE_TTL_SECONDS = 300
MEMBERSHIP_NEGATIVE_CACHE_TTL_SECONDS = 30


hyperion_error_logger = logging.getLogger("hyperion.error")


async def mypayment_callback_callback(
    checkout_id: UUID,
    db: AsyncSession,
    redis_client: Redis | None = None,
) -> None:
    """
    Callback called by MyPayment when the payment status of a checkout changes.

    It will update the checkout and the associated tickets status according to the payment status.

    ASYNC FLOW: the confirmation is done in Redis by an atomic Lua script (the hold leaves the
    expiration ZSET, so the reaper can never give this stock back) which also publishes a CONFIRM
    event. PostgreSQL is updated later by the write-behind worker. `db` is only used by the fallbacks.

    The signature is imposed by the MyPayment callback contract, so the Redis client can not be
    injected: it is the one registered by `utils_redis_tickets.init_tickets_redis` at startup.
    """

    if redis_client is None:
        # Redis is not configured (development / tests): historical synchronous behaviour.
        await cruds_tickets.mark_checkout_as_paid(
            checkout_id=checkout_id,
            db=db,
        )
        return

    status = await RedisEventTool(redis_client).confirm_hold(checkout_id=checkout_id)
    if status in (LuaResult.CONFIRMED, LuaResult.ALREADY_CONFIRMED):
        # ALREADY_CONFIRMED: MyPayment called us twice, nothing to do.
        return

    # HOLD_NOT_FOUND: the payment reached us after the hold was released (payment window plus grace
    # period elapsed) or Redis lost it. The money has been taken, so the payment wins: fall back to
    # the historical behaviour and make it visible to the operators.
    hyperion_error_logger.error(
        "Payment received for checkout but Redis has no pending hold. "
        "Marking it as paid in the database; the Redis stock may be off by one.",
        extra={"checkout_id": checkout_id, "status": status},
    )
    updated = await cruds_tickets.mark_checkout_as_paid(
        checkout_id=checkout_id,
        db=db,
    )
    if not updated:
        hyperion_error_logger.critical(
            "Payment received for unknown checkout: a refund is probably required",
            extra={"checkout_id": checkout_id},
        )


async def is_event_sold_out(
    event_id: UUID,
    quota: int | None,
    db: AsyncSession,
) -> bool:
    if quota is None:
        return False

    nb_valid_checkouts_and_tickets_by_event_id = (
        await cruds_tickets.count_valid_checkouts_and_tickets_by_event_id(
            event_id=event_id,
            db=db,
        )
    )

    return nb_valid_checkouts_and_tickets_by_event_id >= quota


async def is_category_sold_out(
    category_id: UUID,
    quota: int | None,
    db: AsyncSession,
) -> bool:
    if quota is None:
        return False

    nb_valid_checkouts_and_tickets_by_category_id = (
        await cruds_tickets.count_valid_checkouts_and_tickets_by_category_id(
            category_id=category_id,
            db=db,
        )
    )

    return nb_valid_checkouts_and_tickets_by_category_id >= quota


async def is_session_sold_out(
    session_id: UUID,
    quota: int | None,
    db: AsyncSession,
) -> bool:
    if quota is None:
        return False

    nb_valid_checkouts_and_tickets_by_session_id = (
        await cruds_tickets.count_valid_checkouts_and_tickets_by_session_id(
            session_id=session_id,
            db=db,
        )
    )

    return nb_valid_checkouts_and_tickets_by_session_id >= quota


async def convert_to_event_admin(
    event: schemas_tickets.EventComplete,
    db: AsyncSession,
):
    return schemas_tickets.EventAdmin(
        id=event.id,
        name=event.name,
        store_id=event.store_id,
        open_datetime=event.open_datetime,
        close_datetime=event.close_datetime,
        sessions=[
            schemas_tickets.SessionAdmin(
                id=session.id,
                event_id=session.event_id,
                name=session.name,
                start_datetime=session.start_datetime,
                quota=session.quota,
                disabled=session.disabled,
                tickets_in_checkout=await cruds_tickets.count_valid_checkouts_by_event_id(
                    event_id=event.id,
                    db=db,
                ),
                tickets_sold=await cruds_tickets.count_tickets_by_event_id(
                    event_id=event.id,
                    db=db,
                ),
            )
            for session in event.sessions
        ],
        categories=[
            schemas_tickets.CategoryAdmin(
                id=category.id,
                event_id=category.event_id,
                name=category.name,
                price=category.price,
                required_membership=category.required_membership,
                quota=category.quota,
                disabled=category.disabled,
                tickets_in_checkout=await cruds_tickets.count_valid_checkouts_by_category_id(
                    category_id=category.id,
                    db=db,
                ),
                tickets_sold=await cruds_tickets.count_tickets_by_category_id(
                    category_id=category.id,
                    db=db,
                ),
            )
            for category in event.categories
        ],
        questions=[
            schemas_tickets.QuestionAdmin(
                id=question.id,
                event_id=question.event_id,
                question=question.question,
                answer_type=question.answer_type,
                price=question.price,
                required=question.required,
                disabled=question.disabled,
            )
            for question in event.questions
        ],
        quota=event.quota,
        disabled=event.disabled,
        tickets_in_checkout=await cruds_tickets.count_valid_checkouts_by_event_id(
            event_id=event.id,
            db=db,
        ),
        tickets_sold=await cruds_tickets.count_tickets_by_event_id(
            event_id=event.id,
            db=db,
        ),
    )


async def get_events_from_store(
    store_id: uuid.UUID,
    user_id: str,
    db: AsyncSession,
) -> Sequence[schemas_tickets.EventSimple]:
    await utils_mypayment.ensure_user_can_manage_events(
        user_id=user_id,
        store_id=store_id,
        db=db,
    )

    return await cruds_tickets.get_events_by_store_id(
        store_id=store_id,
        db=db,
    )


def raise_if_reservation_failed(
    reservation: ReservationResult,
) -> None:
    """Translate the result code of the Lua reservation script into the historical HTTP errors."""
    if reservation.status == LuaResult.RESERVED:
        return
    if reservation.status == LuaResult.USER_ALREADY_HAS_RESERVATION:
        raise HTTPException(
            400,
            "User already has a pending reservation for this event",
        )
    if reservation.status == LuaResult.SOLD_OUT_OR_INSUFFICIENT_STOCK:
        raise HTTPException(
            400,
            SOLD_OUT_MESSAGES.get(reservation.dimension or "", "Event is sold out"),
        )
    # STOCK_NOT_INITIALIZED (even after a warm-up): transient, the client can retry.
    raise HTTPException(
        503,
        "Event stock is not ready, please retry",
        headers={"Retry-After": "1"},
    )


def check_answer_validity_and_calculate_price(
    questions: Sequence[schemas_tickets.Question],
    checkout: schemas_tickets.Checkout,
) -> int:
    """
    Validate the answers of a checkout and return the price of the paid questions.

    Pure CPU function: `questions` comes from the Redis snapshot of the event, so there is no
    database access (it used to run one SELECT per checkout).
    """
    price = 0

    questions_dict = {question.id: question for question in questions}
    required_questions_ids = {
        question.id for question in questions if question.required
    }
    answered_questions_ids = set()

    for answer in checkout.answers:
        if answer.question_id in answered_questions_ids:
            raise HTTPException(
                400,
                f"Question with id {answer.question_id} is answered multiple times",
            )
        answered_questions_ids.add(answer.question_id)
        required_questions_ids.discard(answer.question_id)

        question = questions_dict.get(answer.question_id)
        if question is None:
            raise HTTPException(
                400,
                f"Question with id {answer.question_id} not found for this event",
            )
        if question.disabled:
            raise HTTPException(
                400,
                f"Question with id {answer.question_id} is disabled",
            )

        if question.answer_type != answer.answer.answer_type:
            raise HTTPException(
                400,
                f"Answer type for question with id {answer.question_id} should be {question.answer_type.value}",
            )

        if question.price is not None:
            price += question.price

    if len(required_questions_ids) > 0:
        raise HTTPException(
            400,
            f"Answers for questions {', '.join(str(q) for q in required_questions_ids)} are required",
        )

    return price


async def user_has_required_membership(
    redis_event_tool: RedisEventTool,
    association_membership_id: UUID,
    user_id: str,
    db: AsyncSession,
) -> bool:
    """
    Membership check, cached in Redis.

    This is the one remaining read that can reach PostgreSQL on the checkout path: only for
    categories with `required_membership`, and only for the first request of a user (then served
    from RAM for MEMBERSHIP_CACHE_TTL_SECONDS). It is a plain SELECT: no write, no commit.
    """
    cache_key = f"tickets:membership:{association_membership_id}:{user_id}"
    cached = await redis_event_tool.redis_client.get(cache_key)
    if cached is not None:
        return utils_redis_tickets.to_str(cached) == "1"

    membership = (
        await utils_memberships.get_user_active_membership_to_association_membership(
            association_membership_id=association_membership_id,
            user_id=user_id,
            db=db,
        )
    )
    has_membership = membership is not None
    await redis_event_tool.redis_client.set(
        cache_key,
        "1" if has_membership else "0",
        ex=MEMBERSHIP_CACHE_TTL_SECONDS
        if has_membership
        else MEMBERSHIP_NEGATIVE_CACHE_TTL_SECONDS,
    )
    return has_membership

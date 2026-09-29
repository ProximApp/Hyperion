"""
Slow path of the tickets module: everything that touches PostgreSQL happens here, off the request path.

    Redis Stream (HOLD / CONFIRM / RELEASE)
        --XREADGROUP--> TicketsWriteBehindWorker --buffer: N events or T seconds--> ONE SQL transaction --XACK-->

    Redis ZSET of expirations --ZRANGEBYSCORE--> reaper --atomic Lua release (stock back + RELEASE event)-->

The worker can run as a background task of the API (see `start_tickets_background_workers`) or in its own
process (build a Redis client and an `async_sessionmaker`, then `await TicketsWriteBehindWorker(...).run()`).
Several instances can run at the same time: the consumer group spreads the entries between them.
"""

import asyncio
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import NoScriptError, ResponseError
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tickets import cruds_tickets, schemas_tickets, utils_redis_tickets
from app.core.tickets.utils_redis_tickets import StreamAction, to_str

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
DEFAULT_FLUSH_INTERVAL_SECONDS = 2.0
REAPER_INTERVAL_SECONDS = 5
REAPER_BATCH_SIZE = 500
REAPER_MAX_BATCHES_PER_SWEEP = 100
# Refresh-ahead of the event snapshots. Must stay well below utils_redis_tickets.SNAPSHOT_TTL_SECONDS
# so that the cache of an active event is never cold while a sale is running.
SNAPSHOT_REFRESH_INTERVAL_SECONDS = 10.0


# ======================================================================================
# Step 3: Write-behind worker
# ======================================================================================


@dataclass(frozen=True)
class StreamEvent:
    message_id: str
    action: StreamAction
    checkout_id: UUID
    user_id: str
    qty: int
    ts: int
    expires_at: int
    payload: schemas_tickets.CheckoutPayload


def parse_stream_event(message_id: str, fields: dict[Any, Any]) -> StreamEvent:
    """Raises KeyError / ValueError / ValidationError on a malformed entry (sent to the dead-letter stream)."""
    data = {to_str(key): to_str(value) for key, value in fields.items()}
    return StreamEvent(
        message_id=message_id,
        action=StreamAction(data["action"]),
        checkout_id=UUID(data["checkout_id"]),
        user_id=data["user_id"],
        qty=int(data["qty"]),
        ts=int(float(data["ts"])),
        expires_at=int(float(data["expires_at"])),
        payload=schemas_tickets.CheckoutPayload.model_validate_json(
            data["payload"],
        ),
    )


def is_transient_db_error(exc: BaseException) -> bool:
    """Connection lost, timeout, deadlock...: retrying the same batch later can succeed."""
    if isinstance(
        exc,
        OperationalError | InterfaceError | TimeoutError | ConnectionError,
    ):
        return True
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


class TicketsWriteBehindWorker:
    """
    Consumes the checkout stream and persists it to PostgreSQL in batches.

    Delivery is at-least-once: an entry is acknowledged (XACK) only after the SQL transaction that
    persists it has been committed. If the worker crashes in between, the entry is claimed again
    (XAUTOCLAIM) by a live consumer and replayed, which is harmless because every write is idempotent.
    """

    def __init__(
        self,
        redis_client: Redis,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        consumer_name: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        claim_idle_ms: int = 60_000,
        claim_every_seconds: float = 30.0,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.consumer_name = (
            consumer_name
            or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self.claim_idle_ms = claim_idle_ms
        self.claim_every_seconds = claim_every_seconds

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop_event = stop_event or asyncio.Event()
        await self._ensure_group()
        logger.info("Tickets write-behind worker %s started", self.consumer_name)

        buffer: list[tuple[str, dict[Any, Any]]] = []
        first_buffered_at = 0.0
        last_claim_at = float("-inf")
        backoff = 1.0

        while not stop_event.is_set():
            try:
                # Recover entries left pending by a crashed consumer (or by a previous run of this one).
                if time.monotonic() - last_claim_at >= self.claim_every_seconds:
                    last_claim_at = time.monotonic()
                    claimed = await self._claim_stale_entries(
                        exclude={message_id for message_id, _ in buffer},
                    )
                    if claimed and not buffer:
                        first_buffered_at = time.monotonic()
                    buffer.extend(claimed)

                # Accumulate: read at most what is missing to complete the batch, and never wait
                # longer than what remains of the flush interval.
                if len(buffer) < self.batch_size:
                    entries = await self._read_new_entries(
                        count=self.batch_size - len(buffer),
                        block_ms=self._block_timeout_ms(buffer, first_buffered_at),
                    )
                    if entries and not buffer:
                        first_buffered_at = time.monotonic()
                    buffer.extend(entries)

                # Flush when the batch is full OR the oldest buffered entry is older than the interval.
                if buffer and (
                    len(buffer) >= self.batch_size
                    or time.monotonic() - first_buffered_at
                    >= self.flush_interval_seconds
                ):
                    if await self._flush(buffer):
                        backoff = 1.0
                    else:
                        # PostgreSQL unavailable: nothing was acknowledged, keep the batch and retry.
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30.0)
                    if buffer:
                        first_buffered_at = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Tickets write-behind loop error")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

        if buffer:
            await self._flush(
                buffer,
            )  # graceful shutdown: do not leave a batch unpersisted
        logger.info("Tickets write-behind worker %s stopped", self.consumer_name)

    # ---- Redis side -------------------------------------------------------------------

    async def _ensure_group(self) -> None:
        try:
            # id="0": a group created after the API published its first events must still see them.
            await self.redis.xgroup_create(
                name=utils_redis_tickets.STREAM_KEY,
                groupname=utils_redis_tickets.STREAM_GROUP,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _block_timeout_ms(
        self,
        buffer: Sequence[Any],
        first_buffered_at: float,
    ) -> int:
        if not buffer:
            return int(self.flush_interval_seconds * 1000)
        remaining = self.flush_interval_seconds - (time.monotonic() - first_buffered_at)
        return max(1, int(remaining * 1000))  # BLOCK 0 would mean "forever"

    async def _read_new_entries(
        self,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, dict[Any, Any]]]:
        response = await self.redis.xreadgroup(
            groupname=utils_redis_tickets.STREAM_GROUP,
            consumername=self.consumer_name,
            streams={utils_redis_tickets.STREAM_KEY: ">"},
            count=count,
            block=block_ms,
        )
        entries: list[tuple[str, dict[Any, Any]]] = []
        for _stream, messages in response or []:
            entries.extend(
                (to_str(message_id), fields) for message_id, fields in messages
            )
        return entries

    async def _claim_stale_entries(
        self,
        exclude: set[str],
    ) -> list[tuple[str, dict[Any, Any]]]:
        """XAUTOCLAIM: take over entries that another consumer read but never acknowledged."""
        claimed: list[tuple[str, dict[Any, Any]]] = []
        start_id = "0-0"
        while len(claimed) < self.batch_size:
            response = await self.redis.xautoclaim(
                name=utils_redis_tickets.STREAM_KEY,
                groupname=utils_redis_tickets.STREAM_GROUP,
                consumername=self.consumer_name,
                min_idle_time=self.claim_idle_ms,
                start_id=start_id,
                count=self.batch_size,
            )
            next_id, messages = to_str(response[0]), response[1]
            for message_id, fields in messages:
                message_id = to_str(message_id)
                if fields and message_id not in exclude:
                    claimed.append((message_id, fields))
            if next_id == "0-0":
                break
            start_id = next_id
        return claimed

    async def _ack(self, message_ids: Sequence[str]) -> None:
        """XACK, then XDEL so the stream does not grow forever (there is a single consumer group)."""
        if not message_ids:
            return
        pipe = self.redis.pipeline(transaction=True)
        pipe.xack(
            utils_redis_tickets.STREAM_KEY,
            utils_redis_tickets.STREAM_GROUP,
            *message_ids,
        )
        pipe.xdel(utils_redis_tickets.STREAM_KEY, *message_ids)
        await pipe.execute()

    async def _dead_letter(
        self,
        message_id: str,
        fields: dict[Any, Any],
        reason: str,
    ) -> None:
        """Park an entry that can never succeed (malformed, or rejected by a constraint) and unblock the stream."""
        logger.error("Dead-lettering stream entry %s: %s", message_id, reason)
        parked = {to_str(key): to_str(value) for key, value in fields.items()}
        parked["original_id"] = message_id
        parked["error"] = reason[:500]
        await self.redis.xadd(utils_redis_tickets.DEAD_LETTER_STREAM_KEY, parked)
        await self._ack([message_id])

    # ---- PostgreSQL side ----------------------------------------------------------------

    async def _flush(self, buffer: list[tuple[str, dict[Any, Any]]]) -> bool:
        """
        Persist the buffered entries in ONE transaction, then acknowledge them.
        Returns False on a transient database failure: the buffer is left untouched and retried.
        `buffer` is updated in place.
        """
        raw_by_id = dict(buffer)
        events: list[StreamEvent] = []
        malformed: set[str] = set()
        for message_id, fields in buffer:
            try:
                events.append(parse_stream_event(message_id, fields))
            except (KeyError, ValueError, ValidationError) as exc:
                malformed.add(message_id)
                await self._dead_letter(message_id, fields, f"malformed event: {exc!r}")
        if malformed:
            buffer[:] = [(m, f) for m, f in buffer if m not in malformed]
        if not events:
            return True

        try:
            await self._persist(events)
        except Exception as exc:
            if is_transient_db_error(exc):
                logger.warning("PostgreSQL unavailable, will retry the batch: %r", exc)
                return False
            if not isinstance(exc, SQLAlchemyError):
                raise
            # One bad event would make the whole batch fail forever: isolate it.
            logger.exception(
                "Batch of %d events failed, retrying them one by one",
                len(events),
            )
            return await self._persist_one_by_one(events, raw_by_id, buffer)

        await self._ack([event.message_id for event in events])
        logger.debug("Persisted a batch of %d tickets events", len(events))
        buffer.clear()
        return True

    async def _persist_one_by_one(
        self,
        events: Sequence[StreamEvent],
        raw_by_id: dict[str, dict[Any, Any]],
        buffer: list[tuple[str, dict[Any, Any]]],
    ) -> bool:
        for event in events:
            try:
                await self._persist([event])
            except Exception as exc:
                if is_transient_db_error(exc):
                    return False
                if not isinstance(exc, SQLAlchemyError):
                    raise
                await self._dead_letter(
                    event.message_id,
                    raw_by_id[event.message_id],
                    f"database error: {exc.__class__.__name__}: {str(exc)[:300]}",
                )
            else:
                await self._ack([event.message_id])
            buffer[:] = [(m, f) for m, f in buffer if m != event.message_id]
        return True

    async def _persist(self, events: Sequence[StreamEvent]) -> None:
        """
        One transaction, three statements (bulk INSERT checkouts, bulk INSERT answers, bulk UPDATE),
        however many events the batch contains: this is what divides the number of disk syncs.
        """
        checkouts: dict[UUID, schemas_tickets.CheckoutBulkRow] = {}
        answers: dict[UUID, schemas_tickets.AnswerBulkRow] = {}
        confirmed: set[UUID] = set()
        released: set[UUID] = set()

        for event in events:
            if event.action in (StreamAction.HOLD, StreamAction.CONFIRM):
                paid = event.action == StreamAction.CONFIRM
                row = checkouts.get(event.checkout_id)
                if row is None:
                    checkouts[event.checkout_id] = schemas_tickets.CheckoutBulkRow(
                        id=event.checkout_id,
                        event_id=event.payload.event_id,
                        category_id=event.payload.category_id,
                        session_id=event.payload.session_id,
                        user_id=event.user_id,
                        price=event.payload.price,
                        expiration=datetime.fromtimestamp(event.expires_at, tz=UTC),
                        paid=paid,
                    )
                elif paid:
                    # HOLD and CONFIRM of the same checkout in one batch: a single row, paid.
                    # (ON CONFLICT DO UPDATE can not touch the same row twice in one statement.)
                    row.paid = True
                for answer in event.payload.answers:
                    answers[answer.id] = schemas_tickets.AnswerBulkRow(
                        id=answer.id,
                        question_id=answer.question_id,
                        checkout_id=event.checkout_id,
                        answer=answer.answer,
                    )
            if event.action == StreamAction.CONFIRM:
                confirmed.add(event.checkout_id)
            elif event.action == StreamAction.RELEASE:
                released.add(event.checkout_id)

        released -= (
            confirmed  # paid always wins over a RELEASE, whatever the processing order
        )

        async with self.session_factory() as db:
            # Order matters: answers reference checkouts. The release runs last so that a HOLD and
            # its RELEASE handled in the same batch end up released.
            await cruds_tickets.bulk_upsert_checkouts(list(checkouts.values()), db)
            await cruds_tickets.bulk_insert_answers(list(answers.values()), db)
            await cruds_tickets.bulk_release_checkouts(released, datetime.now(UTC), db)
            await (
                db.commit()
            )  # the only commit of the whole flow, and it is not on the request path


# ======================================================================================
# Step 4: the reaper
# ======================================================================================


class SchedulerProtocol(Protocol):
    """The part of the application Scheduler used here (see `get_scheduler`)."""

    async def queue_job_defer_to(
        self,
        job_function: Callable[..., Coroutine[Any, Any, Any]],
        job_id: str,
        defer_date: datetime,
        **kwargs: Any,
    ) -> Any: ...

    async def cancel_job(self, job_id: str) -> Any: ...


async def _release_expired_batch(
    redis_client: Redis,
    checkout_ids: Sequence[str],
    now_ts: int,
    *,
    _retried: bool = False,
) -> int:
    """
    Pipeline one EVALSHA RELEASE per checkout id (one Redis round trip for the whole batch) and
    return how many were actually released.

    Self-heals a `SCRIPT FLUSH` landing in the narrow window between the sweep's own
    `ensure_scripts_loaded` call and this pipeline: a NoScriptError among the pipelined results
    triggers exactly one reload-and-retry of the affected ids, within this same call, instead of
    silently leaving them in the ZSET for the next sweep (REAPER_INTERVAL_SECONDS later). The
    `_retried` guard bounds this to a single extra round trip, even if Redis keeps flushing.
    """
    pipe = redis_client.pipeline(transaction=False)
    for checkout_id in checkout_ids:
        pipe.evalsha(
            utils_redis_tickets.RELEASE_SCRIPT.sha,
            3,
            utils_redis_tickets.hold_key(checkout_id),
            utils_redis_tickets.EXPIRATIONS_ZSET_KEY,
            utils_redis_tickets.STREAM_KEY,
            checkout_id,
            now_ts,
            utils_redis_tickets.REAPER_GRACE_SECONDS,
        )
    results = await pipe.execute(raise_on_error=False)

    released = 0
    to_retry: list[str] = []
    for checkout_id, result in zip(checkout_ids, results, strict=True):
        if isinstance(result, NoScriptError):
            to_retry.append(checkout_id)
        elif isinstance(result, Exception):
            # Not a missing-script error: the hold stays in the ZSET, retried at the next sweep.
            logger.error("Reaper could not release hold %s: %r", checkout_id, result)
        elif to_str(result) == utils_redis_tickets.RELEASED:
            released += 1

    if to_retry:
        if _retried:
            # Reloading did not help (Redis itself unreachable, or flushed again mid-retry): give
            # up for this sweep. The ids are still in the ZSET, so the next sweep retries them.
            logger.error(
                "Reaper: %d holds still NOSCRIPT after a reload, deferring to the next sweep",
                len(to_retry),
            )
        else:
            await utils_redis_tickets.load_scripts(redis_client)
            released += await _release_expired_batch(
                redis_client,
                to_retry,
                now_ts,
                _retried=True,
            )
    return released


async def reap_expired_holds(
    redis_client: Redis,
    *,
    now: int | None = None,
    batch_size: int = REAPER_BATCH_SIZE,
) -> int:
    """
    Sweep the expiration ZSET (ZRANGEBYSCORE) and release every hold that was not paid in time.

    For each expired hold, ONE atomic Lua script does: INCRBY stock, DEL hold, ZREM, XADD RELEASE.
    Doing it in a script (rather than 4 Python calls) guarantees that a crash can never restore the
    stock twice or restore it without deleting the hold, and that a payment confirmed at the same
    instant is never released (CONFIRM removes the hold from the ZSET atomically as well).
    The sweep is idempotent: running it concurrently from several processes is safe.
    """
    await utils_redis_tickets.ensure_scripts_loaded(redis_client)
    now_ts = int(time.time()) if now is None else now
    released = 0

    for _ in range(REAPER_MAX_BATCHES_PER_SWEEP):
        expired = await redis_client.zrangebyscore(
            utils_redis_tickets.EXPIRATIONS_ZSET_KEY,
            "-inf",
            now_ts - utils_redis_tickets.REAPER_GRACE_SECONDS,
            start=0,
            num=batch_size,
        )
        if not expired:
            break

        released += await _release_expired_batch(
            redis_client,
            [to_str(checkout_id) for checkout_id in expired],
            now_ts,
        )

        if len(expired) < batch_size:
            break

    if released:
        logger.info("Tickets reaper released %d expired holds", released)
    return released


@dataclass
class _ReaperRuntime:
    redis_client: Redis | None = None
    scheduler: SchedulerProtocol | None = None


_reaper_runtime = _ReaperRuntime()


async def tickets_reaper_job(ctx: Any = None, **_kwargs: Any) -> None:
    """
    Recurring job executed by the Scheduler. It sweeps, then queues its own next run.

    Job kwargs must be serialisable, so the Redis client and the scheduler are taken from the
    runtime set by `start_tickets_background_workers`. If the Scheduler executes jobs in another
    process, call `configure_reaper_runtime` in that process startup hook.
    """
    runtime = _reaper_runtime
    try:
        if runtime.redis_client is not None:
            await reap_expired_holds(runtime.redis_client)
    except Exception:
        logger.exception("Tickets reaper sweep failed")
    finally:
        if runtime.scheduler is not None:
            try:
                await schedule_reaper(runtime.scheduler)
            except Exception:
                logger.exception("Could not queue the next tickets reaper run")


async def schedule_reaper(scheduler: SchedulerProtocol) -> None:
    """
    Queue the next sweep. The job id is derived from the run time, aligned on the interval:
    queuing the same tick twice (several API instances, restart...) is a harmless duplicate id,
    so there is always exactly one reaper chain and it can not multiply.
    """
    next_tick = (
        int(time.time()) // REAPER_INTERVAL_SECONDS + 1
    ) * REAPER_INTERVAL_SECONDS
    await scheduler.queue_job_defer_to(
        job_function=tickets_reaper_job,
        job_id=f"tickets_reaper_{next_tick}",
        defer_date=datetime.fromtimestamp(next_tick, tz=UTC),
    )


def configure_reaper_runtime(
    redis_client: Redis,
    scheduler: SchedulerProtocol | None,
) -> None:
    _reaper_runtime.redis_client = redis_client
    _reaper_runtime.scheduler = scheduler


async def _reaper_loop(redis_client: Redis, stop_event: asyncio.Event) -> None:
    """Fallback when there is no Scheduler (worker running in its own process)."""
    while not stop_event.is_set():
        try:
            await reap_expired_holds(redis_client)
        except Exception:
            logger.exception("Tickets reaper sweep failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=REAPER_INTERVAL_SECONDS)
        except TimeoutError:
            pass


# ======================================================================================
# Refresh-ahead of the event configuration cache
# ======================================================================================


async def _snapshot_refresh_loop(
    redis_client: Redis,
    session_factory: async_sessionmaker[AsyncSession],
    stop_event: asyncio.Event,
) -> None:
    """
    Reload the snapshots of the active events every few seconds, in the background.

    The snapshots have a short TTL so that admin changes (event disabled, dates...) are taken into
    account quickly. Refreshing them here, before they expire, means the checkout endpoint never
    has to read PostgreSQL to rebuild them in the middle of a flash sale, and an admin change is
    visible after SNAPSHOT_REFRESH_INTERVAL_SECONDS without any change in the admin endpoints.
    Several instances can run this loop: the warm-up is single-flight per event.
    """
    while True:
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=SNAPSHOT_REFRESH_INTERVAL_SECONDS,
            )
            return
        except TimeoutError:
            pass
        try:
            async with session_factory() as db:
                await utils_redis_tickets.warm_up_active_events(redis_client, db)
        except Exception:
            logger.exception("Tickets: snapshot refresh failed")


# ======================================================================================
# Startup / shutdown wiring
# ======================================================================================


@dataclass
class TicketsBackgroundWorkers:
    worker: TicketsWriteBehindWorker
    stop_event: asyncio.Event
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def stop(self, timeout: float = 15.0) -> None:
        self.stop_event.set()
        _done, pending = await asyncio.wait(self.tasks, timeout=timeout)
        for task in pending:
            task.cancel()


async def start_tickets_background_workers(
    *,
    redis_client: Redis,
    session_factory: async_sessionmaker[AsyncSession],
    scheduler: SchedulerProtocol | None = None,
) -> TicketsBackgroundWorkers:
    """
    Call it once in the application startup event, after the Redis client and the database exist.

    1. SCRIPT LOAD of the Lua scripts (the endpoint then only uses EVALSHA)
    2. pre-warm the open / upcoming events (configuration snapshot + stock counters)
    3. start the write-behind worker as a background task
    4. keep the snapshots warm in the background (refresh-ahead)
    5. start the reaper (recurring Scheduler job, or a plain loop when no Scheduler is given)
    """
    await utils_redis_tickets.init_tickets_redis(redis_client)

    try:
        async with session_factory() as db:
            nb_events = await utils_redis_tickets.warm_up_active_events(
                redis_client,
                db,
            )
        logger.info("Tickets: %d events pre-warmed in Redis", nb_events)
    except Exception:
        # Not fatal: the endpoint falls back to a single-flight warm-up on first use.
        logger.exception("Tickets: pre-warming failed")

    worker = TicketsWriteBehindWorker(redis_client, session_factory)
    stop_event = asyncio.Event()
    handle = TicketsBackgroundWorkers(worker=worker, stop_event=stop_event)
    handle.tasks.append(
        asyncio.create_task(worker.run(stop_event), name="tickets-write-behind"),
    )
    handle.tasks.append(
        asyncio.create_task(
            _snapshot_refresh_loop(redis_client, session_factory, stop_event),
            name="tickets-snapshot-refresh",
        ),
    )

    configure_reaper_runtime(redis_client, scheduler)
    if scheduler is not None:
        await schedule_reaper(scheduler)
    else:
        handle.tasks.append(
            asyncio.create_task(
                _reaper_loop(redis_client, stop_event),
                name="tickets-reaper",
            ),
        )
    return handle

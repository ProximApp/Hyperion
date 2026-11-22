import asyncio
import hashlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from redis.asyncio import Redis
from redis.exceptions import NoScriptError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tickets import cruds_tickets, schemas_tickets
from app.core.tickets.schemas_tickets import ReservationResult
from app.core.tickets.types_tickets import EventRedisKeys, LuaResult, StreamAction
from app.core.tickets.utils_redis_tickets import (
    hold_key,
    snapshot_key,
    stock_key_category,
    stock_key_event,
    stock_key_session,
    to_str,
    user_hold_key,
    warmup_lock_key,
)
from app.dependencies import get_redis_client
from app.types.exceptions import RedisScriptShaMismatchError

hyperion_error_logger = logging.getLogger("hyperion.error")


@dataclass(frozen=True)
class LuaScript:
    name: str
    body: str
    sha: str  # SHA1 of the body, identical to what SCRIPT LOAD returns

    @classmethod
    def register(cls, name: str, body: str) -> "LuaScript":
        return cls(name=name, body=body, sha=hashlib.sha1(body.encode()).hexdigest())  # noqa: S324 (not a security use)


class RedisEventTool:
    """Redis operations for the tickets module, without any PostgreSQL access.

        Redis "fast path" of the tickets module.

    Redis is the transactional source of truth for stock and pending holds.
    PostgreSQL is an asynchronous projection of it (write-behind):

        HTTP request
           | 1. EVALSHA reserve   (atomic: check 3 stocks, decrement, create the hold, register its expiration)
           | 2. XADD action=HOLD  (event sourcing)
           v
        HTTP response  ---> no PostgreSQL I/O on this path

        payment callback  -> EVALSHA confirm -> XADD action=CONFIRM   (atomic with the state change)
        reaper (periodic) -> EVALSHA release -> XADD action=RELEASE   (atomic with the stock restore)

        worker_tickets.py: XREADGROUP -> batch (N events / T seconds) -> ONE bulk SQL transaction -> XACK

    Redis keys
        tickets:stock:{event|category|session}:<id>  int     remaining stock (absent = never initialised)
        tickets:hold:<checkout_id>                   hash    the hold (status pending|paid, expiration, stock keys, payload)
        tickets:user_hold:<event_id>:<user_id>       string  guard: at most one pending hold per user and per event
        tickets:holds:expirations                    zset    checkout_id -> expiration timestamp (scanned by the reaper)
        tickets:stream:checkouts                     stream  HOLD / CONFIRM / RELEASE events (group: write-behind)
        tickets:event:<event_id>:snapshot            string  JSON copy of the event configuration (no PostgreSQL read)

    Every event of the stream carries the FULL checkout payload (not a delta). The database writes done by
    the worker are therefore idempotent and commutative, which makes at-least-once delivery and
    out-of-order processing between several consumers safe:
        - a CONFIRM processed before its HOLD creates the (paid) row itself,
        - a paid checkout is never downgraded by a late RELEASE ("paid always wins").
    """

    # --------------------------------------------------------------------------------------
    # Tunables
    # --------------------------------------------------------------------------------------

    # How long a user has to pay before the hold is reclaimed.
    #
    # CONFIRMED OPERATIONAL INVARIANT: this value is set (by configuration, outside this code) with a
    # safety margin over MyPayment's own payment-window timeout, so a payment can never be confirmed
    # after this TTL has elapsed. Concretely: payment_request_info.end_date <= now + CHECKOUT_HOLD_TTL_SECONDS,
    # always. There is therefore no need to ever push a hold's expiration further out to "catch up" with
    # the payment provider's deadline (an `extend_hold` EVALSHA call used to sit on the checkout's critical
    # path for exactly that purpose; it was removed as dead weight once this invariant was confirmed - it
    # could never have extended anything, so it was only an extra Redis round trip and an extra failure
    # surface). If the relationship between this constant and MyPayment's window ever changes operationally
    # (e.g. a slower payment method is added), this constant must be raised accordingly.
    CHECKOUT_HOLD_TTL_SECONDS = 15 * 60
    # The Redis TTL of a hold hash is only a memory safety net (ttl + margin). It must be larger than
    # any realistic reaper downtime: if the hash disappeared before the reaper ran, its stock would leak.
    HOLD_KEY_TTL_MARGIN_SECONDS = 60 * 60
    # A confirmed hold is kept for a while so that a duplicated payment callback stays idempotent.
    CONFIRMED_HOLD_RETENTION_SECONDS = 24 * 60 * 60
    # The reaper only releases holds that expired at least this long ago (clock skew, late callbacks).
    REAPER_GRACE_SECONDS = 30
    # Event configuration cache. Short TTL: admin changes (disabled, dates...) are visible after at most this delay.
    SNAPSHOT_TTL_SECONDS = 30
    WARMUP_LOCK_TTL_SECONDS = 30
    WARMUP_WAIT_SECONDS = 3.0

    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client

    # --------------------------------------------------------------------------------------
    # Lua scripts. They are loaded once at startup (SCRIPT LOAD) and called with EVALSHA.
    # Each script is atomic: Redis runs it without interleaving any other command.
    # --------------------------------------------------------------------------------------

    _RESERVE_LUA = """
    -- Atomic reservation ("hold") of requested_qty tickets.
    -- KEYS[1] event stock      KEYS[2] category stock     KEYS[3] session stock
    -- KEYS[4] user_hold_key    guard: at most one pending hold per user and per event
    -- KEYS[5] held_key         hash describing the hold (addressed by checkout id)
    -- KEYS[6] exp_zset_key     ZSET: checkout id -> hold expiration timestamp
    local event_stock_key    = KEYS[1]
    local category_stock_key = KEYS[2]
    local session_stock_key  = KEYS[3]
    local user_hold_key      = KEYS[4]
    local held_key           = KEYS[5]
    local exp_zset_key       = KEYS[6]

    local requested_qty     = tonumber(ARGV[1])
    local user_id           = ARGV[2]
    local current_timestamp = tonumber(ARGV[3])
    local ttl_seconds       = tonumber(ARGV[4])
    local checkout_id       = ARGV[5]
    local key_ttl_margin    = tonumber(ARGV[6])
    local payload           = ARGV[7]
    -- ARGV[8..10]: "1" if the event / category / session has a quota ("0" = unlimited)
    local limited    = { ARGV[8] == "1", ARGV[9] == "1", ARGV[10] == "1" }
    local stock_keys = { event_stock_key, category_stock_key, session_stock_key }
    local labels     = { "EVENT", "CATEGORY", "SESSION" }

    if redis.call("EXISTS", user_hold_key) == 1 then return "USER_ALREADY_HAS_RESERVATION" end

    -- 1) Check every limited stock before touching anything (all-or-nothing).
    for i = 1, 3 do
    if limited[i] then
        local raw = redis.call("GET", stock_keys[i])
        if raw == false then return "STOCK_NOT_INITIALIZED:" .. labels[i] end
        if tonumber(raw) < requested_qty then return "SOLD_OUT_OR_INSUFFICIENT_STOCK:" .. labels[i] end
    end
    end

    -- 2) Decrement, remembering which counters were touched so RELEASE can give the stock back.
    local touched = { "", "", "" }
    for i = 1, 3 do
    if limited[i] then
        redis.call("DECRBY", stock_keys[i], requested_qty)
        touched[i] = stock_keys[i]
    end
    end

    -- 3) Record the hold, its TTL, and register it in the expiration ZSET.
    local expires_at = current_timestamp + ttl_seconds
    local key_ttl = ttl_seconds + key_ttl_margin
    redis.call("HSET", held_key,
    "qty", requested_qty, "timestamp", current_timestamp, "user_id", user_id,
    "checkout_id", checkout_id, "status", "pending", "expires_at", expires_at,
    "user_hold_key", user_hold_key, "stock_event", touched[1], "stock_category", touched[2],
    "stock_session", touched[3], "payload", payload)
    redis.call("EXPIRE", held_key, key_ttl)
    redis.call("SET", user_hold_key, checkout_id, "EX", key_ttl)
    redis.call("ZADD", exp_zset_key, expires_at, checkout_id)
    return "RESERVED"
    """

    _CONFIRM_LUA = """
    -- Payment received: the hold becomes a definitive ticket. The stock stays consumed.
    -- KEYS[1] held_key  KEYS[2] exp_zset_key  KEYS[3] stream_key
    -- ARGV[1] checkout_id  ARGV[2] current_timestamp  ARGV[3] confirmed_retention_seconds
    local held_key, exp_zset_key, stream_key = KEYS[1], KEYS[2], KEYS[3]
    if redis.call("EXISTS", held_key) == 0 then return "HOLD_NOT_FOUND" end
    if redis.call("HGET", held_key, "status") == "paid" then return "ALREADY_CONFIRMED" end

    local f = redis.call("HMGET", held_key, "user_id", "qty", "expires_at", "user_hold_key", "payload")
    redis.call("HSET", held_key, "status", "paid")
    redis.call("EXPIRE", held_key, tonumber(ARGV[3]))
    -- Out of the ZSET: the reaper will never give this stock back.
    redis.call("ZREM", exp_zset_key, ARGV[1])
    if redis.call("GET", f[4]) == ARGV[1] then redis.call("DEL", f[4]) end
    -- Published in the same atomic step as the state change: no dual-write gap.
    redis.call("XADD", stream_key, "*",
    "action", "CONFIRM", "checkout_id", ARGV[1], "user_id", f[1], "qty", f[2],
    "ts", ARGV[2], "expires_at", f[3], "payload", f[5])
    return "CONFIRMED"
    """

    _RELEASE_LUA = """
    -- Give the stock of an abandoned hold back (reaper, or compensation when the payment request failed).
    -- KEYS[1] held_key  KEYS[2] exp_zset_key  KEYS[3] stream_key
    -- ARGV[1] checkout_id  ARGV[2] current_timestamp
    -- ARGV[3] grace_seconds: only release if expires_at + grace <= now; a negative value releases unconditionally
    local held_key, exp_zset_key, stream_key = KEYS[1], KEYS[2], KEYS[3]
    local checkout_id       = ARGV[1]
    local current_timestamp = tonumber(ARGV[2])
    local grace             = tonumber(ARGV[3])

    if redis.call("EXISTS", held_key) == 0 then
    redis.call("ZREM", exp_zset_key, checkout_id)  -- orphan ZSET member
    return "HOLD_NOT_FOUND"
    end
    local f = redis.call("HMGET", held_key, "status", "user_id", "qty", "expires_at", "user_hold_key",
                        "payload", "stock_event", "stock_category", "stock_session")
    if f[1] ~= "pending" then
    redis.call("ZREM", exp_zset_key, checkout_id)
    return "NOT_PENDING"  -- a paid hold is never released
    end
    -- Re-checked here (not only in the reaper's ZRANGEBYSCORE): the hold may have been extended or confirmed meanwhile.
    if grace >= 0 and tonumber(f[4]) + grace > current_timestamp then return "NOT_EXPIRED" end

    local qty = tonumber(f[3])
    for i = 7, 9 do
    if f[i] and f[i] ~= "" then redis.call("INCRBY", f[i], qty) end
    end
    redis.call("DEL", held_key)
    redis.call("ZREM", exp_zset_key, checkout_id)
    if redis.call("GET", f[5]) == checkout_id then redis.call("DEL", f[5]) end
    redis.call("XADD", stream_key, "*",
    "action", "RELEASE", "checkout_id", checkout_id, "user_id", f[2], "qty", f[3],
    "ts", ARGV[2], "expires_at", f[4], "payload", f[6])
    return "RELEASED"
    """

    RESERVE_SCRIPT = LuaScript.register("Reserve", _RESERVE_LUA)
    CONFIRM_SCRIPT = LuaScript.register("Confirm", _CONFIRM_LUA)
    RELEASE_SCRIPT = LuaScript.register("Release", _RELEASE_LUA)
    ALL_SCRIPTS = (RESERVE_SCRIPT, CONFIRM_SCRIPT, RELEASE_SCRIPT)

    async def load_scripts(self) -> None:
        """SCRIPT LOAD every Lua script. Called once in the application startup event."""
        for script in self.ALL_SCRIPTS:
            sha = to_str(await self.redis_client.script_load(script.body))
            if sha != script.sha:
                raise RedisScriptShaMismatchError(
                    name=script.name,
                    received_sha=sha,
                    expected_sha=script.sha,
                )

    async def ensure_scripts_loaded(self) -> None:
        """Reload the scripts if Redis lost them (restart, SCRIPT FLUSH). Cheap: one SCRIPT EXISTS."""
        exists = await self.redis_client.script_exists(
            *(script.sha for script in self.ALL_SCRIPTS),
        )
        if not all(exists):
            await self.load_scripts()

    async def run_script(
        self,
        script: LuaScript,
        keys: Sequence[str],
        args: Sequence[Any],
    ) -> str:
        """EVALSHA with a one-shot reload if the script cache was flushed."""
        try:
            result = await self.redis_client.evalsha(
                script.sha,
                len(keys),
                *keys,
                *args,
            )
        except NoScriptError:
            await self.load_scripts()
            result = await self.redis_client.evalsha(
                script.sha,
                len(keys),
                *keys,
                *args,
            )
        return to_str(result)

    async def init_tickets_redis(self) -> None:
        """
        To be called in the application startup event.
        Loads the Lua scripts and registers the client for code that has no FastAPI dependency
        injection (the MyPayment callback, which has a fixed signature).
        """
        await self.load_scripts()

    # --------------------------------------------------------------------------------------
    # Fast path operations (RAM only)
    # --------------------------------------------------------------------------------------

    async def reserve(
        self,
        *,
        event: schemas_tickets.EventComplete,
        category: schemas_tickets.CategoryComplete,
        session: schemas_tickets.SessionComplete,
        user_id: str,
        checkout_id: UUID,
        payload_json: str,
        requested_qty: int = 1,
    ) -> ReservationResult:
        """
        Atomically check the event / category / session stocks, decrement them and create the hold.
        A single EVALSHA round trip, no PostgreSQL access.
        """
        now_ts = int(time.time())
        raw = await self.run_script(
            self.RESERVE_SCRIPT,
            keys=[
                stock_key_event(event.id),
                stock_key_category(category.id),
                stock_key_session(session.id),
                user_hold_key(event.id, user_id),
                hold_key(checkout_id),
                EventRedisKeys.EXPIRATIONS_ZSET_KEY,
            ],
            args=[
                requested_qty,
                user_id,
                now_ts,
                self.CHECKOUT_HOLD_TTL_SECONDS,
                str(checkout_id),
                self.HOLD_KEY_TTL_MARGIN_SECONDS,
                payload_json,
                "1" if event.quota is not None else "0",
                "1" if category.quota is not None else "0",
                "1" if session.quota is not None else "0",
            ],
        )
        status, _, dimension = raw.partition(":")
        return ReservationResult(
            status=status,
            dimension=dimension or None,
            expires_at=now_ts + self.CHECKOUT_HOLD_TTL_SECONDS,
        )

    async def confirm_hold(
        self,
        checkout_id: UUID,
        raise_on_fail: bool = False,
    ) -> str:
        """Payment received. Atomically marks the hold as paid and publishes the CONFIRM event."""
        status = await self.run_script(
            self.CONFIRM_SCRIPT,
            keys=[
                hold_key(checkout_id),
                EventRedisKeys.EXPIRATIONS_ZSET_KEY,
                EventRedisKeys.STREAM_KEY,
            ],
            args=[
                str(checkout_id),
                int(time.time()),
                self.CONFIRMED_HOLD_RETENTION_SECONDS,
            ],
        )

        if raise_on_fail and status not in (
            LuaResult.CONFIRMED,
            LuaResult.ALREADY_CONFIRMED,
        ):
            raise HTTPException(503, "Reservation lost, please retry")
        return status

    async def release_hold(
        self,
        checkout_id: UUID | str,
        *,
        grace_seconds: int = -1,
        now: int | None = None,
    ) -> str:
        """
        Atomically restore the stock, delete the hold and publish the RELEASE event.
        `grace_seconds < 0` releases unconditionally (compensation); otherwise the hold is only
        released if it expired at least `grace_seconds` ago (reaper).
        """
        return await self.run_script(
            self.RELEASE_SCRIPT,
            keys=[
                hold_key(checkout_id),
                EventRedisKeys.EXPIRATIONS_ZSET_KEY,
                EventRedisKeys.STREAM_KEY,
            ],
            args=[
                str(checkout_id),
                now if now is not None else int(time.time()),
                grace_seconds,
            ],
        )

    async def publish_hold_event(
        self,
        *,
        checkout_id: UUID,
        user_id: str,
        qty: int,
        expires_at: int,
        payload_json: str,
    ) -> None:
        """
        Event sourcing: publish `ACTION: HOLD` so that the write-behind worker persists the checkout.

        This XADD is the only non-atomic step (it happens after the payment request, because the event
        carries the final expiration). If it is lost, nothing is corrupted: the CONFIRM event published
        by the Lua script carries the same full payload and creates the row itself, and an abandoned hold
        is simply released by the reaper.
        """
        try:
            await self.redis_client.xadd(
                EventRedisKeys.STREAM_KEY,
                {
                    "action": StreamAction.HOLD.value,
                    "checkout_id": str(checkout_id),
                    "user_id": user_id,
                    "qty": qty,
                    "ts": int(time.time()),
                    "expires_at": expires_at,
                    "payload": payload_json,
                },
            )
        except Exception:
            hyperion_error_logger.exception(
                "Could not publish the HOLD event of checkout",
                extra={"checkout_id": checkout_id},
            )

    # --------------------------------------------------------------------------------------
    # Event configuration cache + stock initialisation (cold path: the only place that reads PostgreSQL)
    # --------------------------------------------------------------------------------------

    async def _read_snapshot(
        self,
        event_id: UUID,
    ) -> schemas_tickets.EventComplete | None:
        raw = await self.redis_client.get(snapshot_key(event_id))
        if raw is None:
            return None
        return schemas_tickets.EventComplete.model_validate_json(raw)

    async def get_event_snapshot(
        self,
        event_id: UUID,
        db: AsyncSession,
    ) -> schemas_tickets.EventComplete | None:
        """
        Event, sessions, categories and questions, served from Redis (RAM).
        PostgreSQL is only read on a cold cache: it is pre-warmed at startup and refreshed every
        SNAPSHOT_TTL_SECONDS by a single request (single-flight lock), never by 50 000 at once.
        """
        snapshot = await self._read_snapshot(event_id)
        if snapshot is not None:
            return snapshot
        return await self.warm_up_event(event_id=event_id, db=db)

    async def invalidate_event_snapshot(self, event_id: UUID) -> None:
        """To be called by admin endpoints after they modify an event, to make the change immediate."""
        await self.redis_client.delete(snapshot_key(event_id))

    async def _initialize_counter(
        self,
        key: str,
        quota: int | None,
        count_used: Any,
    ) -> None:
        if quota is None or await self.redis_client.exists(key):
            return
        used = await count_used()
        # NX: never overwrite a counter that already contains live reservations.
        await self.redis_client.set(key, max(quota - used, 0), nx=True)

    async def _initialize_stock_counters(
        self,
        event: schemas_tickets.EventComplete,
        db: AsyncSession,
    ) -> None:
        """remaining stock = quota - (paid tickets + non expired unpaid checkouts already in PostgreSQL)"""
        await self._initialize_counter(
            stock_key_event(event.id),
            event.quota,
            partial(
                cruds_tickets.count_valid_checkouts_and_tickets_by_event_id,
                event_id=event.id,
                db=db,
            ),
        )
        for category in event.categories:
            await self._initialize_counter(
                stock_key_category(category.id),
                category.quota,
                partial(
                    cruds_tickets.count_valid_checkouts_and_tickets_by_category_id,
                    category_id=category.id,
                    db=db,
                ),
            )
        for session in event.sessions:
            await self._initialize_counter(
                stock_key_session(session.id),
                session.quota,
                partial(
                    cruds_tickets.count_valid_checkouts_and_tickets_by_session_id,
                    session_id=session.id,
                    db=db,
                ),
            )

    async def warm_up_event(
        self,
        event_id: UUID,
        db: AsyncSession,
    ) -> schemas_tickets.EventComplete | None:
        """
        (Re)load the event configuration from PostgreSQL into Redis and initialise the missing stock counters.

        Single-flight: when 50 000 requests hit a cold cache at once, exactly one of them reads PostgreSQL,
        the others wait for it (bounded) instead of stampeding the database.
        """
        lock_key = warmup_lock_key(event_id)
        acquired = await self.redis_client.set(
            lock_key,
            "1",
            nx=True,
            ex=self.WARMUP_LOCK_TTL_SECONDS,
        )
        if not acquired:
            deadline = time.monotonic() + self.WARMUP_WAIT_SECONDS
            # This polls a DISTRIBUTED lock in Redis, not a local in-process condition: it may be
            # released by a coroutine in *this* process, but just as well by a different instance of
            # this API on another machine. asyncio.Event only wakes waiters within the same event
            # loop, so it cannot observe a remote process deleting this key - ASYNC110 does not apply
            # here, it assumes the condition lives in local memory.
            while time.monotonic() < deadline and await self.redis_client.exists(  # noqa: ASYNC110
                lock_key,
            ):
                await asyncio.sleep(0.05)
            snapshot = await self._read_snapshot(event_id)
            if snapshot is None:
                raise HTTPException(
                    503,
                    "Event data is being loaded, please retry",
                    headers={"Retry-After": "1"},
                )
            return snapshot

        try:
            event = await cruds_tickets.get_event_complete_by_id(
                event_id=event_id,
                db=db,
            )
            if event is None:
                return None
            # Counters first, snapshot last: once the snapshot is visible, the stock is guaranteed to exist.
            await self._initialize_stock_counters(event, db)
            await self.redis_client.set(
                snapshot_key(event_id),
                event.model_dump_json(),
                ex=self.SNAPSHOT_TTL_SECONDS,
            )
            return event
        finally:
            await self.redis_client.delete(lock_key)

    async def warm_up_active_events(self, db: AsyncSession) -> int:
        """Pre-warm every open or upcoming event at startup, before the first flash sale request."""
        event_ids = await cruds_tickets.get_active_event_ids(db=db)
        for event_id in event_ids:
            try:
                await self.warm_up_event(event_id=event_id, db=db)
            except HTTPException:
                # Another instance is warming this event up right now.
                continue
        return len(event_ids)


def get_redis_event_tool(
    redis_client: Redis | None = Depends(get_redis_client),
) -> RedisEventTool:
    if redis_client is None:
        raise HTTPException(503, "Redis is not configured")
    return RedisEventTool(redis_client)

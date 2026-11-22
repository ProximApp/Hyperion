import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from redis.asyncio import Redis
from redis.commands.core import AsyncScript
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.checkout.utils_checkout import CHECKOUT_EXPIRATION
from app.core.mypayment.exceptions_mypayment import InvalidRequestTypeError
from app.core.mypayment.types_mypayment import REQUEST_EXPIRATION, RequestType
from app.core.tickets import cruds_tickets, schemas_tickets
from app.core.tickets.exceptions_tickets import (
    InvalidRedisStateError,
    InvalidTTLValueError,
    RedisCheckoutConfirmationError,
    RedisCheckoutConfirmationUnableError,
    RedisCheckoutConfirmPublishError,
)
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


class RedisEventTool:
    """Redis operations for the tickets module, without any PostgreSQL access on the fast path.

        Redis "fast path" of the tickets module.

    Redis is the transactional source of truth for stock and pending holds.
    PostgreSQL is an asynchronous projection of it (write-behind):

        HTTP request
           | 1. registered Lua script   (atomic: check 3 stocks, decrement, create the hold, register its expiration)
           | HOLD event is appended inside the same Lua script
           v
        HTTP response  ---> no PostgreSQL I/O on this path

        payment callback  -> registered Lua script -> XADD action=CONFIRM   (atomic with the state change)
        reaper (periodic) -> registered Lua script -> XADD action=RELEASE   (atomic with the stock restore)

        worker_tickets.py: XREADGROUP -> batch (N events / T seconds) -> ONE bulk SQL transaction -> XACK

    Redis keys
        tickets:stock:{event|category|session}:<id>  int     remaining stock (absent = never initialised)
        tickets:hold:<checkout_id>                   hash    the hold (status pending|paid, expiration, stock keys, payload)
        tickets:user_hold:<event_id>:<user_id>       string  guard: at most one pending hold per user and per event
        tickets:holds:expirations                    zset    checkout_id -> expiration timestamp (scanned by the reaper)
        tickets:stream:checkouts                     stream  HOLD / CONFIRM / RELEASE events (group: write-behind)
        tickets:event:<event_id>:snapshot            string  JSON copy of the event configuration (no PostgreSQL read)
        tickets:events:active_ids                    string  JSON list of active (non-disabled, non-closed) event IDs
    """

    # --------------------------------------------------------------------------------------
    # Tunables
    # --------------------------------------------------------------------------------------

    PAYMENT_CALLBACK_MAX_DELAY_SECONDS = 5 * 60
    PAYMENT_REQUEST_CREATION_MARGIN_SECONDS = 5 * 60
    PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS = 2 * 60
    CHECKOUT_HOLD_TTL_SECONDS = (
        15 * 60
        + PAYMENT_CALLBACK_MAX_DELAY_SECONDS
        + PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS
    )
    HOLD_KEY_TTL_MARGIN_SECONDS = 60 * 60
    CONFIRMED_HOLD_RETENTION_SECONDS = 24 * 60 * 60
    RELEASED_HOLD_RETENTION_SECONDS = 24 * 60 * 60
    REAPER_GRACE_SECONDS = 2 * 60
    SNAPSHOT_TTL_SECONDS = 30
    WARMUP_LOCK_TTL_SECONDS = 30
    WARMUP_WAIT_SECONDS = 3.0

    ACTIVE_EVENTS_CACHE_KEY = "tickets:events:active_ids"

    # --------------------------------------------------------------------------------------
    # Lua scripts
    # --------------------------------------------------------------------------------------

    _RESERVE_LUA = """
    -- Atomic reservation plus HOLD publication.
    -- KEYS[1..3] event/category/session stock; KEYS[4] user guard; KEYS[5] hold hash;
    -- KEYS[6] expiration ZSET; KEYS[7] stream.
    local event_stock_key, category_stock_key, session_stock_key = KEYS[1], KEYS[2], KEYS[3]
    local user_hold_key, held_key, exp_zset_key, stream_key = KEYS[4], KEYS[5], KEYS[6], KEYS[7]
    local requested_qty, user_id = tonumber(ARGV[1]), ARGV[2]
    local current_timestamp, ttl_seconds = tonumber(ARGV[3]), tonumber(ARGV[4])
    local checkout_id, key_ttl_margin, payload = ARGV[5], tonumber(ARGV[6]), ARGV[7]
    local limited = { ARGV[8] == "1", ARGV[9] == "1", ARGV[10] == "1" }
    local stock_keys = { event_stock_key, category_stock_key, session_stock_key }
    local labels = { "EVENT", "CATEGORY", "SESSION" }

    local function keytype(key)
        local t = redis.call("TYPE", key)
        if type(t) == "table" then return t.ok end
        return t
    end
    -- Validate key types before the first mutation; Lua scripts are atomic but do NOT roll back
    -- earlier writes when a later Redis command errors.
    for i = 1, 3 do
        local t = keytype(stock_keys[i])
        if t ~= "none" and t ~= "string" then return "REDIS_STATE_ERROR:stock_type_" .. labels[i] end
    end
    local user_type, hold_type = keytype(user_hold_key), keytype(held_key)
    local exp_type, stream_type = keytype(exp_zset_key), keytype(stream_key)
    if user_type ~= "none" and user_type ~= "string" then return "REDIS_STATE_ERROR:user_guard_type" end
    if hold_type ~= "none" then return "REDIS_STATE_ERROR:hold_key_already_exists" end
    if exp_type ~= "none" and exp_type ~= "zset" then return "REDIS_STATE_ERROR:expirations_type" end
    if stream_type ~= "none" and stream_type ~= "stream" then return "REDIS_STATE_ERROR:stream_type" end

    if redis.call("EXISTS", user_hold_key) == 1 then return "USER_ALREADY_HAS_RESERVATION" end
    for i = 1, 3 do
        if limited[i] then
            local raw = redis.call("GET", stock_keys[i])
            if raw == false then return "STOCK_NOT_INITIALIZED:" .. labels[i] end
            if tonumber(raw) < requested_qty then return "SOLD_OUT_OR_INSUFFICIENT_STOCK:" .. labels[i] end
        end
    end

    for i = 1, 3 do
        if limited[i] then
            redis.call("DECRBY", stock_keys[i], requested_qty)
        end
    end

    local expires_at = current_timestamp + ttl_seconds
    local key_ttl = ttl_seconds + key_ttl_margin
    redis.call("HSET", held_key,
        "qty", requested_qty, "timestamp", current_timestamp, "user_id", user_id,
        "checkout_id", checkout_id, "status", "pending", "expires_at", expires_at,
        "user_hold_key", user_hold_key,
        "stock_event", stock_keys[1],
        "stock_category", stock_keys[2],
        "stock_session", stock_keys[3],
        "payload", payload,
        "confirm_event_published", "0", "release_event_published", "0",
        "released_stock_applied", "0", "late_confirmed", "0")
    redis.call("EXPIRE", held_key, key_ttl)
    redis.call("SET", user_hold_key, checkout_id, "EX", key_ttl)
    redis.call("ZADD", exp_zset_key, expires_at, checkout_id)
    -- HOLD is emitted inside the reservation script: there is no request-path dual-write gap.
    redis.call("XADD", stream_key, "*",
        "action", "HOLD", "checkout_id", checkout_id, "user_id", user_id,
        "qty", requested_qty, "ts", current_timestamp, "expires_at", expires_at,
        "payload", payload)
    return "RESERVED"
    """

    _CONFIRM_LUA = """
    -- Idempotently confirm a hold; repair a previous missing CONFIRM XADD on retries.
    -- A released hold is retained as a tombstone so a late, real payment can still be represented.
    local held_key, exp_zset_key, stream_key = KEYS[1], KEYS[2], KEYS[3]
    local checkout_id, now_ts, retention = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
    if redis.call("EXISTS", held_key) == 0 then return "HOLD_NOT_FOUND" end
    local status = redis.call("HGET", held_key, "status")
    local late = redis.call("HGET", held_key, "late_confirmed") == "1"
    if status == "paid" then
        if redis.call("HGET", held_key, "confirm_event_published") ~= "1" then
            local f = redis.call("HMGET", held_key, "user_id", "qty", "expires_at", "payload")
            local result = redis.pcall("XADD", stream_key, "*",
                "action", "CONFIRM", "checkout_id", checkout_id, "user_id", f[1],
                "qty", f[2], "ts", now_ts, "expires_at", f[3], "payload", f[4])
            if type(result) == "table" and result.err then return "CONFIRM_PUBLISH_FAILED" end
            redis.call("HSET", held_key, "confirm_event_published", "1")
        end
        if late then return "CONFIRMED_LATE" end
        return "ALREADY_CONFIRMED"
    end
    if status ~= "pending" and status ~= "released" then return "HOLD_NOT_FOUND" end

    local function keytype(key)
        local t = redis.call("TYPE", key)
        if type(t) == "table" then return t.ok end
        return t
    end

    local was_released = status == "released"
    local f = redis.call("HMGET", held_key, "user_id", "qty", "expires_at", "user_hold_key", "payload",
        "stock_event", "stock_category", "stock_session", "released_stock_applied")

    if was_released and f[9] == "1" then
        for i = 6, 8 do
            if f[i] and f[i] ~= "" then
                local t = keytype(f[i])
                if t ~= "none" and t ~= "string" then return "CONFIRM_PUBLISH_FAILED" end
            end
        end
        -- Reclaim the stock that was restored when the hold was released.
        redis.call("HSET", held_key, "released_stock_applied", "0")
        local qty = tonumber(f[2])
        for i = 6, 8 do
            if f[i] and f[i] ~= "" then
                if redis.call("EXISTS", f[i]) == 1 then
                    redis.call("DECRBY", f[i], qty)
                end
            end
        end
    end

    -- If it was released, payment is authoritative but stock may already have been sold again.
    redis.call("HSET", held_key, "status", "paid", "late_confirmed", was_released and "1" or "0")
    redis.call("EXPIRE", held_key, retention)
    redis.call("ZREM", exp_zset_key, checkout_id)
    if f[4] and f[4] ~= "" and redis.call("GET", f[4]) == checkout_id then redis.call("DEL", f[4]) end
    local result = redis.pcall("XADD", stream_key, "*",
        "action", "CONFIRM", "checkout_id", checkout_id, "user_id", f[1],
        "qty", f[2], "ts", now_ts, "expires_at", f[3], "payload", f[5])
    if type(result) == "table" and result.err then return "CONFIRM_PUBLISH_FAILED" end
    redis.call("HSET", held_key, "confirm_event_published", "1")
    if was_released then return "CONFIRMED_LATE" end
    return "CONFIRMED"
    """

    _RELEASE_LUA = """
    -- Release a pending hold once. Keep a tombstone and retry stream publication when necessary.
    local held_key, exp_zset_key, stream_key = KEYS[1], KEYS[2], KEYS[3]
    local checkout_id, now_ts, grace = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
    if redis.call("EXISTS", held_key) == 0 then
        redis.call("ZREM", exp_zset_key, checkout_id)
        return "HOLD_NOT_FOUND"
    end
    local f = redis.call("HMGET", held_key, "status", "user_id", "qty", "expires_at",
        "user_hold_key", "payload", "stock_event", "stock_category", "stock_session",
        "release_event_published", "released_stock_applied", "released_at")
    if f[1] == "paid" then
        redis.call("ZREM", exp_zset_key, checkout_id)
        return "NOT_PENDING"
    end
    if f[1] == "released" then
        if f[10] ~= "1" then
            local published_at = tonumber(f[12]) or now_ts
            local result = redis.pcall("XADD", stream_key, "*",
                "action", "RELEASE", "checkout_id", checkout_id, "user_id", f[2],
                "qty", f[3], "ts", published_at, "expires_at", f[4], "payload", f[6])
            if type(result) == "table" and result.err then return "RELEASE_PUBLISH_FAILED" end
            redis.call("HSET", held_key, "release_event_published", "1")
        end
        redis.call("ZREM", exp_zset_key, checkout_id)
        return "NOT_PENDING"
    end
    if f[1] ~= "pending" then return "HOLD_NOT_FOUND" end
    if grace >= 0 and tonumber(f[4]) + grace > now_ts then return "NOT_EXPIRED" end

    local function keytype(key)
        local t = redis.call("TYPE", key)
        if type(t) == "table" then return t.ok end
        return t
    end
    local exp_type, stream_type = keytype(exp_zset_key), keytype(stream_key)
    if exp_type ~= "none" and exp_type ~= "zset" then return "RELEASE_PUBLISH_FAILED" end
    if stream_type ~= "none" and stream_type ~= "stream" then return "RELEASE_PUBLISH_FAILED" end
    for i = 7, 9 do
        if f[i] and f[i] ~= "" then
            local t = keytype(f[i])
            if t ~= "none" and t ~= "string" then return "RELEASE_PUBLISH_FAILED" end
        end
    end
    -- Publish first. If XADD fails, stock remains held and the ZSET will trigger another attempt.
    if f[10] ~= "1" then
        local result = redis.pcall("XADD", stream_key, "*",
            "action", "RELEASE", "checkout_id", checkout_id, "user_id", f[2],
            "qty", f[3], "ts", now_ts, "expires_at", f[4], "payload", f[6])
        if type(result) == "table" and result.err then return "RELEASE_PUBLISH_FAILED" end
        redis.call("HSET", held_key, "release_event_published", "1", "released_at", now_ts)
    end
    -- Set the marker before INCRBY: in the exceptional case of a later command error, prefer a
    -- capacity leak (fail-closed) over restoring the same stock twice. Reconciliation must monitor it.
    if f[11] ~= "1" then
        redis.call("HSET", held_key, "released_stock_applied", "1")
        local qty = tonumber(f[3])
        for i = 7, 9 do
            if f[i] and f[i] ~= "" then
                -- We give the stock back only if the counter still exists as inexistent means unlimited.
                if redis.call("EXISTS", f[i]) == 1 then
                    redis.call("INCRBY", f[i], qty)
                end
            end
        end
    end
    redis.call("HSET", held_key, "status", "released", "released_at", now_ts)
    redis.call("ZREM", exp_zset_key, checkout_id)
    if f[5] and f[5] ~= "" and redis.call("GET", f[5]) == checkout_id then redis.call("DEL", f[5]) end
    redis.call("EXPIRE", held_key, tonumber(ARGV[4]))
    return "RELEASED"
    """

    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self._init_holds_cache_enabled: bool = False
        self._init_holds_cache: (
            list[tuple[UUID, str, int, dict[str, str], str]] | None
        ) = None
        self.RESERVE_SCRIPT: AsyncScript = redis_client.register_script(
            self._RESERVE_LUA,
        )
        self.CONFIRM_SCRIPT: AsyncScript = redis_client.register_script(
            self._CONFIRM_LUA,
        )
        self.RELEASE_SCRIPT: AsyncScript = redis_client.register_script(
            self._RELEASE_LUA,
        )
        self._named_scripts: tuple[tuple[str, str, str], ...] = (
            ("Reserve", self._RESERVE_LUA, self._script_sha(self._RESERVE_LUA)),
            ("Confirm", self._CONFIRM_LUA, self._script_sha(self._CONFIRM_LUA)),
            ("Release", self._RELEASE_LUA, self._script_sha(self._RELEASE_LUA)),
        )

    @staticmethod
    def _event_stock_keys_registry_key(event_id: UUID) -> str:
        return f"{snapshot_key(event_id)}:stock_keys"

    def _script_sha(self, source: str) -> str:
        """Compute Redis' required SHA-1 using the same encoder as the Redis client."""
        encoded_source = self.redis_client.connection_pool.get_encoder().encode(source)
        return hashlib.sha1(encoded_source, usedforsecurity=False).hexdigest()

    async def load_scripts(self) -> None:
        """SCRIPT LOAD every Lua script eagerly."""
        for name, source, expected_sha in self._named_scripts:
            sha = to_str(await self.redis_client.script_load(source))
            if sha != expected_sha:
                raise RedisScriptShaMismatchError(
                    name=name,
                    received_sha=sha,
                    expected_sha=expected_sha,
                )

    async def ensure_scripts_loaded(self) -> None:
        """Reload the scripts if Redis lost them (restart, SCRIPT FLUSH)."""
        exists = await self.redis_client.script_exists(
            *(expected_sha for _, _, expected_sha in self._named_scripts),
        )
        if not all(exists):
            await self.load_scripts()

    async def run_script(
        self,
        script: AsyncScript,
        keys: Sequence[str],
        args: Sequence[Any],
    ) -> str:
        """Call a registered Lua script."""
        result = await script(keys=keys, args=args)
        return to_str(result)

    async def init_tickets_redis(self) -> None:
        """To be called in the application startup event."""
        await self.load_scripts()

    # --------------------------------------------------------------------------------------
    # Fast path operations (RAM only)
    # --------------------------------------------------------------------------------------

    def hold_ttl_for_request(self, request_type: Any) -> int:
        """Return a hold TTL that covers the provider deadline and callback delivery lag."""
        if request_type == RequestType.TRANSFER_REQUEST:
            return (
                (CHECKOUT_EXPIRATION * 60)
                + self.PAYMENT_REQUEST_CREATION_MARGIN_SECONDS
                + self.PAYMENT_CALLBACK_MAX_DELAY_SECONDS
                + self.PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS
            )
        if request_type == RequestType.TRANSACTION_REQUEST:
            return (
                (REQUEST_EXPIRATION * 60)
                + self.PAYMENT_REQUEST_CREATION_MARGIN_SECONDS
                + self.PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS
            )
        raise InvalidRequestTypeError(request_type=request_type)

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
        hold_ttl_seconds: int | None = None,
    ) -> ReservationResult:
        """Atomically check the event / category / session stocks, decrement them and create the hold."""
        now_ts = int(time.time())
        ttl_seconds = (
            self.CHECKOUT_HOLD_TTL_SECONDS
            if hold_ttl_seconds is None
            else hold_ttl_seconds
        )
        if ttl_seconds <= 0:
            raise InvalidTTLValueError(ttl_seconds)
        try:
            raw = await self.run_script(
                self.RESERVE_SCRIPT,
                keys=[
                    stock_key_event(event.id),
                    stock_key_category(category.id),
                    stock_key_session(session.id),
                    user_hold_key(event.id, user_id),
                    hold_key(checkout_id),
                    EventRedisKeys.EXPIRATIONS_ZSET_KEY,
                    EventRedisKeys.STREAM_KEY,
                ],
                args=[
                    requested_qty,
                    user_id,
                    now_ts,
                    ttl_seconds,
                    str(checkout_id),
                    self.HOLD_KEY_TTL_MARGIN_SECONDS,
                    payload_json,
                    "1" if event.quota is not None else "0",
                    "1" if category.quota is not None else "0",
                    "1" if session.quota is not None else "0",
                ],
            )
        except Exception:
            try:
                compensation = await self.release_hold(checkout_id)
                if compensation == "RELEASE_PUBLISH_FAILED":
                    hyperion_error_logger.critical(
                        "Could not publish RELEASE while compensating checkout %s; stock may remain held",
                        checkout_id,
                    )
            except Exception:
                hyperion_error_logger.exception(
                    "Could not compensate ambiguous Redis reservation %s",
                    checkout_id,
                )
            raise
        status, _, dimension = raw.partition(":")
        if status == "REDIS_STATE_ERROR":
            raise InvalidRedisStateError(checkout_id=checkout_id, state=dimension)
        return ReservationResult(
            status=status,
            dimension=dimension or None,
            expires_at=now_ts + ttl_seconds,
        )

    async def confirm_hold(
        self,
        checkout_id: UUID,
        raise_on_fail: bool = False,
    ) -> str:
        """Confirm a hold and retry the Lua transition if a partial Redis error is surfaced."""
        last_error: Exception | None = None
        status = "CONFIRM_PUBLISH_FAILED"
        for attempt in range(3):
            try:
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
                if status != "CONFIRM_PUBLISH_FAILED":
                    last_error = None
                    break
            except Exception as exc:
                last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise RedisCheckoutConfirmationError(checkout_id) from last_error
        if status == "CONFIRM_PUBLISH_FAILED":
            raise RedisCheckoutConfirmPublishError(checkout_id)
        if raise_on_fail and status not in (
            LuaResult.CONFIRMED,
            LuaResult.ALREADY_CONFIRMED,
            LuaResult.CONFIRMED_LATE,
        ):
            raise RedisCheckoutConfirmationUnableError(checkout_id, status)
        return status

    async def release_hold(
        self,
        checkout_id: UUID | str,
        *,
        grace_seconds: int = -1,
        now: int | None = None,
    ) -> str:
        """Atomically restore stock, publish RELEASE, and retain a short-lived tombstone."""
        args = [
            str(checkout_id),
            now if now is not None else int(time.time()),
            grace_seconds,
            self.RELEASED_HOLD_RETENTION_SECONDS,
        ]
        result = await self.run_script(
            self.RELEASE_SCRIPT,
            keys=[
                hold_key(checkout_id),
                EventRedisKeys.EXPIRATIONS_ZSET_KEY,
                EventRedisKeys.STREAM_KEY,
            ],
            args=args,
        )
        if result == "RELEASE_PUBLISH_FAILED":
            result = await self.run_script(
                self.RELEASE_SCRIPT,
                keys=[
                    hold_key(checkout_id),
                    EventRedisKeys.EXPIRATIONS_ZSET_KEY,
                    EventRedisKeys.STREAM_KEY,
                ],
                args=[
                    str(checkout_id),
                    int(time.time()) if now is None else now,
                    grace_seconds,
                    self.RELEASED_HOLD_RETENTION_SECONDS,
                ],
            )
        return result

    async def publish_hold_event(
        self,
        *,
        checkout_id: UUID,
        user_id: str,
        qty: int,
        expires_at: int,
        payload_json: str,
    ) -> None:
        """Legacy helper retained for compatibility only."""
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
    # Event configuration cache, public projections & stock counters
    # --------------------------------------------------------------------------------------

    @staticmethod
    def _to_event_simple(
        event: schemas_tickets.EventComplete,
    ) -> schemas_tickets.EventSimple:
        return schemas_tickets.EventSimple(
            id=event.id,
            name=event.name,
            open_datetime=event.open_datetime,
            close_datetime=event.close_datetime,
            store_id=event.store_id,
            disabled=event.disabled,
        )

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
        PostgreSQL is only read on a cold cache.
        """
        snapshot = await self._read_snapshot(event_id)
        if snapshot is not None:
            return snapshot
        return await self.warm_up_event(event_id=event_id, db=db)

    async def get_event_simple(
        self,
        event_id: UUID,
        db: AsyncSession,
    ) -> schemas_tickets.EventSimple | None:
        """
        Return EventSimple from the Redis snapshot if available, or warm it up.
        Avoids hitting PostgreSQL for simple event checks (scans, transfers, admin guards).
        """
        snapshot = await self.get_event_snapshot(event_id=event_id, db=db)
        if snapshot is None:
            return None
        return self._to_event_simple(snapshot)

    async def get_open_and_enabled_events(
        self,
        db: AsyncSession,
    ) -> list[schemas_tickets.EventSimple]:
        """
        Return all open and enabled events directly from Redis snapshots.
        Because active (open + upcoming) event snapshots are pre-warmed, an upcoming event becomes
        visible at the exact millisecond its `open_datetime` is reached with zero SQL queries.
        """

        raw_active_ids = await self.redis_client.get(self.ACTIVE_EVENTS_CACHE_KEY)
        active_ids: list[UUID] | None = None
        if raw_active_ids is not None:
            try:
                active_ids = [UUID(eid) for eid in json.loads(to_str(raw_active_ids))]
            except ValueError, TypeError:
                active_ids = None

        if active_ids is None:
            await self.warm_up_active_events(db=db)
            raw_active_ids = await self.redis_client.get(self.ACTIVE_EVENTS_CACHE_KEY)
            if raw_active_ids is not None:
                try:
                    active_ids = [
                        UUID(eid) for eid in json.loads(to_str(raw_active_ids))
                    ]
                except ValueError, TypeError:
                    active_ids = None

        if active_ids is None:
            return list(await cruds_tickets.get_open_and_enabled_events(db=db))

        if not active_ids:
            return []

        raw_snapshots = await self.redis_client.mget(
            [snapshot_key(eid) for eid in active_ids],
        )
        now = datetime.now(UTC)
        open_events: list[schemas_tickets.EventSimple] = []

        for eid, raw_snap in zip(active_ids, raw_snapshots, strict=True):
            if raw_snap is not None:
                event = schemas_tickets.EventComplete.model_validate_json(raw_snap)
            else:
                event = await self.get_event_snapshot(event_id=eid, db=db)
            if event is None or event.disabled:
                continue
            if event.open_datetime <= now and (
                event.close_datetime is None or event.close_datetime > now
            ):
                open_events.append(self._to_event_simple(event))

        return open_events

    async def build_event_public(
        self,
        event: schemas_tickets.EventComplete,
        db: AsyncSession,
    ) -> schemas_tickets.EventPublic:
        """
        Build the `EventPublic` projection of an event using a single `MGET` on Redis stock counters
        to compute real-time `sold_out` flags for the event, its enabled sessions, and its enabled
        categories without touching PostgreSQL.
        """
        enabled_sessions = [s for s in event.sessions if not s.disabled]
        enabled_categories = [c for c in event.categories if not c.disabled]

        limited_keys: list[str] = []
        if event.quota is not None:
            limited_keys.append(stock_key_event(event.id))
        limited_keys.extend(
            stock_key_session(session.id)
            for session in enabled_sessions
            if session.quota is not None
        )
        limited_keys.extend(
            stock_key_category(category.id)
            for category in enabled_categories
            if category.quota is not None
        )

        stock_by_key: dict[str, int] = {}
        if limited_keys:
            raw_values = await self.redis_client.mget(limited_keys)
            if any(v is None for v in raw_values):
                await self._initialize_stock_counters(event, db)
                raw_values = await self.redis_client.mget(limited_keys)

            for key, raw_val in zip(limited_keys, raw_values, strict=True):
                stock_by_key[key] = int(to_str(raw_val)) if raw_val is not None else 0

        def is_key_sold_out(quota: int | None, key: str) -> bool:
            if quota is None:
                return False
            if quota <= 0:
                return True
            return stock_by_key.get(key, 0) <= 0

        return schemas_tickets.EventPublic(
            id=event.id,
            name=event.name,
            store_id=event.store_id,
            sessions=[
                schemas_tickets.SessionPublic(
                    event_id=session.event_id,
                    id=session.id,
                    name=session.name,
                    start_datetime=session.start_datetime,
                    sold_out=is_key_sold_out(
                        session.quota,
                        stock_key_session(session.id),
                    ),
                    disabled=session.disabled,
                )
                for session in enabled_sessions
            ],
            categories=[
                schemas_tickets.CategoryPublic(
                    event_id=category.event_id,
                    id=category.id,
                    name=category.name,
                    price=category.price,
                    required_membership=category.required_membership,
                    sold_out=is_key_sold_out(
                        category.quota,
                        stock_key_category(category.id),
                    ),
                    disabled=category.disabled,
                )
                for category in enabled_categories
            ],
            questions=[
                schemas_tickets.QuestionPublic(
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
            sold_out=is_key_sold_out(event.quota, stock_key_event(event.id)),
            open_datetime=event.open_datetime,
            close_datetime=event.close_datetime,
            disabled=event.disabled,
        )

    async def invalidate_event_snapshot(self, event_id: UUID) -> None:
        """
        To be called by admin endpoints after they modify an event, to make the change immediate.
        Clears the snapshot, the active events list, and all registered stock keys for the event
        (even if the 30-second snapshot had already expired).
        """
        reg_key = self._event_stock_keys_registry_key(event_id)
        snapshot, raw_registered_keys = await self.redis_client.mget(
            [snapshot_key(event_id), reg_key],
        )
        keys_to_delete: set[str] = {
            snapshot_key(event_id),
            reg_key,
            stock_key_event(event_id),
            self.ACTIVE_EVENTS_CACHE_KEY,
        }
        if raw_registered_keys is not None:
            try:
                keys_to_delete.update(json.loads(to_str(raw_registered_keys)))
            except ValueError, TypeError:
                pass
        if snapshot is not None:
            try:
                parsed = schemas_tickets.EventComplete.model_validate_json(snapshot)
                keys_to_delete.update(
                    stock_key_category(c.id) for c in parsed.categories
                )
                keys_to_delete.update(stock_key_session(s.id) for s in parsed.sessions)
            except Exception:
                hyperion_error_logger.exception(
                    "Could not parse event snapshot for invalidation",
                    extra={"event_id": event_id, "snapshot": to_str(snapshot)},
                )
        await self.redis_client.delete(*keys_to_delete)

    async def _collect_redis_holds(
        self,
    ) -> list[tuple[UUID, str, int, dict[str, str], str]]:
        """
        Collect all holds currently stored in Redis (pending, paid, or released) in a single pipeline.
        Combines the expiration ZSET with a scan of hold hashes so recently confirmed holds (removed
        from the ZSET by CONFIRM_SCRIPT but not yet flushed to PostgreSQL) are never missed.
        """
        checkout_ids_set: set[str] = set()

        zset_members = await self.redis_client.zrange(
            EventRedisKeys.EXPIRATIONS_ZSET_KEY,
            0,
            -1,
        )
        for member in zset_members:
            checkout_ids_set.add(to_str(member))

        prefix = hold_key("")
        async for raw_key in self.redis_client.scan_iter(match=hold_key("*")):
            key_str = to_str(raw_key)
            if key_str.startswith(prefix):
                cid_str = key_str[len(prefix) :]
                if cid_str:
                    checkout_ids_set.add(cid_str)

        if not checkout_ids_set:
            return []

        checkout_ids = list(checkout_ids_set)
        fields = [
            "status",
            "qty",
            "stock_event",
            "stock_category",
            "stock_session",
            "payload",
        ]

        pipe = self.redis_client.pipeline()
        for cid in checkout_ids:
            pipe.hmget(hold_key(cid), fields)
        results = await pipe.execute()

        holds: list[tuple[UUID, str, int, dict[str, str], str]] = []
        for cid, data in zip(checkout_ids, results, strict=True):
            if not data or not data[0]:
                continue
            try:
                checkout_uuid = UUID(cid)
            except ValueError:
                continue
            status = to_str(data[0])
            qty = int(to_str(data[1])) if data[1] else 0
            stock_map = {
                "stock_event": to_str(data[2]) if data[2] else "",
                "stock_category": to_str(data[3]) if data[3] else "",
                "stock_session": to_str(data[4]) if data[4] else "",
            }
            payload_str = to_str(data[5]) if data[5] else ""
            holds.append((checkout_uuid, status, qty, stock_map, payload_str))

        return holds

    async def _get_redis_pending_stock(
        self,
        target_stock_key: str,
        target_field: str,
    ) -> tuple[int, list[UUID]]:
        """
        Count the total quantity of tickets held in Redis (pending or paid) for a given stock key
        (event, category or session), and return the list of checkout IDs tracked in Redis for that
        key so they can be excluded from the PostgreSQL query.
        """
        if self._init_holds_cache_enabled:
            if self._init_holds_cache is None:
                self._init_holds_cache = await self._collect_redis_holds()
            holds = self._init_holds_cache
        else:
            holds = await self._collect_redis_holds()

        total_qty = 0
        matching_checkout_ids: list[UUID] = []

        for checkout_uuid, status, qty, stock_map, _payload_str in holds:
            if stock_map.get(target_field) == target_stock_key:
                if status in ("pending", "paid"):
                    total_qty += qty
                    matching_checkout_ids.append(checkout_uuid)
                elif status == "released":
                    matching_checkout_ids.append(checkout_uuid)

        return total_qty, matching_checkout_ids

    async def count_used_stock_by_event_id(
        self,
        event_id: UUID,
        db: AsyncSession,
    ) -> int:
        """Count total valid checkouts and paid tickets for an event across Redis and PostgreSQL."""
        redis_qty, redis_checkout_ids = await self._get_redis_pending_stock(
            target_stock_key=stock_key_event(event_id),
            target_field="stock_event",
        )
        db_used = await cruds_tickets.count_valid_checkouts_and_tickets_by_event_id(
            event_id=event_id,
            db=db,
            exclude_ids=redis_checkout_ids,
        )
        return db_used + redis_qty

    async def count_used_stock_by_category_id(
        self,
        category_id: UUID,
        db: AsyncSession,
    ) -> int:
        """Count total valid checkouts and paid tickets for a category across Redis and PostgreSQL."""
        redis_qty, redis_checkout_ids = await self._get_redis_pending_stock(
            target_stock_key=stock_key_category(category_id),
            target_field="stock_category",
        )
        db_used = await cruds_tickets.count_valid_checkouts_and_tickets_by_category_id(
            category_id=category_id,
            db=db,
            exclude_ids=redis_checkout_ids,
        )
        return db_used + redis_qty

    async def count_used_stock_by_session_id(
        self,
        session_id: UUID,
        db: AsyncSession,
    ) -> int:
        """Count total valid checkouts and paid tickets for a session across Redis and PostgreSQL."""
        redis_qty, redis_checkout_ids = await self._get_redis_pending_stock(
            target_stock_key=stock_key_session(session_id),
            target_field="stock_session",
        )
        db_used = await cruds_tickets.count_valid_checkouts_and_tickets_by_session_id(
            session_id=session_id,
            db=db,
            exclude_ids=redis_checkout_ids,
        )
        return db_used + redis_qty

    async def count_answers_by_question_id(
        self,
        question_id: UUID,
        db: AsyncSession,
    ) -> int:
        """
        Count answers for a question across PostgreSQL and active Redis holds (pending or paid)
        so a question cannot be deleted or altered while a write-behind checkout references it.
        """
        db_count = await cruds_tickets.count_answers_by_question_id(
            question_id=question_id,
            db=db,
        )
        if db_count > 0:
            return db_count

        holds = await self._collect_redis_holds()
        question_id_str = str(question_id)
        redis_answers = 0
        for _cid, status, _qty, _stock_map, payload_str in holds:
            if status in ("pending", "paid") and question_id_str in payload_str:
                try:
                    payload = schemas_tickets.CheckoutPayload.model_validate_json(
                        payload_str,
                    )
                    if any(a.question_id == question_id for a in payload.answers):
                        redis_answers += 1
                except Exception:
                    hyperion_error_logger.exception(
                        "Could not parse checkout payload for question count",
                        extra={"question_id": question_id, "payload": payload_str},
                    )
                    continue
        return redis_answers

    async def _initialize_counter(
        self,
        key: str,
        quota: int | None,
        count_used_db_func: Any,
        target_field: Literal["stock_event", "stock_category", "stock_session"],
    ) -> None:
        if quota is None:
            await self.redis_client.delete(key)
            return

        if await self.redis_client.exists(key):
            return

        redis_qty, redis_checkout_ids = await self._get_redis_pending_stock(
            target_stock_key=key,
            target_field=target_field,
        )
        db_used = await count_used_db_func(exclude_ids=redis_checkout_ids)
        total_used = db_used + redis_qty

        await self.redis_client.set(key, max(quota - total_used, 0), nx=True)

    async def _initialize_stock_counters(
        self,
        event: schemas_tickets.EventComplete,
        db: AsyncSession,
    ) -> None:
        """
        Initialize the stock counters for the event, its categories and sessions in Redis.
        Use the database to count the used stock, excluding the checkouts that are still pending in Redis.
        """
        reg_key = self._event_stock_keys_registry_key(event.id)
        all_event_stock_keys = [
            stock_key_event(event.id),
            *(stock_key_category(c.id) for c in event.categories),
            *(stock_key_session(s.id) for s in event.sessions),
        ]

        self._init_holds_cache_enabled = True
        self._init_holds_cache = None
        try:
            await self._initialize_counter(
                stock_key_event(event.id),
                event.quota,
                partial(
                    cruds_tickets.count_valid_checkouts_and_tickets_by_event_id,
                    event_id=event.id,
                    db=db,
                ),
                target_field="stock_event",
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
                    target_field="stock_category",
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
                    target_field="stock_session",
                )
            await self.redis_client.set(reg_key, json.dumps(all_event_stock_keys))
        finally:
            self._init_holds_cache_enabled = False
            self._init_holds_cache = None

    async def warm_up_event(
        self,
        event_id: UUID,
        db: AsyncSession,
    ) -> schemas_tickets.EventComplete | None:
        """
        (Re)load the event configuration from PostgreSQL into Redis and initialise the missing stock counters.
        Single-flight: when 50 000 requests hit a cold cache at once, exactly one of them reads PostgreSQL.
        """
        lock_key = warmup_lock_key(event_id)
        lock_token = uuid4().hex
        acquired = await self.redis_client.set(
            lock_key,
            lock_token,
            nx=True,
            ex=self.WARMUP_LOCK_TTL_SECONDS,
        )
        if not bool(acquired):
            deadline = time.monotonic() + self.WARMUP_WAIT_SECONDS
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

            previous_snapshot = await self._read_snapshot(event_id)
            if previous_snapshot is not None:
                stale_stock_keys: list[str] = []
                if previous_snapshot.quota != event.quota:
                    stale_stock_keys.append(stock_key_event(event.id))
                prev_cats = {c.id: c.quota for c in previous_snapshot.categories}
                stale_stock_keys.extend(
                    stock_key_category(cat.id)
                    for cat in event.categories
                    if cat.id in prev_cats and prev_cats[cat.id] != cat.quota
                )
                prev_sessions = {s.id: s.quota for s in previous_snapshot.sessions}
                stale_stock_keys.extend(
                    stock_key_session(sess.id)
                    for sess in event.sessions
                    if sess.id in prev_sessions and prev_sessions[sess.id] != sess.quota
                )
                if stale_stock_keys:
                    await self.redis_client.delete(*stale_stock_keys)

            # Counters first, snapshot last: once the snapshot is visible, the stock is guaranteed to exist.
            await self._initialize_stock_counters(event, db)
            await self.redis_client.set(
                snapshot_key(event_id),
                event.model_dump_json(),
                ex=self.SNAPSHOT_TTL_SECONDS,
            )
            return event
        finally:
            await self.redis_client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
                1,
                lock_key,
                lock_token,
            )

    async def warm_up_active_events(self, db: AsyncSession) -> int:
        """Pre-warm every open or upcoming event at startup and refresh the active event IDs cache."""
        event_ids = await cruds_tickets.get_active_event_ids(db=db)
        for event_id in event_ids:
            try:
                await self.warm_up_event(event_id=event_id, db=db)
            except HTTPException:
                continue
        await self.redis_client.set(
            self.ACTIVE_EVENTS_CACHE_KEY,
            json.dumps([str(eid) for eid in event_ids]),
            ex=self.SNAPSHOT_TTL_SECONDS,
        )
        return len(event_ids)

    async def is_stock_key_sold_out(
        self,
        key: str,
        quota: int | None,
        count_used_func: Any,
    ) -> bool:
        """
        Check if a stock key is sold out in O(1) using Redis counters, falling back to initializing
        the counter from Redis holds + PostgreSQL if the counter key is not yet in Redis.
        """
        if quota is None:
            return False
        if quota <= 0:
            return True

        raw = await self.redis_client.get(key)
        if raw is not None:
            return int(to_str(raw)) <= 0

        used = await count_used_func()
        remaining = max(quota - used, 0)
        await self.redis_client.set(key, remaining, nx=True)
        return remaining <= 0

    def _split_holds_for_target(
        self,
        holds: Sequence[tuple[UUID, str, int, dict[str, str], str]],
        target_stock_key: str,
        target_field: Literal["stock_event", "stock_category", "stock_session"],
    ) -> tuple[int, int, list[UUID]]:
        """Return (pending_qty, paid_qty, all_tracked_checkout_ids) for a specific stock key."""
        pending_qty = 0
        paid_qty = 0
        tracked_ids: list[UUID] = []

        for checkout_uuid, status, qty, stock_map, _payload_str in holds:
            if stock_map.get(target_field) == target_stock_key:
                tracked_ids.append(checkout_uuid)
                if status == "pending":
                    pending_qty += qty
                elif status == "paid":
                    paid_qty += qty

        return pending_qty, paid_qty, tracked_ids

    async def build_event_admin(
        self,
        event: schemas_tickets.EventComplete,
        db: AsyncSession,
    ) -> schemas_tickets.EventAdmin:
        """
        Build `EventAdmin` combining live Redis holds (`pending`, `paid`, `released`) and PostgreSQL
        (excluding checkouts still tracked in Redis) in a single Redis scan.
        """
        holds = await self._collect_redis_holds()

        ev_pending, ev_paid, ev_exclude = self._split_holds_for_target(
            holds,
            stock_key_event(event.id),
            "stock_event",
        )
        event_in_checkout = (
            ev_pending
            + await cruds_tickets.count_valid_checkouts_by_event_id(
                event_id=event.id,
                db=db,
                exclude_ids=ev_exclude,
            )
        )
        event_sold = ev_paid + await cruds_tickets.count_tickets_by_event_id(
            event_id=event.id,
            db=db,
            exclude_ids=ev_exclude,
        )

        sessions_admin: list[schemas_tickets.SessionAdmin] = []
        for session in event.sessions:
            s_pending, s_paid, s_exclude = self._split_holds_for_target(
                holds,
                stock_key_session(session.id),
                "stock_session",
            )
            s_in_checkout = (
                s_pending
                + await cruds_tickets.count_valid_checkouts_by_session_id(
                    session_id=session.id,
                    db=db,
                    exclude_ids=s_exclude,
                )
            )
            s_sold = s_paid + await cruds_tickets.count_tickets_by_session_id(
                session_id=session.id,
                db=db,
                exclude_ids=s_exclude,
            )
            sessions_admin.append(
                schemas_tickets.SessionAdmin(
                    id=session.id,
                    event_id=session.event_id,
                    name=session.name,
                    start_datetime=session.start_datetime,
                    quota=session.quota,
                    disabled=session.disabled,
                    tickets_in_checkout=s_in_checkout,
                    tickets_sold=s_sold,
                ),
            )

        categories_admin: list[schemas_tickets.CategoryAdmin] = []
        for category in event.categories:
            c_pending, c_paid, c_exclude = self._split_holds_for_target(
                holds,
                stock_key_category(category.id),
                "stock_category",
            )
            c_in_checkout = (
                c_pending
                + await cruds_tickets.count_valid_checkouts_by_category_id(
                    category_id=category.id,
                    db=db,
                    exclude_ids=c_exclude,
                )
            )
            c_sold = c_paid + await cruds_tickets.count_tickets_by_category_id(
                category_id=category.id,
                db=db,
                exclude_ids=c_exclude,
            )
            categories_admin.append(
                schemas_tickets.CategoryAdmin(
                    id=category.id,
                    event_id=category.event_id,
                    name=category.name,
                    price=category.price,
                    required_membership=category.required_membership,
                    quota=category.quota,
                    disabled=category.disabled,
                    tickets_in_checkout=c_in_checkout,
                    tickets_sold=c_sold,
                ),
            )

        return schemas_tickets.EventAdmin(
            id=event.id,
            name=event.name,
            store_id=event.store_id,
            open_datetime=event.open_datetime,
            close_datetime=event.close_datetime,
            sessions=sessions_admin,
            categories=categories_admin,
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
            tickets_in_checkout=event_in_checkout,
            tickets_sold=event_sold,
        )


def get_redis_event_tool(
    redis_client: Redis | None = Depends(get_redis_client),
) -> RedisEventTool:
    if redis_client is None:
        raise HTTPException(503, "Redis is not configured")
    return RedisEventTool(redis_client)

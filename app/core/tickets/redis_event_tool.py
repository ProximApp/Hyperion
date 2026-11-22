import asyncio
import hashlib
import logging
import time
from collections.abc import Sequence
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from redis.asyncio import Redis
from redis.commands.core import AsyncScript
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


class RedisEventTool:
    """Redis operations for the tickets module, without any PostgreSQL access.

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

    Every event of the stream carries the FULL checkout payload (not a delta). The database writes done by
    the worker are therefore idempotent and commutative, which makes at-least-once delivery and
    out-of-order processing between several consumers safe:
        - a CONFIRM processed before its HOLD creates the (paid) row itself,
        - a paid checkout is never downgraded by a late RELEASE ("paid always wins").
    """

    # --------------------------------------------------------------------------------------
    # Tunables
    # --------------------------------------------------------------------------------------

    # A hold must cover the provider-side payment window PLUS the observed callback-delivery delay.
    # HelloAsso may call us up to 5 minutes after the payer sees a confirmed payment. The route should
    # pass a TTL based on the actual RequestType; this value is only a conservative backwards-compatible
    # default for call sites that have not yet been updated.
    PAYMENT_CALLBACK_MAX_DELAY_SECONDS = 5 * 60
    # Reserve happens before MyPayment creates the HelloAsso checkout. This covers bounded API/DB
    # creation latency as well as the callback-delivery delay; keep aligned with network timeouts.
    PAYMENT_REQUEST_CREATION_MARGIN_SECONDS = 5 * 60
    PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS = 2 * 60
    CHECKOUT_HOLD_TTL_SECONDS = 15 * 60 + PAYMENT_CALLBACK_MAX_DELAY_SECONDS + PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS
    # The Redis TTL of a hold hash is only a memory safety net (ttl + margin). It must be larger than
    # any realistic reaper downtime: if the hash disappeared before the reaper ran, its stock would leak.
    HOLD_KEY_TTL_MARGIN_SECONDS = 60 * 60
    # A confirmed hold is kept for a while so that a duplicated payment callback stays idempotent.
    CONFIRMED_HOLD_RETENTION_SECONDS = 24 * 60 * 60
    # Keep the payload after a RELEASE so that a delayed payment callback can still be reconciled.
    RELEASED_HOLD_RETENTION_SECONDS = 24 * 60 * 60
    # The reaper only releases holds that expired at least this long ago (clock skew, late callbacks).
    REAPER_GRACE_SECONDS = 30
    # Event configuration cache. Short TTL: admin changes (disabled, dates...) are visible after at most this delay.
    SNAPSHOT_TTL_SECONDS = 30
    WARMUP_LOCK_TTL_SECONDS = 30
    WARMUP_WAIT_SECONDS = 3.0

    # --------------------------------------------------------------------------------------
    # Lua scripts. The bodies below are wrapped as redis-py AsyncScript objects (register_script)
    # in __init__, which is the API the redis-py docs recommend over raw EVALSHA/SCRIPT LOAD calls
    # (https://redis.io/commands/evalsha: "In practice, use the object returned by register_script").
    # An AsyncScript computes and caches its own sha, and self-heals a missing script (reloads via
    # SCRIPT LOAD and retries) on every call, single or pipelined - see run_script and
    # _release_expired_batch in worker_tickets.py. Each script is atomic: Redis runs it without
    # interleaving any other command.
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
            if raw == false then return "STOCK_NOT_INITIALIZED:\" .. labels[i] end
            if tonumber(raw) < requested_qty then return "SOLD_OUT_OR_INSUFFICIENT_STOCK:\" .. labels[i] end
        end
    end

    local touched = { "", "", "" }
    for i = 1, 3 do
        if limited[i] then
            redis.call("DECRBY", stock_keys[i], requested_qty)
            touched[i] = stock_keys[i]
        end
    end
    local expires_at = current_timestamp + ttl_seconds
    local key_ttl = ttl_seconds + key_ttl_margin
    redis.call("HSET", held_key,
        "qty", requested_qty, "timestamp", current_timestamp, "user_id", user_id,
        "checkout_id", checkout_id, "status", "pending", "expires_at", expires_at,
        "user_hold_key", user_hold_key, "stock_event", touched[1],
        "stock_category", touched[2], "stock_session", touched[3], "payload", payload,
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
    local was_released = status == "released"
    local f = redis.call("HMGET", held_key, "user_id", "qty", "expires_at", "user_hold_key", "payload")
    -- If it was released, payment is authoritative but stock may already have been sold again.
    redis.call("HSET", held_key, "status", "paid", "late_confirmed", was_released and "1" or "0")
    redis.call("EXPIRE", held_key, retention)
    redis.call("ZREM", exp_zset_key, checkout_id)
    if redis.call("GET", f[4]) == checkout_id then redis.call("DEL", f[4]) end
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
            if t ~= "string" then return "RELEASE_PUBLISH_FAILED" end
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
            if f[i] and f[i] ~= "" then redis.call("INCRBY", f[i], qty) end
        end
    end
    redis.call("HSET", held_key, "status", "released", "released_at", now_ts)
    redis.call("ZREM", exp_zset_key, checkout_id)
    if redis.call("GET", f[5]) == checkout_id then redis.call("DEL", f[5]) end
    redis.call("EXPIRE", held_key, tonumber(ARGV[4]))
    return "RELEASED"
    """

    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        # register_script() is purely local (computes the sha from the script text with the
        # client's own encoder; no network call), so building these per instance costs nothing -
        # this tool is already constructed fresh per request/call site throughout this module.
        self.RESERVE_SCRIPT: AsyncScript = redis_client.register_script(
            self._RESERVE_LUA,
        )
        self.CONFIRM_SCRIPT: AsyncScript = redis_client.register_script(
            self._CONFIRM_LUA,
        )
        self.RELEASE_SCRIPT: AsyncScript = redis_client.register_script(
            self._RELEASE_LUA,
        )
        # Keep the source and expected SHA separately from AsyncScript: some redis-py
        # type stubs expose AsyncScript as callable only, without its runtime .script/.sha attrs.
        self._named_scripts: tuple[tuple[str, str, str], ...] = (
            ("Reserve", self._RESERVE_LUA, self._script_sha(self._RESERVE_LUA)),
            ("Confirm", self._CONFIRM_LUA, self._script_sha(self._CONFIRM_LUA)),
            ("Release", self._RELEASE_LUA, self._script_sha(self._RELEASE_LUA)),
        )

    def _script_sha(self, source: str) -> str:
        """Compute Redis' required SHA-1 using the same encoder as the Redis client."""
        encoded_source = self.redis_client.connection_pool.get_encoder().encode(source)
        # SHA-1 here is Redis' Lua-script cache identifier, not a security primitive.
        return hashlib.sha1(encoded_source, usedforsecurity=False).hexdigest()

    async def load_scripts(self) -> None:
        """
        SCRIPT LOAD every Lua script eagerly. Called once in the application startup event so the
        checkout's critical path never pays for the extra round trip an AsyncScript would otherwise
        spend self-healing on its first use after a Redis restart or SCRIPT FLUSH.
        """
        for name, source, expected_sha in self._named_scripts:
            sha = to_str(await self.redis_client.script_load(source))
            if sha != expected_sha:
                raise RedisScriptShaMismatchError(
                    name=name,
                    received_sha=sha,
                    expected_sha=expected_sha,
                )

    async def ensure_scripts_loaded(self) -> None:
        """
        Reload the scripts if Redis lost them (restart, SCRIPT FLUSH). Cheap: one SCRIPT EXISTS.
        Belt-and-suspenders: every call below (single via run_script, or pipelined in
        worker_tickets._release_expired_batch) already self-heals a missing script on its own, this
        just avoids paying for that extra round trip on the very first call after a flush.
        """
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
        """
        Call a registered Lua script. No manual script-loading/error-retry handling here:
        AsyncScript (from register_script) already tries the cached SHA and, on a missing-script error,
        reloads via SCRIPT LOAD and retries once automatically.
        """
        result = await script(keys=keys, args=args)
        return to_str(result)

    async def init_tickets_redis(self) -> None:
        """
        To be called in the application startup event.
        Loads Lua scripts. The MyPayment callback obtains the current client from app.dependencies.get_redis_client();
        script registration itself does not store a process-global client.
        """
        await self.load_scripts()

    # --------------------------------------------------------------------------------------
    # Fast path operations (RAM only)
    # --------------------------------------------------------------------------------------

    def hold_ttl_for_request(self, request_type: Any) -> int:
        """
        Return a hold TTL that covers the provider deadline and callback delivery lag.

        The TRANSFER_REQUEST flow uses HelloAsso and is known to deliver callbacks up to five minutes
        after payment confirmation. TRANSACTION_REQUEST uses MyPayment's own request expiration.
        A two-minute safety margin is added in both cases; the reaper grace is additional.
        """
        from app.core.checkout.utils_checkout import CHECKOUT_EXPIRATION
        from app.core.mypayment.types_mypayment import REQUEST_EXPIRATION, RequestType

        if request_type == RequestType.TRANSFER_REQUEST:
            return (CHECKOUT_EXPIRATION * 60) + self.PAYMENT_REQUEST_CREATION_MARGIN_SECONDS + self.PAYMENT_CALLBACK_MAX_DELAY_SECONDS + self.PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS
        if request_type == RequestType.TRANSACTION_REQUEST:
            return (REQUEST_EXPIRATION * 60) + self.PAYMENT_REQUEST_CREATION_MARGIN_SECONDS + self.PAYMENT_CALLBACK_SAFETY_MARGIN_SECONDS
        raise ValueError(f"Unsupported MyPayment request type: {request_type!r}")

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
        """
        Atomically check the event / category / session stocks, decrement them and create the hold.
        A single registered-Lua-script round trip, no PostgreSQL access.
        """
        now_ts = int(time.time())
        ttl_seconds = (
            self.CHECKOUT_HOLD_TTL_SECONDS
            if hold_ttl_seconds is None
            else hold_ttl_seconds
        )
        if ttl_seconds <= 0:
            raise ValueError("hold_ttl_seconds must be positive")
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
                    requested_qty, user_id, now_ts, ttl_seconds, str(checkout_id),
                    self.HOLD_KEY_TTL_MARGIN_SECONDS, payload_json,
                    "1" if event.quota is not None else "0",
                    "1" if category.quota is not None else "0",
                    "1" if session.quota is not None else "0",
                ],
            )
        except Exception:
            # A Redis timeout can mean the script ran but its reply was lost. Compensate by checkout id.
            try:
                compensation = await self.release_hold(checkout_id)
                if compensation == "RELEASE_PUBLISH_FAILED":
                    hyperion_error_logger.critical(
                        "Could not publish RELEASE while compensating checkout %s; stock may remain held",
                        checkout_id,
                    )
            except Exception:
                hyperion_error_logger.exception(
                    "Could not compensate ambiguous Redis reservation %s", checkout_id,
                )
            raise
        status, _, dimension = raw.partition(":")
        if status == "REDIS_STATE_ERROR":
            raise RuntimeError(f"Redis tickets state invalid ({dimension}); reservation was not changed")
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
                    keys=[hold_key(checkout_id), EventRedisKeys.EXPIRATIONS_ZSET_KEY, EventRedisKeys.STREAM_KEY],
                    args=[str(checkout_id), int(time.time()), self.CONFIRMED_HOLD_RETENTION_SECONDS],
                )
                if status != "CONFIRM_PUBLISH_FAILED":
                    last_error = None
                    break
            except Exception as exc:
                # A script can have changed status before an error surfaced. Replaying by checkout id
                # repairs the publication marker; the Lua script is idempotent for already-paid holds.
                last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError(
                f"Redis could not confirm checkout {checkout_id} after retries"
            ) from last_error
        if status == "CONFIRM_PUBLISH_FAILED":
            raise RuntimeError(f"Could not publish CONFIRM event for checkout {checkout_id}")
        if raise_on_fail and status not in (
            LuaResult.CONFIRMED, LuaResult.ALREADY_CONFIRMED, LuaResult.CONFIRMED_LATE,
        ):
            # Use a non-HTTP exception: get_db commits on HTTPException by design.
            raise RuntimeError(f"Reservation {checkout_id} could not be confirmed: {status}")
        return status

    async def release_hold(
        self,
        checkout_id: UUID | str,
        *,
        grace_seconds: int = -1,
        now: int | None = None,
    ) -> str:
        """
        Atomically restore stock, publish RELEASE, and retain a short-lived tombstone for late callbacks.
        `grace_seconds < 0` releases unconditionally (compensation); otherwise the hold is only
        released if it expired at least `grace_seconds` ago (reaper).
        """
        args = [
            str(checkout_id),
            now if now is not None else int(time.time()),
            grace_seconds,
            self.RELEASED_HOLD_RETENTION_SECONDS,
        ]
        result = await self.run_script(
            self.RELEASE_SCRIPT,
            keys=[hold_key(checkout_id), EventRedisKeys.EXPIRATIONS_ZSET_KEY, EventRedisKeys.STREAM_KEY],
            args=args,
        )
        if result == "RELEASE_PUBLISH_FAILED":
            # If XADD temporarily failed, the hold remains pending or retained as a tombstone. Retry once.
            result = await self.run_script(
                self.RELEASE_SCRIPT,
                keys=[hold_key(checkout_id), EventRedisKeys.EXPIRATIONS_ZSET_KEY, EventRedisKeys.STREAM_KEY],
                args=[str(checkout_id), int(time.time()) if now is None else now, grace_seconds, self.RELEASED_HOLD_RETENTION_SECONDS],
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
        """
        Legacy helper retained for compatibility only. New checkout code must NOT call it: reserve()
        now publishes HOLD atomically with the reservation, avoiding duplicate events and the dual-write gap.
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
        lock_token = uuid4().hex
        acquired = await self.redis_client.set(
            lock_key, lock_token, nx=True, ex=self.WARMUP_LOCK_TTL_SECONDS,
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
            # Do not delete a new owner's lock if our lease expired during a slow PostgreSQL read.
            await self.redis_client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
                1, lock_key, lock_token,
            )

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

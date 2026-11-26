"""Access keys for the public demo: personal, revocable, expiring, with daily quotas.

Only a SHA-256 of each key is stored. Keys are 144-bit random tokens, so a plain hash is
enough (no password stretching needed) and lookups stay a single indexed query.
Quotas count questions per UTC day, per key and for the whole server. A question that
fails on the provider side is refunded, so provider incidents do not burn a visitor's quota.
"""

import hashlib
import secrets
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

KEY_PREFIX = "k8s_"

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL UNIQUE,
    hint TEXT NOT NULL,
    label TEXT NOT NULL,
    daily_limit INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT,
    total_questions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS access_usage (
    key_id INTEGER NOT NULL REFERENCES access_keys(id),
    day TEXT NOT NULL,
    questions INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, day)
);

CREATE INDEX IF NOT EXISTS idx_access_usage_day ON access_usage(day);
"""


class AccessError(Exception):
    """Raised for a request the demo must refuse; ``code`` is stable for the UI."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AccessKey:
    id: int
    hint: str
    label: str
    daily_limit: int
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    total_questions: int

    def state(self, now: datetime) -> str:
        if self.revoked_at:
            return "revoked"
        return "expired" if now >= self.expires_at else "active"


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode()).hexdigest()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row(row: sqlite3.Row) -> AccessKey:
    return AccessKey(
        id=row["id"],
        hint=row["hint"],
        label=row["label"],
        daily_limit=row["daily_limit"],
        created_at=_dt(row["created_at"]),  # type: ignore[arg-type]
        expires_at=_dt(row["expires_at"]),  # type: ignore[arg-type]
        revoked_at=_dt(row["revoked_at"]),
        last_used_at=_dt(row["last_used_at"]),
        total_questions=row["total_questions"],
    )


def next_reset(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)


class AccessStore:
    def __init__(self, path: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.path = path
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Autocommit mode; write paths open BEGIN IMMEDIATE so a quota check and its
        # increment are one atomic step even with parallel requests.
        with closing(sqlite3.connect(self.path, timeout=30, isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    def _day(self) -> str:
        return self.clock().date().isoformat()

    # ---------------------------------------------------------------- management
    def create(self, label: str, days: int, daily_limit: int) -> tuple[AccessKey, str]:
        if days < 1 or daily_limit < 1:
            raise ValueError("days and daily_limit must be positive")
        secret = KEY_PREFIX + secrets.token_urlsafe(18)
        now = self.clock()
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO access_keys (key_hash, hint, label, daily_limit, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    hash_key(secret),
                    f"{secret[:8]}…{secret[-4:]}",
                    label.strip() or "unnamed",
                    daily_limit,
                    now.isoformat(),
                    (now + timedelta(days=days)).isoformat(),
                ),
            )
            key_id = cursor.lastrowid
        return self.get(key_id), secret  # type: ignore[arg-type]

    def get(self, key_id: int) -> AccessKey:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM access_keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            raise KeyError(f"no access key with id {key_id}")
        return _row(row)

    def list(self) -> list[tuple[AccessKey, int]]:
        """All keys with today's question count, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT k.*, COALESCE(u.questions, 0) AS today FROM access_keys k "
                "LEFT JOIN access_usage u ON u.key_id = k.id AND u.day = ? ORDER BY k.id DESC",
                (self._day(),),
            ).fetchall()
        return [(_row(row), row["today"]) for row in rows]

    def revoke(self, key_id: int) -> AccessKey:
        self.get(key_id)  # KeyError for an unknown id
        with self._transaction() as connection:
            connection.execute(
                "UPDATE access_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (self.clock().isoformat(), key_id),
            )
        return self.get(key_id)

    def extend(self, key_id: int, days: int, daily_limit: int | None = None) -> AccessKey:
        """Make the key valid for ``days`` from now (and optionally change its limit)."""
        key = self.get(key_id)
        if key.revoked_at:
            raise ValueError("a revoked key cannot be extended; create a new one")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE access_keys SET expires_at = ?, daily_limit = ? WHERE id = ?",
                (
                    (self.clock() + timedelta(days=days)).isoformat(),
                    daily_limit or key.daily_limit,
                    key_id,
                ),
            )
        return self.get(key_id)

    # ---------------------------------------------------------------- requests
    def authenticate(self, secret: str | None) -> AccessKey:
        if not secret:
            raise AccessError(401, "key_required", "An access key is required.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM access_keys WHERE key_hash = ?", (hash_key(secret),)
            ).fetchone()
        if row is None:
            raise AccessError(401, "invalid_key", "This access key is not valid.")
        key = _row(row)
        state = key.state(self.clock())
        if state == "revoked":
            raise AccessError(401, "key_revoked", "This access key has been revoked.")
        if state == "expired":
            raise AccessError(401, "key_expired", "This access key has expired.")
        return key

    def used_today(self, key_id: int | None = None) -> int:
        with self._connect() as connection:
            if key_id is None:
                row = connection.execute(
                    "SELECT COALESCE(SUM(questions), 0) FROM access_usage WHERE day = ?",
                    (self._day(),),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COALESCE(SUM(questions), 0) FROM access_usage "
                    "WHERE day = ? AND key_id = ?",
                    (self._day(), key_id),
                ).fetchone()
        return int(row[0])

    def reserve(self, key: AccessKey, global_daily_limit: int) -> str:
        """Count one question against the key and the server; returns the usage day."""
        day = self._day()
        reset = next_reset(self.clock()).strftime("%H:%M UTC")
        with self._transaction() as connection:
            mine = connection.execute(
                "SELECT questions FROM access_usage WHERE key_id = ? AND day = ?", (key.id, day)
            ).fetchone()
            everyone = connection.execute(
                "SELECT COALESCE(SUM(questions), 0) FROM access_usage WHERE day = ?", (day,)
            ).fetchone()[0]
            if (mine["questions"] if mine else 0) >= key.daily_limit:
                raise AccessError(
                    429,
                    "key_quota",
                    f"This key has reached its daily limit ({key.daily_limit}). "
                    f"It resets at {reset}.",
                )
            if everyone >= global_daily_limit:
                raise AccessError(
                    429,
                    "global_quota",
                    f"The demo has reached its daily question budget. It resets at {reset}.",
                )
            connection.execute(
                "INSERT INTO access_usage (key_id, day, questions) VALUES (?, ?, 1) "
                "ON CONFLICT(key_id, day) DO UPDATE SET questions = questions + 1",
                (key.id, day),
            )
            connection.execute(
                "UPDATE access_keys SET last_used_at = ?, total_questions = total_questions + 1 "
                "WHERE id = ?",
                (self.clock().isoformat(), key.id),
            )
        return day

    def refund(self, key: AccessKey, day: str) -> None:
        """Give back a question that failed on the provider side."""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE access_usage SET questions = MAX(questions - 1, 0) "
                "WHERE key_id = ? AND day = ?",
                (key.id, day),
            )
            connection.execute(
                "UPDATE access_keys SET total_questions = MAX(total_questions - 1, 0) WHERE id = ?",
                (key.id,),
            )


class FailureThrottle:
    """In-memory limit on invalid-key attempts per client address."""

    def __init__(self, limit: int = 10, window_seconds: float = 600):
        self.limit = limit
        self.window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, client: str, now: float) -> deque[float]:
        events = self._events.setdefault(client, deque())
        while events and now - events[0] > self.window:
            events.popleft()
        return events

    def check(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._recent(client, now)
            if len(events) >= self.limit:
                wait = max(1, round((self.window - (now - events[0])) / 60))
                raise AccessError(
                    429,
                    "too_many_attempts",
                    f"Too many invalid keys from this address. Try again in {wait} min.",
                )

    def failed(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._recent(client, now).append(now)
            if len(self._events) > 10_000:  # bound memory under a spray of addresses
                self._events = {
                    address: events
                    for address, events in self._events.items()
                    if events and now - events[-1] <= self.window
                }

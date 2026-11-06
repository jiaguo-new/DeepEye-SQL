"""Durable fixed-count request budget and at-most-once intent ledger.

Only a fresh claim returned after its transaction commits permits one send.
All exceptions (including uncertain commit errors) prohibit sending. A RESERVED
or UNKNOWN intent consumes its count permanently and can never be resent. A
COMPLETE result contains only a SHA-256 reference, not the response; callers must
persist the sanitized object before completion and verify it on retrieval. This
module does not perform transport, object storage, or claimant authentication.
A Claim is a freely constructible, reusable value, not an enforced capability:
callers must bind it to the same scope/operation/request and send at most once.
The cap applies to distinct reservations in this one ledger, not actual HTTP
requests, token/money expenditure, or other ledger paths. Caller-supplied hashes
are not canonicalized or tied to an actual request by this module.

Provision with create(), then open() with the same scope digest and budget. Use
one stable path on a trusted local filesystem with reliable SQLite locks and
fsync; do not delete, replace, restore, or externally modify the database, its
WAL/SHM, or its directory. A live instance detects a missing or replaced main
file using device/inode identity. Across restarts, replacement with a compatible
old copy or coordinated tampering cannot be detected without an external trust
anchor. The on-disk hashes detect accidental inconsistencies, not adversaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat


class LedgerError(RuntimeError):
    """Ledger identity or data is invalid; never dispatch."""


class BudgetExhausted(LedgerError):
    """No remaining count for a new logical operation."""


@dataclass(frozen=True, slots=True)
class Claim:
    """Only acquired=True authorizes one send by the receiving caller."""
    acquired: bool
    state: str
    response_sha256: str | None = None


_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_APPLICATION_ID = 0x42424C31
_VERSION = 1
_META_SQL = """CREATE TABLE budget (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    scope_sha256 TEXT NOT NULL,
    request_limit INTEGER NOT NULL CHECK (request_limit >= 0),
    spent INTEGER NOT NULL CHECK (spent >= 0 AND spent <= request_limit),
    record_sha256 TEXT NOT NULL
)"""
_INTENTS_SQL = """CREATE TABLE intents (
    operation_sha256 TEXT PRIMARY KEY NOT NULL,
    request_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('RESERVED', 'COMPLETE', 'UNKNOWN')),
    response_sha256 TEXT,
    record_sha256 TEXT NOT NULL,
    CHECK ((state = 'COMPLETE' AND response_sha256 IS NOT NULL) OR
           (state != 'COMPLETE' AND response_sha256 IS NULL))
) WITHOUT ROWID"""


def _digest(value: str, name: str) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _limit(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("request_limit must be a nonnegative SQLite integer")
    return value


def _hash(*parts: object) -> str:
    # Components here have unambiguous boundaries and restricted alphabets.
    return hashlib.sha256("\0".join(str(part) for part in parts).encode("ascii")).hexdigest()


def _budget_hash(scope: str, limit: int, spent: int) -> str:
    return _hash("budget-v1", scope, limit, spent)


def _intent_hash(operation: str, request: str, state: str, response: str | None) -> str:
    return _hash("intent-v1", operation, request, state, response or "")


class BudgetLedger:
    """Explicitly initialized, process-safe SQLite WAL/FULL request budget.

    Each operation uses a fresh connection and serializes under BEGIN IMMEDIATE.
    No refund, reset, deletion, or retry/dispatch API is provided.
    """

    def __init__(self, path: os.PathLike[str] | str, scope_sha256: str, request_limit: int):
        self._path = Path(path).absolute()
        self._scope = _digest(scope_sha256, "scope_sha256")
        self._limit = _limit(request_limit)
        self._identity: tuple[int, int] | None = None

    @classmethod
    def create(cls, path: os.PathLike[str] | str, scope_sha256: str,
               request_limit: int) -> BudgetLedger:
        """Create exclusively; an initialization failure leaves the file in place."""
        ledger = cls(path, scope_sha256, request_limit)
        fd = os.open(ledger._path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            info = os.fstat(fd)
            ledger._identity = (info.st_dev, info.st_ino)
            os.fsync(fd)
        finally:
            os.close(fd)
        directory_fd = os.open(ledger._path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        db = ledger._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_META_SQL)
            db.execute(_INTENTS_SQL)
            db.execute("INSERT INTO budget VALUES (1, ?, ?, 0, ?)",
                       (ledger._scope, ledger._limit, _budget_hash(ledger._scope, ledger._limit, 0)))
            db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={_VERSION}")
            db.commit()
        finally:
            db.close()
        ledger._check()
        return ledger

    @classmethod
    def open(cls, path: os.PathLike[str] | str, scope_sha256: str,
             request_limit: int) -> BudgetLedger:
        """Open an existing ledger, requiring its original scope and limit."""
        ledger = cls(path, scope_sha256, request_limit)
        ledger._check()
        return ledger

    def _stat(self) -> None:
        info = self._path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise LedgerError("ledger must be a private regular file")
        identity = (info.st_dev, info.st_ino)
        if self._identity is None:
            self._identity = identity
        elif self._identity != identity:
            raise LedgerError("ledger file was replaced")

    def _connect(self) -> sqlite3.Connection:
        self._stat()
        db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True,
                             isolation_level=None, timeout=30)
        try:
            self._stat()
            if db.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise LedgerError("ledger requires SQLite WAL mode")
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA synchronous").fetchone() != (2,):
                raise LedgerError("ledger requires SQLite FULL synchronous mode")
            db.execute("PRAGMA trusted_schema=OFF")
            return db
        except BaseException:
            db.close()
            raise

    def _validate(self, db: sqlite3.Connection) -> int:
        if db.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,) or db.execute(
                "PRAGMA user_version").fetchone() != (_VERSION,):
            raise LedgerError("invalid ledger identity or version")
        schema = db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        if schema != [("table", "budget", _META_SQL), ("table", "intents", _INTENTS_SQL)]:
            raise LedgerError("invalid ledger schema")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise LedgerError("ledger integrity check failed")
        rows = db.execute("SELECT singleton, scope_sha256, request_limit, spent, record_sha256 FROM budget").fetchall()
        if len(rows) != 1:
            raise LedgerError("invalid budget record count")
        singleton, scope, limit, spent, checksum = rows[0]
        try:
            _digest(scope, "stored scope")
            _digest(checksum, "stored budget checksum")
            if (singleton != 1 or type(limit) is not int or type(spent) is not int or
                    not 0 <= spent <= limit or checksum != _budget_hash(scope, limit, spent)):
                raise ValueError("invalid budget record")
        except (ValueError, TypeError, UnicodeError) as exc:
            raise LedgerError("corrupt budget record") from exc
        if scope != self._scope or limit != self._limit:
            raise LedgerError("budget scope or limit mismatch")
        count = 0
        for operation, request, state, response, checksum in db.execute(
                "SELECT operation_sha256, request_sha256, state, response_sha256, record_sha256 FROM intents"):
            try:
                _digest(operation, "stored operation")
                _digest(request, "stored request")
                _digest(checksum, "stored intent checksum")
                if state not in ("RESERVED", "UNKNOWN", "COMPLETE"):
                    raise ValueError("invalid state")
                if state == "COMPLETE":
                    _digest(response, "stored response")
                elif response is not None:
                    raise ValueError("unexpected response")
                if checksum != _intent_hash(operation, request, state, response):
                    raise ValueError("intent checksum mismatch")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise LedgerError("corrupt intent") from exc
            count += 1
        if count != spent:
            raise LedgerError("budget count does not match intents")
        return spent

    def _check(self) -> None:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._validate(db)
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _read(db: sqlite3.Connection, operation: str, request: str) -> Claim | None:
        row = db.execute("SELECT request_sha256, state, response_sha256 FROM intents "
                         "WHERE operation_sha256=?", (operation,)).fetchone()
        if row is None:
            return None
        saved_request, state, response = row
        if saved_request != request:
            raise LedgerError("request identity mismatch")
        return Claim(False, state, response)

    def claim(self, operation_sha256: str, request_sha256: str) -> Claim:
        """Reserve and debit atomically, or return a non-sending existing claim."""
        operation = _digest(operation_sha256, "operation_sha256")
        request = _digest(request_sha256, "request_sha256")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            spent = self._validate(db)
            result = self._read(db, operation, request)
            if result is None:
                if spent >= self._limit:
                    raise BudgetExhausted("request budget exhausted")
                db.execute("INSERT INTO intents VALUES (?, ?, 'RESERVED', NULL, ?)",
                           (operation, request, _intent_hash(operation, request, "RESERVED", None)))
                db.execute("UPDATE budget SET spent=?, record_sha256=? WHERE singleton=1",
                           (spent + 1, _budget_hash(self._scope, self._limit, spent + 1)))
                result = Claim(True, "RESERVED")
            self._stat()
            db.commit()
            return result
        finally:
            db.close()

    def complete(self, operation_sha256: str, request_sha256: str, response_sha256: str) -> None:
        """Record a durable external object digest; ownership is caller-enforced."""
        self._transition(operation_sha256, request_sha256, "COMPLETE",
                         _digest(response_sha256, "response_sha256"))

    def mark_unknown(self, operation_sha256: str, request_sha256: str) -> None:
        """Permanently record an uncertain outcome without refunding its cost."""
        self._transition(operation_sha256, request_sha256, "UNKNOWN", None)

    def _transition(self, operation_sha256: str, request_sha256: str,
                    target: str, response: str | None) -> None:
        operation = _digest(operation_sha256, "operation_sha256")
        request = _digest(request_sha256, "request_sha256")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._validate(db)
            current = self._read(db, operation, request)
            if current is None:
                raise LedgerError("operation was not reserved")
            if current.state == target and current.response_sha256 == response:
                pass
            elif current.state != "RESERVED":
                raise LedgerError("terminal ledger state cannot be changed")
            else:
                db.execute("UPDATE intents SET state=?, response_sha256=?, record_sha256=? "
                           "WHERE operation_sha256=?", (target, response,
                           _intent_hash(operation, request, target, response), operation))
            self._stat()
            db.commit()
        finally:
            db.close()

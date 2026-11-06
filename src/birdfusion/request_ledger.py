"""Durable, process-safe at-most-once request intent ledger (no networking).

Callers must identify a logical operation with a unique 32-byte opaque digest and
supply a SHA-256 of the *exact* effective request identity (including all
route/parameter distinctions). Only the caller receiving a fresh claim may send,
exactly once, and only after claim() returns. Never send on any other result or
on an exception. A crash after commit but before returning intentionally loses
availability, not safety. RESERVED and UNKNOWN never authorize another send.

The ledger stores only lowercase SHA-256 digests and fixed state strings, never
requests, credentials, responses, paths, error text, or owner tokens. COMPLETE
stores a content-addressed immutable response reference: callers must securely
persist and sanitize the response themselves, verify its SHA-256 on retrieval,
and make the object durable *before* calling complete(). This module does not
access or validate the external object.

Use a trusted local filesystem/directory, a single stable database path, and
SQLite-compatible locking and durable fsync. Do not delete, restore old copies,
replace or externally modify the ledger, WAL, or shared-memory files. Such
rollback/tampering and external object-store failures cannot be detected here.
SQLite corruption, schema mismatch and inconsistent records fail closed; SQLite
and commit errors propagate and must never be treated as authorization to send.
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
    """Ledger state or identity is inconsistent; do not dispatch."""


@dataclass(frozen=True, slots=True)
class Claim:
    """A claim result. Only acquired=True authorizes one dispatch."""

    acquired: bool
    state: str
    response_sha256: str | None = None


_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_APPLICATION_ID = 0x42524C31
_VERSION = 1
_TABLE_SQL = """CREATE TABLE intents (
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


def _record_hash(operation: str, request: str, state: str, response: str | None) -> str:
    return hashlib.sha256((operation + request + state + (response or "")).encode("ascii")).hexdigest()


class RequestLedger:
    """SQLite WAL/FULL ledger. No retries or administrative reset are provided.

    Initialize explicitly with create() once, then use open() for existing data.
    Missing, empty or unrelated databases are never implicitly initialized by
    open(). The caller must ensure only trusted processes use the database path.
    Each operation opens its own connection, making instances safe across fork
    and threads; SQLite serializes concurrent writers with BEGIN IMMEDIATE.
    """

    def __init__(self, path: os.PathLike[str] | str):
        self._path = Path(path).absolute()

    @classmethod
    def create(cls, path: os.PathLike[str] | str) -> RequestLedger:
        """Create a new ledger, refusing any existing filesystem entry.

        Initialization failure leaves the reserved file in place: never delete
        and silently recreate an uncertain ledger. Provision it before dispatch.
        """
        ledger = cls(path)
        fd = os.open(ledger._path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        # Persist the new directory entry before any caller can dispatch.
        directory_fd = os.open(ledger._path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        db = ledger._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_TABLE_SQL)
            db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={_VERSION}")
            db.commit()
        finally:
            db.close()
        ledger._check()
        return ledger

    @classmethod
    def open(cls, path: os.PathLike[str] | str) -> RequestLedger:
        """Open an already initialized ledger; never create a missing one."""
        ledger = cls(path)
        ledger._check()
        return ledger

    def _connect(self) -> sqlite3.Connection:
        # mode=rw rejects missing files rather than creating a fresh ledger.
        # Refuse insecure or non-regular pre-existing files, including symlinks.
        info = self._path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise LedgerError("ledger must be a private regular file")
        db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True,
                             isolation_level=None, timeout=30)
        try:
            mode = db.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode != ("wal",):
                raise LedgerError("ledger requires SQLite WAL mode")
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA synchronous").fetchone() != (2,):
                raise LedgerError("ledger requires SQLite FULL synchronous mode")
            db.execute("PRAGMA trusted_schema=OFF")
            return db
        except BaseException:
            db.close()
            raise

    def _validate_db(self, db: sqlite3.Connection) -> None:
        if db.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,) or db.execute(
            "PRAGMA user_version"
        ).fetchone() != (_VERSION,):
            raise LedgerError("invalid ledger identity or version")
        schema = db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        if schema != [("table", "intents", _TABLE_SQL)]:
            raise LedgerError("invalid ledger schema")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise LedgerError("ledger integrity check failed")
        # Inspect every key as well as every value: a damaged key otherwise could
        # hide an existing intent and accidentally authorize a second dispatch.
        for operation, request in db.execute("SELECT operation_sha256, request_sha256 FROM intents"):
            try:
                _digest(operation, "stored operation")
                _digest(request, "stored request")
            except (TypeError, ValueError) as exc:
                raise LedgerError("corrupt ledger identity") from exc
            self._read(db, operation, request)

    def _check(self) -> None:
        db = self._connect()
        try:
            self._validate_db(db)
        finally:
            db.close()

    @staticmethod
    def _read(db: sqlite3.Connection, operation: str, request: str) -> Claim | None:
        row = db.execute("SELECT request_sha256, state, response_sha256, record_sha256 "
                         "FROM intents WHERE operation_sha256=?", (operation,)).fetchone()
        if row is None:
            return None
        saved_request, state, response, checksum = row
        try:
            _digest(saved_request, "stored request")
            _digest(checksum, "stored record")
            if state not in ("RESERVED", "COMPLETE", "UNKNOWN"):
                raise ValueError("invalid state")
            if state == "COMPLETE":
                _digest(response, "stored response")
            elif response is not None:
                raise ValueError("unexpected response")
            if checksum != _record_hash(operation, saved_request, state, response):
                raise ValueError("record checksum mismatch")
        except (ValueError, TypeError, UnicodeError) as exc:
            raise LedgerError("corrupt ledger record") from exc
        if saved_request != request:
            raise LedgerError("request identity mismatch")
        return Claim(False, state, response)

    def claim(self, operation_sha256: str, request_sha256: str) -> Claim:
        """Atomically reserve an operation or return its existing state.

        A fresh, committed reservation alone yields acquired=True. Matching
        COMPLETE returns the immutable response digest for caller-verified replay.
        A different request digest for the same operation always raises.
        """
        operation = _digest(operation_sha256, "operation_sha256")
        request = _digest(request_sha256, "request_sha256")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._validate_db(db)
            result = self._read(db, operation, request)
            if result is None:
                state = "RESERVED"
                db.execute("INSERT INTO intents VALUES (?, ?, ?, NULL, ?)",
                           (operation, request, state, _record_hash(operation, request, state, None)))
                result = Claim(True, state)
            db.commit()
            return result
        finally:
            db.close()

    def complete(self, operation_sha256: str, request_sha256: str,
                 response_sha256: str) -> None:
        """Record the durable, sanitized, content-addressed success object.

        Only the original claimant may call this after its dispatch succeeds.
        The API does not authenticate caller ownership; cooperating callers must
        enforce that rule. A duplicate identical completion is harmless; an
        UNKNOWN or a conflicting completion can never be changed.
        """
        response = _digest(response_sha256, "response_sha256")
        self._transition(operation_sha256, request_sha256, "COMPLETE", response)

    def mark_unknown(self, operation_sha256: str, request_sha256: str) -> None:
        """Record an uncertain outcome; it can never be reset or completed."""
        self._transition(operation_sha256, request_sha256, "UNKNOWN", None)

    def _transition(self, operation_sha256: str, request_sha256: str,
                    target: str, response: str | None) -> None:
        operation = _digest(operation_sha256, "operation_sha256")
        request = _digest(request_sha256, "request_sha256")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._validate_db(db)
            current = self._read(db, operation, request)
            if current is None:
                raise LedgerError("operation was not reserved")
            if current.state == target and current.response_sha256 == response:
                db.commit()
                return
            if current.state != "RESERVED":
                raise LedgerError("terminal ledger state cannot be changed")
            db.execute("UPDATE intents SET state=?, response_sha256=?, record_sha256=? "
                       "WHERE operation_sha256=?", (target, response,
                       _record_hash(operation, request, target, response), operation))
            db.commit()
        finally:
            db.close()

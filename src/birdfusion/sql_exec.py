"""Read-only SQLite execution for candidate clustering, never for gold scoring.

The SQLite authorizer is defense in depth, not a sandbox for untrusted databases
or SQLite extensions. It rejects all PRAGMAs, writes, known external-I/O
functions and known external virtual tables, but cannot inspect arbitrary
virtual-table modules or user-defined functions for side effects. Denying all
PRAGMAs can also prevent some otherwise read-only virtual-table queries. Only
open trusted database files with a trusted SQLite build. The timeout is
cooperative: SQLite progress callbacks and fetch checks cannot interrupt every
blocking operation or guarantee a wall-clock deadline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from numbers import Real
import sqlite3
import time
from pathlib import Path


class QueryTimeout(Exception):
    pass


@dataclass
class ExecResult:
    ok: bool
    official_key: str | None
    n_rows: int | None
    empty: bool | None
    timeout: bool
    error_class: str | None
    error_message: str | None = None

    def to_dict(self):
        return asdict(self)


def result_key(rows) -> str:
    def value_key(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Exact rational representation follows Python's int/float equality,
            # including signed zero and large integers that floats cannot represent.
            # SQLite converts NaN to NULL; infinities compare by sign.
            if isinstance(value, float) and not math.isfinite(value):
                if math.isnan(value):
                    raise ValueError("NaN cannot be safely represented as an equality fingerprint")
                return ("number-infinity", "+" if value > 0 else "-")
            numerator, denominator = value.as_integer_ratio()
            return ("number", numerator, denominator)
        if value is None:
            return ("none",)
        return (type(value).__name__, repr(value))

    values = {tuple(value_key(v) for v in row) for row in rows}
    return hashlib.sha256(repr(tuple(sorted(values))).encode()).hexdigest()


# Extension names commonly exposed by the SQLite CLI or third-party builds.
# This list is not exhaustive; see the module docstring's trust limitation.
_EXTERNAL_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "eval"})
_EXTERNAL_TABLES = frozenset({"fsdir", "zipfile", "zipfile_cds"})
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
})


def _read_only_authorizer(action, arg1, arg2, database, source):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _EXTERNAL_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        table = (arg1 or "").lower()
        if table in _EXTERNAL_TABLES or table.startswith("pragma_"):
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def execute_sql(path: str | Path, sql: str, timeout_s: float = 30.0,
                max_result_rows: int = 10000) -> ExecResult:
    # Validate before opening a database; bool is not a meaningful numeric limit.
    try:
        valid_timeout = (isinstance(timeout_s, Real) and not isinstance(timeout_s, bool)
                         and math.isfinite(timeout_s) and timeout_s > 0)
    except (OverflowError, TypeError, ValueError):
        valid_timeout = False
    if not valid_timeout:
        return ExecResult(False, None, None, None, False, "invalid_argument",
                          "timeout_s must be finite and positive")
    if (not isinstance(max_result_rows, int) or isinstance(max_result_rows, bool)
            or max_result_rows <= 0):
        return ExecResult(False, None, None, None, False, "invalid_argument",
                          "max_result_rows must be a positive integer")

    started = time.monotonic()
    con = None
    try:
        if not sql or not sql.strip():
            return ExecResult(False, None, None, None, False, "empty", "SQL is empty")
        path = Path(path).resolve()
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        con.execute("PRAGMA query_only = ON")
        con.set_authorizer(_read_only_authorizer)

        def progress():
            if time.monotonic() - started >= timeout_s:
                raise QueryTimeout()
            return 0

        con.set_progress_handler(progress, 1000)
        cur = con.execute(sql)
        rows = []
        while True:
            if time.monotonic() - started >= timeout_s:
                raise QueryTimeout()
            batch = cur.fetchmany(1000)
            if not batch:
                break
            rows.extend(batch)
            if len(rows) > max_result_rows:
                return ExecResult(False, None, None, None, False, "row_cap", "public result row cap exceeded")
        empty = not rows or all(all(v is None or v == "" for v in row) for row in rows)
        return ExecResult(True, result_key(rows), len(rows), empty, False, None, None)
    except QueryTimeout:
        return ExecResult(False, None, None, None, True, "timeout", "query timed out")
    except sqlite3.Error as exc:
        message = str(exc).lower()
        if "interrupted" in message and time.monotonic() - started >= timeout_s:
            return ExecResult(False, None, None, None, True, "timeout", "query timed out")
        cls = ("table" if "no such table" in message else
               "column" if "no such column" in message else
               "syntax" if "syntax" in message or "near" in message else
               "readonly" if "readonly" in message or "read-only" in message else "other")
        return ExecResult(False, None, None, None, False, cls, str(exc)[:1000])
    except Exception as exc:
        return ExecResult(False, None, None, None, False, "other", f"{type(exc).__name__}: {exc}"[:1000])
    finally:
        if con is not None:
            con.close()

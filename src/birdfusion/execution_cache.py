"""Conservative, standalone identities and successful-result cache for snapshots.

This module does not execute SQL and is not integrated with sql_exec. A caller
must use the exact snapshot, SQL, ordered candidate metadata, execution limits,
SQLite build, and result semantics represented here, and must independently
prove determinism (including dependencies such as functions, collations,
virtual tables, environment, row ordering and connection configuration).
An authorization is an assertion by that caller, *not* a SQL safety analysis.
The caller also must ensure the ExecResult being stored actually came from the
exact identity and execution policy in question; this module does not verify or
produce such an execution receipt. Fingerprint and policy version strings are
caller assertions, not measured runtime identities, and the SQL need not occur
in the candidate list. Never authorize time/random-dependent, externally
dependent, or otherwise unproven queries. Only successful ExecResult values can
be stored.

Security boundary: snapshot and cache directories and all their ancestors must
be trusted and not concurrently modified by another process; the snapshot must
remain isolated and immutable before, during, and after query execution. Checks
on each cache access reject symlinks, WAL/SHM/journal sidecars, changes to the
file's observable identity/stat and any bytes not matching the manifest. These
checks cannot prevent an adversarial ABA replacement, same-stat mutation, or a
change between verification and a separate SQLite open. In particular, this
module does not make sql_exec.execute_sql's path-based open race-free. Use only
when those race assumptions are independently enforced. Hashes and strict JSON
validation detect accidental corruption, not malicious cache tampering.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from typing import Any, Sequence

from .sql_exec import ExecResult
from .sqlite_snapshot import SnapshotManifest

_CACHE_SCHEMA = 1
_IDENTITY_SCHEMA = 1
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CACHE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Candidate:
    """A candidate's exact UTF-8 SQL bytes and caller-defined JSON metadata.

    Candidate order and duplicates are significant. Metadata is restricted to
    JSON values with string keys, integral numbers and no duplicate keys.
    """
    sql: bytes
    metadata: Any


def _canonical(value: Any) -> bytes:
    def validate(item: Any) -> None:
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is list:
            for element in item:
                validate(element)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for element in item.values():
                validate(element)
            return
        raise ValueError("metadata must contain only JSON values with integer numbers")
    validate(value)
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("ascii")
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid JSON metadata") from exc


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sql_bytes(sql: bytes) -> bytes:
    if type(sql) is not bytes:
        raise ValueError("SQL must be exact UTF-8 bytes")
    try:
        sql.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError("SQL must be exact UTF-8 bytes") from exc
    return sql


def _no_symlink_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if stat.S_ISLNK(os.lstat(part).st_mode):
            raise ValueError("symlink in snapshot path")
    return path


def _no_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(path) + suffix):
            raise ValueError("snapshot has SQLite sidecar")


def _stat_identity(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns)


def verify_snapshot(manifest: SnapshotManifest) -> str:
    """Hash snapshot bytes anew, rejecting sidecars, links and observed mutation.

    Raises ValueError/OSError on an invalid, changed, or inaccessible snapshot.
    This verifies the snapshot file, not its provenance or future immutability.
    """
    if not isinstance(manifest, SnapshotManifest) or not _HEX.fullmatch(manifest.snapshot_sha256):
        raise ValueError("invalid snapshot manifest")
    path = _no_symlink_path(Path(manifest.snapshot_path))
    _no_sidecars(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("snapshot must be a regular file")
        if _stat_identity(os.lstat(path)) != _stat_identity(before):
            raise ValueError("snapshot replaced while opening")
        hasher = hashlib.sha256()
        # Bound reads to the initial size: a concurrently growing file must not
        # make verification unbounded. The final stat/digest checks fail closed.
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("snapshot truncated while hashing")
            hasher.update(chunk)
            remaining -= len(chunk)
        if (_stat_identity(os.fstat(fd)) != _stat_identity(before)
                or _stat_identity(os.lstat(path)) != _stat_identity(before)):
            raise ValueError("snapshot changed while hashing")
        _no_symlink_path(path)
        _no_sidecars(path)
        digest = hasher.hexdigest()
        if digest != manifest.snapshot_sha256:
            raise ValueError("snapshot digest does not match manifest")
        return digest
    finally:
        os.close(fd)


def _runtime_sqlite() -> tuple[str, str, tuple[str, ...]]:
    connection = sqlite3.connect(":memory:")
    try:
        version, source_id = connection.execute(
            "SELECT sqlite_version(), sqlite_source_id()"
        ).fetchone()
        options = tuple(sorted(row[0] for row in connection.execute("PRAGMA compile_options")))
        return version, source_id, options
    finally:
        connection.close()


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    """Inputs for one exact execution; construction alone does not authorize reuse."""
    manifest: SnapshotManifest
    database_id: str
    sql: bytes
    candidates: Sequence[Candidate]
    timeout_s: float
    max_result_rows: int
    policy_version: str
    result_fingerprint_version: str

    def key(self) -> str:
        """Validate all inputs, reverify the snapshot and return the identity hash."""
        m = self.manifest
        digest = verify_snapshot(m)
        if (not all(type(x) is str and x for x in
                    (self.database_id, self.policy_version, self.result_fingerprint_version,
                     m.source_path, m.sqlite_version, m.sqlite_source_id))
                or type(m.source_device) is not int or m.source_device < 0
                or type(m.source_inode) is not int or m.source_inode < 0):
            raise ValueError("invalid identity fields")
        if (type(self.timeout_s) not in (int, float) or not math.isfinite(self.timeout_s)
                or self.timeout_s <= 0 or type(self.max_result_rows) is not int
                or self.max_result_rows <= 0):
            raise ValueError("invalid execution limits")
        version, source_id, options = _runtime_sqlite()
        if (version != m.sqlite_version or source_id != m.sqlite_source_id
                or options != tuple(sorted(m.sqlite_compile_options))):
            raise ValueError("SQLite runtime does not match snapshot manifest")
        if type(self.candidates) not in (tuple, list):
            raise ValueError("candidates must be an ordered list or tuple")
        candidate_digests = []
        for candidate in self.candidates:
            if not isinstance(candidate, Candidate):
                raise ValueError("invalid candidate")
            candidate_digests.append({"sql_sha256": _digest(_sql_bytes(candidate.sql)),
                                      "metadata_sha256": _digest(_canonical(candidate.metadata))})
        payload = {
            "identity_schema": _IDENTITY_SCHEMA,
            "database_id": self.database_id,
            "source": {"path": m.source_path, "device": m.source_device, "inode": m.source_inode},
            "snapshot_sha256": digest,
            "sql_sha256": _digest(_sql_bytes(self.sql)),
            "candidates": candidate_digests,
            "timeout_s_hex": float(self.timeout_s).hex(),
            "max_result_rows": self.max_result_rows,
            "sqlite": {"version": version, "source_id": source_id, "options": list(options)},
            "policy_version": self.policy_version,
            "result_fingerprint_version": self.result_fingerprint_version,
        }
        return _digest(_canonical(payload))


def _valid_result(value: Any) -> bool:
    if type(value) is not dict or set(value) != {
        "ok", "official_key", "n_rows", "empty", "timeout", "error_class", "error_message"
    }:
        return False
    return (value["ok"] is True and type(value["official_key"]) is str
            and _HEX.fullmatch(value["official_key"]) is not None
            and type(value["n_rows"]) is int and value["n_rows"] >= 0
            and type(value["empty"]) is bool
            and (value["n_rows"] != 0 or value["empty"])
            and value["timeout"] is False and value["error_class"] is None
            and value["error_message"] is None)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class ExecutionCache:
    """File-backed cache. Every load/store revalidates its snapshot and identity.

    The directory must already exist and be trusted. Writes use same-directory
    temporary files and atomic replacement; this is not a durability guarantee.
    Failed/unknown results are never stored, irrespective of authorization.
    """
    def __init__(self, directory: os.PathLike[str] | str):
        self.directory = Path(directory)

    def load(self, identity: CacheIdentity, *, deterministic_authorized: bool = False) -> ExecResult | None:
        if deterministic_authorized is not True:
            return None
        try:
            key = identity.key()
            path = self.directory / (key + ".json")
            # O_NOFOLLOW plus a size bound avoids following a corrupt cache entry.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_CACHE_BYTES:
                    return None
                with os.fdopen(fd, "rb") as file:
                    fd = -1
                    raw = file.read(_MAX_CACHE_BYTES + 1)
                if len(raw) > _MAX_CACHE_BYTES:
                    return None
            finally:
                if fd != -1:
                    os.close(fd)
            envelope = json.loads(raw, object_pairs_hook=_unique_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (type(envelope) is not dict or set(envelope) != {"schema", "key", "result", "checksum"}
                    or type(envelope["schema"]) is not int or envelope["schema"] != _CACHE_SCHEMA
                    or envelope["key"] != key or not _valid_result(envelope["result"])
                    or type(envelope["checksum"]) is not str
                    or envelope["checksum"] != _digest(_canonical(envelope["result"]))
                    or envelope["result"]["n_rows"] > identity.max_result_rows):
                return None
            # Catch changes while reading the cache entry, as well as each open.
            if identity.key() != key:
                return None
            return ExecResult(**envelope["result"])
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return None

    def store(self, identity: CacheIdentity, result: ExecResult, *,
              deterministic_authorized: bool = False) -> bool:
        if deterministic_authorized is not True or not isinstance(result, ExecResult):
            return False
        value = result.to_dict()
        if not _valid_result(value):
            return False
        temporary = None
        try:
            key = identity.key()
            if value["n_rows"] > identity.max_result_rows:
                return False
            envelope = {"schema": _CACHE_SCHEMA, "key": key, "result": value,
                        "checksum": _digest(_canonical(value))}
            data = _canonical(envelope)
            fd, temporary = tempfile.mkstemp(prefix=".execution-cache-", dir=self.directory)
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            if identity.key() != key:
                return False
            os.replace(temporary, self.directory / (key + ".json"))
            temporary = None
            return True
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return False
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

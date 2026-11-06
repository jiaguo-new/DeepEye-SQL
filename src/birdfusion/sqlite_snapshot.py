"""Create standalone, read-only SQLite online-backup snapshots.

The source identity is the resolved database pathname and its device/inode at
open time, not a hash of its main file: committed data can reside in the WAL.
The SHA-256 is of the closed snapshot file itself.  Callers must use a trusted,
non-concurrently-mutated destination directory; SQLite opens the reserved path
by name and cannot be given an already-open file descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    source_path: str
    source_device: int
    source_inode: int
    snapshot_path: str
    snapshot_sha256: str
    sqlite_version: str
    sqlite_source_id: str
    sqlite_compile_options: tuple[str, ...]


def _same_file(path: Path, identity: os.stat_result) -> bool:
    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(current.st_mode) and current.st_dev == identity.st_dev
            and current.st_ino == identity.st_ino)


def create_snapshot(source: os.PathLike[str] | str,
                    destination: os.PathLike[str] | str) -> SnapshotManifest:
    """Back up *source* to a fresh *destination* and return its manifest.

    The source is opened using SQLite's mode=ro URI, without immutable=1, so
    committed WAL content is included.  Backup reflects a consistent SQLite
    point in time, not necessarily the time this function was called when
    another connection is concurrently committing.  Existing destinations
    (including dangling symlinks) are never overwritten.  On failure, cleanup
    only unlinks the file reserved by this call, if its identity still matches.

    Source and destination directory entries must not be replaced concurrently
    by an adversary.  A source database must be a regular on-disk SQLite file;
    a destination must have an existing parent directory.  This operation does
    not protect the completed snapshot from later modification by its caller.
    """
    source_path = Path(source).resolve(strict=True)
    source_stat = source_path.stat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("source must be a regular SQLite database file")
    destination_input = Path(destination).absolute()
    destination_path = destination_input.parent.resolve(strict=True) / destination_input.name

    # O_EXCL refuses all existing entries, including symlinks.  The open file
    # descriptor is kept until completion to prevent inode reuse during cleanup.
    fd = os.open(destination_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    reserved = os.fstat(fd)
    try:
        # as_uri quotes ?, #, %, and other URI metacharacters in the path.
        source_uri = source_path.as_uri() + "?mode=ro"
        destination_uri = destination_path.as_uri() + "?mode=rw"
        src = sqlite3.connect(source_uri, uri=True)
        try:
            dst = sqlite3.connect(destination_uri, uri=True)
            try:
                src.backup(dst)
                version, source_id = dst.execute(
                    "SELECT sqlite_version(), sqlite_source_id()"
                ).fetchone()
                options = tuple(row[0] for row in dst.execute(
                    "PRAGMA compile_options"
                ))
            finally:
                dst.close()
        finally:
            src.close()

        if not _same_file(destination_path, reserved):
            raise RuntimeError("snapshot destination was replaced during backup")
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        if not _same_file(destination_path, reserved):
            raise RuntimeError("snapshot destination was replaced during hashing")
        return SnapshotManifest(
            source_path=str(source_path),
            source_device=source_stat.st_dev,
            source_inode=source_stat.st_ino,
            snapshot_path=str(destination_path),
            snapshot_sha256=digest.hexdigest(),
            sqlite_version=version,
            sqlite_source_id=source_id,
            sqlite_compile_options=options,
        )
    except BaseException:
        if _same_file(destination_path, reserved):
            os.unlink(destination_path)
        raise
    finally:
        os.close(fd)

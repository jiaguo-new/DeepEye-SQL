from contextlib import closing
import dataclasses
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from birdfusion.sqlite_snapshot import create_snapshot
from _platform import safe_symlink as _safe_symlink


class SQLiteSnapshotTests(unittest.TestCase):
    def test_wal_snapshot_includes_commits_excludes_pending_and_stays_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source ?#% database.sqlite"
            destination = root / "snapshot ?#%.sqlite"
            writer = sqlite3.connect(source)
            self.addCleanup(writer.close)
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE items (value TEXT)")
            writer.execute("INSERT INTO items VALUES ('committed')")
            writer.commit()
            self.assertGreater((root / (source.name + "-wal")).stat().st_size, 0)
            writer.execute("INSERT INTO items VALUES ('uncommitted')")

            manifest = create_snapshot(source, destination)
            with closing(sqlite3.connect(destination)) as snapshot:
                self.assertEqual(snapshot.execute("SELECT value FROM items").fetchall(),
                                 [("committed",)])
            initial_bytes = destination.read_bytes()
            self.assertEqual(manifest.snapshot_sha256, hashlib.sha256(initial_bytes).hexdigest())
            identity = source.stat()
            self.assertEqual((manifest.source_path, manifest.source_device, manifest.source_inode),
                             (str(source.resolve()), identity.st_dev, identity.st_ino))
            self.assertEqual(manifest.snapshot_path, str(destination))
            self.assertEqual(manifest.sqlite_version, sqlite3.sqlite_version)
            self.assertTrue(manifest.sqlite_source_id)
            self.assertIsInstance(manifest.sqlite_compile_options, tuple)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                manifest.snapshot_sha256 = "changed"

            writer.commit()
            writer.execute("INSERT INTO items VALUES ('later')")
            writer.commit()
            self.assertEqual(destination.read_bytes(), initial_bytes)
            with closing(sqlite3.connect(destination)) as snapshot:
                self.assertEqual(snapshot.execute("SELECT value FROM items").fetchall(),
                                 [("committed",)])
            writer.close()

    def test_existing_destination_and_dangling_symlink_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE items (value)")
                connection.commit()
            destination = root / "existing.sqlite"
            destination.write_bytes(b"keep me")
            with self.assertRaises(FileExistsError):
                create_snapshot(source, destination)
            self.assertEqual(destination.read_bytes(), b"keep me")
            link = root / "link.sqlite"
            _safe_symlink(link, root / "missing.sqlite")
            with self.assertRaises(FileExistsError):
                create_snapshot(source, link)
            self.assertTrue(link.is_symlink())

    def test_destination_in_symlinked_parent_uses_actual_reserved_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE items (value)")
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            _safe_symlink(alias, target, target_is_directory=True)
            manifest = create_snapshot(source, alias / "snapshot.sqlite")
            self.assertEqual(manifest.snapshot_path, str(target / "snapshot.sqlite"))
            self.assertEqual(manifest.snapshot_sha256,
                             hashlib.sha256((target / "snapshot.sqlite").read_bytes()).hexdigest())

    def test_invalid_and_missing_sources_leave_no_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "snapshot.sqlite"
            with self.assertRaises(FileNotFoundError):
                create_snapshot(root / "missing.sqlite", destination)
            self.assertFalse(destination.exists())
            invalid = root / "invalid.sqlite"
            invalid.write_bytes(b"not a database")
            with self.assertRaises(sqlite3.DatabaseError):
                create_snapshot(invalid, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(invalid.read_bytes(), b"not a database")

    def test_failure_never_removes_replaced_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE items (value)")
                connection.commit()
            destination = root / "snapshot.sqlite"
            def replace_then_fail(*args, **kwargs):
                os.unlink(destination)
                destination.write_bytes(b"replacement")
                raise sqlite3.OperationalError("simulated failure")

            with mock.patch("birdfusion.sqlite_snapshot.sqlite3.connect", side_effect=replace_then_fail):
                with self.assertRaises(sqlite3.OperationalError):
                    create_snapshot(source, destination)
            self.assertEqual(destination.read_bytes(), b"replacement")


if __name__ == "__main__":
    unittest.main()

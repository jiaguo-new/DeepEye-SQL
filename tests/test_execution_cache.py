from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from birdfusion.execution_cache import CacheIdentity, Candidate, ExecutionCache, verify_snapshot
from birdfusion.sql_exec import ExecResult, execute_sql
from birdfusion.sqlite_snapshot import create_snapshot
from _platform import safe_symlink as _safe_symlink


class ExecutionCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / "source.sqlite"
        with closing(sqlite3.connect(source)) as con:
            con.execute("CREATE TABLE items (v INTEGER)")
            con.execute("INSERT INTO items VALUES (42)")
            con.commit()
        self.manifest = create_snapshot(source, self.root / "snapshot ?#%.sqlite")
        self.identity = CacheIdentity(
            self.manifest, "db", b"SELECT v FROM items",
            [Candidate(b"SELECT v FROM items", {"id": "x", "rank": 1}),
             Candidate(b"SELECT v FROM items", {"id": "x", "rank": 1})],
            10.0, 100, "policy-v1", "result-v1")
        self.cache = ExecutionCache(self.root)
        self.result = execute_sql(self.manifest.snapshot_path, self.identity.sql.decode(), 10.0, 100)
        self.assertTrue(self.result.ok)

    def test_default_deny_and_authorized_success_roundtrip(self):
        self.assertFalse(self.cache.store(self.identity, self.result))
        self.assertIsNone(self.cache.load(self.identity))
        self.assertTrue(self.cache.store(self.identity, self.result, deterministic_authorized=True))
        self.assertIsNone(self.cache.load(self.identity))
        self.assertIsNone(self.cache.load(self.identity, deterministic_authorized=1))
        self.assertEqual(self.cache.load(self.identity, deterministic_authorized=True), self.result)
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)

    def test_every_identity_input_and_duplicates_matter(self):
        key = self.identity.key()
        changes = [
            {"database_id": "other"}, {"sql": b"SELECT v FROM items "},
            {"timeout_s": 11}, {"max_result_rows": 101},
            {"policy_version": "policy-v2"}, {"result_fingerprint_version": "result-v2"},
            {"candidates": self.identity.candidates[:1]},
            {"candidates": [self.identity.candidates[0], Candidate(b"SELECT 1", {"id": "x", "rank": 1})]},
            {"candidates": [self.identity.candidates[0], Candidate(b"SELECT v FROM items", {"id": "x", "rank": 2})]},
            {"manifest": replace(self.manifest, source_path=self.manifest.source_path + "other")},
            {"manifest": replace(self.manifest, source_inode=self.manifest.source_inode + 1)},
            {"manifest": replace(self.manifest, source_device=self.manifest.source_device + 1)},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertNotEqual(key, replace(self.identity, **change).key())
        a = Candidate(b"SELECT 1", {"id": "a"})
        b = Candidate(b"SELECT 2", {"id": "b"})
        self.assertNotEqual(replace(self.identity, candidates=[a, b]).key(),
                            replace(self.identity, candidates=[b, a]).key())
        self.assertEqual(key, replace(self.identity, candidates=list(self.identity.candidates)).key())

    def test_sqlite_build_mismatch_rejected(self):
        for changed in ({"sqlite_source_id": "another"}, {"sqlite_version": "0"},
                        {"sqlite_compile_options": ("UNKNOWN",)}):
            with self.subTest(changed=changed):
                identity = replace(self.identity, manifest=replace(self.manifest, **changed))
                with self.assertRaises(ValueError):
                    identity.key()
                self.assertIsNone(self.cache.load(identity, deterministic_authorized=True))

    def test_errors_and_unknown_never_cached(self):
        for result in [ExecResult(False, None, None, None, True, "timeout", "timeout"),
                       ExecResult(False, None, None, None, False, "row_cap", "cap"),
                       ExecResult(False, None, None, None, False, "other", "error"),
                       ExecResult(False, None, None, None, False, None, None),
                       ExecResult(True, None, 1, False, False, None, None),
                       ExecResult(True, "a" * 64, 101, False, False, None, None)]:
            with self.subTest(result=result):
                self.assertFalse(self.cache.store(self.identity, result, deterministic_authorized=True))
        self.assertIsNone(self.cache.load(self.identity, deterministic_authorized=True))

    def test_sidecars_and_symlinks_rejected(self):
        snapshot = Path(self.manifest.snapshot_path)
        for suffix in ("-wal", "-shm", "-journal"):
            path = Path(str(snapshot) + suffix)
            path.write_bytes(b"")
            with self.assertRaises(ValueError):
                verify_snapshot(self.manifest)
            self.assertIsNone(self.cache.load(self.identity, deterministic_authorized=True))
            path.unlink()
        link = self.root / "link.sqlite"
        _safe_symlink(link, snapshot)
        with self.assertRaises(ValueError):
            verify_snapshot(replace(self.manifest, snapshot_path=str(link)))
        parent_link = self.root / "alias"
        _safe_symlink(parent_link, self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            verify_snapshot(replace(self.manifest, snapshot_path=str(parent_link / snapshot.name)))

    def test_snapshot_mutation_invalidates_existing_cache(self):
        self.assertTrue(self.cache.store(self.identity, self.result, deterministic_authorized=True))
        snapshot = Path(self.manifest.snapshot_path)
        data = bytearray(snapshot.read_bytes())
        data[-1] ^= 1
        snapshot.write_bytes(data)
        with self.assertRaises(ValueError):
            verify_snapshot(self.manifest)
        self.assertIsNone(self.cache.load(self.identity, deterministic_authorized=True))
        self.assertFalse(self.cache.store(self.identity, self.result, deterministic_authorized=True))

    def test_corrupt_legacy_wrong_schema_and_partial_entries_miss(self):
        self.assertTrue(self.cache.store(self.identity, self.result, deterministic_authorized=True))
        path = self.root / (self.identity.key() + ".json")
        original = path.read_bytes()
        entries = [b"{", b"{}", b'{"schema":1,"schema":1}', b"null", b'NaN']
        envelope = json.loads(original)
        for change in ({"schema": 0}, {"schema": True}, {"key": "0" * 64},
                       {"checksum": "0" * 64}, {"extra": 1},
                       {"result": {**envelope["result"], "ok": False}},
                       {"result": {**envelope["result"], "n_rows": True}}):
            entries.append(json.dumps({**envelope, **change}).encode())
        for data in entries:
            with self.subTest(data=data):
                path.write_bytes(data)
                self.assertIsNone(self.cache.load(self.identity, deterministic_authorized=True))
        path.unlink()
        _safe_symlink(path, self.root / "missing")
        self.assertIsNone(self.cache.load(self.identity, deterministic_authorized=True))

    def test_atomic_replace_failure_keeps_previous_file(self):
        self.assertTrue(self.cache.store(self.identity, self.result, deterministic_authorized=True))
        path = self.root / (self.identity.key() + ".json")
        before = path.read_bytes()
        with mock.patch("birdfusion.execution_cache.os.replace", side_effect=OSError("fail")):
            self.assertFalse(self.cache.store(self.identity, self.result, deterministic_authorized=True))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".execution-cache-*")))

    def test_observed_mutation_during_hash_is_rejected(self):
        real_read = os.read
        snapshot = Path(self.manifest.snapshot_path)
        changed = False
        def mutate(fd, size):
            nonlocal changed
            chunk = real_read(fd, size)
            if chunk and not changed:
                changed = True
                with open(snapshot, "ab") as file:
                    file.write(b"x")
            return chunk
        with mock.patch("birdfusion.execution_cache.os.read", side_effect=mutate):
            with self.assertRaises(ValueError):
                verify_snapshot(self.manifest)

    def test_continuously_growing_snapshot_read_is_bounded(self):
        real_read = os.read
        snapshot = Path(self.manifest.snapshot_path)
        calls = 0
        def mutate(fd, size):
            nonlocal calls
            calls += 1
            if calls > 2:
                self.fail("snapshot verification read beyond its original size")
            chunk = real_read(fd, size)
            if chunk:
                with open(snapshot, "ab") as file:
                    file.write(b"x")
            return chunk
        with mock.patch("birdfusion.execution_cache.os.read", side_effect=mutate):
            with self.assertRaises(ValueError):
                verify_snapshot(self.manifest)

    def test_invalid_inputs_fail_closed(self):
        invalid = [replace(self.identity, sql="SELECT 1"),
                   replace(self.identity, sql=b"\xff"),
                   replace(self.identity, timeout_s=float("nan")),
                   replace(self.identity, timeout_s=0),
                   replace(self.identity, max_result_rows=True),
                   replace(self.identity, max_result_rows=0),
                   replace(self.identity, candidates=[Candidate(b"SELECT 1", {"x": 1.0})])]
        for identity in invalid:
            with self.subTest(identity=identity):
                self.assertIsNone(self.cache.load(identity, deterministic_authorized=True))
                self.assertFalse(self.cache.store(identity, self.result, deterministic_authorized=True))


if __name__ == "__main__":
    unittest.main()

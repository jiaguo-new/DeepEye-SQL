import hashlib
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from birdfusion.request_ledger import Claim, LedgerError, RequestLedger
from _platform import safe_symlink as _safe_symlink


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


OP = digest("logical operation")
REQUEST = digest("exact effective request identity")
RESPONSE = digest("sanitized response object")


def claim_worker(path, gate, queue):
    try:
        ledger = RequestLedger.open(path)
        gate.wait(15)
        result = ledger.claim(OP, REQUEST)
        queue.put((result.acquired, result.state))
    except BaseException as exc:
        queue.put(("error", repr(exc)))


def crash_worker(path, stage):
    ledger = RequestLedger.open(path)
    if stage == "uncommitted":
        db = ledger._connect()
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO intents VALUES (?, ?, 'RESERVED', NULL, ?)",
                   (OP, REQUEST, "0" * 64))
        os._exit(31)
    result = ledger.claim(OP, REQUEST)
    if not result.acquired:
        os._exit(32)
    if stage == "complete":
        ledger.complete(OP, REQUEST, RESPONSE)
    elif stage == "unknown":
        ledger.mark_unknown(OP, REQUEST)
    os._exit(0)


class RequestLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger ?#.sqlite"
        self.ledger = RequestLedger.create(self.path)

    def test_claim_completion_and_replay(self):
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(True, "RESERVED"))
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(False, "RESERVED"))
        self.ledger.complete(OP, REQUEST, RESPONSE)
        self.ledger.complete(OP, REQUEST, RESPONSE)
        self.assertEqual(RequestLedger.open(self.path).claim(OP, REQUEST),
                         Claim(False, "COMPLETE", RESPONSE))
        with self.assertRaises(LedgerError):
            self.ledger.complete(OP, REQUEST, digest("different"))
        with self.assertRaises(LedgerError):
            self.ledger.mark_unknown(OP, REQUEST)

    def test_unknown_is_terminal_and_identity_is_exact(self):
        self.ledger.claim(OP, REQUEST)
        self.ledger.mark_unknown(OP, REQUEST)
        self.ledger.mark_unknown(OP, REQUEST)
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(False, "UNKNOWN"))
        with self.assertRaises(LedgerError):
            self.ledger.complete(OP, REQUEST, RESPONSE)
        for action in (lambda: self.ledger.claim(OP, digest("other")),
                       lambda: self.ledger.mark_unknown(OP, digest("other")),
                       lambda: self.ledger.complete(OP, digest("other"), RESPONSE)):
            with self.assertRaises(LedgerError):
                action()
        with self.assertRaises(LedgerError):
            self.ledger.mark_unknown(digest("missing"), REQUEST)

    def test_input_validation_and_credentials_never_written(self):
        secret = "Bearer sk-test-secret-credentials"
        for bad in (secret, "a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n", None, b"a" * 64):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.ledger.claim(bad, REQUEST)
            with self.assertRaises(ValueError):
                self.ledger.claim(OP, bad)
            with self.assertRaises(ValueError):
                self.ledger.complete(OP, REQUEST, bad)
        self.ledger.claim(OP, REQUEST)
        self.ledger.complete(OP, REQUEST, RESPONSE)
        for file in Path(self.tmp.name).iterdir():
            self.assertNotIn(secret.encode(), file.read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_explicit_create_open_and_database_schema(self):
        with self.assertRaises(FileExistsError):
            RequestLedger.create(self.path)
        missing = self.path.parent / "missing"
        with self.assertRaises(FileNotFoundError):
            RequestLedger.open(missing)
        self.assertFalse(missing.exists())
        unrelated = self.path.parent / "unrelated"
        with sqlite3.connect(unrelated) as db:
            db.execute("CREATE TABLE other (x)")
        with self.assertRaises(LedgerError):
            RequestLedger.open(unrelated)
        self.path.chmod(0o644)
        with self.assertRaises(LedgerError):
            RequestLedger.open(self.path)
        self.path.chmod(0o600)
        link = self.path.parent / "link"
        _safe_symlink(link, self.path)
        with self.assertRaises(LedgerError):
            RequestLedger.open(link)
        db = self.ledger._connect()
        try:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone(), (2,))
        finally:
            db.close()

    def test_concurrent_processes_only_one_claim(self):
        ctx = multiprocessing.get_context("spawn")
        gate = ctx.Event()
        queue = ctx.Queue()
        workers = [ctx.Process(target=claim_worker, args=(str(self.path), gate, queue))
                   for _ in range(12)]
        for worker in workers:
            worker.start()
        gate.set()
        results = [queue.get(timeout=40) for _ in workers]
        for worker in workers:
            worker.join(40)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(results.count((True, "RESERVED")), 1, results)
        self.assertEqual(results.count((False, "RESERVED")), 11, results)
        queue.close()

    def test_process_crash_before_and_after_commits(self):
        ctx = multiprocessing.get_context("spawn")
        worker = ctx.Process(target=crash_worker, args=(str(self.path), "uncommitted"))
        worker.start()
        worker.join(30)
        self.assertEqual(worker.exitcode, 31)
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(True, "RESERVED"))
        for stage, expected in (("reserved", Claim(False, "RESERVED")),
                                ("unknown", Claim(False, "UNKNOWN")),
                                ("complete", Claim(False, "COMPLETE", RESPONSE))):
            with self.subTest(stage=stage):
                path = self.path.parent / (stage + ".sqlite")
                RequestLedger.create(path)
                worker = ctx.Process(target=crash_worker, args=(str(path), stage))
                worker.start()
                worker.join(30)
                self.assertEqual(worker.exitcode, 0)
                self.assertEqual(RequestLedger.open(path).claim(OP, REQUEST), expected)

    def test_commit_error_is_not_a_claim(self):
        class CommitFails(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("simulated uncertain commit")
        actual_connect = sqlite3.connect
        def connect(*args, **kwargs):
            kwargs["factory"] = CommitFails
            return actual_connect(*args, **kwargs)
        with mock.patch("birdfusion.request_ledger.sqlite3.connect", side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.ledger.claim(OP, REQUEST)
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(True, "RESERVED"))

    def test_corrupt_records_schema_and_database_fail_closed(self):
        self.ledger.claim(OP, REQUEST)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE intents SET request_sha256=? WHERE operation_sha256=?",
                       (digest("altered"), OP))
        with self.assertRaises(LedgerError):
            self.ledger.claim(OP, REQUEST)
        with self.assertRaises(LedgerError):
            self.ledger.claim(digest("unrelated operation"), REQUEST)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE extra (data)")
        with self.assertRaises(LedgerError):
            self.ledger.claim(digest("new"), REQUEST)
        corrupt = self.path.parent / "corrupt"
        corrupt.write_bytes(b"not sqlite")
        corrupt.chmod(0o600)
        with self.assertRaises(sqlite3.DatabaseError):
            RequestLedger.open(corrupt)


if __name__ == "__main__":
    unittest.main()

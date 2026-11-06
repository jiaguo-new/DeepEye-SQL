from contextlib import closing
import hashlib
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from birdfusion.budget_ledger import BudgetExhausted, BudgetLedger, Claim, LedgerError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


SCOPE = digest("frozen manifest and scope")
REQUEST = digest("effective request")
RESPONSE = digest("sanitized response")
OP = digest("logical operation")


def contention_worker(path, gate, queue, index, same):
    try:
        ledger = BudgetLedger.open(path, SCOPE, 4)
        gate.wait(30)
        result = ledger.claim(OP if same else digest(str(index)), REQUEST)
        queue.put(("claim", result.acquired))
    except BudgetExhausted:
        queue.put(("exhausted", False))
    except BaseException as exc:
        queue.put(("error", repr(exc)))


def crash_worker(path, stage):
    ledger = BudgetLedger.open(path, SCOPE, 1)
    if stage == "uncommitted":
        db = ledger._connect()
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO intents VALUES (?, ?, 'RESERVED', NULL, ?)", (OP, REQUEST, "0" * 64))
        db.execute("UPDATE budget SET spent=1, record_sha256=?", ("0" * 64,))
        os._exit(31)
    if not ledger.claim(OP, REQUEST).acquired:
        os._exit(32)
    if stage == "complete":
        ledger.complete(OP, REQUEST, RESPONSE)
    elif stage == "unknown":
        ledger.mark_unknown(OP, REQUEST)
    os._exit(0)


class BudgetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget ?#.sqlite"
        self.ledger = BudgetLedger.create(self.path, SCOPE, 2)

    def test_exhaustion_reclaims_terminal_states_and_restart(self):
        other = digest("other")
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(True, "RESERVED"))
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(False, "RESERVED"))
        self.assertEqual(self.ledger.claim(other, REQUEST), Claim(True, "RESERVED"))
        self.ledger.mark_unknown(other, REQUEST)
        self.ledger.mark_unknown(other, REQUEST)
        self.ledger.complete(OP, REQUEST, RESPONSE)
        self.ledger.complete(OP, REQUEST, RESPONSE)
        reopened = BudgetLedger.open(self.path, SCOPE, 2)
        self.assertEqual(reopened.claim(OP, REQUEST), Claim(False, "COMPLETE", RESPONSE))
        self.assertEqual(reopened.claim(other, REQUEST), Claim(False, "UNKNOWN"))
        with self.assertRaises(BudgetExhausted):
            reopened.claim(digest("new"), REQUEST)
        with self.assertRaises(LedgerError):
            reopened.claim(OP, digest("changed request"))
        with self.assertRaises(LedgerError):
            reopened.complete(other, REQUEST, RESPONSE)
        with self.assertRaises(LedgerError):
            reopened.complete(OP, REQUEST, digest("different response"))
        with self.assertRaises(LedgerError):
            reopened.mark_unknown(OP, REQUEST)
        with self.assertRaises(LedgerError):
            reopened.complete(digest("missing"), REQUEST, RESPONSE)

    def test_scope_limit_create_and_missing_or_replaced_file(self):
        with self.assertRaises(FileExistsError):
            BudgetLedger.create(self.path, SCOPE, 2)
        for scope, limit in ((digest("changed"), 2), (SCOPE, 3)):
            with self.assertRaises(LedgerError):
                BudgetLedger.open(self.path, scope, limit)
        missing = self.path.parent / "missing"
        with self.assertRaises(FileNotFoundError):
            BudgetLedger.open(missing, SCOPE, 2)
        self.assertFalse(missing.exists())
        replacement = self.path.parent / "replacement"
        BudgetLedger.create(replacement, SCOPE, 2)
        os.replace(replacement, self.path)
        with self.assertRaises(LedgerError):
            self.ledger.claim(OP, REQUEST)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.ledger.claim(OP, REQUEST)

    def test_private_files_digest_validation_and_no_sensitive_data(self):
        secret = "Bearer secret credential prompt response"
        invalid = (secret, "a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n", None, b"a" * 64)
        for bad in invalid:
            for fn in (lambda: self.ledger.claim(bad, REQUEST),
                       lambda: self.ledger.claim(OP, bad),
                       lambda: self.ledger.complete(OP, REQUEST, bad),
                       lambda: BudgetLedger.open(self.path, bad, 2)):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    fn()
        for limit in (-1, True, 1.0, 2**63):
            with self.assertRaises(ValueError):
                BudgetLedger.open(self.path, SCOPE, limit)
        self.ledger.claim(OP, REQUEST)
        self.ledger.complete(OP, REQUEST, RESPONSE)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        for file in self.path.parent.iterdir():
            self.assertNotIn(secret.encode(), file.read_bytes())
            self.assertEqual(file.stat().st_mode & 0o077, 0)
        self.path.chmod(0o644)
        with self.assertRaises(LedgerError):
            BudgetLedger.open(self.path, SCOPE, 2)
        self.path.chmod(0o600)
        link = self.path.parent / "link"
        link.symlink_to(self.path)
        with self.assertRaises(LedgerError):
            BudgetLedger.open(link, SCOPE, 2)
        db = self.ledger._connect()
        try:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone(), (2,))
        finally:
            db.close()

    def test_process_contention_distinct_and_same_operations(self):
        ctx = multiprocessing.get_context("spawn")
        for same in (False, True):
            with self.subTest(same=same):
                path = self.path.parent / ("same" if same else "distinct")
                BudgetLedger.create(path, SCOPE, 4)
                gate, queue = ctx.Event(), ctx.Queue()
                workers = [ctx.Process(target=contention_worker, args=(str(path), gate, queue, i, same))
                           for i in range(16)]
                for worker in workers:
                    worker.start()
                gate.set()
                results = [queue.get(timeout=60) for _ in workers]
                for worker in workers:
                    worker.join(60)
                    self.assertEqual(worker.exitcode, 0)
                queue.close()
                self.assertEqual(results.count(("claim", True)), 1 if same else 4, results)
                self.assertEqual(results.count(("claim", False)), 15 if same else 0, results)
                self.assertEqual(results.count(("exhausted", False)), 0 if same else 12, results)
                with closing(sqlite3.connect(path)) as db, db:
                    self.assertEqual(db.execute("SELECT spent FROM budget").fetchone()[0], 1 if same else 4)
                    self.assertEqual(db.execute("SELECT count(*) FROM intents").fetchone()[0], 1 if same else 4)

    def test_process_crash_before_and_after_commit(self):
        ctx = multiprocessing.get_context("spawn")
        for stage in ("uncommitted", "reserved", "unknown", "complete"):
            with self.subTest(stage=stage):
                path = self.path.parent / stage
                BudgetLedger.create(path, SCOPE, 1)
                worker = ctx.Process(target=crash_worker, args=(str(path), stage))
                worker.start()
                worker.join(30)
                self.assertEqual(worker.exitcode, 31 if stage == "uncommitted" else 0)
                ledger = BudgetLedger.open(path, SCOPE, 1)
                expected = {"uncommitted": Claim(True, "RESERVED"),
                            "reserved": Claim(False, "RESERVED"),
                            "unknown": Claim(False, "UNKNOWN"),
                            "complete": Claim(False, "COMPLETE", RESPONSE)}[stage]
                self.assertEqual(ledger.claim(OP, REQUEST), expected)
                with self.assertRaises(BudgetExhausted):
                    ledger.claim(digest("new"), REQUEST)

    def test_commit_errors_never_authorize_even_if_committed(self):
        class CommitFails(sqlite3.Connection):
            def commit(self):
                super().commit()
                raise sqlite3.OperationalError("uncertain commit")
        actual = sqlite3.connect
        def connect(*args, **kwargs):
            kwargs["factory"] = CommitFails
            return actual(*args, **kwargs)
        with mock.patch("birdfusion.budget_ledger.sqlite3.connect", side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.ledger.claim(OP, REQUEST)
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(False, "RESERVED"))
        self.assertEqual(self.ledger.claim(digest("other"), REQUEST), Claim(True, "RESERVED"))
        with self.assertRaises(BudgetExhausted):
            self.ledger.claim(digest("third"), REQUEST)

    def test_corruption_count_records_schema_and_identity_fail_closed(self):
        self.ledger.claim(OP, REQUEST)
        for name, statement, values in (
            ("count", "UPDATE budget SET spent=0", ()),
            ("count-rehashed", "UPDATE budget SET spent=0, record_sha256=?", (self._budget_zero_hash(),)),
            ("scope", "UPDATE budget SET scope_sha256=?", (digest("changed"),)),
            ("request", "UPDATE intents SET request_sha256=?", (digest("changed"),)),
            ("delete", "DELETE FROM intents", ()),
            ("schema", "CREATE TABLE extra (data)", ()),
            ("app", "PRAGMA application_id=0", ()),
        ):
            with self.subTest(name=name):
                path = self.path.parent / name
                ledger = BudgetLedger.create(path, SCOPE, 2)
                ledger.claim(OP, REQUEST)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(statement, values)
                with self.assertRaises((LedgerError, sqlite3.DatabaseError)):
                    ledger.claim(digest("new"), REQUEST)
                with self.assertRaises((LedgerError, sqlite3.DatabaseError)):
                    BudgetLedger.open(path, SCOPE, 2)
        corrupt = self.path.parent / "corrupt"
        corrupt.write_bytes(b"not sqlite")
        corrupt.chmod(0o600)
        with self.assertRaises(sqlite3.DatabaseError):
            BudgetLedger.open(corrupt, SCOPE, 2)

    @staticmethod
    def _budget_zero_hash():
        from birdfusion.budget_ledger import _budget_hash
        return _budget_hash(SCOPE, 2, 0)

    def test_zero_budget_and_unknown_never_refund(self):
        path = self.path.parent / "zero"
        zero = BudgetLedger.create(path, SCOPE, 0)
        with self.assertRaises(BudgetExhausted):
            zero.claim(OP, REQUEST)
        self.ledger.claim(OP, REQUEST)
        self.ledger.mark_unknown(OP, REQUEST)
        self.assertEqual(self.ledger.claim(OP, REQUEST), Claim(False, "UNKNOWN"))
        self.ledger.claim(digest("other"), REQUEST)
        with self.assertRaises(BudgetExhausted):
            self.ledger.claim(digest("third"), REQUEST)


if __name__ == "__main__":
    unittest.main()

"""Adversarial ledger contract tests using only synthetic temporary databases.

The ledger tracks reservations, not actual sends or claimant identity. These tests
must not be read as a guarantee that callers cannot misuse or reuse a Claim.
"""

from contextlib import closing
import hashlib
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest import mock

from birdfusion.budget_ledger import BudgetExhausted, BudgetLedger, Claim, LedgerError


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SCOPE = digest("synthetic scope")
REQUEST = digest("synthetic request")
CHANGED_REQUEST = digest("different synthetic request")
RESPONSE = digest("synthetic response")
OPERATION = digest("synthetic operation")


class AdversarialBudgetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_changed_request_is_rejected_in_every_state_even_when_full_after_reopen(self):
        for state in ("RESERVED", "UNKNOWN", "COMPLETE"):
            with self.subTest(state=state):
                path = self.root / (state + ".sqlite")
                ledger = BudgetLedger.create(path, SCOPE, 1)
                self.assertEqual(ledger.claim(OPERATION, REQUEST), Claim(True, "RESERVED"))
                if state == "UNKNOWN":
                    ledger.mark_unknown(OPERATION, REQUEST)
                elif state == "COMPLETE":
                    ledger.complete(OPERATION, REQUEST, RESPONSE)

                ledger = BudgetLedger.open(path, SCOPE, 1)
                with self.assertRaisesRegex(LedgerError, "request identity mismatch"):
                    ledger.claim(OPERATION, CHANGED_REQUEST)
                with self.assertRaisesRegex(LedgerError, "request identity mismatch"):
                    ledger.mark_unknown(OPERATION, CHANGED_REQUEST)
                with self.assertRaisesRegex(LedgerError, "request identity mismatch"):
                    ledger.complete(OPERATION, CHANGED_REQUEST, RESPONSE)
                with self.assertRaises(BudgetExhausted):
                    ledger.claim(digest("another operation"), REQUEST)

                ledger = BudgetLedger.open(path, SCOPE, 1)
                expected_response = RESPONSE if state == "COMPLETE" else None
                self.assertEqual(ledger.claim(OPERATION, REQUEST),
                                 Claim(False, state, expected_response))
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute("SELECT spent FROM budget").fetchone(), (1,))
                    self.assertEqual(db.execute("SELECT count(*) FROM intents").fetchone(), (1,))

    def test_same_request_distinct_operations_debit_and_terminal_replay_while_full(self):
        path = self.root / "distinct.sqlite"
        first, second, third = (digest(name) for name in ("first", "second", "third"))
        ledger = BudgetLedger.create(path, SCOPE, 2)
        self.assertEqual(ledger.claim(first, REQUEST), Claim(True, "RESERVED"))
        self.assertEqual(ledger.claim(second, REQUEST), Claim(True, "RESERVED"))
        ledger.complete(first, REQUEST, RESPONSE)
        ledger.mark_unknown(second, REQUEST)

        ledger = BudgetLedger.open(path, SCOPE, 2)
        with self.assertRaises(BudgetExhausted):
            ledger.claim(third, REQUEST)
        self.assertEqual(ledger.claim(first, REQUEST), Claim(False, "COMPLETE", RESPONSE))
        self.assertEqual(ledger.claim(second, REQUEST), Claim(False, "UNKNOWN"))
        ledger.complete(first, REQUEST, RESPONSE)
        ledger.mark_unknown(second, REQUEST)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT spent FROM budget").fetchone(), (2,))
            self.assertEqual(db.execute("SELECT count(*), count(DISTINCT request_sha256) FROM intents").fetchone(),
                             (2, 1))

    def test_unexpected_schema_objects_and_pragma_versions_are_rejected(self):
        mutations = {
            "trigger": "CREATE TRIGGER unexpected AFTER INSERT ON intents BEGIN SELECT 1; END",
            "view": "CREATE VIEW unexpected AS SELECT operation_sha256 FROM intents",
            "index": "CREATE INDEX unexpected ON intents(request_sha256)",
            "application_id": "PRAGMA application_id=0",
            "user_version_zero": "PRAGMA user_version=0",
            "user_version_future": "PRAGMA user_version=2",
        }
        for name, statement in mutations.items():
            with self.subTest(mutation=name):
                path = self.root / (name + ".sqlite")
                ledger = BudgetLedger.create(path, SCOPE, 1)
                ledger.claim(OPERATION, REQUEST)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(statement)
                expected = "schema" if name in ("trigger", "view", "index") else "identity or version"
                with self.assertRaisesRegex(LedgerError, expected):
                    BudgetLedger.open(path, SCOPE, 1)
                with self.assertRaisesRegex(LedgerError, expected):
                    ledger.claim(OPERATION, REQUEST)
                with self.assertRaisesRegex(LedgerError, expected):
                    ledger.complete(OPERATION, REQUEST, RESPONSE)

    def test_claims_are_not_capabilities_and_transition_ownership_is_not_enforced(self):
        # This demonstrates a limitation, not authorization or a safe way to send.
        path = self.root / "claimant.sqlite"
        first = BudgetLedger.create(path, SCOPE, 1)
        issued = first.claim(OPERATION, REQUEST)
        forged = Claim(True, "RESERVED")
        self.assertEqual(issued, forged)
        self.assertTrue(issued.acquired)
        self.assertTrue(issued.acquired)  # Merely inspecting a Claim does not consume it.

        # An independent instance that never acquired the claim can transition it.
        other = BudgetLedger.open(path, SCOPE, 1)
        self.assertEqual(other.claim(OPERATION, REQUEST), Claim(False, "RESERVED"))
        other.complete(OPERATION, REQUEST, RESPONSE)
        self.assertEqual(first.claim(OPERATION, REQUEST), Claim(False, "COMPLETE", RESPONSE))
        # No transport is performed: a caller must enforce identity and at-most-once sends.

    def test_synthetic_lifecycle_never_uses_network_or_stores_payload(self):
        path = self.root / "private.sqlite"
        secret_request = "synthetic-secret-request-payload-71e3"
        secret_response = "synthetic-secret-response-payload-96f2"
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            ledger = BudgetLedger.create(path, SCOPE, 1)
            ledger.claim(OPERATION, digest(secret_request))
            ledger.complete(OPERATION, digest(secret_request), digest(secret_response))
            reopened = BudgetLedger.open(path, SCOPE, 1)
            self.assertEqual(reopened.claim(OPERATION, digest(secret_request)),
                             Claim(False, "COMPLETE", digest(secret_response)))
        for file in self.root.iterdir():
            if file.is_file():
                data = file.read_bytes()
                self.assertNotIn(secret_request.encode(), data, file.name)
                self.assertNotIn(secret_response.encode(), data, file.name)


if __name__ == "__main__":
    unittest.main()

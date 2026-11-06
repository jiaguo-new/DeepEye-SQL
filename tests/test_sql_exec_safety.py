"""Regression tests for the read-only SQLite execution boundary."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

from birdfusion.sql_exec import execute_sql, result_key


class SqlExecSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "data.sqlite"
        self.make_database(self.db)

    @staticmethod
    def make_database(path):
        with closing(sqlite3.connect(path)) as con:
            con.execute("CREATE TABLE items (id INTEGER, name TEXT)")
            con.executemany("INSERT INTO items VALUES (?, ?)", [(1, "one"), (2, "two")])
            con.commit()

    def assert_success_rows(self, result, rows):
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.official_key, result_key(rows))
        self.assertEqual(result.n_rows, len(rows))
        self.assertFalse(result.timeout)
        self.assertIsNone(result.error_class)

    def assert_rejected(self, result):
        self.assertFalse(result.ok, result)
        self.assertIsNone(result.official_key)
        self.assertIsNotNone(result.error_class)

    def test_regular_aggregate_and_cte_select(self):
        cases = [
            ("SELECT id, name FROM items ORDER BY id", [(1, "one"), (2, "two")]),
            ("SELECT count(*), sum(id) FROM items", [(2, 3)]),
            ("WITH chosen AS (SELECT id FROM items WHERE id > 1) SELECT id FROM chosen", [(2,)]),
        ]
        for sql, rows in cases:
            with self.subTest(sql=sql):
                self.assert_success_rows(execute_sql(self.db, sql), rows)

    def test_database_filename_uri_special_characters(self):
        # ? and # are illegal filename characters on NTFS; the URI-quoting logic
        # itself is exercised on every platform via the percent case.
        names = ["percent%.sqlite"]
        if os.name != "nt":
            names = ["question?.sqlite", "hash#.sqlite", "percent%.sqlite", "all ?#%.sqlite"]
        for filename in names:
            with self.subTest(filename=filename):
                path = self.root / filename
                try:
                    self.make_database(path)
                except OSError as exc:  # platform refuses the name: skip that case
                    self.skipTest(f"filename unsupported on this platform: {exc}")
                self.assert_success_rows(execute_sql(path, "SELECT count(*) FROM items"), [(2,)])

    def test_attach_and_detach_are_rejected(self):
        attached = self.root / "attached.sqlite"
        self.make_database(attached)
        statements = [
            "ATTACH DATABASE ':memory:' AS extra",
            "ATTACH DATABASE '{}' AS extra".format(str(attached).replace("'", "''")),
            "DETACH DATABASE extra",
        ]
        for sql in statements:
            with self.subTest(sql=sql):
                self.assert_rejected(execute_sql(self.db, sql))

    def test_writes_and_ddl_are_rejected_without_modifying_database(self):
        statements = [
            "INSERT INTO items VALUES (3, 'three')",
            "UPDATE items SET name = 'changed'",
            "DELETE FROM items",
            "CREATE TABLE danger (x)",
            "DROP TABLE items",
            "ALTER TABLE items ADD COLUMN danger TEXT",
            "CREATE TEMP TABLE danger (x)",
        ]
        for sql in statements:
            with self.subTest(sql=sql):
                self.assert_rejected(execute_sql(self.db, sql))
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT id, name FROM items ORDER BY id").fetchall(),
                             [(1, "one"), (2, "two")])
            self.assertEqual(con.execute("PRAGMA table_info(items)").fetchall()[0][1], "id")
            self.assertEqual(len(con.execute("PRAGMA table_info(items)").fetchall()), 2)

    def test_pragma_and_load_extension_are_rejected(self):
        statements = [
            "PRAGMA query_only = OFF",
            "PRAGMA writable_schema = ON",
            "PRAGMA journal_mode = WAL",
            "PRAGMA table_info(items)",
            "SELECT load_extension('nonexistent_extension')",
        ]
        for sql in statements:
            with self.subTest(sql=sql):
                self.assert_rejected(execute_sql(self.db, sql))

    def assert_invalid_option(self, **kwargs):
        try:
            result = execute_sql(self.db, "SELECT 1", **kwargs)
        except ValueError:
            return
        self.assert_rejected(result)

    def test_invalid_timeouts_fail_closed(self):
        for timeout in (float("nan"), float("inf"), float("-inf"), 0, -0.1):
            with self.subTest(timeout=timeout):
                self.assert_invalid_option(timeout_s=timeout)

    def test_invalid_result_caps_fail_closed(self):
        for cap in (False, True, -1, 0, 1.5, "10"):
            with self.subTest(cap=cap):
                self.assert_invalid_option(max_result_rows=cap)

    def test_result_cap_boundary(self):
        self.assert_success_rows(execute_sql(self.db, "SELECT id FROM items", max_result_rows=2),
                                 [(1,), (2,)])
        self.assert_rejected(execute_sql(self.db, "SELECT id FROM items", max_result_rows=1))


if __name__ == "__main__":
    unittest.main()

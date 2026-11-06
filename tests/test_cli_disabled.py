"""The live CLI must stop before opening configuration or benchmark inputs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from birdfusion.cli import main


class CliDisabledTests(unittest.TestCase):
    def test_run_fails_before_reading_or_creating_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(Path, "read_text", side_effect=AssertionError("unexpected read")), \
                 patch.object(Path, "mkdir", side_effect=AssertionError("unexpected mkdir")):
                with self.assertRaisesRegex(RuntimeError, "Live CLI disabled"):
                    main(["run", "--input", str(root / "missing.json"),
                          "--database-root", str(root / "missing-dbs"),
                          "--work", str(root / "work"), "--config", str(root / "missing-config")])

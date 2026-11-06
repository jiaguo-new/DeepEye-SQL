"""Windows-compat helpers for the test suite (remediation P1-4).

On Windows, creating a symlink needs developer mode or admin rights (OSError
WinError 1314), and filenames such as "question?.sqlite" are invalid by NTFS
rules (OSError WinError 123). Neither is a logic defect: skip, don't fail.
"""
import unittest
from pathlib import Path

def safe_symlink(link, target, *, target_is_directory=False):
    """Path.symlink_to with platform-skip semantics (call inside unittest)."""
    try:
        Path(link).symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        raise unittest.SkipTest(f"symlinks unavailable on this platform: {exc}")

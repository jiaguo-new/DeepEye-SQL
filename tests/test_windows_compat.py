"""P1-4 Windows-compat regression tests (runnable on every platform).

1. data/ MANIFEST must byte-match after checkout (guards the .gitattributes EOL freeze).
2. safe_symlink must SKIP (not fail) when symlink permission is denied — simulated on
   every platform so the Windows behaviour is actually asserted even on Linux CI.
3. CI workflow must define the ubuntu+windows matrix.
"""
import hashlib, os, unittest
from pathlib import Path
from unittest import mock

DATA = Path(__file__).resolve().parent.parent / 'data'

class ManifestBytes(unittest.TestCase):
    def test_manifest_matches_file_bytes(self):
        lines = (DATA / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines()
        checked = 0
        for line in lines:
            want, _, name = line.partition('  ')
            p = DATA / name.strip()
            if not p.exists():
                continue  # documented-but-not-redistributed artifact (upstream B)
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(got, want.split()[0], f'SHA mismatch {name}')
            checked += 1
        self.assertGreaterEqual(checked, 5)

class SymlinkSkipSemantics(unittest.TestCase):
    def test_denied_symlink_raises_skiptest(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _platform import safe_symlink
        with mock.patch('pathlib.Path.symlink_to', side_effect=OSError(1314, 'A required privilege is not held by the client.')):
            with self.assertRaises(unittest.SkipTest):
                safe_symlink(Path('some_link'), 'some_target')

class CiMatrix(unittest.TestCase):
    def test_dual_os_workflow_defined(self):
        wf = Path(__file__).resolve().parent.parent / '.github' / 'workflows' / 'ci.yml'
        self.assertTrue(wf.exists())
        text = wf.read_text(encoding='utf-8')
        self.assertIn('windows-latest', text)
        self.assertIn('ubuntu-latest', text)
        self.assertIn('pytest', text)
        self.assertIn('func-timeout', text)

if __name__ == '__main__':
    unittest.main()

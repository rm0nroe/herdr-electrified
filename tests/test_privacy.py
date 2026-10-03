import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest

scan = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/build-electric.py'))['privacy_scan']
HOME = '/Users/alice'


class PrivacyScan(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bundle = Path(tmp.name)

    def write(self, name, data):
        path = self.bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_clean_bundle_passes(self):
        self.write('herdr', b'\0/build/src/main.rs\0')
        self.assertEqual(scan(self.bundle, {}, HOME), [])

    def test_home_path_fails_even_in_upstream_file(self):
        digest = self.write('codex/codex-path/rg', b'x/Users/alice/y')
        self.assertTrue(scan(self.bundle, {'codex/codex-path/rg': digest}, HOME))

    def test_temp_dir_fails(self):
        self.write('herdr', b'x/private/var/folders/ab/T/y')
        self.assertTrue(scan(self.bundle, {}, HOME))

    def test_ci_runner_path_allowed_only_in_unmodified_upstream_file(self):
        digest = self.write('codex/codex-path/rg', b'x/Users/runner/work/y')
        self.assertEqual(scan(self.bundle, {'codex/codex-path/rg': digest}, HOME), [])
        self.assertTrue(scan(self.bundle, {'codex/codex-path/rg': 'other'}, HOME))
        self.assertTrue(scan(self.bundle, {}, HOME))

    def test_ci_temp_path_allowed_only_in_unmodified_upstream_file(self):
        digest = self.write('codex/codex-resources/voice/lib/libopus.0.dylib', b'x/var/folders/nj/T/voice-native/y/Users/runner/z')
        self.assertEqual(scan(self.bundle, {'codex/codex-resources/voice/lib/libopus.0.dylib': digest}, HOME), [])
        self.assertTrue(scan(self.bundle, {'codex/codex-resources/voice/lib/libopus.0.dylib': 'other'}, HOME))
        self.assertTrue(scan(self.bundle, {}, HOME))

    def test_other_user_fails_in_upstream_file(self):
        digest = self.write('codex/bin/helper', b'x/Users/bob/y')
        self.assertTrue(scan(self.bundle, {'codex/bin/helper': digest}, HOME))

    def test_match_across_chunk_boundary(self):
        self.write('herdr', b'a' * (1024 * 1024 - 5) + b'/Users/alice/z')
        self.assertTrue(scan(self.bundle, {}, HOME))


if __name__ == '__main__':
    unittest.main()

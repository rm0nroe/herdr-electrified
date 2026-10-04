"""Run only config validation against an explicitly supplied native Herdr.

Usage: uv run python tests/native_check.py /absolute/path/to/herdr
No server, session, install or GUI is started.
"""
from pathlib import Path
import subprocess
import sys
import unittest

from test_cli import CLI
from test_electric import Electric

BINARY = str(Path(sys.argv.pop(1)).resolve(strict=True))


class Native(CLI):
    def test_native_supported_preset_and_existing_unknown_key(self):
        original = self.target.read_text() + 'unknown_option = 1\n'
        self.target.write_text(original)
        for command in [('apply', '--dry-run'), ('apply', '--yes'), ('check',), ('undo',)]:
            code, result = self.run_cli(*command, '--herdr-bin', BINARY)
            self.assertEqual(code, 0, result)
            self.assertEqual(result['targets'][0]['validation'], 'passed')
        self.assertEqual(self.target.read_text(), original)

    def test_native_with_optional_claude_and_all_component_undo(self):
        original = self.target.read_bytes()
        for command in [('apply', '--dry-run'), ('apply', '--yes'), ('check',), ('undo',)]:
            code, result = self.run_cli(*command, *(('--claude-statusline',) if command[0] != 'undo' else ()), '--herdr-bin', BINARY)
            self.assertEqual(code, 0, result)
            herdr = next(row for row in result['targets'] if row['path'] == str(self.target.resolve()))
            self.assertEqual(herdr['validation'], 'passed')
        self.assertEqual(self.target.read_bytes(), original)
        self.assertFalse((self.root / '.claude/settings.json').exists())
        self.assertFalse((self.root / '.local/share/herdr-electrified/claude-statusline.py').exists())


class NativeElectric(Electric):
    def test_stock_config_check_after_electric_apply(self):
        original = self.target.read_bytes()
        code, result = self.run_cli('apply', '--electric', str(self.bundle()), '--yes')
        self.assertEqual(code, 0, result)
        self.assertEqual(self.target.read_bytes(), original)
        electric = self.root / 'config/herdr-electrified/electric/config.toml'
        self.assertIn('#FF7BC2', electric.read_text())
        for config in (self.target, electric):
            check = subprocess.run([BINARY, 'config', 'check'], env=self.env | {'HERDR_CONFIG_PATH': str(config)},
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(check.stdout.strip(), 'config: ok')
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertFalse(electric.exists())


suite = unittest.TestSuite([Native('test_native_supported_preset_and_existing_unknown_key'),
                           Native('test_native_with_optional_claude_and_all_component_undo'),
                           NativeElectric('test_stock_config_check_after_electric_apply')])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

from importlib.resources import files
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(str(files('herdr_electrified').joinpath('data/claude-statusline.py')))


def run(stdin, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, cwd=cwd, timeout=10)


def plain(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class Statusline(unittest.TestCase):
    def test_renders_directory_context_bar_and_token_counts(self):
        sample = {'workspace': {'current_dir': '/nonexistent/project'}, 'context_window': {
            'used_percentage': 42.4, 'context_window_size': 200000,
            'current_usage': {'input_tokens': 80000, 'cache_creation_input_tokens': 3000, 'cache_read_input_tokens': 1000}}}
        result = run(json.dumps(sample))
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(result.stdout.count('\n'), 1)
        self.assertEqual(plain(result.stdout), '/nonexistent/project > ctx ' + '─' * 10 + ' 42% 84k/200k\n')
        self.assertIn('\x1b[38;2;166;227;161m────\x1b[0m', result.stdout)
        self.assertIn('\x1b[38;2;78;201;212m/nonexistent/project\x1b[0m', result.stdout)

    def test_git_branch_and_dirty_marker_come_from_the_reported_directory(self):
        with tempfile.TemporaryDirectory(prefix='herdr-statusline-') as directory:
            subprocess.run(['git', 'init', '-q', '-b', 'feature-x', directory], check=True)
            Path(directory, 'untracked.txt').write_text('test')
            result = run(json.dumps({'workspace': {'current_dir': directory}}))
        self.assertIn('feature-x *', plain(result.stdout))
        self.assertIn('\x1b[38;2;215;186;125mfeature-x', result.stdout)

    def test_malformed_input_prints_one_minimal_line_without_traceback(self):
        for stdin in ('', '{bad', '[]', 'null', '"text"', '{"workspace":"x","context_window":{"used_percentage":"abc","current_usage":[]}}',
                      '{"context_window":{"used_percentage":1e999,"context_window_size":"big"}}'):
            with self.subTest(stdin=stdin):
                result = run(stdin, cwd=tempfile.gettempdir())
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                self.assertEqual(result.stdout.count('\n'), 1)
                self.assertTrue(plain(result.stdout).strip())

    def test_control_characters_are_stripped_and_home_is_abbreviated(self):
        render = runpy.run_path(str(SCRIPT))['render']
        self.assertEqual(plain(render({'workspace': {'current_dir': '/tmp/a\x1b]0;x\x07b'}})).split(' > ')[0], '/tmp/a]0;xb')
        self.assertTrue(plain(render({'workspace': {'current_dir': str(Path.home() / 'nonexistent-dir')}})).startswith('~/nonexistent-dir > '))

    def test_script_is_self_contained_and_private(self):
        source = SCRIPT.read_text()
        imports = set(re.findall(r'^import (\w+)|^from (\w+)', source, re.M))
        self.assertLessEqual({name for pair in imports for name in pair if name}, {'json', 'os', 're', 'subprocess', 'sys'})
        for banned in ('/Users/', 'quota', 'usage_percent', 'account', 'urllib', 'socket', 'http', '—'):
            self.assertNotIn(banned, source)


if __name__ == '__main__':
    unittest.main()

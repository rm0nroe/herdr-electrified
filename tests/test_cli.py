import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class NeverRead(io.StringIO):
    def readline(self, *args):
        raise AssertionError('stdin must not be read')

    def isatty(self):
        return False


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(lambda: self.assertFalse(Path(self.tmp.name).exists()))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Hermetic: the developer machine's /Applications/Ghostty.app must not count as a host terminal.
        apps = patch('herdr_electrified.electric.GHOSTTY_APPS', ())
        apps.start()
        self.addCleanup(apps.stop)
        self.target = self.root / 'config/herdr/config.toml'
        self.target.parent.mkdir(parents=True)
        self.target.write_text('# keep me\n[ui]\nsidebar_width = 19 # original\n')
        self.ledger = self.root / 'state/herdr-electrified/receipt.json'
        self.bin = self.root / 'herdr'
        self.bin.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
if sys.argv[1:] == ['--help']:
 print('Config: ' + os.environ['XDG_CONFIG_HOME'] + '/herdr/config.toml')
elif sys.argv[1:] == ['--version']: print('herdr 0.8.2')
elif sys.argv[1:] == ['config', 'check']: print('config: ok')
elif sys.argv[1:] == ['server', 'reload-config']: print('reloaded')
else: sys.exit(2)
''')
        self.bin.chmod(0o700)
        self.env = {'HERDR_CONFIG_PATH':str(self.target), 'HOME':str(self.root), 'XDG_CONFIG_HOME':str(self.root/'config'),
                    'XDG_STATE_HOME':str(self.root/'state'), 'PATH':str(self.root)+os.pathsep+os.environ['PATH']}

    def run_cli(self, *args, env=None, stdin=None):
        from herdr_electrified.cli import main
        out = io.StringIO()
        with patch.dict(os.environ, self.env | (env or {}), clear=True), patch('sys.stdin', stdin or NeverRead()), contextlib.redirect_stdout(out):
            code = main([*args, '--json'])
        return code, json.loads(out.getvalue())

    def legacy_theme(self, env=None):
        """Own the global Claude theme the way v1.0.5 did, for migration tests."""
        from herdr_electrified import config as c
        with patch.dict(os.environ, self.env | (env or {}), clear=True):
            receipt = c.receipt_path()
            data = c.load_receipt(receipt)
            root = Path(os.environ.get('CLAUDE_CONFIG_DIR') or str(self.root / '.claude'))
            for path, kind in ((root / 'themes/herdr-electrified.json', 'claude-theme'), (root / 'settings.json', 'claude-settings')):
                path = c.canonical(path)
                entry = data['targets'].get(str(path))
                values = {'theme': 'custom:herdr-electrified'} | {k: v['installed'] for k, v in (entry or {}).get('owned', {}).items() if k != '$file'}
                before, after, updated, _ = c.claude_plan(path, entry, kind, False, values, {'theme'})
                if before != after:
                    c.transact(receipt, data, path, before, after, updated, None, kind)

    def test_first_preview_is_read_only_and_shows_diff_without_identity(self):
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--dry-run')
        self.assertEqual(code, 0, result)
        self.assertIn('sidebar_width', result['targets'][0]['diff'])
        self.assertEqual(result['targets'][0]['validation'], 'not run: executable not selected')
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(self.ledger.parent.exists())

    def test_apply_reapply_and_undo_preserve_original_and_unrelated_content(self):
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 0, result)
        self.assertIn('sidebar_width = 31 # original', self.target.read_text())
        self.assertIn('# keep me', self.target.read_text())
        receipt = self.ledger.read_bytes()
        self.assertEqual(self.run_cli('apply', '--yes')[0], 0)
        self.assertEqual(self.ledger.read_bytes(), receipt)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(json.loads(self.ledger.read_text())['targets'], {})

    def test_claude_invalid_json_blocks_all_selected_writes(self):
        claude = self.root / '.claude'
        claude.mkdir()
        settings = claude / 'settings.json'
        for invalid in ('{"theme":"dark","theme":"light"}', '{"other":NaN}', '{"other":1e999}', '[]', '', '{bad'):
            with self.subTest(invalid=invalid):
                settings.write_text(invalid)
                before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))
                self.assertEqual(code, 1, result)
                self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
                self.assertFalse(self.ledger.parent.exists())

    def test_claude_preview_and_decline_are_readonly_and_diff_is_key_only(self):
        claude = self.root / '.claude'
        claude.mkdir()
        (claude / 'settings.json').write_text('{"theme":"dark","env":{"API_KEY":"synthetic-private-value"}}')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for command in [('check',), ('apply', '--dry-run')]:
            code, result = self.run_cli(*command, '--claude-statusline')
            self.assertEqual(code, 0, result)
            self.assertEqual(len(result['targets']), 3)
            self.assertNotIn('synthetic-private-value', json.dumps(result))
            self.assertIn('claude-statusline.py', result['targets'][2]['diff'])
            self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
            self.assertFalse(self.ledger.parent.exists())
        class TTY(io.StringIO):
            def isatty(self): return True
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, result = self.run_cli('apply', '--claude-statusline', '--herdr-bin', str(self.bin), stdin=TTY('n\n'))
        self.assertEqual(code, 0, result)
        self.assertNotIn('synthetic-private-value', err.getvalue())
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertFalse(self.ledger.parent.exists())

    def test_claude_settings_writes_keep_formatting_through_apply_and_key_level_undo(self):
        from herdr_electrified.cli import render
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        _, value = self.statusline()
        indented = '{\n    "theme": "dark",\n    "secret_note": "caf\u00e9 \u25b8",\n    "hooks": {"a": [1, 2]}\n}\n'
        member = ',\n    "statusLine": ' + json.dumps(value, indent=4).replace('\n', '\n    ')
        minified = '{"theme":"dark","env":{"K":"v"}}'
        for original, applied in ((indented, indented[:-3] + member + '\n}\n'),
                                  (minified, minified[:-1] + ', "statusLine": ' + json.dumps(value) + '}')):
            with self.subTest(original=original):
                settings.write_text(original)
                code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))
                self.assertEqual(code, 0, result)
                row = next(row for row in result['targets'] if row.get('component') == 'claude-settings')
                self.assertFalse('whole-file formatting' in row.get('notice', '') + render(result))
                self.assertNotIn('secret_note', row['diff'].split('@@', 2)[-1])
                self.assertEqual(settings.read_text(), applied)
                # A later edit forces key-level undo, which removes only the statusLine member.
                settings.write_text(applied.replace('"theme"', '"later": "keep this edit", "theme"'))
                code, result = self.run_cli('undo')
                self.assertEqual(code, 0, result)
                self.assertEqual(settings.read_text(), original.replace('"theme"', '"later": "keep this edit", "theme"'))
                self.assertFalse(self.ledger.exists() and json.loads(self.ledger.read_text())['targets'])

    def test_settings_splice_edits_only_changed_members(self):
        from herdr_electrified.config import splice
        cases = [
            ('{"statusLine": 1, "a": "}{\\"", "b": 2}', {'a': '}{"', 'b': 2}, '{"a": "}{\\"", "b": 2}'),
            ('{"a": 1, "statusLine": [1], "b": 2}', {'a': 1, 'b': 2}, '{"a": 1, "b": 2}'),
            ('{\r\n\t"statusLine": 1\r\n}\r\n', {}, '{}\r\n'),
            ('{}', {'theme': 'x'}, '{\n  "theme": "x"\n}'),
            ('{\r\n\t"a": {"k": 1}\r\n}\r\n', {'a': {'k': 2}}, '{\r\n\t"a": {\n\t\t"k": 2\n\t}\r\n}\r\n'),
            (None, {'theme': 'x'}, '{\n  "theme": "x"\n}\n'),
        ]
        for before, doc, after in cases:
            with self.subTest(before=before):
                self.assertEqual(splice(before, doc), after)

    def test_claude_conflicts_require_confirmation_and_preserve_first_original(self):
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text('{"other":1}\n')
        script, value = self.statusline()
        script.parent.mkdir(parents=True)
        original_script = '# my own script\n'
        script.write_text(original_script)
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        settings.write_text('{"statusLine":{"type":"command","command":"mine"},"other":2}\n')
        script.write_text('# user edited\n')
        before = (settings.read_bytes(), script.read_bytes())
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertEqual((settings.read_bytes(), script.read_bytes()), before)
        self.assertEqual({tuple(row['conflicts']) for row in result['targets'] if row['conflicts']}, {('statusLine',), ('$file',)})
        code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertEqual((settings.read_bytes(), script.read_bytes()), before)
        class TTY(io.StringIO):
            def isatty(self): return True
        with contextlib.redirect_stderr(io.StringIO()):
            code, result = self.run_cli('apply', '--claude-statusline', '--herdr-bin', str(self.bin), stdin=TTY('y\n'))
        self.assertEqual(code, 0, result)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(json.loads(settings.read_text()), {'other': 2})
        self.assertEqual(script.read_text(), original_script)

    def test_claude_interrupted_apply_and_undo_recover_per_file(self):
        settings = (self.root / '.claude/settings.json').resolve()
        theme = self.statusline()[0]
        replace = os.replace
        unlink = Path.unlink
        for operation, target, after_replace in [('apply', theme, False), ('undo', settings, True), ('apply', settings, False), ('undo', theme, True)]:
            with self.subTest(operation=operation, target=target, after_replace=after_replace):
                def interrupted(source, dest):
                    if Path(dest) == target:
                        if after_replace:
                            replace(source, dest)
                        raise OSError('simulated Claude replacement interruption')
                    return replace(source, dest)
                def interrupted_unlink(path, *args, **kwargs):
                    if path == target:
                        if after_replace:
                            unlink(path, *args, **kwargs)
                        raise OSError('simulated Claude deletion interruption')
                    return unlink(path, *args, **kwargs)
                args = (operation, '--claude-statusline', '--yes', '--herdr-bin', str(self.bin)) if operation == 'apply' else (operation,)
                with patch('os.replace', interrupted), patch.object(Path, 'unlink', interrupted_unlink):
                    self.assertEqual(self.run_cli(*args)[0], 1)
                receipt = self.ledger.read_bytes()
                self.assertIn('pending', json.loads(receipt))
                self.assertEqual(self.run_cli('check')[0], 0)
                self.assertEqual(self.ledger.read_bytes(), receipt)
                code, result = self.run_cli(*args)
                self.assertEqual(code, 0, result)
                self.assertNotIn('pending', json.loads(self.ledger.read_text()))
        self.assertFalse(settings.exists())
        self.assertFalse(theme.exists())
        self.assertEqual(json.loads(self.ledger.read_text())['targets'], {})

    def test_claude_deleted_files_stay_absent_with_unresolved_receipts(self):
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        settings = self.root / '.claude/settings.json'
        theme = self.statusline()[0]
        settings.unlink()
        theme.unlink()
        entries = json.loads(self.ledger.read_text())['targets']
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertFalse(settings.exists())
        self.assertFalse(theme.exists())
        self.assertEqual(json.loads(self.ledger.read_text())['targets'], {str(p.resolve()): entries[str(p.resolve())] for p in (settings, theme)})
        receipt = self.ledger.read_bytes()
        self.assertEqual(self.run_cli('undo')[0], 1)
        self.assertEqual(self.ledger.read_bytes(), receipt)

    def test_claude_all_directories_undo_preserves_later_unrelated_settings(self):
        value = self.statusline()[1]
        for name in ('claude-a', 'claude-b'):
            root = self.root / name
            root.mkdir()
            (root / 'settings.json').write_text('{"other":1}')
            env = {'CLAUDE_CONFIG_DIR': str(root)}
            self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin), env=env)[0], 0)
            (root / 'settings.json').write_text(json.dumps({'statusLine': value, 'other': 2}))
        self.assertEqual(self.run_cli('undo', env={'CLAUDE_CONFIG_DIR': str(self.root / 'unselected')})[0], 0)
        for name in ('claude-a', 'claude-b'):
            self.assertEqual(json.loads((self.root / name / 'settings.json').read_text()), {'other': 2})
        self.assertFalse(self.statusline()[0].exists())
        self.assertFalse((self.root / 'unselected').exists())

    def test_claude_canonical_target_collision_blocks_all_writes(self):
        claude = self.root / '.claude'
        claude.mkdir()
        (claude / 'settings.json').symlink_to(self.target)
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertIn('same target', result['error'])
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(self.ledger.parent.exists())

    def test_readonly_identity_matrix_and_scripted_reselection(self):
        for command in [('check',), ('apply', '--dry-run')]:
            for flag in [(), ('--herdr-bin', str(self.bin))]:
                with self.subTest(command=command, flag=flag):
                    code, result = self.run_cli(*command, *flag)
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result['targets'][0]['validation'], 'passed' if flag else 'not run: executable not selected')
                    self.assertFalse(self.ledger.parent.exists())
        self.assertEqual(self.run_cli('apply', '--yes')[0], 1)
        self.assertFalse(self.ledger.parent.exists())
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        before = self.ledger.read_bytes()
        for command in [('check',), ('apply', '--dry-run')]:
            self.assertEqual(self.run_cli(*command)[1]['targets'][0]['validation'], 'passed')
            self.assertEqual(self.ledger.read_bytes(), before)
        self.bin.write_text(self.bin.read_text() + '\n# same version, different bytes\n')
        for command in [('check',), ('apply', '--dry-run')]:
            self.assertEqual(self.run_cli(*command)[1]['targets'][0]['validation'], 'not run: pin mismatch')
            self.assertEqual(self.ledger.read_bytes(), before)
        for command in [('apply', '--yes'), ('undo',), ('undo', '--yes')]:
            self.assertEqual(self.run_cli(*command)[0], 1)
            self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.assertNotEqual(self.ledger.read_bytes(), before)
        self.bin.unlink()
        code, result = self.run_cli('check')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['validation'], 'not run: pin mismatch')

    def test_undo_keeps_user_edits_arrays_and_all_owned_targets(self):
        original = self.target.read_text()
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.target.write_text(self.target.read_text().replace('sidebar_width = 31', 'sidebar_width = 44') + '\n[custom]\nanswer = 42\n')
        second = self.root / 'second.toml'
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin), '--herdr-config', str(second))[0], 0)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertIn('sidebar_width = 44', self.target.read_text())
        self.assertIn('answer = 42', self.target.read_text())
        self.assertFalse(second.exists())
        self.assertEqual(result['targets'][0]['conflicts'], ['ui.sidebar_width'])
        # After a partial undo, retry must not restore unrelated values over a new edit.
        self.target.write_text(self.target.read_text().replace('sidebar_width = 44', 'sidebar_width = 31'))
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertIn('sidebar_width = 19', self.target.read_text())
        self.assertIn('answer = 42', self.target.read_text())

    def test_key_level_undo_removes_tables_apply_created(self):
        import tomlkit
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.target.write_text(self.target.read_text() + '\n[custom]\nanswer = 42\n')
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        text = self.target.read_text()
        self.assertEqual(tomlkit.parse(text).unwrap(), {'ui': {'sidebar_width': 19}, 'custom': {'answer': 42}})
        self.assertNotIn('[theme', text)
        self.assertNotIn('[ui.sidebar', text)
        self.assertIn('# keep me', text)

    def test_key_level_undo_keeps_an_emptied_table_holding_a_user_comment(self):
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.target.write_text(self.target.read_text() + '# my note\n')
        self.assertEqual(self.run_cli('undo')[0], 0)
        text = self.target.read_text()
        self.assertIn('[theme.custom]\n# my note', text)
        self.assertNotIn('[ui.sidebar', text)

    def test_key_level_undo_keeps_emptied_tables_with_header_comments(self):
        for header in ('[theme]', '[theme.custom]'):
            with self.subTest(header=header):
                self.target.write_text('# keep me\n[ui]\nsidebar_width = 19 # original\n')
                self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
                self.target.write_text(self.target.read_text().replace(header + '\n', header + ' # my note\n', 1) + '\n[custom]\nanswer = 42\n')
                self.assertEqual(self.run_cli('undo')[0], 0)
                text = self.target.read_text()
                self.assertIn(header + ' # my note', text)
                self.assertNotIn('[ui.sidebar', text)

    def test_undo_keeps_a_created_config_holding_user_comments_or_tables(self):
        edits = {'parent header comment': ('[theme]\n', '[theme] # my note\n'),
                 'child header comment': ('[theme.custom]\n', '[theme.custom] # my note\n'),
                 'standalone comment': (None, '# my note\n'),
                 'user empty table': (None, '[my_table]\n')}
        for name, (old, new) in edits.items():
            with self.subTest(name):
                self.target.unlink(missing_ok=True)
                self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
                text = self.target.read_text()
                self.target.write_text(text.replace(old, new, 1) if old else text + new)
                self.assertEqual(self.run_cli('undo')[0], 0)
                self.assertTrue(self.target.exists())
                self.assertIn(new.strip(), self.target.read_text())
                self.assertNotIn('[ui.sidebar.spaces]', self.target.read_text())

    def test_missing_herdr_on_path_is_a_clear_error(self):
        env = {'HERDR_CONFIG_PATH': '', 'PATH': '/usr/bin:/bin'}
        for command in ('check', 'apply'):
            code, result = self.run_cli(command, env=env)
            self.assertEqual(code, 1, result)
            self.assertEqual(result['error'], 'Herdr not found on PATH; install Herdr first, or pass --herdr-config')

    def test_text_error_never_claims_already_applied(self):
        from herdr_electrified.cli import main
        out = io.StringIO()
        with patch.dict(os.environ, self.env | {'HERDR_CONFIG_PATH': '', 'PATH': '/usr/bin:/bin'}, clear=True), \
                patch('sys.stdin', NeverRead()), contextlib.redirect_stdout(out):
            self.assertEqual(main(['apply', '--yes']), 1)
        self.assertIn('Herdr not found on PATH', out.getvalue())
        self.assertNotIn('Nothing to change', out.getvalue())

    def test_reapply_requires_new_conflict_confirmation_and_preserves_first_original(self):
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.target.write_text(self.target.read_text().replace('sidebar_width = 31', 'sidebar_width = 44'))
        code, result = self.run_cli('apply', '--yes')
        self.assertEqual(code, 1, result)
        self.assertIn('sidebar_width = 44', self.target.read_text())

    def test_interrupted_apply_and_undo_recover_from_journal(self):
        original = self.target.read_bytes()
        replace = os.replace
        for operation, after_replace in [('apply', False), ('undo', True)]:
            with self.subTest(operation=operation):
                def interrupted(source, dest):
                    if Path(dest) == self.target.resolve():
                        if after_replace:
                            replace(source, dest)
                        raise OSError('simulated interruption at target replacement')
                    return replace(source, dest)
                args = (operation, '--yes', '--herdr-bin', str(self.bin))
                with patch('os.replace', interrupted):
                    self.assertEqual(self.run_cli(*args)[0], 1)
                receipt = self.ledger.read_bytes()
                self.assertIn('pending', json.loads(receipt))
                self.assertEqual(self.run_cli('check')[0], 0)
                self.assertEqual(self.ledger.read_bytes(), receipt)
                code, result = self.run_cli(*args)
                self.assertEqual(code, 0, result)
                self.assertNotIn('pending', json.loads(self.ledger.read_bytes()))
        self.assertEqual(self.target.read_bytes(), original)

    def test_reapply_does_not_make_later_unrelated_edits_owned(self):
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.target.write_text(self.target.read_text() + '\n[custom]\nanswer = 42\n')
        self.assertEqual(self.run_cli('apply', '--yes')[0], 0)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertIn('answer = 42', self.target.read_text())
        self.assertIn('sidebar_width = 19', self.target.read_text())

    def test_reload_requires_complete_matching_association(self):
        triple = {'HERDR_ENV':'1', 'HERDR_BIN_PATH':str(self.bin), 'HERDR_SOCKET_PATH':str(self.root/'herdr.sock')}
        code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin), env=triple)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['reload'], 'requested')
        self.assertEqual(result['targets'][0]['loaded'], 'unknown')
        other = self.root / 'other.toml'
        code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin), '--herdr-config', str(other), env=triple)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['reload'], 'skipped')
        self.assertEqual(self.run_cli('undo')[1]['targets'][0]['reload'], 'skipped')

    def test_unknown_native_diagnostics_are_compared_and_malformed_input_blocks(self):
        self.bin.write_text(self.bin.read_text().replace("print('config: ok')", """\n text = Path(os.environ['HERDR_CONFIG_PATH']).read_text()
 if 'unknown_option' in text:
  print('config: issues found\\nunknown config key ui.unknown_option; ignoring key'); sys.exit(1)
 print('config: ok')"""))
        self.target.write_text(self.target.read_text() + 'unknown_option = 1\n')
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.target.write_text('[ui\n')
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertEqual(self.target.read_bytes(), before)

    def test_new_native_diagnostic_leaves_target_and_state_untouched(self):
        self.bin.write_text(self.bin.read_text().replace("print('config: ok')", """\n text = Path(os.environ['HERDR_CONFIG_PATH']).read_text()
 if 'sidebar_max_width' in text:
  print('config: issues found\\nunknown config key ui.sidebar_max_width; ignoring key'); sys.exit(1)
 print('config: ok')"""))
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertIn('new or worsened', result['error'])
        self.assertIn('run herdr-electrified install', result['error'])
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(self.ledger.parent.exists())

    def test_array_override_is_reported_without_silent_replacement(self):
        self.target.write_text(self.target.read_text() + '\n[ui.sidebar.agents.rows_by_agent]\nclaude = [["agent"]]\n')
        code, result = self.run_cli('apply', '--dry-run', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 0, result)
        self.assertIn('partial coverage', result['targets'][0]['notice'])

    def pty_cli(self, *args, answer=b'n\n'):
        """Run the CLI on a real terminal; return what it showed before reading stdin, then the rest."""
        import pty, select, subprocess, sys, time
        primary, secondary = pty.openpty()
        src = str(Path(__file__).resolve().parents[1] / 'src')
        proc = subprocess.Popen([sys.executable, '-m', 'herdr_electrified.cli', *args], stdin=secondary, stdout=secondary, stderr=secondary,
                                env=self.env | {'PYTHONPATH': src}, close_fds=True)
        os.close(secondary)
        def read_until(marker, seconds):
            seen, deadline = b'', time.monotonic() + seconds
            while (marker is None or marker not in seen) and time.monotonic() < deadline:
                if select.select([primary], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(primary, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    seen += chunk
            return seen
        try:
            shown = read_until(b'[y/N]', 10)
            os.write(primary, answer)
            rest = read_until(None, 10)
            proc.wait(5)
        finally:
            proc.kill()
            proc.wait()
            os.close(primary)
        return proc.returncode, shown.decode(), rest.decode()

    def test_prompt_is_visible_on_a_terminal_before_reading_the_answer(self):
        code, shown, rest = self.pty_cli('apply', '--herdr-bin', str(self.bin))
        self.assertIn('[y/N]', shown)
        self.assertIn('config.toml', shown)
        self.assertIn('nothing written', rest.lower())
        self.assertEqual(code, 0)
        self.assertFalse(self.ledger.parent.exists())

    def test_text_apply_prints_a_summary_once_then_done(self):
        from herdr_electrified.cli import main
        out = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch('sys.stdin', NeverRead()), contextlib.redirect_stdout(out):
            self.assertEqual(main(['apply', '--yes', '--herdr-bin', str(self.bin)]), 0)
        text = out.getvalue()
        self.assertIn('Wrote:\n  Herdr config\n    changed   ~/config/herdr/config.toml', text)
        self.assertIn('Wrote 1 file. Undo: herdr-electrified undo', text)
        self.assertNotIn('+++', text)
        self.assertNotIn('\033[', text)

    def test_done_next_steps_follow_the_terminal_it_runs_in(self):
        from types import SimpleNamespace
        from herdr_electrified.cli import outcome
        args = SimpleNamespace(dry_run=False, prompted=True)
        def row(path, component='electric-file', **extra):
            return {'path': path, 'component': component, 'change': 'changed', 'saved': True, **extra}
        upgrade = {'bundle': '0.4.0', 'agents': ['ghostty'], 'targets': [row('/h/.config/herdr-electrified/electric/ghostty.conf')]}
        def run(result, terminal):
            with patch.dict(os.environ, {'TERM_PROGRAM': terminal} if terminal else {}, clear=True):
                return outcome(result, args)
        # herdr-electric opens its own Ghostty window, which loads the look and a new font itself.
        # Herdr Electric paints its own pane colors, so no terminal background advice with the bundle.
        for terminal in ('ghostty', 'iTerm.app', None):
            self.assertEqual(run(upgrade, terminal), 'Wrote 1 file. Undo: herdr-electrified undo\n'
                             'Next: run herdr-electric (it opens its own Ghostty window).')
        fonts = dict(upgrade, fonts_installed=True)
        self.assertEqual(run(fonts, 'ghostty'), 'Wrote 1 file and installed the font. Undo: herdr-electrified undo\n'
                         'Next: run herdr-electric (it opens its own Ghostty window).')
        # Upgrading from v1.2.x removes the global include; open Ghostty windows drop the look on reload.
        released = dict(upgrade, targets=upgrade['targets'] + [row('/h/Library/Application Support/com.mitchellh.ghostty/config', 'ghostty-config')])
        self.assertEqual(run(released, 'ghostty'), 'Wrote 2 files. Undo: herdr-electrified undo\n'
                         'Next: reload Ghostty (cmd+shift+,) so other windows drop the Electric look, then run herdr-electric (it opens its own Ghostty window).')
        self.assertEqual(run(released, 'iTerm.app'), 'Wrote 2 files. Undo: herdr-electrified undo\n'
                         'Next: run herdr-electric (it opens its own Ghostty window).\n'
                         'Ghostty: reload it (cmd+shift+,) so other windows drop the Electric look.')
        # Without Ghostty, Electric runs in the terminal it is started from.
        plain = {'bundle': '0.4.0', 'targets': [row('/h/.local/bin/herdr-electric')]}
        self.assertEqual(run(plain, 'iTerm.app'), 'Wrote 1 file. Undo: herdr-electrified undo\n'
                         'Next: run herdr-electric from a new terminal window (not inside a Herdr pane).')
        stock = run({'targets': [row('/h/.config/herdr/config.toml', 'herdr')]}, 'iTerm.app')
        self.assertEqual(stock, "Wrote 1 file. Undo: herdr-electrified undo\nThis terminal isn't styled by Electric; give it a dark background.")
        # Settings-only still styles every Ghostty window through the include.
        look = {'targets': [row('/h/.config/herdr-electrified/ghostty.conf')], 'fonts_installed': True}
        self.assertIn('Next: restart Ghostty once so it loads the new font.', run(look, 'ghostty'))
        self.assertIn('Ghostty: restart it once so it loads the new font.', run(look, 'iTerm.app'))
        notice = 'Reload Ghostty (cmd+shift+,); restart Ghostty once if fonts were installed.'
        settings = run({'targets': [row('/h/.config/ghostty/config.ghostty', 'ghostty-config', notice=notice)]}, 'ghostty')
        self.assertEqual(settings, 'Wrote 1 file. Undo: herdr-electrified undo\nNext: reload Ghostty (cmd+shift+,).')
        # Without a prompt (--yes), the written files are listed once.
        listed = outcome(upgrade, SimpleNamespace(dry_run=False, prompted=False))
        self.assertIn('Wrote:\n  Ghostty windows\n    changed   /h/.config/herdr-electrified/electric/ghostty.conf', listed)
        # Saved but unchanged rows keep a space between the word and the path.
        kept = dict(upgrade, targets=[row('/h/.config/herdr-electrified/electric/config.toml', 'herdr', change='unchanged')])
        self.assertIn('    unchanged /h/.config/herdr-electrified/electric/config.toml', outcome(kept, SimpleNamespace(dry_run=False, prompted=False)))

    def test_summary_names_themes_and_only_asks_about_an_executable_to_pin(self):
        from herdr_electrified.cli import summary
        exe = {'path': '/b/herdr', 'sha256': 'x'}
        result = {'bundle': '0.4.0', 'agents': ['claude', 'codex', 'ghostty', 'opencode'], 'targets': [
            {'path': '/h/c.toml', 'component': 'herdr', 'change': 'changed', 'conflicts': [], 'executable': exe, 'version': 'herdr 0.8.2', 'confirmation_required': False}]}
        text = summary(result, io.StringIO())
        self.assertIn('Themes: Claude Code, Codex, Ghostty, OpenCode', text)
        self.assertNotIn('Detected', text)
        self.assertNotIn('/b/herdr', text)
        self.assertIn("Untouched: stock herdr and codex binaries, other terminals' settings, your shell profiles. Undo: herdr-electrified undo", text)
        result['targets'][0]['confirmation_required'] = True
        self.assertIn('Herdr executable to pin: /b/herdr (herdr 0.8.2)', summary(result, io.StringIO()))
        result['targets'].append({'path': '/h/.config/ghostty/config.ghostty', 'component': 'ghostty-config', 'change': 'new', 'conflicts': [],
                                  'notice': 'Reload Ghostty (cmd+shift+,); restart Ghostty once if fonts were installed.'})
        self.assertNotIn('Reload Ghostty', summary(result, io.StringIO()))

    def test_unanswered_prompt_times_out_without_writing(self):
        class TTY(io.StringIO):
            def isatty(self): return True
        with patch('herdr_electrified.cli.select.select', return_value=([], [], [])), contextlib.redirect_stderr(io.StringIO()):
            code, result = self.run_cli('apply', '--herdr-bin', str(self.bin), stdin=TTY('y\n'))
        self.assertEqual(code, 1)
        self.assertEqual(result['error'], 'no answer in 5 min; nothing written')
        self.assertFalse(self.ledger.parent.exists())

    def test_ctrl_c_exits_130_without_traceback(self):
        from herdr_electrified.cli import main
        err = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch('herdr_electrified.cli.execute', side_effect=KeyboardInterrupt), contextlib.redirect_stderr(err):
            self.assertEqual(main(['apply', '--herdr-bin', str(self.bin)]), 130)
        self.assertEqual(err.getvalue(), '\nCancelled; nothing written\n')

    def test_interactive_decline_never_validates_hint_or_creates_state(self):
        class TTY(io.StringIO):
            def isatty(self): return True
        self.bin.write_text(self.bin.read_text().replace("elif sys.argv[1:] == ['config', 'check']: print('config: ok')", "elif sys.argv[1:] == ['config', 'check']: raise AssertionError('unconfirmed hint ran')"))
        triple = {'HERDR_ENV':'1', 'HERDR_BIN_PATH':str(self.bin), 'HERDR_SOCKET_PATH':str(self.root/'herdr.sock')}
        with contextlib.redirect_stderr(io.StringIO()):
            code, result = self.run_cli('apply', env=triple, stdin=TTY('n\n'))
        self.assertEqual(code, 0, result)
        self.assertFalse(self.ledger.parent.exists())
        self.assertEqual(result['notice'], 'declined; nothing written')

    def test_interactive_accept_and_undo_reselection_use_one_confirmation(self):
        class TTY(io.StringIO):
            def isatty(self): return True
        triple = {'HERDR_ENV':'1', 'HERDR_BIN_PATH':str(self.bin), 'HERDR_SOCKET_PATH':str(self.root/'herdr.sock')}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, result = self.run_cli('apply', env=triple, stdin=TTY('y\n'))
            self.assertEqual(code, 0, result)
            self.assertIn('Herdr executable to pin:', err.getvalue())
            self.assertIn('pin this Herdr executable? [y/N]', err.getvalue())
            row = result['targets'][0]
            self.assertEqual((row['identity'], row['confirmation_required']), ('pinned', False))
            self.assertEqual(self.run_cli('check')[1]['targets'][0]['identity'], 'pinned')
            self.bin.write_text(self.bin.read_text()+'\n# update\n')
            before = self.ledger.read_bytes()
            self.assertEqual(self.run_cli('undo', stdin=TTY('n\n'))[0], 0)
            self.assertEqual(before, self.ledger.read_bytes())
            code, result = self.run_cli('undo', stdin=TTY('y\n'))
            self.assertEqual(code, 0, result)
            row = result['targets'][0]
            self.assertEqual((row['identity'], row['confirmation_required']), ('pin retired', False))
        self.assertEqual(json.loads(self.ledger.read_text())['targets'], {})

    def test_busy_check_does_not_take_lock_or_change_receipt(self):
        import fcntl
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        lock = self.ledger.with_name('lock')
        lock.write_text(str(os.getpid()))
        before = self.ledger.read_bytes()
        with lock.open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for command in [('check',), ('apply','--dry-run')]:
                code, result = self.run_cli(*command)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['notice'], 'busy, results may be stale')
                self.assertEqual(lock.read_text(), str(os.getpid()))
                self.assertEqual(self.ledger.read_bytes(), before)

    def test_state_symlink_is_rejected_before_writes(self):
        outside = self.root/'outside'
        outside.mkdir()
        self.ledger.parent.parent.mkdir()
        self.ledger.parent.symlink_to(outside, target_is_directory=True)
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.target.read_bytes(), before)

    def test_receipt_failure_and_target_race_never_overwrite_unjournaled_content(self):
        replace = os.replace
        before = self.target.read_bytes()
        def receipt_failure(source, dest):
            if Path(dest) == self.ledger.resolve(): raise OSError('disk full')
            return replace(source, dest)
        with patch('os.replace', receipt_failure):
            self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 1)
        self.assertEqual(self.target.read_bytes(), before)
        def changed_after_journal(source, dest):
            value = replace(source, dest)
            if Path(dest) == self.ledger.resolve(): self.target.write_text('# concurrent writer\n')
            return value
        with patch('os.replace', changed_after_journal):
            self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 1)
        self.assertEqual(self.target.read_text(), '# concurrent writer\n')
        receipt = self.ledger.read_bytes()
        code, result = self.run_cli('apply','--yes','--herdr-bin',str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertIn('conflicts with user edit', result['error'])
        self.assertEqual(self.ledger.read_bytes(), receipt)

    def test_pin_path_change_and_incomplete_hint_do_not_select_a_validator(self):
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        other = self.root/'other-herdr'
        other.write_bytes(self.bin.read_bytes())
        other.chmod(0o700)
        before = self.ledger.read_bytes()
        code, result = self.run_cli('check','--herdr-bin',str(other))
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['validation'], 'not run: pin mismatch')
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(self.run_cli('undo')[0], 0)
        for hint in ({'HERDR_BIN_PATH':str(other)}, {'HERDR_ENV':'1','HERDR_BIN_PATH':str(other)}):
            code, result = self.run_cli('check', env=hint)
            self.assertEqual(result['targets'][0]['identity'], 'none')
            self.assertEqual(result['targets'][0]['validation'], 'not run: executable not selected')

    def test_already_styled_without_owned_keys_does_not_create_pin(self):
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        self.ledger.unlink()
        self.ledger.with_name('lock').unlink()
        self.ledger.parent.rmdir()
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        self.assertFalse(self.ledger.parent.exists())

    def test_default_target_comes_from_installed_help_and_reload_is_skipped(self):
        code, result = self.run_cli('apply','--yes','--herdr-bin',str(self.bin), env={'HERDR_CONFIG_PATH':''})
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['path'], str(self.target.resolve()))
        self.assertEqual(result['targets'][0]['reload'], 'skipped')

    def test_malformed_receipt_is_reported_without_traceback_or_target_changes(self):
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text('{"version":1,"targets":{"/bad":{}}}')
        before = self.target.read_bytes()
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertIn('receipt', result['error'])
        self.assertEqual(self.target.read_bytes(), before)

    def test_partial_undo_reports_completed_files_and_preserves_pending_journal(self):
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        second = self.root/'second.toml'
        second.write_text('[ui]\nsidebar_width = 17\n')
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin),'--herdr-config',str(second))[0], 0)
        replace = os.replace
        def fail_second(source, dest):
            if Path(dest) == second.resolve(): raise OSError('second file write failed')
            return replace(source, dest)
        with patch('os.replace', fail_second):
            code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertEqual(result['targets'][0]['saved'], True)
        self.assertIn('partial', result['error'])
        self.assertIn('sidebar_width = 19', self.target.read_text())
        self.assertIn('sidebar_width = 31', second.read_text())
        self.assertIn('pending', json.loads(self.ledger.read_text()))
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertIn('sidebar_width = 17', second.read_text())

    def test_array_conflict_is_indivisible_and_edited_new_file_remains(self):
        self.target.unlink()
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        self.target.write_text(self.target.read_text().replace('"branch", "git_status"', '"workspace"') + '\n# later note\n')
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertIn('ui.sidebar.spaces.rows', result['targets'][0]['conflicts'])
        self.assertIn('"workspace"', self.target.read_text())
        self.assertIn('# later note', self.target.read_text())

    def test_readonly_missing_and_matching_pin_matrix_with_and_without_flag(self):
        for state in ('absent', 'matching', 'mismatch'):
            if state == 'matching': self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
            if state == 'mismatch': self.bin.write_text(self.bin.read_text()+'\n# drift\n')
            before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            for command in [('check',), ('apply','--dry-run')]:
                for flags in [(), ('--herdr-bin',str(self.bin))]:
                    with self.subTest(state=state,command=command,flags=flags):
                        code, result = self.run_cli(*command,*flags)
                        self.assertEqual(code, 0, result)
                        expected = 'not run: pin mismatch' if state=='mismatch' else 'passed' if state=='matching' or flags else 'not run: executable not selected'
                        self.assertEqual(result['targets'][0]['validation'], expected)
                        self.assertEqual({str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_native_execution_failure_and_nonregular_target_block_without_writes(self):
        self.bin.write_text(self.bin.read_text().replace("print('config: ok')", "sys.exit(9)"))
        before = self.target.read_bytes()
        code, result = self.run_cli('apply','--yes','--herdr-bin',str(self.bin))
        self.assertEqual(code, 1, result)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(self.ledger.parent.exists())
        code, result = self.run_cli('apply','--yes','--herdr-bin',str(self.bin),'--herdr-config',str(self.target.parent))
        self.assertEqual(code, 1, result)
        self.assertFalse(self.ledger.parent.exists())

    def test_existing_mode_preserved_and_new_receipt_is_private(self):
        self.target.chmod(0o640)
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.ledger.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o640)

    def test_reload_failure_keeps_saved_receipt_and_safe_undo(self):
        self.bin.write_text(self.bin.read_text().replace("print('reloaded')", "sys.exit(1)"))
        triple = {'HERDR_ENV':'1','HERDR_BIN_PATH':str(self.bin),'HERDR_SOCKET_PATH':str(self.root/'herdr.sock')}
        code, result = self.run_cli('apply','--yes','--herdr-bin',str(self.bin),env=triple)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['reload'], 'failed')
        self.assertIn('saved-but-not-loaded', result['targets'][0]['notice'])
        self.assertTrue(self.ledger.exists())
        self.assertEqual(self.run_cli('undo')[0], 0)

    def test_invalid_explicit_executable_cannot_replace_existing_pin(self):
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        before = self.ledger.read_bytes()
        code, result = self.run_cli('apply','--yes','--herdr-bin',str(self.root/'missing'))
        self.assertEqual(code, 1, result)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_undo_preserves_original_line_endings(self):
        before = b'# keep CRLF\r\n[ui]\r\nsidebar_width = 19\r\n'
        self.target.write_bytes(before)
        self.assertEqual(self.run_cli('apply','--yes','--herdr-bin',str(self.bin))[0], 0)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(self.target.read_bytes(), before)

    def test_undo_preserves_deleted_target_and_receipt(self):
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.target.unlink()
        receipt = self.ledger.read_bytes()
        entry = json.loads(receipt)['targets'][str(self.target.resolve())]
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.ledger.read_bytes(), receipt)
        self.assertEqual(result['targets'][0]['conflicts'], list(entry['owned']))
        second = self.root / 'second.toml'
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin), '--herdr-config', str(second))[0], 0)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertFalse(self.target.exists())
        self.assertFalse(second.exists())
        self.assertNotIn('saved', result['targets'][0])
        self.assertEqual(json.loads(self.ledger.read_text())['targets'], {str(self.target.resolve()): entry})

    def test_already_styled_unpinned_apply_needs_no_executable(self):
        from importlib.resources import files
        self.target.write_text(files('herdr_electrified').joinpath('data/herdr.toml').read_text())
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--yes')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['diff'], '')
        self.assertEqual(result['targets'][0]['validation'], 'not run: executable not selected')
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(self.ledger.parent.exists())

    def test_foreign_live_lock_pid_is_busy_and_readonly(self):
        self.ledger.parent.mkdir(parents=True)
        lock = self.ledger.with_name('lock')
        lock.write_text('1')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('herdr_electrified.config.os.kill', side_effect=PermissionError('Operation not permitted')):
            for command in [('check',), ('apply', '--dry-run')]:
                code, result = self.run_cli(*command)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['notice'], 'busy, results may be stale')
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_agent_claude_is_a_deprecated_noop_outside_electric(self):
        claude = self.root / 'alternate-claude'
        claude.mkdir()
        settings = claude / 'settings.json'
        original = '{"theme":"dark", "hooks":{}}\n'
        settings.write_text(original)
        env = {'CLAUDE_CONFIG_DIR': str(claude)}
        code, result = self.run_cli('apply', '--agent', 'claude', '--yes', '--herdr-bin', str(self.bin), env=env)
        self.assertEqual(code, 0, result)
        self.assertIn('--agent claude is deprecated', result['notice'])
        self.assertEqual(settings.read_text(), original)
        self.assertFalse((claude / 'themes').exists())
        self.assertEqual([row['path'] for row in result['targets']], [str(self.target.resolve())])

    def test_legacy_global_theme_is_released_on_reapply(self):
        for case in ('untouched-new', 'untouched-existing', 'user-changed'):
            with self.subTest(case=case):
                self.tearDown_state()
                settings = self.root / '.claude/settings.json'
                theme = self.root / '.claude/themes/herdr-electrified.json'
                if case != 'untouched-new':
                    settings.parent.mkdir(parents=True)
                    settings.write_text('{"theme": "custom:herdr-reference", "other": 1}\n')
                original = settings.read_bytes() if settings.exists() else None
                self.legacy_theme()
                self.assertEqual(json.loads(settings.read_text())['theme'], 'custom:herdr-electrified')
                if case == 'user-changed':
                    settings.write_text('{"theme": "light", "other": 1}\n')
                code, result = self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))
                self.assertEqual(code, 0, result)
                targets = json.loads(self.ledger.read_text())['targets']
                self.assertNotIn(str(settings.resolve()), targets)
                self.assertNotIn(str(theme.resolve()), targets)
                self.assertFalse(theme.exists())
                if case == 'untouched-new':
                    self.assertFalse(settings.exists())
                elif case == 'untouched-existing':
                    self.assertEqual(settings.read_bytes(), original)
                else:
                    self.assertEqual(json.loads(settings.read_text()), {'theme': 'light', 'other': 1})
                self.assertEqual(self.run_cli('apply', '--yes')[0], 0)
                self.assertEqual(self.run_cli('undo')[0], 0)

    def tearDown_state(self):
        import shutil
        for path in (self.root / '.claude', self.root / 'state'):
            shutil.rmtree(path, ignore_errors=True)

    def test_legacy_theme_release_keeps_owned_statusline(self):
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text('{"theme":"dark","other":1}')
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.legacy_theme()
        script, value = self.statusline()
        self.assertEqual(set(json.loads(self.ledger.read_text())['targets'][str(settings.resolve())]['owned']), {'theme', 'statusLine'})
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        code, result = self.run_cli('check')
        self.assertEqual(code, 0, result)
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertEqual(self.run_cli('apply', '--yes')[0], 0)
        self.assertEqual(json.loads(settings.read_text()), {'theme': 'dark', 'other': 1, 'statusLine': value})
        self.assertEqual(list(json.loads(self.ledger.read_text())['targets'][str(settings.resolve())]['owned']), ['statusLine'])
        self.assertTrue(all(row['configured'] for row in self.run_cli('check')[1]['targets']))
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(json.loads(settings.read_text()), {'theme': 'dark', 'other': 1})
        self.assertFalse(script.exists())

    def test_legacy_theme_undo_still_restores(self):
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        original = '{"theme": "custom:herdr-reference"}\n'
        settings.write_text(original)
        self.assertEqual(self.run_cli('apply', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.legacy_theme()
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(settings.read_text(), original)
        self.assertFalse((self.root / '.claude/themes/herdr-electrified.json').exists())

    def statusline(self):
        import shlex
        script = (self.root / '.local/share/herdr-electrified/claude-statusline.py').resolve()
        return script, {'type': 'command', 'command': 'python3 ' + shlex.quote(str(script))}

    def test_statusline_opt_in_check_and_undo_restore_exact_bytes(self):
        import subprocess
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        original = '{"other": 1,\n    "theme": "dark"}\n'
        settings.write_text(original)
        script, value = self.statusline()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        code, result = self.run_cli('apply', '--dry-run', '--claude-statusline')
        self.assertEqual(code, 0, result)
        self.assertIn('statusLine', next(row for row in result['targets'] if row.get('component') == 'claude-settings')['diff'])
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 0, result)
        self.assertEqual(json.loads(settings.read_text()), {'other': 1, 'theme': 'dark', 'statusLine': value})
        self.assertFalse((self.root / '.claude/themes').exists())
        line = subprocess.run(value['command'], shell=True, input='{"context_window":{"used_percentage":42}}', capture_output=True, text=True, env={'PATH': os.environ['PATH']})
        self.assertEqual((line.returncode, line.stderr), (0, ''), line)
        self.assertIn('42%', line.stdout)
        receipt = self.ledger.read_bytes()
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes')[0], 0)
        self.assertEqual(self.ledger.read_bytes(), receipt)
        code, result = self.run_cli('check')
        rows = {row.get('component'): row for row in result['targets']}
        self.assertTrue(rows['claude-statusline']['configured'] and rows['claude-settings']['configured'], result)
        script.write_text('# edited\n')
        rows = {row.get('component'): row for row in self.run_cli('check')[1]['targets']}
        self.assertEqual(rows['claude-statusline']['conflicts'], ['$file'])
        script.unlink()
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes')[0], 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli('apply', '--claude-statusline', stdin=type('TTY', (io.StringIO,), {'isatty': lambda self: True})('y\n'))[0], 0)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        self.assertEqual(settings.read_text(), original)
        self.assertFalse(script.parent.exists())
        self.assertEqual(json.loads(self.ledger.read_text())['targets'], {})

    def test_statusline_never_replaces_foreign_statusline_without_review(self):
        class TTY(io.StringIO):
            def isatty(self): return True
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        original = '{"statusLine": {"type": "command", "command": "~/mine.sh"}}\n'
        settings.write_text(original)
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for command in [('check', '--claude-statusline'), ('apply', '--dry-run', '--claude-statusline')]:
            code, result = self.run_cli(*command)
            row = next(row for row in result['targets'] if row.get('component') == 'claude-settings')
            self.assertEqual(row['conflicts'], ['statusLine'])
            self.assertIn('existing statusLine', row['notice'])
        for stdin in (None, TTY('y\n')):
            code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin), stdin=stdin)
            self.assertEqual(code, 1, result)
            self.assertIn('review the diff interactively', result['error'])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli('apply', '--claude-statusline', '--herdr-bin', str(self.bin), stdin=TTY('n\n'))[0], 0)
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertFalse(self.ledger.parent.exists())
        with contextlib.redirect_stderr(io.StringIO()):
            code, result = self.run_cli('apply', '--claude-statusline', '--herdr-bin', str(self.bin), stdin=TTY('y\n'))
        self.assertEqual(code, 0, result)
        self.assertEqual(json.loads(settings.read_text())['statusLine'], self.statusline()[1])
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(settings.read_text(), original)

    def test_statusline_user_edits_are_undo_conflicts(self):
        settings = self.root / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text('{"theme":"dark","other":1}')
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        script, value = self.statusline()
        settings.write_text(json.dumps({'theme': 'dark', 'other': 2, 'statusLine': {'type': 'command', 'command': 'mine'}}))
        self.assertEqual(next(row for row in self.run_cli('check')[1]['targets'] if row.get('component') == 'claude-settings')['conflicts'], ['statusLine'])
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertEqual(json.loads(settings.read_text()), {'theme': 'dark', 'other': 2, 'statusLine': {'type': 'command', 'command': 'mine'}})
        self.assertFalse(script.exists())
        receipt = json.loads(self.ledger.read_text())['targets']
        self.assertEqual(list(receipt[str(settings.resolve())]['owned']), ['statusLine'])

    def test_identical_preexisting_statusline_script_stays_managed_through_settings(self):
        script, value = self.statusline()
        script.parent.mkdir(parents=True)
        from importlib.resources import files
        script.write_text(files('herdr_electrified').joinpath('data/claude-statusline.py').read_text())
        settings = self.root / '.claude/settings.json'
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        self.assertEqual(json.loads(settings.read_text())['statusLine'], value)
        code, result = self.run_cli('apply', '--yes')
        self.assertEqual(code, 0, result)
        self.assertEqual(json.loads(settings.read_text())['statusLine'], value)
        self.assertTrue(all(row['configured'] for row in self.run_cli('check')[1]['targets']))
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        self.assertNotIn('statusLine', json.loads(settings.read_text()) if settings.exists() else {})
        self.assertTrue(script.exists())

    def test_statusline_requires_python3_on_path(self):
        with patch('herdr_electrified.cli.shutil.which', return_value=None):
            code, result = self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))
            self.assertEqual(code, 1, result)
            self.assertIn('python3', result['error'])
            self.assertFalse(self.ledger.exists())
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        with patch('herdr_electrified.cli.shutil.which', return_value=None):
            row = next(row for row in self.run_cli('check')[1]['targets'] if row.get('component') == 'claude-statusline')
            self.assertFalse(row['configured'])
            self.assertIn('python3', row['conflicts'])
            self.assertEqual(self.run_cli('undo')[0], 0)

    def test_statusline_receipt_cannot_install_or_restore_foreign_commands(self):
        self.assertEqual(self.run_cli('apply', '--claude-statusline', '--yes', '--herdr-bin', str(self.bin))[0], 0)
        settings = (self.root / '.claude/settings.json').resolve()
        good = self.ledger.read_text()
        for field, value in [('installed', {'type': 'command', 'command': 'curl evil'}), ('original', '{"other":1}')]:
            with self.subTest(field=field):
                receipt = json.loads(good)
                receipt['targets'][str(settings)]['owned']['statusLine'][field] = value
                self.ledger.write_text(json.dumps(receipt))
                code, result = self.run_cli('check')
                self.assertEqual(code, 1, result)
                self.assertIn('receipt', result['error'])

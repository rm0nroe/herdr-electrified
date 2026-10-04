import hashlib
import os
import json
import shutil
from pathlib import Path
import unittest
import unittest.mock
import test_cli
from herdr_electrified import electric


class Electric(unittest.TestCase):
    run_cli = test_cli.CLI.run_cli

    def setUp(self):
        # Hermetic: these cases model a host with Codex and without Claude Code.
        test_cli.CLI.setUp(self)
        tools = self.root / 'tools'
        tools.mkdir()
        (tools / 'codex').write_text('#!/bin/sh\nexit 0\n')
        (tools / 'codex').chmod(0o700)
        self.env['PATH'] = os.pathsep.join([str(self.root), str(tools), '/usr/bin', '/bin'])
    def bundle(self):
        root = self.root / 'bundle with spaces'
        contents = {'herdr': self.bin.read_bytes(), 'codex/bin/codex': b'#!/bin/sh\nexit 0\n',
                    'codex/bin/codex-code-mode-host': b'helper', 'codex/codex-path/rg': b'rg',
                    'codex/codex-resources/zsh/bin/zsh': b'zsh',
                    'themes/codex-electric.tmTheme': b'<?xml version="1.0"?><plist version="1.0"><dict/></plist>'}
        for name, content in contents.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            p.chmod(0o700)
        (root / 'manifest.json').write_text(json.dumps({'version': '0.1.0', 'target': 'aarch64-apple-darwin',
            'files': {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}}))
        return root

    def test_electric_preview_apply_check_undo(self):
        root = self.bundle()
        before = self.target.read_bytes()
        code, result = self.run_cli('preview-apply', '--electric', str(root))
        self.assertEqual(code, 0, result)
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.target.read_bytes(), before)
        code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 0, result)
        launcher = self.root / '.local/bin/codex-electric'
        self.assertIn('CODEX_HERDR_REFERENCE_UI=1', launcher.read_text())
        # Any -c override forces Codex off the shared background server.
        self.assertNotIn(' -c ', launcher.read_text())
        # The pinned download must pass the bundle version check.
        self.assertIn(electric.BUNDLE.split('-')[2], electric.VERSIONS)
        self.assertTrue(launcher.stat().st_mode & 0o100)
        electric_config = self.root / 'config/herdr-electrified/electric/config.toml'
        self.assertEqual(self.target.read_bytes(), before)
        self.assertIn('#FF7BC2', electric_config.read_text())
        self.assertIn('HERDR_CONFIG_PATH=' + str(electric_config.resolve()), (self.root / '.local/bin/herdr-electric').read_text())
        self.assertEqual(self.run_cli('check')[0], 0)
        self.assertEqual(self.target.read_bytes(), before)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(electric_config.exists())
        self.assertFalse(launcher.exists())
        self.assertFalse((self.root / '.codex/themes/herdr-electric.tmTheme').exists())
        self.assertTrue((root / 'herdr').exists())

    def test_tampered_bundle_rejected_before_any_write(self):
        root = self.bundle()
        (root / 'codex/bin/codex-code-mode-host').write_text('tampered')
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 1, result)
        self.assertIn('checksum', result['error'])
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.target.read_bytes(), before)

    def test_missing_bundle_has_recovery_hint_without_writes(self):
        root = self.root / 'missing bundle'
        before = self.target.read_bytes()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 1, result)
        for hint in ('Electric bundle missing', str(root), 'restore', '--electric', 'undo --herdr-bin'):
            self.assertIn(hint, result['error'])
        self.assertNotIn('[Errno', result['error'])
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.target.read_bytes(), before)

    def test_moved_bundle_check_apply_and_recovery(self):
        root = self.bundle()
        original = self.target.read_bytes()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes')[0], 0)
        moved = root.rename(self.root / 'moved bundle')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for command in [('check',), ('preview-apply',), ('apply', '--yes')]:
            code, result = self.run_cli(*command)
            self.assertEqual(code, 1, result)
            self.assertIn('Electric bundle missing', result['error'])
            self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        code, result = self.run_cli('undo', '--herdr-bin', str(self.bin))
        self.assertEqual(code, 0, result)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.run_cli('apply', '--electric', str(moved), '--yes')[0], 0)
        self.assertEqual(self.run_cli('check')[0], 0)

    def test_edited_launcher_survives_undo(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes')[0], 0)
        launcher = self.root / '.local/bin/codex-electric'
        launcher.write_text('# user replacement\n')
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertEqual(launcher.read_text(), '# user replacement\n')

    def test_reapply_restores_modes_and_uses_recorded_paths(self):
        root = self.bundle()
        launcher = self.root / '.local/bin/codex-electric'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('# original launcher\n')
        launcher.chmod(0o751)
        env = {'CODEX_HOME': str(self.root / 'custom codex')}
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        receipt = self.ledger.read_bytes()
        self.assertEqual(self.run_cli('apply', '--yes')[0], 0)
        self.assertEqual(self.ledger.read_bytes(), receipt)
        self.assertIn('custom codex', launcher.read_text())
        herdr = (self.root / '.local/bin/herdr-electric').read_text()
        self.assertIn('unset HERDR_SOCKET_PATH HERDR_CLIENT_SOCKET_PATH HERDR_SESSION', herdr)
        self.assertIn('--session herdr-electrified', herdr)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(launcher.read_text(), '# original launcher\n')
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o751)

    def test_interrupted_apply_recovers_electric_metadata(self):
        from unittest.mock import patch
        from herdr_electrified import config as c
        root = self.bundle()
        install = c.install
        calls = 0
        def interrupt(path, before, after, mode):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated interruption')
            return install(path, before, after, mode)
        with patch.object(c, 'install', side_effect=interrupt):
            code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 1, result)
        self.assertEqual(json.loads(self.ledger.read_text())['electric']['root'], str(root.resolve()))
        self.assertEqual(self.run_cli('apply', '--yes')[0], 0)
        self.assertTrue((self.root / '.local/bin/herdr-electric').exists())
        self.assertEqual(self.run_cli('undo')[0], 0)

    def test_bundle_does_not_reload_inherited_stock_session(self):
        root = self.bundle()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env={
            'HERDR_ENV': '1', 'HERDR_BIN_PATH': str(self.bin), 'HERDR_SOCKET_PATH': '/tmp/stock.sock'})
        self.assertEqual(code, 0, result)
        self.assertEqual(result['targets'][0]['reload'], 'skipped')

    def test_mode_edit_is_preserved_as_undo_conflict(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes')[0], 0)
        launcher = self.root / '.local/bin/codex-electric'
        launcher.chmod(0o750)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 1, result)
        self.assertTrue(launcher.exists())
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o750)

    def test_rewritten_manifest_requires_explicit_reselection(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes')[0], 0)
        manifest = root / 'manifest.json'
        manifest.write_text(manifest.read_text() + ' ')
        code, result = self.run_cli('check')
        self.assertEqual(code, 1, result)
        self.assertIn('bundle changed', result['error'])
        self.assertEqual(self.run_cli('undo')[0], 0)

    def test_launcher_symlink_cannot_overwrite_stock_command(self):
        root = self.bundle()
        stock = self.root / 'stock-codex'
        stock.write_text('# stock command\n')
        launcher = self.root / '.local/bin/codex-electric'
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(stock)
        code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 1, result)
        self.assertIn('symlink', result['error'])
        self.assertEqual(stock.read_text(), '# stock command\n')
        self.assertFalse(self.ledger.exists())

    def test_unsupported_platform_and_unsafe_manifest_do_not_write(self):
        from unittest.mock import patch
        root = self.bundle()
        with patch('herdr_electrified.electric.platform.system', return_value='Linux'):
            code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 1, result)
        self.assertIn('macOS arm64', result['error'])
        manifest = root / 'manifest.json'
        data = json.loads(manifest.read_text())
        data['files']['../outside'] = '0' * 64
        manifest.write_text(json.dumps(data))
        code, result = self.run_cli('apply', '--electric', str(root), '--yes')
        self.assertEqual(code, 1, result)
        self.assertIn('unsafe bundle member', result['error'])
        self.assertFalse(self.ledger.exists())

    def test_settings_only_and_electric_keep_separate_configs(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--herdr-bin', str(self.bin), '--yes')[0], 0)
        stock = self.target.read_bytes()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes')[0], 0)
        self.assertEqual(self.target.read_bytes(), stock)
        self.assertNotIn(b'#FF7BC2', stock)
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(self.target.read_text(), '# keep me\n[ui]\nsidebar_width = 19 # original\n')

    def test_legacy_receipt_on_shared_config_still_undoes(self):
        root = self.bundle()
        before = self.target.read_bytes()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--herdr-config', str(self.target), '--yes')[0], 0)
        self.assertIn('#FF7BC2', self.target.read_text())
        self.assertEqual(self.run_cli('undo')[0], 0)
        self.assertEqual(self.target.read_bytes(), before)


class Agents(unittest.TestCase):
    setUp = test_cli.CLI.setUp
    run_cli = test_cli.CLI.run_cli
    bundle = Electric.bundle

    def hermetic(self, codex=False, claude=False):
        tools = self.root / 'tools'
        tools.mkdir(exist_ok=True)
        for name, wanted in (('codex', codex), ('claude', claude)):
            if wanted:
                (tools / name).write_text('#!/bin/sh\nexit 0\n')
                (tools / name).chmod(0o700)
        return {'PATH': str(tools) + ':/usr/bin:/bin'}

    def files(self):
        return {p for p in self.root.rglob('*') if 'bundle with spaces' not in p.parts and 'Library' not in p.parts}

    def test_claude_only_host_gets_no_codex_pieces_and_undo_leaves_nothing(self):
        root = self.bundle()
        env = self.hermetic(claude=True)
        before = self.files()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], ['claude'])
        self.assertFalse((self.root / '.codex').exists())
        self.assertFalse((self.root / '.local/bin/codex-electric').exists())
        self.assertFalse((self.root / '.local/share/herdr-electrified').exists())
        herdr = (self.root / '.local/bin/herdr-electric').read_text()
        self.assertNotIn('export PATH=', herdr)
        self.assertIn('custom:herdr-electrified', (self.root / '.claude/settings.json').read_text())
        self.assertTrue((self.root / '.claude/themes/herdr-electrified.json').exists())
        self.assertEqual(self.run_cli('check', env=env)[0], 0)
        # Runtime files the bundled Herdr writes on first launch.
        notes = self.root / 'config/herdr-electrified/electric/release-notes.json'
        notes.write_text('{}')
        session = self.root / 'config/herdr/sessions/herdr-electrified'
        session.mkdir(parents=True)
        (session / 'state.json').write_text('{}')
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(self.files() - {self.ledger, self.ledger.with_name('lock'), self.ledger.parent, self.ledger.parent.parent}, before)

    def test_codex_only_host_gets_no_claude_pieces_and_undo_removes_created_dirs(self):
        root = self.bundle()
        env = self.hermetic(codex=True)
        before = self.files()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], ['codex'])
        self.assertTrue((self.root / '.codex/themes/herdr-electric.tmTheme').exists())
        self.assertTrue((self.root / '.local/share/herdr-electrified/commands/codex').exists())
        self.assertFalse((self.root / '.claude').exists())
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertEqual(self.files() - {self.ledger, self.ledger.with_name('lock'), self.ledger.parent, self.ledger.parent.parent}, before)

    @unittest.skipUnless(Path('/bin/zsh').exists(), 'needs zsh')
    def test_pane_codex_beats_a_profile_that_prepends_stock_codex(self):
        import subprocess
        root = self.bundle()
        env = self.hermetic(codex=True)
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        stock = self.root / 'stock'
        stock.mkdir()
        (stock / 'codex').write_text('#!/bin/sh\nexit 0\n')
        (stock / 'codex').chmod(0o700)
        for name in ('.zshenv', '.zshrc'):
            (self.root / name).write_text(f'export PATH="{stock}:$PATH"\n')
        # Run the real launcher, but start the pane shell where it would exec Herdr.
        launcher = (self.root / '.local/bin/herdr-electric').read_text().splitlines()
        pane = self.root / 'pane.sh'
        pane.write_text('\n'.join(launcher[:-1] + ['exec /bin/zsh -i -l']) + '\n')
        out = self.root / 'which'
        shell_env = {'HOME': str(self.root), 'PATH': '/usr/bin:/bin', 'TERM': 'dumb'}
        subprocess.run(['/bin/sh', str(pane)], input=f'command -v codex > {out}\necho "${{ZDOTDIR-unset}}" >> {out}\nexit\n',
                       env=shell_env, capture_output=True, text=True, timeout=20)
        lines = out.read_text().splitlines()
        self.assertEqual(lines, [str(self.root / '.local/share/herdr-electrified/commands/codex'), 'unset'])
        # A user ZDOTDIR is honored and restored for nested shells.
        (self.root / 'zd').mkdir()
        (self.root / 'zd/.zshrc').write_text(f'export PATH="{stock}:$PATH"\n')
        subprocess.run(['/bin/sh', str(pane)], input=f'command -v codex > {out}\necho "$ZDOTDIR" >> {out}\nexit\n',
                       env=shell_env | {'ZDOTDIR': str(self.root / 'zd')}, capture_output=True, text=True, timeout=20)
        self.assertEqual(out.read_text().splitlines(), [str(self.root / '.local/share/herdr-electrified/commands/codex'), str(self.root / 'zd')])
        for name in ('.zshenv', '.zshrc', 'pane.sh', 'which', '.zsh_history'):
            (self.root / name).unlink(missing_ok=True)
        shutil.rmtree(self.root / 'zd')
        shutil.rmtree(stock)
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertFalse((self.root / '.local/share/herdr-electrified').exists())

    def test_no_agent_host_installs_herdr_only(self):
        root = self.bundle()
        env = self.hermetic()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], [])
        self.assertEqual({t['path'] for t in result['targets']},
                         {str((self.root / 'config/herdr-electrified/electric/config.toml').resolve()),
                          str((self.root / '.local/bin/herdr-electric').resolve())})

    def test_codex_home_dir_alone_is_not_codex(self):
        root = self.bundle()
        env = self.hermetic()
        (self.root / '.codex').mkdir()
        (self.root / '.codex/logs_2.sqlite').write_text('')
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], [])
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertTrue((self.root / '.codex/logs_2.sqlite').exists())

    def test_nonempty_created_dir_is_kept(self):
        root = self.bundle()
        env = self.hermetic(codex=True)
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        (self.root / '.codex/history.jsonl').write_text('mine')
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertEqual((self.root / '.codex/history.jsonl').read_text(), 'mine')
        self.assertFalse((self.root / '.codex/themes').exists())

    def test_detected_agents_are_sticky_across_reapply(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=self.hermetic(codex=True))[0], 0)
        (self.root / 'tools/codex').unlink()
        receipt = self.ledger.read_bytes()
        code, result = self.run_cli('apply', '--yes', env=self.hermetic())
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], ['codex'])
        self.assertEqual(self.ledger.read_bytes(), receipt)

    def test_legacy_receipt_without_agents_keeps_codex_and_undoes(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=self.hermetic(codex=True))[0], 0)
        data = json.loads(self.ledger.read_text())
        del data['electric']['agents'], data['electric']['created_dirs']
        self.ledger.write_text(json.dumps(data))
        code, result = self.run_cli('check', env=self.hermetic())
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], ['codex'])
        self.assertEqual(self.run_cli('undo', env=self.hermetic())[0], 0)
        self.assertFalse((self.root / '.local/bin/codex-electric').exists())

    def test_undo_refuses_while_electric_session_is_live(self):
        import socket
        root = self.bundle()
        env = self.hermetic()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        session = self.root / 'config/herdr/sessions/herdr-electrified'
        session.mkdir(parents=True)
        # AF_UNIX paths are short on macOS; bind via a relative path.
        cwd = os.getcwd()
        os.chdir(session)
        server = socket.socket(socket.AF_UNIX)
        try:
            server.bind('herdr.sock')
            server.listen()
        finally:
            os.chdir(cwd)
        self.addCleanup(server.close)
        before = self.ledger.read_bytes()
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 1, result)
        self.assertIn('Herdr Electric is running', result['error'])
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_launcher_clears_inherited_claude_child_marker(self):
        root = self.bundle()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=self.hermetic(claude=True))[0], 0)
        herdr = (self.root / '.local/bin/herdr-electric').read_text()
        self.assertIn('unset HERDR_SOCKET_PATH HERDR_CLIENT_SOCKET_PATH HERDR_SESSION CLAUDE_CODE_CHILD_SESSION', herdr)

    def test_undo_removes_electric_config_after_herdr_records_onboarding(self):
        import re
        root = self.bundle()
        env = self.hermetic()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        config = self.root / 'config/herdr-electrified/electric/config.toml'
        # Herdr's upsert_top_level_bool inserts the key before the first table.
        config.write_text(re.sub(r'^\[', 'onboarding = false\n\n[', config.read_text(), count=1, flags=re.M))
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 0, result)
        self.assertFalse(config.exists())
        self.assertFalse(config.parent.exists())

    def test_undo_keeps_electric_config_with_user_settings(self):
        root = self.bundle()
        env = self.hermetic()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        config = self.root / 'config/herdr-electrified/electric/config.toml'
        config.write_text('onboarding = false\n' + config.read_text() + '\n[keys]\nprefix = "ctrl-a"\n')
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertIn('prefix = "ctrl-a"', config.read_text())

    def test_quarantined_bundle_is_rejected_with_fix_before_any_write(self):
        import subprocess
        root = self.bundle()
        subprocess.run(['xattr', '-w', 'com.apple.quarantine', '0083;00000000;Safari;', str(root / 'herdr')], check=True)
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env=self.hermetic())
        self.assertEqual(code, 1, result)
        self.assertIn('xattr -dr com.apple.quarantine', result['error'])
        self.assertIn(str(root.resolve()), result['error'])
        self.assertFalse(self.ledger.exists())

    def test_bundle_0_2_0_manifest_is_accepted(self):
        root = self.bundle()
        manifest = root / 'manifest.json'
        manifest.write_text(manifest.read_text().replace('"version": "0.1.0"', '"version": "0.2.0"'))
        code, result = self.run_cli('preview-apply', '--electric', str(root), env=self.hermetic())
        self.assertEqual(code, 0, result)


class Ghostty(unittest.TestCase):
    setUp = test_cli.CLI.setUp
    run_cli = test_cli.CLI.run_cli
    bundle = Electric.bundle
    files = Agents.files

    def ghostty_host(self):
        apps = self.root / 'Applications'
        (apps / 'Ghostty.app').mkdir(parents=True)
        patcher = unittest.mock.patch('herdr_electrified.electric.GHOSTTY_APPS', (str(apps),))
        patcher.start()
        self.addCleanup(patcher.stop)
        return {'PATH': '/usr/bin:/bin'}

    def font_bundle(self):
        root = self.bundle()
        fonts = {'fonts/JetBrainsMonoNerdFontMono-Regular.ttf': b'regular', 'fonts/JetBrainsMonoNerdFontMono-Bold.ttf': b'bold'}
        manifest = json.loads((root / 'manifest.json').read_text())
        for name, content in fonts.items():
            (root / name).parent.mkdir(exist_ok=True)
            (root / name).write_bytes(content)
            manifest['files'][name] = hashlib.sha256(content).hexdigest()
        manifest['version'] = '0.2.1'
        (root / 'manifest.json').write_text(json.dumps(manifest))
        return root

    def test_fresh_host_gets_ghostty_look_and_undo_leaves_nothing(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        before = self.files()
        code, result = self.run_cli('apply', '--electric', str(root), '--yes', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['agents'], ['ghostty'])
        look = self.root / 'config/herdr-electrified/electric/ghostty.conf'
        self.assertIn('background = #11111b', look.read_text())
        self.assertIn('font-family = "JetBrainsMono Nerd Font Mono"', look.read_text())
        ghostty = self.root / 'config/ghostty/config.ghostty'
        self.assertIn('config-file = "?' + str(look.resolve()) + '"', ghostty.read_text())
        self.assertEqual((self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Bold.ttf').read_bytes(), b'bold')
        self.assertEqual(self.run_cli('check', env=env)[0], 0)
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 0, result)
        self.assertFalse((self.root / 'config/ghostty').exists())
        self.assertFalse((self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Bold.ttf').exists())
        state = {self.ledger, self.ledger.with_name('lock'), self.ledger.parent, self.ledger.parent.parent}
        self.assertEqual(self.files() - state, before)

    def test_include_goes_in_last_loaded_file_and_undo_keeps_user_edits(self):
        root = self.bundle()
        env = self.ghostty_host()
        xdg = self.root / 'config/ghostty/config'
        xdg.parent.mkdir(parents=True)
        xdg.write_text('font-size = 11\n')
        support = self.root / 'Library/Application Support/com.mitchellh.ghostty/config.ghostty'
        support.parent.mkdir(parents=True)
        support.write_text('')
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        self.assertEqual(xdg.read_text(), 'font-size = 11\n')
        self.assertIn('config-file = "?', support.read_text())
        support.write_text(support.read_text() + 'cursor-color = #ffffff\n')
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(support.read_text(), 'cursor-color = #ffffff\n')

    def test_removed_include_is_an_undo_conflict(self):
        root = self.bundle()
        env = self.ghostty_host()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        ghostty = self.root / 'config/ghostty/config.ghostty'
        ghostty.write_text('font-size = 14\n')
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 1, result)
        self.assertEqual(ghostty.read_text(), 'font-size = 14\n')

    def test_users_own_font_is_never_replaced_or_removed(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        mine = self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Regular.ttf'
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b'users copy')
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        self.assertEqual(mine.read_bytes(), b'users copy')
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertEqual(mine.read_bytes(), b'users copy')
        self.assertFalse((self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Bold.ttf').exists())

    def test_edited_font_survives_undo(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        bold = self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Bold.ttf'
        bold.write_bytes(b'replaced by user')
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertEqual(bold.read_bytes(), b'replaced by user')

    def test_planted_staging_symlink_never_redirects_a_font_write(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        victim = self.root / 'victim.txt'
        victim.write_text('keep')
        fonts = self.root / 'Library/Fonts'
        fonts.mkdir(parents=True)
        for name in ('Regular', 'Bold'):
            (fonts / f'.herdr-electrified-JetBrainsMonoNerdFontMono-{name}.ttf').symlink_to(victim)
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        self.assertEqual(victim.read_text(), 'keep')
        self.assertEqual((fonts / 'JetBrainsMonoNerdFontMono-Bold.ttf').read_bytes(), b'bold')

    def test_interrupted_font_install_recovers_on_retry_and_undoes(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        real = os.link
        calls = []
        def flaky(src, dst):
            if str(dst).endswith('.ttf'):
                calls.append(dst)
            if len(calls) == 2 and str(dst).endswith('.ttf'):
                raise OSError('disk full')
            return real(src, dst)
        with unittest.mock.patch('herdr_electrified.electric.os.link', flaky):
            self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 1)
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        fonts = self.root / 'Library/Fonts'
        self.assertEqual(sorted(p.name for p in fonts.iterdir()), ['JetBrainsMonoNerdFontMono-Bold.ttf', 'JetBrainsMonoNerdFontMono-Regular.ttf'])
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertEqual(list(fonts.iterdir()), [])

    def test_failed_cleanup_keeps_its_record_and_retry_finishes(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        with unittest.mock.patch('herdr_electrified.electric.cleanup', side_effect=OSError('busy')):
            self.assertEqual(self.run_cli('undo', env=env)[0], 1)
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertFalse((self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Bold.ttf').exists())
        self.assertNotIn('electric', json.loads(self.ledger.read_text()))

    def test_check_reports_edited_font(self):
        root = self.font_bundle()
        env = self.ghostty_host()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        (self.root / 'Library/Fonts/JetBrainsMonoNerdFontMono-Bold.ttf').write_bytes(b'edited')
        code, result = self.run_cli('check', env=env)
        self.assertEqual(code, 0, result)
        status = {Path(f['path']).name: f['status'] for f in result['fonts']}
        self.assertEqual(status, {'JetBrainsMonoNerdFontMono-Bold.ttf': 'changed', 'JetBrainsMonoNerdFontMono-Regular.ttf': 'ok'})


class Cleanup(unittest.TestCase):
    setUp = test_cli.CLI.setUp
    run_cli = test_cli.CLI.run_cli
    bundle = Electric.bundle
    hermetic = Agents.hermetic

    def test_undo_keeps_release_notes_in_a_directory_it_did_not_create(self):
        root = self.bundle()
        custom = self.root / 'mine'
        custom.mkdir()
        notes = custom / 'release-notes.json'
        notes.write_text('{"mine": true}')
        env = self.hermetic()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--herdr-config', str(custom / 'config.toml'), '--yes', env=env)[0], 0)
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertEqual(notes.read_text(), '{"mine": true}')

    def test_undo_cleans_the_session_recorded_at_apply_not_the_current_xdg(self):
        root = self.bundle()
        env = self.hermetic()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        ours = self.root / 'config/herdr/sessions/herdr-electrified'
        ours.mkdir(parents=True)
        (ours / 'state.json').write_text('{}')
        other = self.root / 'other/herdr/sessions/herdr-electrified'
        other.mkdir(parents=True)
        (other / 'keep.json').write_text('{}')
        self.assertEqual(self.run_cli('undo', env=env | {'XDG_CONFIG_HOME': str(self.root / 'other')})[0], 0)
        self.assertTrue((other / 'keep.json').exists())
        self.assertFalse(ours.exists())

    def strip_session(self):
        data = json.loads(self.ledger.read_text())
        del data['electric']['session']
        self.ledger.write_text(json.dumps(data))

    def test_legacy_receipt_derives_session_from_recorded_config(self):
        root = self.bundle()
        env = self.hermetic()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--yes', env=env)[0], 0)
        self.strip_session()
        ours = self.root / 'config/herdr/sessions/herdr-electrified'
        ours.mkdir(parents=True)
        other = self.root / 'other/herdr/sessions/herdr-electrified'
        other.mkdir(parents=True)
        (other / 'keep.json').write_text('{}')
        self.assertEqual(self.run_cli('undo', env=env | {'XDG_CONFIG_HOME': str(self.root / 'other')})[0], 0)
        self.assertTrue((other / 'keep.json').exists())
        self.assertFalse(ours.exists())

    def test_legacy_receipt_with_custom_config_leaves_sessions_alone(self):
        root = self.bundle()
        env = self.hermetic()
        custom = self.root / 'mine'
        custom.mkdir()
        self.assertEqual(self.run_cli('apply', '--electric', str(root), '--herdr-config', str(custom / 'config.toml'), '--yes', env=env)[0], 0)
        self.strip_session()
        session = self.root / 'config/herdr/sessions/herdr-electrified'
        session.mkdir(parents=True)
        (session / 'keep.json').write_text('{}')
        self.assertEqual(self.run_cli('undo', env=env)[0], 0)
        self.assertTrue((session / 'keep.json').exists())

    def test_plain_text_output_lists_font_status(self):
        from herdr_electrified.cli import render
        self.assertIn('font changed: /x/A.ttf', render({'fonts': [{'path': '/x/A.ttf', 'status': 'changed'}]}))

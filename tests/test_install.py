import hashlib
import io
import tarfile
import unittest
import unittest.mock

import test_cli
import test_electric


class Install(unittest.TestCase):
    setUp = test_electric.Electric.setUp
    run_cli = test_cli.CLI.run_cli
    bundle = test_electric.Electric.bundle
    ghostty_host = test_electric.Ghostty.ghostty_host
    files = test_electric.Agents.files

    def serve(self, assets):
        """Answer downloads from memory; anything not listed fails the test."""
        self.fetched = []
        def urlopen(url, timeout=None):
            self.fetched.append(url)
            return io.BytesIO(assets[url])
        patcher = unittest.mock.patch('herdr_electrified.electric.urlopen', urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def archive(self):
        from herdr_electrified import electric
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as tar:
            tar.add(self.bundle(), arcname=electric.BUNDLE)
        return raw.getvalue()

    def pin(self, name, data):
        patcher = unittest.mock.patch('herdr_electrified.electric.' + name, hashlib.sha256(data).hexdigest())
        patcher.start()
        self.addCleanup(patcher.stop)

    def fonts(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:xz') as tar:
            for name in ('JetBrainsMonoNerdFontMono-Regular.ttf', 'JetBrainsMonoNerdFontMono-Bold.ttf',
                         'JetBrainsMonoNerdFontMono-Italic.ttf', 'JetBrainsMonoNerdFontMono-BoldItalic.ttf', 'Other.ttf'):
                data = name.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return raw.getvalue()

    def test_install_downloads_verified_bundle_and_applies_electric(self):
        from herdr_electrified import electric
        data = self.archive()
        self.pin('BUNDLE_SHA', data)
        self.serve({electric.BUNDLE_URL: data})
        code, result = self.run_cli('install', '--yes')
        self.assertEqual(code, 0, result)
        root = self.root / '.local/share/herdr-electrified/bundles' / electric.BUNDLE
        self.assertIn(str(root.resolve() / 'herdr'), (self.root / '.local/bin/herdr-electric').read_text())
        self.assertTrue((root / 'codex/bin/codex').stat().st_mode & 0o100)
        self.assertEqual([p.name for p in root.parent.iterdir()], [electric.BUNDLE])
        # Re-running reuses the extracted bundle and changes nothing.
        receipt = self.ledger.read_bytes()
        code, result = self.run_cli('install', '--yes')
        self.assertEqual(code, 0, result)
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(self.ledger.read_bytes(), receipt)

    def test_install_dry_run_caches_the_bundle_but_writes_no_config(self):
        from herdr_electrified import electric
        data = self.archive()
        self.pin('BUNDLE_SHA', data)
        self.serve({electric.BUNDLE_URL: data})
        code, result = self.run_cli('install', '--dry-run')
        self.assertEqual(code, 0, result)
        self.assertIn('new', {row['change'] for row in result['targets']})
        self.assertFalse((self.root / '.local/bin/herdr-electric').exists())
        self.assertFalse(self.ledger.exists())

    def test_download_past_its_deadline_leaves_nothing(self):
        from herdr_electrified import electric
        self.serve({'https://example.invalid/x': b'data'})
        with self.assertRaisesRegex(ValueError, 'took over -1 min; nothing installed'):
            electric.download('https://example.invalid/x', 'unused', self.root / 'dl', 'x', deadline=-1)
        self.assertEqual(list((self.root / 'dl').iterdir()), [])

    def test_install_refuses_a_bundle_with_the_wrong_checksum(self):
        from herdr_electrified import electric
        self.serve({electric.BUNDLE_URL: self.archive()})
        code, result = self.run_cli('install', '--yes')
        self.assertEqual(code, 1, result)
        self.assertIn('checksum mismatch', result['error'])
        self.assertEqual(list((self.root / '.local/share/herdr-electrified/bundles').iterdir()), [])
        self.assertFalse(self.ledger.exists())

    def test_settings_only_install_adds_ghostty_look_and_undo_removes_it(self):
        from herdr_electrified import electric
        data = self.fonts()
        self.pin('FONT_SHA', data)
        self.serve({electric.FONT_URL: data})
        env = self.ghostty_host() | {'PATH': f'{self.root}:/usr/bin:/bin'}
        before = self.files()
        original = self.target.read_bytes()
        code, result = self.run_cli('install', '--settings-only', '--yes', env=env)
        self.assertEqual(code, 0, result)
        self.assertIn('sidebar_width = 31', self.target.read_text())
        look = self.root / 'config/herdr-electrified/ghostty.conf'
        self.assertIn('background = #11111b', look.read_text())
        ghostty = self.root / 'config/ghostty/config.ghostty'
        self.assertIn('config-file = "?' + str(look.resolve()) + '"', ghostty.read_text())
        fonts = self.root / 'Library/Fonts'
        self.assertEqual(sorted(p.name for p in fonts.iterdir()), sorted(electric.FONTS))
        self.assertEqual(self.run_cli('check', env=env)[0], 0)
        self.assertEqual(self.run_cli('install', '--settings-only', '--yes', env=env)[0], 0)
        self.assertEqual(len(self.fetched), 1)
        code, result = self.run_cli('undo', env=env)
        self.assertEqual(code, 0, result)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(list(fonts.iterdir()), [])
        cache = self.root / '.local'
        state = {self.ledger, self.ledger.with_name('lock'), self.ledger.parent, self.ledger.parent.parent}
        self.assertEqual({p for p in self.files() - state if cache not in p.parents and p != cache}, before)

    def test_settings_only_install_selects_codex_theme_and_undo_restores_config(self):
        from importlib.resources import files
        from herdr_electrified.cli import component
        self.serve({})
        config = self.root / '.codex/config.toml'
        config.parent.mkdir()
        original = 'model = "gpt"  # mine\n\n[tui]\ntheme = "dark"\nanimations = false\n'
        config.write_text(original)
        before = self.files()
        # Opt-in only: the theme is dark-tuned and tui.theme applies in every terminal.
        self.assertEqual(self.run_cli('install', '--settings-only', '--yes')[0], 0)
        self.assertEqual(config.read_text(), original)
        self.assertFalse((self.root / '.codex/themes').exists())
        self.assertEqual(self.run_cli('undo')[0], 0)
        code, result = self.run_cli('install', '--settings-only', '--codex-theme', '--yes')
        self.assertEqual(code, 0, result)
        self.assertEqual(config.read_text(), original.replace('"dark"', '"herdr-electric"'))
        theme = self.root / '.codex/themes/herdr-electric.tmTheme'
        self.assertEqual(theme.read_text(), files('herdr_electrified').joinpath('data/codex-electric.tmTheme').read_text())
        self.assertEqual({component(row) for row in result['targets']}, {'Herdr config', 'Codex'})
        code, result = self.run_cli('check')
        self.assertEqual(code, 0, result)
        self.assertTrue(all(row['configured'] for row in result['targets']), result)
        # Once owned, the theme stays managed without the flag until undo.
        code, result = self.run_cli('install', '--settings-only', '--yes')
        self.assertEqual((code, [row['change'] for row in result['targets']]), (0, ['unchanged'] * 3), result)
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        self.assertEqual(config.read_text(), original)
        state = {self.ledger, self.ledger.with_name('lock'), self.ledger.parent, self.ledger.parent.parent}
        self.assertEqual(self.files() - state, before)

    def test_settings_only_codex_theme_on_a_fresh_codex_home_undoes_to_nothing(self):
        self.serve({})
        before = self.files()
        self.assertEqual(self.run_cli('install', '--settings-only', '--codex-theme', '--yes')[0], 0)
        self.assertEqual((self.root / '.codex/config.toml').read_text(), '[tui]\ntheme = "herdr-electric"\n')
        code, result = self.run_cli('undo')
        self.assertEqual(code, 0, result)
        self.assertFalse((self.root / '.codex').exists())
        state = {self.ledger, self.ledger.with_name('lock'), self.ledger.parent, self.ledger.parent.parent}
        self.assertEqual(self.files() - state, before)

    def test_font_archive_missing_a_font_is_refused_cleanly(self):
        from herdr_electrified import electric
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:xz') as tar:
            info = tarfile.TarInfo('Other.ttf')
            tar.addfile(info, io.BytesIO(b''))
        self.pin('FONT_SHA', raw.getvalue())
        self.serve({electric.FONT_URL: raw.getvalue()})
        env = self.ghostty_host() | {'PATH': f'{self.root}:/usr/bin:/bin'}
        code, result = self.run_cli('install', '--settings-only', '--yes', env=env)
        self.assertEqual(code, 1, result)
        self.assertIn('JetBrainsMonoNerdFontMono-Regular.ttf', result['error'])
        self.assertEqual(list((self.root / '.local/share/herdr-electrified/nerd-fonts-3.5.1').iterdir()), [])
        self.assertFalse(self.ledger.exists())

    def test_settings_only_install_without_ghostty_downloads_nothing(self):
        self.serve({})
        code, result = self.run_cli('install', '--settings-only', '--yes', env={'PATH': f'{self.root}:/usr/bin:/bin'})
        self.assertEqual(code, 0, result)
        self.assertEqual(self.fetched, [])
        self.assertEqual([row['path'] for row in result['targets']], [str(self.target.resolve())])

    def test_settings_only_install_needs_herdr_on_path(self):
        code, result = self.run_cli('install', '--settings-only', '--yes', env={'PATH': '/usr/bin:/bin'})
        self.assertEqual(code, 1, result)
        self.assertIn('Herdr', result['error'])


if __name__ == '__main__':
    unittest.main()

"""Reversible appearance settings through the apply/check/undo seam."""
import argparse
import hashlib
import json
import os
import select
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import config as c
from . import electric


def association(path):
    env = os.environ
    return bool(env.get('HERDR_ENV') == '1' and env.get('HERDR_BIN_PATH') and env.get('HERDR_SOCKET_PATH')
                and env.get('HERDR_CONFIG_PATH') and c.canonical(env['HERDR_CONFIG_PATH']) == path)


def codex_home():
    return str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve())


def python3_ok():
    """Claude Code runs the statusline as python3 from PATH, not uv's interpreter."""
    path = shutil.which('python3')
    try:
        return bool(path) and subprocess.run([path, '-c', 'import sys; sys.exit(sys.version_info < (3, 9))'], stdin=subprocess.DEVNULL,
                                             capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ask(question, timeout=300):
    """The lowercased answer, or None when nobody answers in time."""
    print(question, end='', file=sys.stderr, flush=True)
    try:
        ready = select.select([sys.stdin], [], [], timeout)[0]
    except (OSError, ValueError):
        ready = True  # no file descriptor (test doubles): read directly
    if not ready:
        print(file=sys.stderr)
        return None
    return sys.stdin.readline().strip().lower()


def execute(args):
    readonly = args.command == 'check' or args.dry_run
    receipt = c.receipt_path()
    data = c.load_receipt(receipt)
    electric_targets = {}
    agents = set()
    blocked = args.command == 'undo' and data.get('electric') and electric.running(electric.recorded_session(data['electric']))
    if blocked and not readonly:
        raise ValueError('Herdr Electric is running; quit it, then rerun herdr-electrified undo')
    # A settings-only install targets stock Herdr even when an Electric install is recorded beside it.
    bundle = args.electric or (data.get('electric', {}).get('root') if args.command != 'undo' and not args.settings_only else None)
    if bundle:
        bundle_root, manifest = electric.verify(bundle)
        manifest_hash = hashlib.sha256((bundle_root / 'manifest.json').read_bytes()).hexdigest()
        if not args.electric and data.get('electric', {}).get('manifest_hash', manifest_hash) != manifest_hash:
            raise ValueError('Electric bundle changed; verify the release checksum and explicitly select --electric again')
        # Electric owns its own config so stock Herdr never inherits the palette.
        target = c.resolve_target(args.herdr_config or data.get('electric', {}).get('config') or str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'herdr-electrified/electric/config.toml'))
        if args.herdr_bin and c.canonical(args.herdr_bin) != bundle_root / 'herdr':
            raise ValueError('Electric uses its bundled Herdr; omit --herdr-bin')
        args.herdr_config = str(target)
        args.herdr_bin = str(bundle_root / 'herdr')
        codex = data.get('electric', {}).get('codex_home') or codex_home()
        prior = data.get('electric', {})
        # Early pre-release receipts predate detection and always installed the Codex pieces.
        agents = set(prior.get('agents', ['codex'] if prior else []))
        if args.command != 'check' or not prior:
            agents |= electric.detect(codex)
        electric_targets = electric.targets(bundle_root, target, codex, agents)
    # Settings-only Ghostty look: same appearance file as Electric, owned outside any bundle.
    layer = not bundle and args.command == 'apply' and (args.ghostty or 'ghostty' in data)
    xdg = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    look = c.canonical((target.parent if bundle else xdg / 'herdr-electrified') / 'ghostty.conf') if bundle or layer else None
    if layer:
        electric_targets[look] = c.files('herdr_electrified').joinpath('data/ghostty.conf').read_text()
    # Settings-only Codex theme: the theme file plus tui.theme, managed until undo once owned.
    codex_config = next((c.canonical(p) for p, e in data['targets'].items() if e.get('kind') == 'codex-config'), None)
    if bundle and args.codex_theme:
        raise ValueError("--codex-theme is for settings-only installs; Electric's Codex has the theme built in")
    themed = not bundle and args.command == 'apply' and bool(args.codex_theme or codex_config)
    if themed:
        codex_config = codex_config or c.canonical(Path(codex_home()) / 'config.toml')
        electric_targets[c.canonical(codex_config.parent / 'themes/herdr-electric.tmTheme')] = c.files('herdr_electrified').joinpath('data/codex-electric.tmTheme').read_text()
    result = {'targets': [], 'loaded': 'unknown', 'tier2': 'Electric bundle verified' if bundle else 'not selected', 'installed': 'CLI available'}
    if bundle:
        result['agents'] = sorted(agents)
        result['bundle'] = manifest['version']
    if args.agent == 'claude':
        result['notice'] = '--agent claude is deprecated and does nothing: Claude Code gets the Electric theme only inside herdr-electric panes.'
    fonts = data.get('electric', {}).get('fonts', []) + data.get('ghostty', {}).get('fonts', [])
    if fonts:
        result['fonts'] = electric.font_status(fonts)
    if readonly and c.busy(receipt):
        result['notice'] = 'busy, results may be stale'
    if 'pending' in data:
        result['pending'] = 'interrupted operation; recovery required'
        if not readonly:
            with c.locked(receipt):
                if c.load_receipt(receipt) != data:
                    raise ValueError('receipt changed; retry')
                c.recover(receipt, data)
            result['pending'] = 'recovered'
    release = set()
    if args.command == 'undo' and not data['targets'] and not any(record in data for record in ('electric', 'ghostty', 'codex')):
        result['notice'] = ' '.join(filter(None, [result.get('notice'), 'Nothing to undo.']))
    if args.command == 'undo':
        paths = [(c.canonical(p), e.get('kind', 'herdr')) for p, e in data['targets'].items()]
        paths.sort(key=lambda item: item[1] != 'claude-settings')
    else:
        paths = [(c.resolve_target(args.herdr_config), 'herdr')]
        if args.command == 'check':
            paths.extend((c.canonical(p), e['kind']) for p, e in data['targets'].items() if e.get('kind', 'herdr') != 'herdr')
        # Once owned, the statusline stays managed until undo, like the other Claude pieces.
        owned = [c.canonical(p) for p, e in data['targets'].items() if e.get('kind') == 'claude-statusline']
        # An identical pre-existing script stays unowned, so the owned settings key also keeps it managed.
        statusline = args.claude_statusline or bool(owned) or any('statusLine' in e['owned'] for e in data['targets'].values() if e.get('kind') == 'claude-settings')
        # Before v1.0.6 the Claude theme was global; re-applying releases it (the theme now rides on the pane-only claude wrapper).
        legacy = [(c.canonical(p), e['kind']) for p, e in data['targets'].items()
                  if e.get('kind') == 'claude-theme' or e.get('kind') == 'claude-settings' and 'theme' in e['owned']]
        root = Path(os.environ.get('CLAUDE_CONFIG_DIR') or str(Path.home() / '.claude')).expanduser()
        if 'claude' in agents:
            paths.append((c.canonical(root / 'themes/herdr-electrified.json'), 'claude-theme'))
        if statusline:
            paths.append((owned[0] if owned else c.canonical(Path(os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local/share')).expanduser() / 'herdr-electrified/claude-statusline.py'), 'claude-statusline'))
            paths.append((c.canonical(root / 'settings.json'), 'claude-settings'))
        release = {path for path, kind in legacy if kind == 'claude-theme'} - {path for path, kind in paths}
        paths.extend(legacy)
        owned = [c.canonical(p) for p, e in data['targets'].items() if e.get('kind') == 'ghostty-config']
        if layer:
            paths.append((owned[0] if owned else electric.ghostty_config(), 'ghostty-config'))
        elif bundle and owned and 'ghostty' not in data:
            # Before v1.3.0 Electric styled every Ghostty window; herdr-electric now opens its own.
            paths.append((owned[0], 'ghostty-config'))
            release.add(owned[0])
        paths.extend((p, 'electric-file') for p in electric_targets)
        if themed:
            paths.append((codex_config, 'codex-config'))
        paths = list(dict.fromkeys(paths))
    script = next((path for path, kind in paths if kind == 'claude-statusline'), None)
    values = {'statusLine': {'type': 'command', 'command': 'python3 ' + shlex.quote(str(script))}}
    requested = {'statusLine'} if args.claude_statusline else set()
    if len({path for path, kind in paths}) != len(paths):
        raise ValueError('selected components resolve to the same target')
    plans = []
    for path, kind in paths:
        entry = data['targets'].get(str(path))
        if entry and entry.get('kind', 'herdr') != kind:
            raise ValueError(f'target already owned by another component: {path}')
        if kind == 'electric-file':
            before, after, updated, conflicts = electric.file_plan(path, entry, electric_targets.get(path), args.command == 'undo')
            result['targets'].append({'path': str(path), 'component': kind, 'diff': c.diff(path, before, after),
                                      'conflicts': conflicts, 'configured': before == after and updated == entry and not conflicts, 'loaded': 'unknown',
                                      'reload': 'skipped', 'validation': 'bundle checksums verified' if bundle else 'receipt verified',
                                      'confirmation_required': False})
            plans.append((path, before, after, updated, None, kind))
            continue
        if kind == 'ghostty-config':
            before, after, updated, conflicts = c.ghostty_plan(path, entry, look, args.command == 'undo', path in release)
            result['targets'].append({'path': str(path), 'component': kind, 'diff': c.diff(path, before, after),
                                      'conflicts': conflicts, 'configured': before == after, 'loaded': 'unknown',
                                      'reload': 'skipped', 'validation': 'include only', 'confirmation_required': False,
                                      'notice': 'Reload Ghostty (cmd+shift+,); restart Ghostty once if fonts were installed.'})
            plans.append((path, before, after, updated, None, kind))
            continue
        if kind == 'codex-config':
            before, after, updated, conflicts = c.undo_plan(path, entry) if args.command == 'undo' else c.apply_plan(path, entry, 'codex.toml')
            if updated:
                updated.update(kind=kind, pin=None)
            c.validate(None, before, after, kind)
            result['targets'].append({'path': str(path), 'component': kind, 'diff': c.diff(path, before, after),
                                      'conflicts': conflicts, 'configured': before == after, 'loaded': 'unknown',
                                      'reload': 'skipped', 'validation': 'TOML valid; Codex selection unverified', 'confirmation_required': False})
            plans.append((path, before, after, updated, None, kind))
            continue
        if kind != 'herdr':
            before, after, updated, conflicts = c.claude_plan(path, entry, kind, args.command == 'undo' or path in release, values, requested)
            c.validate(None, before, after, kind)
            if kind == 'claude-statusline' and args.command != 'undo' and not python3_ok():
                if not readonly:
                    raise ValueError('the statusline runs as python3, but no Python 3.9+ python3 is on PATH; install one, or omit --claude-statusline')
                conflicts = conflicts + ['python3']
            result['targets'].append({'path': str(path), 'component': kind, 'diff': c.diff(path, before, after, kind),
                                      'conflicts': conflicts, 'configured': before == after and 'python3' not in conflicts, 'loaded': 'unknown',
                                      'reload': 'skipped', 'validation': 'statusline script; rendering unverified' if kind == 'claude-statusline' else 'JSON valid; Claude selection unverified',
                                      'confirmation_required': False})
            notices = []
            if kind == 'claude-settings' and 'statusLine' in conflicts and args.command != 'undo' and not (entry and 'statusLine' in entry['owned']):
                notices.append('An existing statusLine is never chained or silently replaced; replacing it needs an interactive review of this diff (not --yes), and undo restores it.')
            if notices:
                result['targets'][-1]['notice'] = ' '.join(notices)
            plans.append((path, before, after, updated, None, kind))
            continue
        if args.command == 'undo':
            before, after, updated, conflicts = c.undo_plan(path, entry)
        else:
            before, after, updated, conflicts = c.apply_plan(path, entry, 'electric.toml' if bundle else 'herdr.toml')
        pin = entry.get('pin') if entry else None
        candidate = args.herdr_bin or (pin['path'] if pin else None)
        hint = os.environ['HERDR_BIN_PATH'] if association(path) else None
        try:
            binary = c.identity(candidate) if candidate else None
        except (OSError, ValueError):
            if not pin or (args.herdr_bin and not readonly):
                raise
            binary = None
        mismatch = bool(pin and binary != pin)
        row = {'path': str(path), 'diff': c.diff(path, before, after), 'conflicts': conflicts,
               'configured': before == after, 'loaded': 'unknown', 'reload': 'skipped',
               'identity': 'pinned' if pin and not mismatch else 'explicit, not pinned' if binary else 'hint unconfirmed' if hint else 'none',
               'validation': 'not run: pin mismatch' if mismatch else 'not run: executable not selected'}
        if mismatch:
            row['old_identity'], row['new_identity'] = pin, binary
        if c.get(c.tomlkit.parse(before or ''), ('ui','sidebar','agents','rows_by_agent')) is not None:
            row['notice'] = 'partial coverage: rows_by_agent overrides are preserved'
        result['targets'].append(row)
        unconfirmed = False
        if not readonly and (before != after or updated != entry or mismatch) and (not binary or mismatch):
            if args.herdr_bin:
                pass
            elif sys.stdin.isatty() and not args.yes and (binary or hint):
                binary = binary or c.identity(hint)
                unconfirmed = True
            else:
                raise ValueError('executable selection required; run herdr-electrified apply --herdr-bin <path> in a terminal')
        if readonly and (not binary or mismatch):
            row['instruction'] = 'run herdr-electrified apply in a terminal to select the executable'
        elif binary and not unconfirmed:
            row['diagnostics'] = c.validate(binary, before, after)
            row['validation'] = 'passed'
        if not readonly and sys.stdin.isatty() and not args.yes and binary:
            version = subprocess.run([binary['path'], '--version'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=True)
            row['version'] = version.stdout.strip()
        row['selected_path'] = candidate or hint
        row['executable'] = binary
        row['confirmation_required'] = unconfirmed
        if updated:
            updated['pin'] = binary
        plans.append((path, before, after, updated, binary, kind))
    for row, (path, before, after, *_) in zip(result['targets'], plans):
        row['change'] = 'unchanged' if before == after else 'new' if before is None else 'removed' if after is None else 'changed'
    if readonly:
        if blocked:
            # Undo would fail now (plugin Undo always runs inside Electric): still preview, then say why.
            result['notice'] = ' '.join(filter(None, [result.get('notice'), 'Herdr Electric is running; quit it (herdr-electric server stop closes its panes), then run herdr-electrified undo.']))
        return result, int(bool(blocked))
    conflicts = any(row['conflicts'] for row in result['targets'])
    font_root = bundle_root if bundle and 'ghostty' in agents else args.fonts if layer else None
    fonts_due = bool(font_root and args.command == 'apply' and electric.missing_fonts(font_root))
    cleanup_due = args.command == 'undo' and ('electric' in data or 'ghostty' in data or 'codex' in data) and not data['targets']
    if not fonts_due and not cleanup_due and (not plans or all(before == after and data['targets'].get(str(path)) == updated for path,before,after,updated,binary,kind in plans)):
        return result, int(conflicts and args.command == 'undo')
    needs_identity = any(row['confirmation_required'] for row in result['targets'])
    if args.command == 'apply' and conflicts and (args.yes or not sys.stdin.isatty()):
        result['error'] = 'conflicting managed keys; review the diff interactively before reapplying'
        return result, 1
    if (args.command == 'apply' and not args.yes) or needs_identity:
        if not sys.stdin.isatty():
            raise ValueError('confirmation requires a terminal; use --yes with explicit executable selection')
        print(render(result) if args.diff else summary(result, sys.stderr), file=sys.stderr)
        answer = ask('Apply these changes and pin this Herdr executable? [y/N] ' if needs_identity else 'Apply these changes? [y/N] ')
        if answer is None:
            result['error'] = 'no answer in 5 min; nothing written'
            return result, 1
        if answer not in ('y', 'yes'):
            result['notice'] = 'declined; nothing written'
            return result, 0
        args.prompted = True  # the summary is already on screen
    for row, (path, before, after, updated, binary, kind) in zip(result['targets'], plans):
        if row['confirmation_required']:
            row['diagnostics'] = c.validate(binary, before, after)
            row['validation'] = 'passed'
    args.writing = True  # from here an interrupt can leave a journaled, recoverable write
    with c.locked(receipt):
        if c.load_receipt(receipt) != data:
            raise ValueError('receipt changed; retry with a fresh diff')
        record = 'electric' if bundle else 'ghostty' if layer else 'codex' if themed else None
        if record:
            created = data.get(record, {}).get('created_dirs', [])
            for path, before, after, updated, binary, kind in plans:
                parent = path.parent
                while after is not None and not parent.exists() and str(parent) not in created:
                    created.append(str(parent))
                    parent = parent.parent
        if layer:
            data['ghostty'] = {'fonts': data.get('ghostty', {}).get('fonts', []), 'created_dirs': created}
            c.save(receipt, data)
        elif themed:
            data['codex'] = {'created_dirs': created}
            c.save(receipt, data)
        if bundle:
            data['electric'] = {'root': str(bundle_root), 'config': str(target), 'codex_home': codex, 'manifest_hash': manifest_hash,
                                'agents': sorted(agents), 'created_dirs': created, 'fonts': data.get('electric', {}).get('fonts', []),
                                'session': data.get('electric', {}).get('session') or str(electric.session_dir())}
            c.save(receipt, data)
        for row, (path, before, after, updated, binary, kind) in zip(result['targets'], plans):
            if before == after and data['targets'].get(str(path)) == updated:
                continue
            try:
                if kind == 'herdr' and c.identity(binary['path']) != binary:
                    raise ValueError('executable changed before write')
                mode = None
                if kind == 'electric-file':
                    mode = (data['targets'][str(path)].get('original_mode', 0o600)) if args.command == 'undo' else (0o700 if after and after.startswith('#!/bin/sh') else 0o600)
                c.transact(receipt, data, path, before, after, updated, binary, kind, mode)
                if kind == 'claude-statusline' and after is None:
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass
            except (OSError, ValueError) as error:
                result['error'] = 'partial or pending operation; check receipt before retry: ' + str(error)
                return result, 1
            row['saved'] = True
            row['configured'] = args.command == 'apply'
            row['confirmation_required'] = False
            if kind == 'herdr':
                row['identity'] = 'pinned' if updated else 'pin retired'
            if kind == 'herdr' and not data.get('electric') and before != after and association(path):
                try:
                    reload = subprocess.run([binary['path'], 'server', 'reload-config'],
                                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
                    row['reload'] = 'requested' if reload.returncode == 0 else 'failed'
                    row['notice'] = 'client reload may be needed; visually verify' if reload.returncode == 0 else 'saved-but-not-loaded; undo remains available'
                except (OSError, subprocess.SubprocessError) as error:
                    row['reload'] = 'failed'
                    row['notice'] = 'saved-but-not-loaded; undo remains available: ' + str(error)
        if fonts_due:
            def save_fonts(fonts):
                data[record]['fonts'] = fonts
                c.save(receipt, data)
            electric.install_fonts(font_root, data[record]['fonts'], save_fonts)
            result['fonts_installed'] = True
        if args.command == 'undo' and not data['targets'] and 'electric' in data:
            # Clean up first: if it fails, the record stays and a retried undo finishes the job.
            electric.cleanup(data['electric'])
            data.pop('electric')
            c.save(receipt, data)
        for layer_record in ('ghostty', 'codex'):
            if args.command == 'undo' and not data['targets'] and layer_record in data:
                electric.prune(data[layer_record])
                data.pop(layer_record)
                c.save(receipt, data)
    return result, int(any(row['conflicts'] for row in result['targets']) and args.command == 'undo')


def parse(argv):
    parser = argparse.ArgumentParser(prog='herdr-electrified')
    parser.add_argument('--version', action='version', version='herdr-electrified ' + __version__)
    parser.add_argument('command', choices=['install', 'apply', 'preview-apply', 'check', 'undo', 'upgrade'])
    parser.add_argument('--check', action='store_true', help='upgrade: only look for a newer release')
    parser.add_argument('--settings-only', action='store_true', help='install: stock Herdr settings, no Electric bundle')
    parser.add_argument('--dry-run', action='store_true', help='install, apply or undo: show what would change, write nothing')
    parser.add_argument('--diff', action='store_true', help='install or apply: show full diffs instead of the summary')
    parser.add_argument('--herdr-config')
    parser.add_argument('--herdr-bin')
    parser.add_argument('--electric', metavar='BUNDLE', help='opt in to a verified macOS arm64 Electric bundle')
    parser.add_argument('--agent', choices=['claude'])
    parser.add_argument('--claude-statusline', action='store_true', help='opt in to the managed Claude statusline')
    parser.add_argument('--codex-theme', action='store_true', help='settings-only: opt in to the Electric syntax theme in stock Codex')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.check and args.command != 'upgrade':
        parser.error('--check is only valid for upgrade')
    if args.command == 'upgrade' and any((args.settings_only, args.dry_run, args.diff, args.herdr_config, args.herdr_bin, args.electric,
                                          args.agent, args.claude_statusline, args.codex_theme)):
        parser.error('upgrade takes only --check, --yes and --json; it keeps the options you installed with')
    if args.command == 'preview-apply':
        args.command, args.dry_run = 'apply', True
    if args.electric and args.command == 'undo':
        parser.error('--electric is only valid for apply, preview-apply or check')
    if args.claude_statusline and args.command == 'undo':
        parser.error('--claude-statusline is only valid for install, apply, preview-apply or check')
    if args.codex_theme and args.command == 'undo':
        parser.error('--codex-theme is only valid for install, apply, preview-apply or check')
    if args.dry_run and args.command not in ('install', 'apply', 'undo'):
        parser.error('--dry-run is only valid for install, apply or undo')
    if args.settings_only and args.command != 'install':
        parser.error('--settings-only is only valid for install')
    if args.command == 'install' and args.electric:
        parser.error('install fetches its own bundle; use apply --electric for a local one')
    args.ghostty, args.fonts, args.writing, args.prompted = False, None, False, False
    return args


def run(args):
    if args.command == 'install':
        args.command = 'apply'
        if args.settings_only:
            args.herdr_bin = args.herdr_bin or shutil.which('herdr')
            if not args.herdr_bin:
                raise ValueError('Herdr not found on PATH; install Herdr first, or pass --herdr-bin')
            args.ghostty = electric.ghostty_installed()
            if args.ghostty:
                args.fonts = electric.fetch_fonts()
        else:
            args.electric = str(electric.fetch_bundle())
    result, code = execute(args)
    if args.command == 'apply' and not args.dry_run and not code and 'error' not in result:
        clear_notice()
        if result.get('bundle') and result.get('notice') != 'declined; nothing written':
            result['removed'] = electric.prune_bundles(c.load_receipt(c.receipt_path())['electric']['root'])
    return result, code


def main(argv=None):
    args = parse(argv)
    try:
        result, code = upgrade(args) if args.command == 'upgrade' else run(args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result, code = {'error': str(error), 'loaded': 'unknown'}, 1
    except KeyboardInterrupt:
        print('\nInterrupted; run herdr-electrified check to see the state' if args.writing else '\nCancelled; nothing written', file=sys.stderr)
        return 130
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.command == 'upgrade':
        if text := upgrade_text(result):
            print(text)
    elif args.command == 'apply' and not args.diff:
        print(outcome(result, args))
        if result.get('removed'):
            print('Removed old Electric bundles: ' + ', '.join(result['removed']))
    else:
        print(render(result))
    return code


def version(tag):
    return tuple(int(part) for part in tag.removeprefix('v').split('.'))


def latest():
    try:
        tag = electric.latest_release()
        version(tag)
        return tag.removeprefix('v')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f'could not look up the latest release: {error!r}') from None


def clear_notice():
    """Drop an update notice this version already satisfies."""
    notice = electric.notice_path()
    words = notice.read_text().split() if notice.is_file() else []
    try:
        if words[:1] == ['herdr-electrified'] and version(words[1]) <= version(__version__):
            notice.write_text('')
    except (IndexError, ValueError):
        notice.write_text('')  # unreadable: the next daily check rewrites it


def upgrade(args):
    """Bring the CLI, every install it recorded, and the Herdr plugin to the latest release."""
    data = c.load_receipt(c.receipt_path())
    root = data.get('electric', {}).get('root')
    if args.check:
        notice = electric.notice_path()
        notice.parent.mkdir(parents=True, exist_ok=True)
        try:
            newest = latest()
        except ValueError:
            notice.touch()  # keep the last answer; the launcher retries tomorrow
            raise
        text = (f'herdr-electrified {newest} is available (you have {__version__}). Upgrade: herdr-electrified upgrade\n'
                if version(newest) > version(__version__) else '')
        notice.write_text(text)
        return {'current': __version__, 'latest': newest, 'notice': text.strip() or f'herdr-electrified {__version__} is up to date.',
                'removed': electric.prune_bundles(root) if root else []}, 0
    newest = latest()
    if version(newest) > version(__version__):
        reinstall(newest, args)
    say = (lambda line: None) if args.json else print
    say(f'herdr-electrified {__version__} is up to date.')
    result = {'current': __version__, 'latest': newest, 'installs': [], 'plugin': None, 'removed': [], 'restart': None}
    # Re-running install keeps every option the receipt recorded (statusline, Codex theme, Ghostty look).
    jobs = [['install']] if root else []
    for path, entry in data['targets'].items():
        if entry.get('kind', 'herdr') == 'herdr' and path != data.get('electric', {}).get('config'):
            jobs.append(['install', '--settings-only', '--herdr-config', path] + (['--herdr-bin', entry['pin']['path']] if entry.get('pin') else []))
    if not jobs:
        result['notice'] = 'Nothing is installed yet; run herdr-electrified install.'
        return result, 0
    for job in jobs:
        sub = parse(job + ['--yes'] * args.yes)
        try:
            installed, code = run(sub)
        finally:
            args.writing = args.writing or sub.writing  # main's interrupt message must know a write began
        result['installs'].append(installed)
        result['removed'] += installed.get('removed', [])
        say(outcome(installed, sub))
        if installed.get('notice') == 'declined; nothing written':
            result['notice'] = 'Upgrade declined; the plugin, bundles and Electric were left as they were.'
            return result, 0
        if code:
            result['error'] = installed.get('error', 'install failed; nothing else was changed')
            return result, code
    launcher = c.canonical(Path.home() / '.local/bin/herdr-electric')
    herdr = [str(launcher)] if root else [jobs[0][-1] if '--herdr-bin' in jobs[0] else 'herdr']
    try:
        result['plugin'] = sync_plugin(herdr)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        result['plugin_error'] = (f'Could not update the Herdr plugin ({error}); run: '
                                  + shlex.join([*herdr, 'plugin', 'install', f'{electric.REPO}/plugin', '--ref', f'v{__version__}', '--yes']))
    if root and electric.running(electric.recorded_session(data['electric'])):
        # Bundles still on disk after pruning are in use: the running server predates this install.
        current = Path(c.load_receipt(c.receipt_path())['electric']['root'])
        stale = [p for p in current.parent.glob('herdr-electrified-*-macos-arm64') if p.resolve() != current.resolve()]
        if stale or any(row.get('saved') for row in result['installs'][0]['targets']):
            result['restart'] = 'Restart Herdr Electric to finish: herdr-electric server stop, then herdr-electric (it reopens your agent panes).'
            # Stopping the server from inside one of its panes would kill this command before the relaunch.
            if sys.stdin.isatty() and not args.yes and os.environ.get('HERDR_ENV') != '1':
                if ask('Restart Herdr Electric now? Its panes close and reopen. [y/N] ') in ('y', 'yes'):
                    subprocess.run([str(launcher), 'server', 'stop'], stdin=subprocess.DEVNULL, timeout=60, check=True)
                    sys.stdout.flush()
                    os.execv(str(launcher), [str(launcher)])
    return result, 0


def reinstall(newest, args):
    """Replace this CLI with the release, then hand off to it: the new version runs its own upgrade steps."""
    command = ['uv', 'tool', 'install', '--python', '3.12', f'git+https://github.com/{electric.REPO}@v{newest}']
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('uv not found on PATH; run: ' + shlex.join(command))
    tools = subprocess.run([uv, 'tool', 'dir'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30).stdout.strip()
    if not tools or Path(sys.prefix).resolve().parent != Path(tools).resolve():
        raise ValueError(f'this herdr-electrified ({sys.prefix}) was not installed with uv tool; upgrade it the way you installed it')
    if not args.json:
        print(f'Upgrading herdr-electrified {__version__} -> {newest}: https://github.com/{electric.REPO}/releases/tag/v{newest}', flush=True)
    subprocess.run([uv, *command[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL if args.json else None, check=True)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, '-m', 'herdr_electrified.cli', 'upgrade'] + ['--yes'] * args.yes + ['--json'] * args.json)


def sync_plugin(herdr):
    """Move a GitHub-installed herdr-electrified plugin to this release's tag; a local link is left alone."""
    listed = subprocess.run([*herdr, 'plugin', 'list', '--plugin', 'herdr-electrified', '--json'],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    answer = json.loads(listed.stdout)
    if 'error' in answer:
        raise ValueError(answer['error'].get('message', answer['error']))
    plugins = answer['result']['plugins']
    source = plugins[0]['source'] if plugins else {}
    tag = f'v{__version__}'
    if source.get('kind') != 'github' or f"{source.get('owner')}/{source.get('repo')}" != electric.REPO or source.get('requested_ref') == tag:
        return None
    installed = subprocess.run([*herdr, 'plugin', 'install', f'{electric.REPO}/plugin', '--ref', tag, '--yes'],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    if installed.returncode:
        raise ValueError((installed.stderr or installed.stdout).strip() or f'exit status {installed.returncode}')
    return tag


def upgrade_text(result):
    lines = [result['error']] if 'error' in result else []
    lines += [result[key] for key in ('notice', 'plugin_error') if result.get(key)]
    if result.get('plugin'):
        lines.append(f"Herdr plugin updated to {result['plugin']}.")
    if result.get('removed'):
        lines.append('Removed old Electric bundles: ' + ', '.join(result['removed']))
    if result.get('restart'):
        lines.append(result['restart'])
    return '\n'.join(lines)


COMPONENTS = {'herdr': 'Herdr config', 'electric-file': 'Electric files', 'ghostty-config': 'Ghostty windows', 'codex-config': 'Codex',
              'claude-theme': 'Claude Code', 'claude-settings': 'Claude Code', 'claude-statusline': 'Claude Code'}
THEMES = {'claude': 'Claude Code', 'codex': 'Codex', 'ghostty': 'Ghostty', 'opencode': 'OpenCode'}


def component(row):
    # Electric owns the appearance file the Ghostty include points at; it only affects Ghostty windows.
    return ('Ghostty windows' if row['path'].endswith('/ghostty.conf') else 'Codex' if row['path'].endswith('.tmTheme')
            else COMPONENTS[row.get('component', 'herdr')])


def summary(result, stream=sys.stdout, written=False):
    """What apply will change (or changed), grouped by component; the full diff is behind --diff."""
    color = (stream.isatty() or bool(os.environ.get('CLICOLOR_FORCE'))) and not os.environ.get('NO_COLOR')
    def paint(code, text):
        return f'\033[{code}m{text}\033[0m' if color else text
    home = str(Path.home().resolve()) + os.sep
    lines = [result['error']] if 'error' in result else []
    lines.extend(result[key] for key in ('pending', 'notice') if result.get(key))
    lines.append(paint('1', 'herdr-electrified ' + __version__) + (f" · Electric bundle {result['bundle']} verified" if result.get('bundle') else ''))
    if result.get('agents'):
        lines.append('Themes: ' + ', '.join(THEMES[a] for a in result['agents']))
    rows = [r for r in result.get('targets', []) if (r.get('saved') if written else r.get('change', 'unchanged') != 'unchanged')]
    if rows or 'error' not in result:
        lines.append(('Wrote:' if written else 'Will write:') if rows else 'Nothing to change; already applied.')
    for label in dict.fromkeys(COMPONENTS.values()):
        group = [r for r in rows if component(r) == label]
        if group:
            lines.append('  ' + label)
        for r in group:
            path = '~/' + r['path'][len(home):] if r['path'].startswith(home) else r['path']
            # Bold, not yellow: yellow is unreadable on a light terminal background.
            lines.append('    ' + paint({'new': '32', 'removed': '31'}.get(r.get('change'), '1'), r.get('change', 'changed').ljust(10)) + path)
    for r in result.get('targets', []):
        if r.get('executable') and (r.get('confirmation_required') or 'old_identity' in r) and not written:
            lines.append(f"Herdr executable to pin: {r['executable']['path']}" + (f" ({r['version']})" if r.get('version') else ''))
        if r.get('conflicts'):
            lines.extend([paint('31', f"Conflicts in {r['path']}: " + ', '.join(r['conflicts'])), r['diff']])
        if r.get('instruction'):
            lines.append(r['instruction'])
    # outcome() gives the Ghostty reload as a terminal-aware next step; the settings.json statusLine note is a preview caveat.
    lines.extend(dict.fromkeys(r['notice'] for r in rows if r.get('notice') and r.get('component') != 'ghostty-config'
                               and not (written and r.get('component') == 'claude-settings')))
    if rows and not written:
        lines.append("Untouched: stock herdr and codex binaries, other terminals' settings, your shell profiles. Undo: herdr-electrified undo")
    return '\n'.join(lines)


def outcome(result, args):
    if args.dry_run or 'error' in result:
        return summary(result)
    if result.get('notice') == 'declined; nothing written':
        return 'Declined; nothing written.'
    saved = sum(1 for r in result.get('targets', []) if r.get('saved'))
    fonts = result.get('fonts_installed')
    if not saved and not fonts:
        return summary(result)
    lines = [] if args.prompted else [summary(result, written=True)]
    lines.append(f"Wrote {saved} file{'s' * (saved != 1)}" + (' and installed the font' if fonts else '') + '. Undo: herdr-electrified undo')
    ghostty = fonts or any(r.get('saved') and component(r) == 'Ghostty windows' for r in result['targets'])
    restart = 'restart Ghostty once so it loads the new font' if fonts else 'reload Ghostty (cmd+shift+,)'
    run = 'run herdr-electric from a new {} window (not inside a Herdr pane)'
    if result.get('bundle'):
        # herdr-electric opens its own Ghostty window, which loads the look and any new font itself;
        # only a released pre-v1.3.0 include needs the open windows reloaded.
        window = 'ghostty' in result.get('agents', [])
        ghostty = any(r.get('saved') and r.get('component') == 'ghostty-config' for r in result['targets'])
        restart, run = 'reload Ghostty (cmd+shift+,) so other windows drop the Electric look', 'run herdr-electric (it opens its own Ghostty window)' if window else run
    if os.environ.get('TERM_PROGRAM') == 'ghostty':
        steps = ([restart] if ghostty else []) + ([run.format('Ghostty')] if result.get('bundle') else [])
        if steps:
            lines.append('Next: ' + ', then '.join(steps) + '.')
    else:
        if result.get('bundle'):
            lines.append('Next: ' + run.format('terminal') + '.')
        if ghostty:
            lines.append('Ghostty: ' + ('reload it (cmd+shift+,) so other windows drop the Electric look.' if result.get('bundle') else
                                        'restart it once so it loads the new font.' if fonts else 'open Ghostty windows pick up the new look after cmd+shift+,.'))
        if not result.get('bundle'):
            # Herdr Electric paints its own panes; stock Herdr shows this terminal's background.
            lines.append("This terminal isn't styled by Electric; give it a dark background.")
    return '\n'.join(lines)


def render(result):
    lines = [result['error']] if 'error' in result else []
    if result.get('pending'):
        lines.append(result['pending'])
    if result.get('notice'):
        lines.append(result['notice'])
    lines.extend(f"font {font['status']}: {font['path']}" for font in result.get('fonts', []))
    for row in result.get('targets', []):
        lines.extend([row['path'], row['diff'], 'validation: ' + row['validation'], 'loaded: ' + row['loaded']])
        lines.extend(row.get('diagnostics', []))
        lines.append('reload: ' + row['reload'])
        if row.get('notice'):
            lines.append(row['notice'])
        if row.get('conflicts'):
            lines.append('conflicts: ' + ', '.join(row['conflicts']))
        if row.get('executable'):
            lines.append('executable: ' + json.dumps(row['executable']))
        if row.get('instruction'):
            lines.append(row['instruction'])
    return '\n'.join(lines)


if __name__ == '__main__':
    raise SystemExit(main())

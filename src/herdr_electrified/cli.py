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
    if args.command == 'undo' and data.get('electric') and electric.running(electric.recorded_session(data['electric'])):
        raise ValueError('Herdr Electric is running; quit it, then rerun herdr-electrified undo')
    bundle = args.electric or (data.get('electric', {}).get('root') if args.command != 'undo' else None)
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
        codex_home = data.get('electric', {}).get('codex_home') or str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve())
        prior = data.get('electric', {})
        # Early pre-release receipts predate detection and always installed the Codex pieces.
        agents = set(prior.get('agents', ['codex'] if prior else []))
        if args.command != 'check' or not prior:
            agents |= electric.detect(codex_home)
        electric_targets = electric.targets(bundle_root, target, codex_home, agents)
    # Settings-only Ghostty look: same appearance file as Electric, owned outside any bundle.
    layer = not bundle and args.command == 'apply' and (args.ghostty or 'ghostty' in data)
    xdg = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    look = c.canonical((target.parent if bundle else xdg / 'herdr-electrified') / 'ghostty.conf') if bundle or layer else None
    if layer:
        electric_targets[look] = c.files('herdr_electrified').joinpath('data/ghostty.conf').read_text()
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
        if 'ghostty' in agents or layer:
            owned = [c.canonical(p) for p, e in data['targets'].items() if e.get('kind') == 'ghostty-config']
            paths.append((owned[0] if owned else electric.ghostty_config(), 'ghostty-config'))
        paths.extend((p, 'electric-file') for p in electric_targets)
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
            before, after, updated, conflicts = c.ghostty_plan(path, entry, look, args.command == 'undo')
            result['targets'].append({'path': str(path), 'component': kind, 'diff': c.diff(path, before, after),
                                      'conflicts': conflicts, 'configured': before == after, 'loaded': 'unknown',
                                      'reload': 'skipped', 'validation': 'include only', 'confirmation_required': False,
                                      'notice': 'Reload Ghostty (cmd+shift+,); restart Ghostty once if fonts were installed.'})
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
            if kind == 'claude-settings' and before != after:
                notices.append('Managed-keys-only preview: writing settings.json can change whole-file formatting, Unicode escapes and key order; unrelated values are preserved.')
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
        return result, 0
    conflicts = any(row['conflicts'] for row in result['targets'])
    font_root = bundle_root if bundle and 'ghostty' in agents else args.fonts if layer else None
    fonts_due = bool(font_root and args.command == 'apply' and electric.missing_fonts(font_root))
    cleanup_due = args.command == 'undo' and ('electric' in data or 'ghostty' in data) and not data['targets']
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
        record = 'electric' if bundle else 'ghostty' if layer else None
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
        if bundle:
            data['electric'] = {'root': str(bundle_root), 'config': str(target), 'codex_home': codex_home, 'manifest_hash': manifest_hash,
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
                row['identity'] = 'pinned' if updated else 'explicit, not pinned'
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
        if args.command == 'undo' and not data['targets'] and 'ghostty' in data:
            electric.prune(data['ghostty'])
            data.pop('ghostty')
            c.save(receipt, data)
    return result, int(any(row['conflicts'] for row in result['targets']) and args.command == 'undo')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='herdr-electrified')
    parser.add_argument('--version', action='version', version='herdr-electrified ' + __version__)
    parser.add_argument('command', choices=['install', 'apply', 'preview-apply', 'check', 'undo'])
    parser.add_argument('--settings-only', action='store_true', help='install: stock Herdr settings, no Electric bundle')
    parser.add_argument('--dry-run', action='store_true', help='install or apply: show what would change, write nothing')
    parser.add_argument('--diff', action='store_true', help='install or apply: show full diffs instead of the summary')
    parser.add_argument('--herdr-config')
    parser.add_argument('--herdr-bin')
    parser.add_argument('--electric', metavar='BUNDLE', help='opt in to a verified macOS arm64 Electric bundle')
    parser.add_argument('--agent', choices=['claude'])
    parser.add_argument('--claude-statusline', action='store_true', help='opt in to the managed Claude statusline')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'preview-apply':
        args.command, args.dry_run = 'apply', True
    if args.electric and args.command == 'undo':
        parser.error('--electric is only valid for apply, preview-apply or check')
    if args.claude_statusline and args.command == 'undo':
        parser.error('--claude-statusline is only valid for install, apply, preview-apply or check')
    if args.dry_run and args.command not in ('install', 'apply'):
        parser.error('--dry-run is only valid for install or apply')
    if args.settings_only and args.command != 'install':
        parser.error('--settings-only is only valid for install')
    if args.command == 'install' and args.electric:
        parser.error('install fetches its own bundle; use apply --electric for a local one')
    args.ghostty, args.fonts, args.writing, args.prompted = False, None, False, False
    try:
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
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result, code = {'error': str(error), 'loaded': 'unknown'}, 1
    except KeyboardInterrupt:
        print('\nInterrupted; run herdr-electrified check to see the state' if args.writing else '\nCancelled; nothing written', file=sys.stderr)
        return 130
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.command == 'apply' and not args.diff:
        print(outcome(result, args))
    else:
        print(render(result))
    return code


COMPONENTS = {'herdr': 'Herdr config', 'electric-file': 'Electric files', 'ghostty-config': 'Ghostty windows',
              'claude-theme': 'Claude Code', 'claude-settings': 'Claude Code', 'claude-statusline': 'Claude Code'}
THEMES = {'claude': 'Claude Code', 'codex': 'Codex', 'ghostty': 'Ghostty', 'opencode': 'OpenCode'}


def component(row):
    # Electric owns the appearance file the Ghostty include points at; it only affects Ghostty windows.
    return 'Ghostty windows' if row['path'].endswith('/ghostty.conf') else COMPONENTS[row.get('component', 'herdr')]


def summary(result, stream=sys.stdout, written=False):
    """What apply will change (or changed), grouped by component; the full diff is behind --diff."""
    color = stream.isatty() and not os.environ.get('NO_COLOR')
    def paint(code, text):
        return f'\033[{code}m{text}\033[0m' if color else text
    home = str(Path.home().resolve()) + os.sep
    lines = [result['error']] if 'error' in result else []
    lines.extend(result[key] for key in ('pending', 'notice') if result.get(key))
    lines.append(paint('1', 'herdr-electrified ' + __version__) + (f" · Electric bundle {result['bundle']} verified" if result.get('bundle') else ''))
    if result.get('agents'):
        lines.append('Themes: ' + ', '.join(THEMES[a] for a in result['agents']))
    rows = [r for r in result.get('targets', []) if (r.get('saved') if written else r.get('change', 'unchanged') != 'unchanged')]
    lines.append(('Wrote:' if written else 'Will write:') if rows else 'Nothing to change; already applied.')
    for label in dict.fromkeys(COMPONENTS.values()):
        group = [r for r in rows if component(r) == label]
        if group:
            lines.append('  ' + label)
        for r in group:
            path = '~/' + r['path'][len(home):] if r['path'].startswith(home) else r['path']
            # Bold, not yellow: yellow is unreadable on a light terminal background.
            lines.append('    ' + paint({'new': '32', 'removed': '31'}.get(r.get('change'), '1'), r.get('change', 'changed').ljust(8)) + path)
    for r in result.get('targets', []):
        if r.get('executable') and (r.get('confirmation_required') or 'old_identity' in r) and not written:
            lines.append(f"Herdr executable to pin: {r['executable']['path']}" + (f" ({r['version']})" if r.get('version') else ''))
        if r.get('conflicts'):
            lines.extend([paint('31', f"Conflicts in {r['path']}: " + ', '.join(r['conflicts'])), r['diff']])
        if r.get('instruction'):
            lines.append(r['instruction'])
    # outcome() gives the Ghostty reload as a terminal-aware next step; the settings.json formatting note is a preview caveat.
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
    if os.environ.get('TERM_PROGRAM') == 'ghostty':
        steps = ([restart] if ghostty else []) + ([run.format('Ghostty')] if result.get('bundle') else [])
        if steps:
            lines.append('Next: ' + ', then '.join(steps) + '.')
    else:
        if result.get('bundle'):
            lines.append('Next: ' + run.format('terminal') + '.')
        if ghostty:
            lines.append('Ghostty: ' + ('restart it once so it loads the new font.' if fonts else 'open Ghostty windows pick up the new look after cmd+shift+,.'))
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

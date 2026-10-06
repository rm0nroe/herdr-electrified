"""Key-scoped TOML edits and private, per-file recovery receipts."""
import copy
import difflib
import fcntl
import hashlib
import json
import stat
import tempfile
from contextlib import contextmanager
import os
from importlib.resources import files
from pathlib import Path
import subprocess

import tomlkit


def canonical(path):
    path = Path(path).expanduser().resolve()
    if path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid()):
        raise ValueError(f'not an owned regular file: {path}')
    return path


def read(path):
    canonical(path)
    return path.read_bytes().decode('utf-8') if path.exists() else None


def leaves(doc, prefix=()):
    for key, value in doc.items():
        if isinstance(value, dict):
            yield from leaves(value, (*prefix, key))
        else:
            yield (*prefix, key), value


def get(doc, keys):
    for key in keys:
        if not isinstance(doc, dict) or key not in doc:
            return None
        doc = doc[key]
    return doc


def put(doc, keys, value):
    for key in keys[:-1]:
        if key not in doc:
            doc[key] = tomlkit.table()
        if not isinstance(doc[key], dict):
            raise ValueError(f'not a table: {key}')
        doc = doc[key]
    if value is None:
        doc.pop(keys[-1], None)
    else:
        doc[keys[-1]] = value


def diff(path, before, after, kind='herdr'):
    if kind == 'claude-settings':
        before, after = [json.dumps({k: v for k, v in json_object(text).items() if k in SETTINGS}, indent=2) + '\n' for text in (before, after)]
    return ''.join(difflib.unified_diff((before or '').splitlines(True), (after or '').splitlines(True), fromfile=str(path), tofile=str(path)))


def resolve_target(explicit):
    selected = explicit or os.environ.get('HERDR_CONFIG_PATH')
    if selected:
        return canonical(selected)
    try:
        result = subprocess.run(['herdr', '--help'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=True)
    except FileNotFoundError:
        raise ValueError('Herdr not found on PATH; install Herdr first, or pass --herdr-config') from None
    for line in (result.stdout + result.stderr).splitlines():
        if line.strip().startswith('Config:'):
            return canonical(line.split('Config:', 1)[1].strip())
    raise ValueError('cannot resolve installed Herdr default config; use --herdr-config')

# Writes are journaled per file; no cross-file transaction is claimed.

SETTINGS = ('theme', 'statusLine')


def statusline_command(command):
    return isinstance(command, dict) and set(command) == {'type', 'command'} and command['type'] == 'command' and isinstance(command['command'], str) and command['command'].startswith('python3 ') and command['command'].rstrip("'").endswith('/herdr-electrified/claude-statusline.py')


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def identity(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f'not executable: {path}')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def receipt_path():
    root = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))).expanduser().resolve()
    path = root / 'herdr-electrified/receipt.json'
    secure_state(path)
    return path


def secure_state(path):
    for item in (path.parent, path, path.with_name('lock')):
        if item.is_symlink():
            raise ValueError(f'state symlink rejected: {item}')
        if item.exists() and (item.stat().st_uid != os.getuid() or item.stat().st_mode & 0o022):
            raise ValueError(f'state must be owned and not writable by others: {item}')


def load_receipt(path):
    secure_state(path)
    text = read(path)
    data = json.loads(text) if text is not None else {'version': 1, 'targets': {}}
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('targets'), dict):
        raise ValueError('unsupported receipt; leave it intact')
    validate_receipt(data)
    return data


def validate_receipt(data):
    allowed = {'.'.join(keys) for preset in ('herdr.toml', 'electric.toml') for keys, _ in leaves(tomlkit.parse(files('herdr_electrified').joinpath('data/' + preset).read_text()))}
    def pin(value):
        return isinstance(value, dict) and isinstance(value['path'], str) and Path(value['path']).is_absolute() and len(value['sha256']) == 64 and all(c in '0123456789abcdef' for c in value['sha256'])
    def entry(value):
        if not isinstance(value, dict):
            return False
        kind = value.get('kind', 'herdr')
        keys = allowed if kind == 'herdr' else set(SETTINGS) if kind == 'claude-settings' else {'$file'} if kind in ('claude-theme', 'claude-statusline', 'electric-file') else {'$include'} if kind == 'ghostty-config' else set()
        valid = ((pin(value['pin']) if kind == 'herdr' else value['pin'] is None)
                and isinstance(value['installed_hash'], str) and len(value['installed_hash']) == 64
                and (value['original_file'] is None or isinstance(value['original_file'], str))
                and isinstance(value['owned'], dict) and bool(value['owned'])
                and set(value['owned']) <= keys
                and all(isinstance(v['original'], str) and 'installed' in v for v in value['owned'].values()))
        if valid and kind == 'claude-settings':
            original = json_object(value['original_file'])
            for key, owned in value['owned'].items():
                # A key added to an untouched file shares its original; after user edits only its shape is checked.
                fragment = json_object(owned['original'])
                valid = valid and (owned['installed'] == 'custom:herdr-electrified' if key == 'theme' else statusline_command(owned['installed']))
                valid = valid and set(fragment) <= {key} and (not value.get('exact_restore', True) or fragment == {k: v for k, v in original.items() if k == key})
        if valid and kind == 'ghostty-config':
            valid = isinstance(value['owned']['$include']['installed'], str)
        if valid and kind in ('claude-theme', 'claude-statusline', 'electric-file'):
            owned = value['owned']['$file']
            valid = isinstance(owned['installed'], str) and json.loads(owned['original']) == value['original_file']
            if kind == 'electric-file':
                valid = valid and (value.get('original_mode') is None or type(value['original_mode']) is int and 0 <= value['original_mode'] <= 0o777)
                valid = valid and type(value.get('installed_mode')) is int and value['installed_mode'] in (0o600, 0o700)
            if kind == 'claude-theme':
                json_object(value['original_file'])
                json_object(owned['installed'])
        return valid
    try:
        valid = all(isinstance(path, str) and Path(path).is_absolute() and str(canonical(path)) == path and entry(value) for path, value in data['targets'].items())
        if 'electric' in data:
            bundle = data['electric']
            valid = valid and isinstance(bundle, dict) and all(isinstance(bundle[k], str) and Path(bundle[k]).is_absolute() for k in ('root', 'config', 'codex_home'))
            valid = valid and isinstance(bundle['manifest_hash'], str) and len(bundle['manifest_hash']) == 64
            valid = valid and set(bundle.get('agents', [])) <= {'codex', 'claude', 'ghostty', 'opencode'}
            valid = valid and all(isinstance(f['path'], str) and Path(f['path']).is_absolute() and len(f['sha256']) == 64 for f in bundle.get('fonts', []))
            valid = valid and (bundle.get('session') is None or isinstance(bundle['session'], str) and Path(bundle['session']).is_absolute())
            valid = valid and all(isinstance(d, str) and Path(d).is_absolute() for d in bundle.get('created_dirs', []))
        if 'ghostty' in data:
            layer = data['ghostty']
            valid = valid and isinstance(layer, dict) and all(isinstance(f['path'], str) and Path(f['path']).is_absolute() and len(f['sha256']) == 64 for f in layer.get('fonts', []))
            valid = valid and all(isinstance(d, str) and Path(d).is_absolute() for d in layer.get('created_dirs', []))
        if 'pending' in data:
            p = data['pending']
            kind = p.get('kind', 'herdr')
            valid = valid and kind in ('herdr', 'claude-settings', 'claude-theme', 'claude-statusline', 'electric-file', 'ghostty-config') and (pin(p['pin']) if kind == 'herdr' else p['pin'] is None)
            valid = valid and Path(p['path']).is_absolute() and str(canonical(p['path'])) == p['path'] and isinstance(p['mode'], int) and 0 <= p['mode'] <= 0o777
            valid = valid and (p['entry'] is None or entry(p['entry']))
            valid = valid and (p['entry'] is None or p['entry'].get('kind', 'herdr') == kind)
            valid = valid and all(p[k] is None or isinstance(p[k], str) for k in ('before', 'after'))
            valid = valid and digest(p['before']) == p['before_hash'] and digest(p['after']) == p['after_hash']
    except (KeyError, TypeError, AttributeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('invalid receipt; preserve it for inspection')


def atomic(path, text, mode=0o600, expected=None):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.herdr-electrified-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if expected is not None and (canonical(path) != path or read(path) != expected['text']):
            raise ValueError(f'file changed before replacement: {path}')
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(path):
    secure_state(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_name('lock')
    canonical(lock)
    with lock.open('a+') as stream:
        os.chmod(lock, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('busy; retry after the current operation') from None
        try:
            stream.seek(0)
            stream.truncate()
            stream.write(str(os.getpid()))
            stream.flush()
            yield
        finally:
            stream.seek(0)
            stream.truncate()
            fcntl.flock(stream, fcntl.LOCK_UN)


def busy(path):
    lock = path.with_name('lock')
    pid = read(lock)
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def save(path, data):
    secure_state(path)
    atomic(path, json.dumps(data, indent=2) + '\n')


def install(path, before, after, mode):
    if canonical(path) != path or read(path) != before:
        raise ValueError(f'file changed before replacement: {path}')
    if after is None:
        path.unlink(missing_ok=True)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    else:
        atomic(path, after, mode, expected={'text': before})


def transact(receipt, data, path, before, after, entry, binary, kind='herdr', mode=None):
    key = str(path)
    mode = mode if mode is not None else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    data['pending'] = {'path': key, 'before': before, 'after': after,
                       'before_hash': digest(before), 'after_hash': digest(after),
                       'mode': mode, 'entry': entry, 'pin': binary, 'kind': kind}
    save(receipt, data)
    install(path, before, after, mode)
    if entry is None:
        data['targets'].pop(key, None)
    else:
        data['targets'][key] = entry
    del data['pending']
    save(receipt, data)


def apply_plan(path, entry, preset_name='herdr.toml'):
    before = read(path)
    doc = tomlkit.parse(before or '')
    preset = tomlkit.parse(files('herdr_electrified').joinpath('data/' + preset_name).read_text())
    owned = copy.deepcopy(entry['owned']) if entry else {}
    conflicts = []
    for keys, value in leaves(preset):
        key = '.'.join(keys)
        current = get(doc, keys)
        if key in owned and current != owned[key]['installed']:
            conflicts.append(key)
        if current == value:
            continue
        if key not in owned:
            fragment = tomlkit.document()
            if current is not None:
                fragment['value'] = copy.deepcopy(current)
            owned[key] = {'original': tomlkit.dumps(fragment), 'installed': value.unwrap() if hasattr(value, 'unwrap') else value}
        put(doc, keys, value)
    after = tomlkit.dumps(doc)
    result = {'original_file': entry['original_file'] if entry else before,
              'installed_hash': digest(after), 'owned': owned,
              'exact_restore': (entry.get('exact_restore', True) and digest(before) == entry['installed_hash']) if entry else True}
    if before == after and entry:
        result = copy.deepcopy(entry)
    if not owned:
        result = None
    return before, after, result, conflicts


def undo_plan(path, entry):
    before = read(path)
    if entry.get('exact_restore', True) and digest(before) == entry['installed_hash']:
        return before, entry['original_file'], None, []
    if before is None:
        return before, before, copy.deepcopy(entry), list(entry['owned'])
    doc = tomlkit.parse(before or '')
    remaining = copy.deepcopy(entry)
    conflicts = []
    for key, values in entry['owned'].items():
        keys = key.split('.')
        if get(doc, keys) != values['installed']:
            conflicts.append(key)
            continue
        fragment = tomlkit.parse(values['original'])
        put(doc, keys, fragment.get('value'))
        del remaining['owned'][key]
    # Tables apply created and undo emptied go too; one holding a user comment (body or header) stays.
    original = tomlkit.parse(entry['original_file'] or '')
    restored = [key.split('.') for key in entry['owned'] if key not in conflicts]
    for prefix in sorted({tuple(keys[:i]) for keys in restored for i in range(1, len(keys))}, key=len, reverse=True):
        table = get(doc, prefix)
        if (isinstance(table, tomlkit.items.Table) and get(original, prefix) is None and not table.trivia.comment
                and all(isinstance(item, (tomlkit.items.Whitespace, tomlkit.items.Null)) for _, item in table.value.body)):
            put(doc, prefix, None)
    after = tomlkit.dumps(doc)
    # A file apply created is ours to remove once only Herdr's own onboarding flag is left;
    # any user comment or table, even an empty one, keeps it.
    rest = tomlkit.parse(after)
    if 'onboarding' in rest and not isinstance(rest['onboarding'], dict) and not rest.item('onboarding').trivia.comment:
        rest.remove('onboarding')
    if entry['original_file'] is None and not remaining['owned'] and not tomlkit.dumps(rest).strip():
        after = None
    return before, after, remaining if remaining['owned'] else None, conflicts


def json_object(text):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('duplicate JSON keys; preserve the file for inspection')
        return result
    doc = json.loads(text, object_pairs_hook=unique) if text is not None else {}
    if not isinstance(doc, dict):
        raise ValueError('Claude config must be a JSON object')
    json.dumps(doc, allow_nan=False)
    return doc


def claude_plan(path, entry, kind, undo=False, values=None, requested=()):
    """Claude files: the theme or statusline script owned whole, or chosen keys in settings.json."""
    before = read(path)
    if kind == 'claude-settings':
        return settings_plan(before, entry, undo, values, requested)
    if kind == 'claude-theme':
        json_object(before)
    if undo:
        if entry.get('exact_restore', True) and digest(before) == entry['installed_hash']:
            return before, entry['original_file'], None, []
        owned = entry['owned']['$file']
        if before != owned['installed']:
            return before, before, copy.deepcopy(entry), ['$file']
        return before, json.loads(owned['original']), None, []
    preset = files('herdr_electrified').joinpath('data/claude.json' if kind == 'claude-theme' else 'data/claude-statusline.py').read_text()
    if not entry and before is not None and (json_object(before) == json_object(preset) if kind == 'claude-theme' else before == preset):
        return before, before, None, []
    conflicts = ['$file'] if entry and before != entry['owned']['$file']['installed'] else []
    if before == preset:
        return before, before, copy.deepcopy(entry), conflicts
    updated = {'kind': kind, 'pin': None, 'original_file': entry['original_file'] if entry else before,
               'installed_hash': digest(preset),
               'exact_restore': (entry.get('exact_restore', True) and digest(before) == entry['installed_hash']) if entry else True,
               'owned': {'$file': {'original': entry['owned']['$file']['original'] if entry else json.dumps(before), 'installed': preset}}}
    return before, preset, updated, conflicts


def settings_plan(before, entry, undo, values, requested):
    """Owned settings keys stay managed until undo; a foreign statusLine is a conflict, never replaced silently."""
    doc = json_object(before)
    owned = copy.deepcopy(entry['owned']) if entry else {}
    if undo:
        if entry.get('exact_restore', True) and digest(before) == entry['installed_hash']:
            return before, entry['original_file'], None, []
        conflicts = [key for key, value in owned.items() if before is None or doc.get(key) != value['installed']]
        if len(conflicts) == len(owned):
            return before, before, copy.deepcopy(entry), conflicts
        for key in [key for key in owned if key not in conflicts]:
            doc.pop(key, None)
            doc.update(json.loads(owned.pop(key)['original']))
        return before, json.dumps(doc, indent=2) + '\n', dict(copy.deepcopy(entry), owned=owned) if owned else None, conflicts
    # Keys no longer managed (the global theme before v1.0.6) are released: restored if still ours, else left as the user set them.
    released = [key for key in owned if key not in values]
    for key in released:
        value = owned.pop(key)
        if doc.get(key) == value['installed']:
            doc.pop(key, None)
            doc.update(json.loads(value['original']))
    wanted = {key: value for key, value in values.items() if key in owned or key in requested}
    conflicts = [key for key in wanted if key in owned and doc.get(key) != owned[key]['installed']
                 or key == 'statusLine' and key not in owned and key in doc and doc[key] != wanted[key]]
    if not released and all(doc.get(key) == value for key, value in wanted.items()):
        return before, before, copy.deepcopy(entry), conflicts
    for key, value in wanted.items():
        if doc.get(key) != value:
            owned.setdefault(key, {'original': json.dumps({key: doc[key]} if key in doc else {})})['installed'] = value
            doc[key] = value
    if entry and not owned:
        if entry.get('exact_restore', True) and digest(before) == entry['installed_hash']:
            return before, entry['original_file'], None, conflicts
        return before, before if doc == json_object(before) else json.dumps(doc, indent=2) + '\n', None, conflicts
    after = json.dumps(doc, indent=2) + '\n'
    updated = {'kind': 'claude-settings', 'pin': None, 'original_file': entry['original_file'] if entry else before,
               'installed_hash': digest(after),
               'exact_restore': (entry.get('exact_restore', True) and digest(before) == entry['installed_hash']) if entry else True,
               'owned': owned}
    return before, after, updated, conflicts


def ghostty_plan(path, entry, include, undo=False, release=False):
    """Append one optional include line; undo restores the file exactly, or removes only that line after user edits.
    Release is undo during apply: an include the user already removed just stops being owned."""
    before = read(path)
    if undo or release:
        if entry.get('exact_restore', True) and digest(before) == entry['installed_hash']:
            return before, entry['original_file'], None, []
        installed = entry['owned']['$include']['installed']
        if before is None or installed not in before:
            return (before, before, None, []) if release else (before, before, copy.deepcopy(entry), ['$include'])
        return before, before.replace(installed, '', 1), None, []
    block = entry['owned']['$include']['installed'] if entry else (
        '# Herdr Electrified Electric appearance; herdr-electrified undo removes these two lines.\n'
        'config-file = "?' + str(include) + '"\n')
    if before is not None and block in before:
        return before, before, copy.deepcopy(entry), []
    conflicts = ['$include'] if entry else []
    base = before or ''
    after = base + ('\n' if base and not base.endswith('\n') else '') + block
    updated = {'kind': 'ghostty-config', 'pin': None, 'original_file': entry['original_file'] if entry else before,
               'installed_hash': digest(after),
               'exact_restore': (entry.get('exact_restore', True) and digest(before) == entry['installed_hash']) if entry else True,
               'owned': {'$include': {'original': '', 'installed': block}}}
    return before, after, updated, conflicts


def validate(binary, before, after, kind='herdr'):
    if kind in ('electric-file', 'ghostty-config', 'claude-statusline'):
        return []
    if kind != 'herdr':
        json_object(before)
        json_object(after)
        return []
    diagnostics = []
    with tempfile.TemporaryDirectory(prefix='herdr-electrified-validation-') as directory:
        for name, text in [('current', before), ('candidate', after)]:
            path = Path(directory) / (name + '.toml')
            path.write_text(text or '')
            result = subprocess.run([binary['path'], 'config', 'check'],
                                    env=os.environ | {'HERDR_CONFIG_PATH': str(path)},
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
            lines = (result.stdout + result.stderr).strip().splitlines()
            if result.returncode == 0 and lines == ['config: ok']:
                diagnostics.append(set())
            elif result.returncode == 1 and lines and lines[0] == 'config: issues found' and all(
                    line.startswith(('unknown config key ', 'unknown config section ')) for line in lines[1:]) and len(lines) > 1:
                diagnostics.append(set(lines[1:]))
            else:
                raise ValueError(f'native validation failed: {name}: ' + '\n'.join(lines))
    if diagnostics[1] - diagnostics[0] or (before is None and diagnostics[1]):
        raise ValueError('new or worsened diagnostics: ' + '; '.join(sorted(diagnostics[1] - diagnostics[0]))
                         + '. If you just upgraded herdr-electrified, run herdr-electrified install')
    if identity(binary['path']) != binary:
        raise ValueError('executable changed during validation')
    return sorted(diagnostics[1])


def recover(receipt, data):
    pending = data['pending']
    path = canonical(pending['path'])
    before, after = pending['before'], pending['after']
    if digest(before) != pending['before_hash'] or digest(after) != pending['after_hash']:
        raise ValueError('invalid journal hashes; preserve receipt for inspection')
    current = read(path)
    if current not in (before, after):
        raise ValueError(f'interrupted operation conflicts with user edit: {path}')
    kind = pending.get('kind', 'herdr')
    if kind == 'herdr' and identity(pending['pin']['path']) != pending['pin']:
        raise ValueError('interrupted operation pin mismatch; preserve journal for inspection')
    if current != after:
        validate(pending['pin'], before, after, kind)
        install(path, before, after, pending['mode'])
    if pending['entry'] is None:
        data['targets'].pop(str(path), None)
    else:
        data['targets'][str(path)] = pending['entry']
    del data['pending']
    save(receipt, data)

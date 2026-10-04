"""Opt-in, relocatable Electric bundle; shared settings stay under receipt ownership."""
import copy
import hashlib
import json
import platform
import plistlib
import shlex
import shutil
import socket
import subprocess
import os
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

from . import __version__
from . import config as c

REQUIRED = {'herdr', 'codex/bin/codex', 'codex/bin/codex-code-mode-host',
            'codex/codex-path/rg', 'codex/codex-resources/zsh/bin/zsh', 'themes/codex-electric.tmTheme'}
GHOSTTY_APPS = ('/Applications', '~/Applications')
# Pinned at release: install trusts only these exact archives.
BUNDLE = 'herdr-electrified-0.4.0-macos-arm64'
BUNDLE_URL = f'https://github.com/rm0nroe/herdr-electrified/releases/download/v{__version__}/{BUNDLE}.tar.gz'
VERSIONS = ('0.1.0', '0.2.0', '0.2.1', '0.2.2', '0.2.3', '0.3.0', '0.4.0')
BUNDLE_SHA = 'a913fd03063cbf9c13fd50b3d84037b3556991e08f9301ca109691992640175c'
FONT_URL = 'https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/JetBrainsMono.tar.xz'
FONT_SHA = '04d5e8f903693f9dd13e16f867e994834e681eb3c72c0d337a770dcda09010cf'
FONTS = [f'JetBrainsMonoNerdFontMono-{style}.ttf' for style in ('Regular', 'Bold', 'Italic', 'BoldItalic')]


def store():
    return Path.home() / '.local/share/herdr-electrified'


def download(url, sha, directory):
    """Fetch into a temporary file beside its destination; nothing survives a checksum mismatch."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.download-', dir=directory)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, 'wb') as out, urlopen(url, timeout=60) as response:
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha:
            raise ValueError(f'checksum mismatch for {url}; nothing installed')
        return Path(name)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def fetch_bundle():
    """The pinned Electric bundle, extracted once; verify() still checks every file before use."""
    root = store() / 'bundles' / BUNDLE
    if root.exists():
        return root
    archive = download(BUNDLE_URL, BUNDLE_SHA, root.parent)
    staging = Path(tempfile.mkdtemp(prefix='.extract-', dir=root.parent))
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(staging, filter='data')
        (staging / BUNDLE).rename(root)
    finally:
        archive.unlink()
        shutil.rmtree(staging)
    return root


def fetch_fonts():
    """The pinned Nerd Font files for the settings-only Ghostty look, laid out like a bundle's fonts/."""
    root = store() / 'nerd-fonts-3.5.1'
    if all((root / 'fonts' / name).is_file() for name in FONTS):
        return root
    archive = download(FONT_URL, FONT_SHA, root)
    staging = Path(tempfile.mkdtemp(prefix='.extract-', dir=root))
    try:
        with tarfile.open(archive) as tar:
            for name in FONTS:
                (staging / name).write_bytes(tar.extractfile(name).read())
        shutil.rmtree(root / 'fonts', ignore_errors=True)
        staging.rename(root / 'fonts')
    except KeyError as error:
        raise ValueError(f'font archive is missing {error}; nothing installed') from error
    finally:
        archive.unlink()
        shutil.rmtree(staging, ignore_errors=True)
    return root


def ghostty_installed(path=None):
    return bool(shutil.which('ghostty', path=path) or any((Path(d).expanduser() / 'Ghostty.app').is_dir() for d in GHOSTTY_APPS))


def verify(directory):
    root = Path(directory).expanduser()
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f'Electric bundle missing at {root}; restore it, reselect with --electric <bundle>, '
                         'or run herdr-electrified undo --herdr-bin <herdr-path>') from error
    if (platform.system(), platform.machine()) != ('Darwin', 'arm64'):
        raise ValueError('Electric supports macOS arm64 only')
    manifest = json.loads((root / 'manifest.json').read_text())
    if not isinstance(manifest, dict):
        raise ValueError('invalid Electric manifest')
    if manifest.get('version') not in VERSIONS or manifest.get('target') != 'aarch64-apple-darwin':
        raise ValueError('unsupported Electric bundle version or target')
    files = manifest.get('files')
    if not isinstance(files, dict) or not REQUIRED <= files.keys():
        raise ValueError('incomplete Electric bundle manifest')
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('invalid bundle checksum entry')
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f'unsafe bundle member: {name}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'bundle checksum mismatch: {name}')
    for name in REQUIRED - {'themes/codex-electric.tmTheme'}:
        if not os.access(root / name, os.X_OK):
            raise ValueError(f'bundle member is not executable: {name}')
    plistlib.loads((root / 'themes/codex-electric.tmTheme').read_bytes())
    # A browser download quarantines every extracted file; macOS then kills the unnotarized binaries.
    for name in REQUIRED - {'themes/codex-electric.tmTheme'}:
        if subprocess.run(['xattr', '-p', 'com.apple.quarantine', str(root / name)], capture_output=True).returncode == 0:
            raise ValueError('macOS quarantined this bundle (browser download), so its binaries would be killed. '
                             'After checking SHA256SUMS, run: xattr -dr com.apple.quarantine ' + shlex.quote(str(root)))
    return root, manifest


def detect(codex_home):
    """Agents the host already has; Electric's own wrappers and a bare ~/.codex do not count."""
    commands = str(Path.home() / '.local/share/herdr-electrified/commands')
    path = os.pathsep.join(p for p in os.environ.get('PATH', '').split(os.pathsep) if p.rstrip('/') != commands)
    agents = set()
    if shutil.which('codex', path=path) or os.environ.get('CODEX_HOME') or (Path(codex_home) / 'auth.json').is_file():
        agents.add('codex')
    if shutil.which('claude', path=path) or os.environ.get('CLAUDE_CONFIG_DIR') or (Path.home() / '.claude').is_dir():
        agents.add('claude')
    if ghostty_installed(path):
        agents.add('ghostty')
    return agents


def ghostty_config():
    """The last config file Ghostty loads wins, so the include goes there."""
    xdg = Path(os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')) / 'ghostty'
    support = Path.home() / 'Library/Application Support/com.mitchellh.ghostty'
    # Load order measured on Ghostty 1.3.1: XDG before Application Support, `config` before `config.ghostty`.
    existing = [p for p in (xdg / 'config', xdg / 'config.ghostty', support / 'config', support / 'config.ghostty') if p.exists()]
    return c.canonical(existing[-1] if existing else xdg / 'config.ghostty')


def missing_fonts(root):
    fonts = Path.home() / 'Library/Fonts'
    return [s for s in sorted((root / 'fonts').glob('*.ttf')) if not (fonts / s.name).exists() and not (fonts / s.name).is_symlink()]


def install_fonts(root, owned, save):
    """Copy missing bundle fonts into ~/Library/Fonts, recording ownership before each write so a retry
    finishes an interrupted install. A file already there is never replaced."""
    fonts = Path.home() / 'Library/Fonts'
    owned = [dict(f) for f in owned]
    for source in missing_fonts(root):
        target = fonts / source.name
        data = source.read_bytes()
        entry = next((f for f in owned if f['path'] == str(target)), None)
        if entry is None:
            entry = {'path': str(target)}
            owned.append(entry)
        entry['sha256'] = hashlib.sha256(data).hexdigest()
        save(owned)
        fonts.mkdir(parents=True, exist_ok=True)
        fd, staged = tempfile.mkstemp(prefix='.herdr-electrified-', suffix='.ttf', dir=fonts)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            os.link(staged, target)  # fails instead of replacing anything that appeared meanwhile
        finally:
            Path(staged).unlink(missing_ok=True)
    return owned


def font_status(owned):
    def status(path, digest):
        if not path.is_file() or path.is_symlink():
            return 'missing'
        return 'ok' if hashlib.sha256(path.read_bytes()).hexdigest() == digest else 'changed'
    return [{'path': f['path'], 'status': status(Path(f['path']), f['sha256'])} for f in owned]


def session_dir():
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base).expanduser() / 'herdr/sessions/herdr-electrified'


def recorded_session(record):
    """The session directory from apply time. Early pre-release receipts lack it: derive it from the default
    config layout, and give up (None) for a custom config rather than guess from the current environment."""
    if record.get('session'):
        return Path(record['session'])
    config = Path(record['config'])
    if config.parts[-3:] == ('herdr-electrified', 'electric', 'config.toml'):
        return config.parents[2] / 'herdr/sessions/herdr-electrified'
    return None


def running(session=None):
    sock = Path(session or session_dir()) / 'herdr.sock'
    if not sock.exists():
        return False
    with socket.socket(socket.AF_UNIX) as probe:
        try:
            probe.connect(str(sock))
        except (ConnectionRefusedError, FileNotFoundError):
            return False
        except OSError:
            return True  # cannot probe (for example path too long): assume live, never delete a live session
    return True


def prune(record, extra_dirs=()):
    """Remove fonts apply installed that are unchanged, then directories apply created, empty ones only."""
    for font in record.get('fonts', []):
        path = Path(font['path'])
        if path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == font['sha256']:
            path.unlink()
    for directory in list(extra_dirs) + sorted(record.get('created_dirs', []), key=lambda d: -len(Path(d).parts)):
        try:
            Path(directory).rmdir()
        except OSError:
            pass


def cleanup(record):
    """Remove what Electric left at runtime, then prune."""
    created = record.get('created_dirs', [])
    # Herdr writes release notes beside its config; only a directory apply created is ours to clean.
    notes = Path(record['config']).parent / 'release-notes.json'
    if str(notes.parent) in created and notes.is_file() and not notes.is_symlink():
        notes.unlink()
    session = recorded_session(record)
    if session and session.is_dir() and not session.is_symlink():
        shutil.rmtree(session)
    prune(record, [session.parent] if session else [])


ZSHENV = '''# herdr-electrified: run your zsh startup as usual, then put Electric's codex first on PATH.
if [[ -n "${HERDR_ELECTRIFIED_ZDOTDIR+x}" ]]; then
  export ZDOTDIR="$HERDR_ELECTRIFIED_ZDOTDIR"
  unset HERDR_ELECTRIFIED_ZDOTDIR
else
  unset ZDOTDIR
fi
[[ -r "${ZDOTDIR-$HOME}/.zshenv" ]] && source "${ZDOTDIR-$HOME}/.zshenv"
typeset -g _herdr_electrified_commands=@COMMANDS@
path=($_herdr_electrified_commands ${path:#$_herdr_electrified_commands})
if [[ -o interactive ]]; then
  # .zprofile and .zshrc run after this file, so reorder once more before the first prompt.
  _herdr_electrified_path() {
    path=($_herdr_electrified_commands ${path:#$_herdr_electrified_commands})
    precmd_functions=(${precmd_functions:#_herdr_electrified_path})
    unfunction _herdr_electrified_path
  }
  precmd_functions+=(_herdr_electrified_path)
fi
'''


def targets(root, config, codex_home, agents=('codex',)):
    home = Path.home()
    codex_home = Path(codex_home)
    bin_dir = home / '.local/bin'
    # This PATH override is scoped to Herdr Electric's children, not the user's shell.
    commands = home / '.local/share/herdr-electrified/commands'
    zdotdir = home / '.local/share/herdr-electrified/zsh'
    for directory in (commands, zdotdir):
        if directory.resolve() != home.resolve() / '.local/share/herdr-electrified' / directory.name:
            raise ValueError('Electric command directory must not redirect through symlinks')
    for path in [codex_home / 'themes/herdr-electric.tmTheme', bin_dir / 'codex-electric', bin_dir / 'herdr-electric', commands / 'codex', zdotdir / '.zshenv']:
        if path.is_symlink():
            raise ValueError(f'Electric target must not be a symlink: {path}')
    codex = '#!/bin/sh\nexport CODEX_HOME=' + shlex.quote(str(codex_home)) + '\nexport CODEX_HERDR_REFERENCE_UI=1\nexec ' + shlex.quote(str(root / 'codex/bin/codex')) + ' "$@"\n'
    # Pane shells re-run the user's profile, which can put stock codex first again; zsh gets a
    # ZDOTDIR shim (the Ghostty shell-integration pattern) that restores order after it.
    path = ('export PATH=' + shlex.quote(str(commands)) + ':"$PATH"\nunset HERDR_ELECTRIFIED_ZDOTDIR\n'
            'if [ -n "${ZDOTDIR+x}" ]; then export HERDR_ELECTRIFIED_ZDOTDIR="$ZDOTDIR"; fi\n'
            'export ZDOTDIR=' + shlex.quote(str(zdotdir)) + '\n') if 'codex' in agents else ''
    herdr = '#!/bin/sh\nunset HERDR_SOCKET_PATH HERDR_CLIENT_SOCKET_PATH HERDR_SESSION CLAUDE_CODE_CHILD_SESSION\n' + path + 'export HERDR_CONFIG_PATH=' + shlex.quote(str(config)) + '\nexec ' + shlex.quote(str(root / 'herdr')) + ' --session herdr-electrified "$@"\n'
    files = {c.canonical(bin_dir / 'herdr-electric'): herdr}
    if 'codex' in agents:
        files |= {c.canonical(codex_home / 'themes/herdr-electric.tmTheme'): (root / 'themes/codex-electric.tmTheme').read_text(),
                  c.canonical(bin_dir / 'codex-electric'): codex,
                  c.canonical(commands / 'codex'): codex,
                  c.canonical(zdotdir / '.zshenv'): ZSHENV.replace('@COMMANDS@', shlex.quote(str(commands)))}
    if 'ghostty' in agents:
        files[c.canonical(Path(config).parent / 'ghostty.conf')] = c.files('herdr_electrified').joinpath('data/ghostty.conf').read_text()
    return files


def file_plan(path, entry, desired=None, undo=False):
    before = c.read(path)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    drift = bool(entry and (c.digest(before) != entry['installed_hash'] or mode != entry.get('installed_mode', mode)))
    if undo:
        if drift:
            return before, before, copy.deepcopy(entry), ['$file']
        return before, entry['original_file'], None, []
    installed = desired if desired is not None else entry['owned']['$file']['installed']
    installed_mode = 0o700 if installed.startswith('#!/bin/sh') else 0o600
    conflicts = ['$file'] if drift else []
    if before == installed and mode == installed_mode:
        return before, before, copy.deepcopy(entry), conflicts
    updated = {'kind': 'electric-file', 'pin': None,
               'original_file': entry['original_file'] if entry else before,
               'original_mode': entry.get('original_mode') if entry else mode,
               'installed_mode': installed_mode,
               'installed_hash': c.digest(installed), 'owned': {'$file': {
                   'original': json.dumps(entry['original_file'] if entry else before), 'installed': installed}}}
    return before, installed, updated, conflicts

#!/usr/bin/env python3
"""Build the pinned macOS arm64 renderers. Requires Python 3.12+, git, Rust, Zig 0.16.0 and full Xcode."""
import argparse
import gzip
import hashlib
import json
import mmap
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]
SOURCES = {'herdr': ('https://github.com/herdrdev/herdr.git', '7b116c05bfda646af39d2524c54e70c751f57ee8'),
           'codex': ('https://github.com/openai/codex.git', 'a956835d020762cb2b570053af06f643a11c0ecc')}
PACKAGE_URL = 'https://github.com/openai/codex/releases/download/rust-v0.160.0/codex-package-aarch64-apple-darwin.tar.gz'
PACKAGE_SHA = '007df41b607dbbc8d204b9746ce7fed2d4ce6c813f44c32ceee54175ca796525'
FONT_URL = 'https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/JetBrainsMono.tar.xz'
FONT_SHA = '04d5e8f903693f9dd13e16f867e994834e681eb3c72c0d337a770dcda09010cf'
FONTS = [f'JetBrainsMonoNerdFontMono-{style}.ttf' for style in ('Regular', 'Bold', 'Italic', 'BoldItalic')]
BUNDLE_VERSION = '0.7.0'


def build_env():
    env = os.environ.copy()
    env['DEVELOPER_DIR'] = env.get('DEVELOPER_DIR', '/Applications/Xcode.app/Contents/Developer')
    env.pop('SDKROOT', None)
    env.pop('CARGO_TARGET_DIR', None)
    env.setdefault('CARGO_BUILD_JOBS', '1')
    env['CARGO_ENCODED_RUSTFLAGS'] = '--remap-path-prefix=' + str(Path.home()) + '=/build'
    env['CFLAGS'] = '-ffile-prefix-map=' + str(Path.home()) + '=/build'
    env['CXXFLAGS'] = env['CFLAGS']
    return env


def run(argv, cwd):
    subprocess.run(argv, cwd=cwd, env=build_env(), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path, help='new empty build directory')
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ('Darwin', 'arm64'):
        parser.error('macOS arm64 is required')
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    for name, (url, revision) in SOURCES.items():
        source = work / name
        source.mkdir()
        for argv in (['git', 'init', '-q'], ['git', 'fetch', '--depth=1', url, revision],
                     ['git', 'checkout', '--detach', 'FETCH_HEAD'], ['git', 'apply', str(REPO / 'patches' / (name + '.patch'))]):
            run(argv, source)
        cwd = source / 'codex-rs' if name == 'codex' else source
        # Codex: smaller codegen units and opt-level 2 keep the final thin-LTO link within memory.
        build = (['rustc', '-p', 'codex-cli', '--release', '--locked', '--bin', name, '--', '-C', 'codegen-units=16', '-C', 'opt-level=2']
                 if name == 'codex' else ['build', '--release', '--locked', '--bin', name])
        run(['cargo', '+1.95.0' if name == 'codex' else '+1.96.1', *build], cwd)
    package, fonts = work / 'upstream-codex.tar.gz', work / 'fonts.tar.xz'
    for url, sha, path in ((PACKAGE_URL, PACKAGE_SHA, package), (FONT_URL, FONT_SHA, fonts)):
        urllib.request.urlretrieve(url, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f'checksum mismatch: {url}')
    assemble(work, work / 'herdr/target/release/herdr', work / 'codex/codex-rs/target/release/codex', package, fonts)


def privacy_scan(bundle, upstream, home=str(Path.home())):
    """Shipped files only. /Users/runner and /var/folders/ (upstream CI) are allowed only in files byte-identical to the pinned package."""
    pattern = re.compile(re.escape(home.encode()) + rb'|/Users/[A-Za-z0-9._-]{0,64}|/private/var/folders/|/var/folders/')
    findings = []
    for path in sorted(bundle.rglob('*')):
        if not path.is_file() or path.is_symlink() or not path.stat().st_size:
            continue
        name = str(path.relative_to(bundle))
        with path.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            hits = {m.group() for m in pattern.finditer(data)}
            if hits <= {b'/Users/runner', b'/var/folders/'} and upstream.get(name) == hashlib.sha256(data).hexdigest():
                continue
        if hits:
            findings.append({'file': name, 'hits': sorted(h.decode(errors='replace') for h in hits)})
    return findings


def assemble(work, herdr, codex, upstream, fonts):
    """Also used by release verification with already-built exact patched sources."""
    work = Path(work)
    bundle = work / f'herdr-electrified-{BUNDLE_VERSION}-macos-arm64'
    bundle.mkdir(exist_ok=False)
    (bundle / 'codex').mkdir()
    with tarfile.open(upstream) as archive:
        archive.extractall(bundle / 'codex', filter='data')
    upstream_hashes = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.rglob('*') if p.is_file()}
    shutil.copy2(herdr, bundle / 'herdr')
    shutil.copy2(codex, bundle / 'codex/bin/codex')
    for executable in (bundle / 'herdr', bundle / 'codex/bin/codex'):
        run(['/usr/bin/strip', '-S', '-x', str(executable)], work)
        run(['/usr/bin/codesign', '--force', '--sign', '-', str(executable)], work)
    (bundle / 'fonts').mkdir()
    with tarfile.open(fonts) as archive:
        for name in FONTS:
            (bundle / 'fonts' / name).write_bytes(archive.extractfile(name).read())
    shutil.copytree(REPO / 'licenses', bundle / 'licenses')
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy2(REPO / name, bundle / name)
    (bundle / 'themes').mkdir()
    shutil.copy2(REPO / 'src/herdr_electrified/data/codex-electric.tmTheme', bundle / 'themes/codex-electric.tmTheme')
    manifest = {'version': BUNDLE_VERSION, 'target': 'aarch64-apple-darwin',
                'sources': {name: {'url': url, 'revision': rev, 'patch_sha256': hashlib.sha256((REPO / 'patches' / (name + '.patch')).read_bytes()).hexdigest()} for name, (url, rev) in SOURCES.items()},
                'upstream_package': {'url': PACKAGE_URL, 'sha256': PACKAGE_SHA}, 'fonts': {'url': FONT_URL, 'sha256': FONT_SHA},
                'files': {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob('*')) if p.is_file()}}
    (bundle / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    findings = privacy_scan(bundle, upstream_hashes)
    if findings:
        raise ValueError(f'privacy scan failed: {findings}')
    output = work / (bundle.name + '.tar.gz')
    with output.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for path in sorted(bundle.rglob('*')):
            info = archive.gettarinfo(str(path), arcname=str(path.relative_to(bundle.parent)))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            if path.is_file():
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
    (work / 'SHA256SUMS').write_text(hashlib.sha256(output.read_bytes()).hexdigest() + '  ' + output.name + '\n')
    print(output)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Install an official Codex package into Xcode's per-user agent directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import uuid
from urllib.request import Request, urlopen

DEFAULT_VERSION = '0.165.0'
DEFAULT_MODEL = 'gpt-6.1-sol'


def fetch(url):
    request = Request(url, headers={'User-Agent': 'xcode-codex-upgrade'})
    with urlopen(request, timeout=120) as response:
        return response.read()


def require_xcode_closed():
    if subprocess.run(['pgrep', '-x', 'Xcode'], capture_output=True).returncode == 0:
        raise RuntimeError('Quit Xcode normally first, then rerun this command.')


def model_config(text, model):
    """Replace only the top-level model; leave tables and other settings intact."""
    original = tomllib.loads(text)
    expected = dict(original, model=model)
    replacement = 'model = ' + json.dumps(model) + '\n'
    lines = text.splitlines(keepends=True)
    # Try candidate source edits, accepting one only if TOML parsing proves
    # that the top-level model is the sole semantic change. This handles quoted
    # keys and multiline strings without rewriting unrelated configuration.
    for index, line in enumerate(lines):
        if re.match(r"^\s*(?:model|\"model\"|'model')\s*=", line):
            candidate = ''.join(lines[:index] + [replacement] + lines[index + 1:])
            try:
                if tomllib.loads(candidate) == expected:
                    return candidate
            except tomllib.TOMLDecodeError:
                pass
    candidate = replacement + text
    if tomllib.loads(candidate) != expected:
        raise RuntimeError('Cannot safely update the top-level model setting.')
    return candidate



def verify_archive(data, sums, digest, filename):
    actual = hashlib.sha256(data).hexdigest()
    matches = [line.split()[0] for line in sums.splitlines()
               if len(line.split()) == 2 and line.split()[1].lstrip('*') == filename]
    if matches != [actual] or digest != 'sha256:' + actual:
        raise RuntimeError('Archive checksum does not match published release metadata and SHA256SUMS.')
    return actual


def unpack(archive, destination):
    # Validate all paths before extraction, including link targets. Packages need
    # only ordinary files, directories and internal relative symlinks.
    with tarfile.open(archive) as package:
        members = package.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('Unsafe archive path.')
            if not (member.isfile() or member.isdir() or member.issym()):
                raise RuntimeError('Unsupported archive member.')
            if member.issym():
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or '..' in target.parts:
                    raise RuntimeError('Unsafe archive link.')
        # Reject descendants of symlinks so an earlier entry cannot redirect a
        # later write. This also makes extraction safe on older Python versions.
        links = {PurePosixPath(m.name) for m in members if m.issym()}
        if any(parent in links for m in members for parent in PurePosixPath(m.name).parents):
            raise RuntimeError('Archive writes through a symlink.')
        package.extractall(destination)


def switch_link(link, target):
    temporary = link.with_name('codex-upgrade-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def snapshot(base, link):
    parent = base / 'upgrade-backups'
    parent.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    manifest = {'link': str(link), 'original_target': str(link.readlink()), 'files': {}}
    for name in ('config.toml', 'models_cache.json'):
        source = base / 'codex' / name
        manifest['files'][name] = source.exists()
        if source.exists():
            shutil.copy2(source, backup / name)
            (backup / name).chmod(0o600)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return backup


def restore(base, backup):
    manifest = json.loads((backup / 'manifest.json').read_text())
    link = Path(manifest['link'])
    expected = base / 'Agents' / 'XcodeVersions'
    if link.name != 'codex' or link.parent.parent != expected:
        raise RuntimeError('Backup belongs to another CodingAssistant directory.')
    target = Path(manifest['original_target'])
    resolved = target if target.is_absolute() else link.parent / target
    if not resolved.is_dir():
        raise RuntimeError('Previous runtime is missing; refusing rollback.')
    for name, existed in manifest['files'].items():
        if name not in ('config.toml', 'models_cache.json'):
            raise RuntimeError('Unexpected backup file.')
        if existed and not (backup / name).is_file():
            raise RuntimeError('Backup is incomplete.')
    for name, existed in manifest['files'].items():
        destination = base / 'codex' / name
        if existed:
            shutil.copy2(backup / name, destination)
        elif destination.exists():
            destination.unlink()
    switch_link(link, target)


def install(base, link, runtime, model):
    config = base / 'codex' / 'config.toml'
    updated = model_config(config.read_text() if config.exists() else '', model)
    backup = snapshot(base, link)
    print('Rollback backup:', backup)
    try:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(updated)
        config.chmod(0o600)
        cache = config.with_name('models_cache.json')
        if cache.exists():
            cache.unlink()  # snapshot already preserves the exact previous cache
        switch_link(link, runtime)
    except BaseException:
        restore(base, backup)
        raise
    return backup



def publish_and_install(base, link, stage, runtime, model):
    with tempfile.TemporaryDirectory(prefix='.codex-upgrade-', dir=runtime.parent) as sibling:
        staged_runtime = Path(sibling) / 'runtime'
        shutil.copytree(stage, staged_runtime)
        runtime.mkdir()  # exclusive ownership; never replace another installation
        try:
            os.replace(staged_runtime, runtime)
            return install(base, link, runtime, model)
        except BaseException:
            if link.resolve() != runtime.resolve():
                shutil.rmtree(runtime)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Install and switch runtime; otherwise inspect only')
    parser.add_argument('--version', default=DEFAULT_VERSION, help='Exact stable Codex version')
    parser.add_argument('--model', default=DEFAULT_MODEL, help='Canonical model ID available to your account')
    parser.add_argument('--rollback', type=Path, metavar='BACKUP', help='Restore a backup (requires --apply)')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('This script requires macOS.')
    base = Path.home() / 'Library/Developer/Xcode/CodingAssistant'
    if args.rollback:
        if not args.apply:
            print('Dry run: would restore', args.rollback)
            return
        require_xcode_closed()
        restore(base, args.rollback.expanduser().resolve())
        print('Restored previous runtime, configuration and cache. Reopen Xcode.')
        return
    if platform.machine() != 'arm64':
        raise RuntimeError('This version supports Apple Silicon only.')
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        raise RuntimeError('Use an exact stable version, such as 0.165.0.')
    if not re.fullmatch(r'gpt-[a-z0-9.-]+', args.model):
        raise RuntimeError('Use a canonical model ID, such as gpt-6.1-sol.')
    output = subprocess.check_output(['xcodebuild', '-version'], text=True)
    match = re.search(r'^Build version (\S+)$', output, re.M)
    if not match:
        raise RuntimeError('Cannot determine the selected Xcode build.')
    link = base / 'Agents/XcodeVersions' / match[1] / 'codex'
    if not link.is_symlink() or not link.resolve().is_dir():
        raise RuntimeError('No existing Codex runtime link for this Xcode build. Enable Codex in Xcode first.')
    runtime = base / 'Agents/codex' / args.version
    print(output.strip())
    print('Current runtime:', link.resolve())
    print('Proposed runtime:', runtime)
    print('Proposed model:', args.model)
    if not args.apply:
        print('Dry run only. Quit Xcode and rerun with --apply to install.')
        return
    require_xcode_closed()
    if runtime.exists():
        raise RuntimeError('Destination version already exists; refusing to overwrite it.')
    release_url = 'https://api.github.com/repos/openai/codex/releases/tags/rust-v' + args.version
    release = json.loads(fetch(release_url))
    if release.get('draft') or release.get('prerelease'):
        raise RuntimeError('Expected a stable published release.')
    assets = {a['name']: a for a in release['assets']}
    name = 'codex-package-aarch64-apple-darwin.tar.gz'
    asset = assets[name]
    urls = [asset['browser_download_url'], assets['codex-package_SHA256SUMS']['browser_download_url']]
    prefix = 'https://github.com/openai/codex/releases/download/rust-v' + args.version + '/'
    if not all(url.startswith(prefix) for url in urls):
        raise RuntimeError('Unexpected release download URL.')
    data = fetch(urls[0])
    checksum = verify_archive(data, fetch(urls[1]).decode(), asset.get('digest'), name)
    print('Verified SHA256:', checksum)
    with tempfile.TemporaryDirectory(prefix='xcode-codex-') as directory:
        archive = Path(directory) / name
        archive.write_bytes(data)
        stage = Path(directory) / 'package'
        stage.mkdir()
        unpack(archive, stage)
        metadata = json.loads((stage / 'codex-package.json').read_text())
        if metadata['version'] != args.version or metadata['entrypoint'] != 'bin/codex':
            raise RuntimeError('Unexpected package version or entrypoint.')
        for binary in ('codex', 'codex-code-mode-host'):
            if not os.access(stage / 'bin' / binary, os.X_OK):
                raise RuntimeError('Missing executable: ' + binary)
        print(subprocess.check_output([str(stage / 'bin/codex'), '--version'], text=True).strip())
        info = plistlib.loads((link / 'Info.plist').read_bytes())
        info.update(version=args.version, url=urls[0], checksum=hashlib.sha512(data).hexdigest(), executableRelativePath='bin/codex')
        (stage / 'Info.plist').write_bytes(plistlib.dumps(info))
        require_xcode_closed()
        backup = publish_and_install(base, link, stage, runtime, args.model)
    print('Installed. Reopen Xcode and complete the README verification checks.')
    print('For existing conversations, reselect the model from Conversation Settings; saved overrides can retain an invalid model name.')
    print('Rollback: python3 scripts/xcode_codex.py --apply --rollback ' + repr(str(backup)))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('Error:', str(error), file=sys.stderr)
        sys.exit(1)

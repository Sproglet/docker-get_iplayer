#!/usr/bin/env python3
"""Import a verified upstream archive from the repository root (Python 3.10+)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive', type=Path, required=True)
parser.add_argument('--commit', required=True)
parser.add_argument('--tag', required=True)
parser.add_argument('--sha256', required=True)
args = parser.parse_args()
if not re.fullmatch('[0-9a-f]{40}', args.commit) or not re.fullmatch('[0-9a-f]{64}', args.sha256):
    parser.error('Expected a full commit SHA and archive SHA-256')
root = Path.cwd()
if not (root / 'Dockerfile').is_file() or not (root / 'vendor/get_iplayer.UPSTREAM.json').is_file():
    parser.error('Run from the repository root')
target = root / 'vendor/get_iplayer'
paths = ['vendor/get_iplayer', 'vendor/get_iplayer.UPSTREAM.json', 'vendor/get_iplayer.sha256']
if subprocess.check_output(['git', 'status', '--porcelain', '--', *paths]).strip():
    parser.error('Commit or stash existing vendor changes first')
# Never overwrite local engine patches, even if they have been committed.
manifest = root / 'vendor/get_iplayer.sha256'
expected = {}
for line in manifest.read_text().splitlines():
    checksum, name = line.split('  ', 1)
    expected[name] = checksum
actual = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in target.rglob('*') if p.is_file()}
if actual != expected:
    parser.error('Local engine changes detected; follow docs/UPDATES.md to preserve and reapply them')
archive = args.archive.read_bytes()
if hashlib.sha256(archive).hexdigest() != args.sha256:
    parser.error('Archive checksum mismatch')
with tempfile.TemporaryDirectory(prefix='gip-import-') as temporary:
    staging = Path(temporary)
    with tarfile.open(args.archive, 'r:gz') as tar:
        for entry in tar:
            parts = Path(entry.name).parts
            if len(parts) == 1 and entry.isdir():
                continue
            if len(parts) < 2 or '..' in parts or entry.name.startswith('/'):
                parser.error('Unsafe archive path')
            destination = staging.joinpath(*parts[1:])
            if entry.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(tar.extractfile(entry).read())
                destination.chmod(0o755 if entry.mode & 0o111 else 0o644)
            else:
                parser.error('Archive links/devices are not supported')
    for required in ('get_iplayer', 'get_iplayer.cgi', 'LICENSE.txt', 'CONTRIBUTORS'):
        if not (staging / required).is_file():
            parser.error(f'Archive is missing {required}')
    shutil.rmtree(target)
    shutil.copytree(staging, target)
metadata = {'repository': 'https://github.com/get-iplayer/get_iplayer', 'tag': args.tag,
            'commit': args.commit,
            'archive_url': f'https://api.github.com/repos/get-iplayer/get_iplayer/tarball/{args.commit}',
            'archive_sha256': args.sha256, 'license': 'GPL-3.0-or-later', 'local_changes': False}
(root / 'vendor/get_iplayer.UPSTREAM.json').write_text(json.dumps(metadata, indent=2) + '\n')
manifest.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root)}\n'
                          for p in sorted(target.rglob('*')) if p.is_file()))
print('Imported upstream source. Review licence/version/diff, build, and run tests before committing.')

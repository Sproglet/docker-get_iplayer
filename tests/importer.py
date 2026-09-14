#!/usr/bin/env python3
"""Exercise import safety entirely in a temporary miniature repository."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

script = Path('scripts/import-get-iplayer.py').resolve()
with tempfile.TemporaryDirectory(prefix='gip-import-test-') as tmp:
    root = Path(tmp)
    source = root / 'vendor/get_iplayer'
    source.mkdir(parents=True)
    (root / 'Dockerfile').touch()
    (root / 'vendor/get_iplayer.UPSTREAM.json').write_text('{}')
    (source / 'get_iplayer').write_text('original')
    checksum = hashlib.sha256(b'original').hexdigest()
    (root / 'vendor/get_iplayer.sha256').write_text(f'{checksum}  vendor/get_iplayer/get_iplayer\n')
    def git(*args):
        subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    git('init')
    git('add', '.')
    git('-c', 'user.name=Import Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'baseline')
    archive = root / 'source.tar.gz'
    def pack(unsafe=False):
        with tarfile.open(archive, 'w:gz') as tar:
            for filename in ['get_iplayer', 'get_iplayer.cgi', 'LICENSE.txt', 'CONTRIBUTORS'] + (['../escape'] if unsafe else []):
                data = b'upstream fixture'
                member = tarfile.TarInfo('upstream/' + filename)
                member.size = len(data)
                member.mode = 0o755 if filename.startswith('get_iplayer') else 0o644
                tar.addfile(member, io.BytesIO(data))
        return hashlib.sha256(archive.read_bytes()).hexdigest()
    def run(checksum):
        return subprocess.run([sys.executable, str(script), '--archive', str(archive),
                               '--commit', 'a' * 40, '--tag', 'v-test', '--sha256', checksum],
                              cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    checksum = pack()
    assert 'checksum mismatch' in run('0' * 64).stdout
    assert 'Unsafe archive path' in run(pack(unsafe=True)).stdout
    assert (source / 'get_iplayer').read_text() == 'original'
    checksum = pack()
    (source / 'get_iplayer').write_text('local patch')
    assert 'Commit or stash' in run(checksum).stdout
    git('add', 'vendor')
    git('-c', 'user.name=Import Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'local patch')
    assert 'Local engine changes detected' in run(checksum).stdout
    (source / 'get_iplayer').write_text('original')
    git('add', 'vendor')
    git('-c', 'user.name=Import Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'restore baseline')
    result = run(checksum)
    assert result.returncode == 0, result.stdout
    assert (source / 'get_iplayer').read_text() == 'upstream fixture'
    assert (source / 'get_iplayer').stat().st_mode & 0o777 == 0o755
    assert json.loads((root / 'vendor/get_iplayer.UPSTREAM.json').read_text())['commit'] == 'a' * 40
print('PASS importer: verified import, metadata/modes, checksum/path rejection, dirty and committed patch protection')

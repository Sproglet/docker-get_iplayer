#!/usr/bin/env python3
"""Create an isolated app + Chromium test container; no published ports or BBC access."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

root = Path.cwd()
key = uuid.uuid4().hex[:10]
network, app = 'gip-browser-' + key, 'gip-browser-app-' + key
runner = 'gip-browser-runner-' + key
image = os.environ.get('TEST_IMAGE', 'docker-get_iplayer:web-test')
artifacts = Path(os.environ.get('TEST_ARTIFACTS', '/tmp/gip-browser-artifacts')).resolve()
artifacts.mkdir(parents=True, exist_ok=True)
def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
with tempfile.TemporaryDirectory(prefix='gip-browser-data-') as tmp:
    output = Path(tmp)
    output.chmod(0o755)
    (output / '.get_iplayer/pvr').mkdir(parents=True)
    (output / '.get_iplayer/pvr/Existing search').write_text('search0 Coast\ntype tv\n')
    for path in [output, *output.rglob('*')]:
        os.chown(path, 12345, 12346)
    try:
        docker('network', 'create', '--internal', network)
        docker('run', '-d', '--name', app, '--network', network, '--label', 'docker-get_iplayer.web-test=true',
               '-e', 'PUID=12345', '-e', 'PGID=12346', '-e', 'WEB_TOKEN=browser-test-token-1234567890',
               '-v', f'{output}:/output', '-v', f'{root / "tests/fixtures/engine.py"}:/opt/get_iplayer/get_iplayer:ro', image)
        for _ in range(60):
            if docker('exec', app, '/usr/local/bin/healthcheck', check=False).returncode == 0:
                break
            time.sleep(.2)
        result = docker('run', '--rm', '--init', '--name', runner, '--label', 'docker-get_iplayer.web-test=true', '--network', network, '--shm-size=256m',
                        '-e', f'TEST_URL=http://{app}:1935', '-e', 'TEST_TOKEN=browser-test-token-1234567890',
                        '-v', f'{root / "tests/browser_scenario.py"}:/test.py:ro', '-v', f'{artifacts}:/artifacts',
                        'docker-get_iplayer:browser-tests', 'python', '/test.py', check=False)
        print(result.stdout)
        if result.returncode:
            print(docker('logs', app).stdout)
            raise SystemExit(result.returncode)
    finally:
        docker('rm', '-f', '-v', runner, check=False)
        docker('rm', '-f', '-v', app, check=False)
        docker('network', 'rm', network, check=False)
print(f'Browser screenshots: {artifacts}; isolated app, network and output removed.')

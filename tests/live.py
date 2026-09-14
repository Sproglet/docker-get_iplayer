#!/usr/bin/env python3
"""Opt-in bounded live BBC check, always against new temporary profile/recordings."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--execute', action='store_true', help='Allow BBC access and a temporary ten-second sample')
parser.add_argument('--programme-type', choices=('radio', 'tv'), default='radio')
parser.add_argument('--query', default='', help='Optional catalogue search to select the sample programme')
args = parser.parse_args()
if not args.execute:
    parser.error('Pass --execute to permit the live check')
name = 'gip-live-' + uuid.uuid4().hex[:10]
network = name + '-net'
image = os.environ.get('TEST_IMAGE', 'docker-get_iplayer:web-test')
def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
    if check and result.returncode:
        raise RuntimeError(result.stdout)
    return result
with tempfile.TemporaryDirectory(prefix='gip-live-data-') as temporary:
    output = Path(temporary)
    output.chmod(0o755)
    profile = output / '.get_iplayer'
    profile.mkdir()
    (profile / 'options').write_text('stop 10\nattempts 1\ndownloadabortonfail 1\n')
    for path in [output, *output.rglob('*')]:
        os.chown(path, 12345, 12346)
    token = secrets.token_urlsafe(32)
    try:
        docker('network', 'create', network)
        docker('run', '-d', '--name', name, '--network', network, '--label', 'docker-get_iplayer.web-test=true',
               '-e', 'PUID=12345', '-e', 'PGID=12346', '-e', 'WEB_TOKEN=' + token, '-v', f'{output}:/output', image)
        ip = docker('inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', name).stdout.strip()
        base = 'http://' + ip + ':1935'
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        csrf = ''
        def call(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
            with opener.open(req, timeout=5) as response:
                return json.load(response)
        for _ in range(50):
            try:
                call('/health')
                break
            except (OSError, urllib.error.URLError):
                time.sleep(.2)
        csrf = call('/api/login', {'token': token})['csrf']
        def wait(job, seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                current = call('/api/jobs/' + job['id'])
                if current['state'] not in ('queued', 'running'):
                    return current
                time.sleep(1)
            call('/api/jobs/' + job['id'] + '/cancel', {})
            time.sleep(7)
            return call('/api/jobs/' + job['id'])
        refresh = call('/api/jobs', {'kind': 'refresh'})['job']
        result = wait(refresh, 100)
        print('LIVE catalogue refresh:', result['state'], 'exit:', result['exit_code'])
        print(result['log'][-6000:])
        search = call('/api/search?' + urlencode({'type': args.programme_type, 'q': args.query, 'limit': 1}))
        print('LIVE cached', args.programme_type, 'search results:', search['total'])
        if search['items']:
            programme = search['items'][0]
            print('LIVE sample target:', programme['pid'], programme['name'])
            sample = call('/api/jobs', {'kind': 'download', 'pid': programme['pid']})['job']
            result = wait(sample, 90)
            print('LIVE ten-second sample:', result['state'], 'exit:', result['exit_code'])
            print(result['log'][-6000:])
            print('LIVE history entries:', call('/api/history')['total'])
            for media in output.rglob('*'):
                if media.suffix.lower() in ('.mp4', '.m4a', '.mkv'):
                    container_path = '/output/' + media.relative_to(output).as_posix()
                    probe = docker('exec', name, 'ffprobe', '-v', 'error', '-show_entries',
                                   'stream=codec_type,codec_name,width,height:format=duration,format_name', '-of', 'json', container_path)
                    print('LIVE media probe:', probe.stdout)

        else:
            print('LIVE sample NOT VERIFIED: no matching catalogue entry available; no PID guessed.')
    finally:
        docker('rm', '-f', '-v', name, check=False)
        docker('network', 'rm', network, check=False)
print('Live test container, network and all temporary data removed.')

#!/usr/bin/env python3
"""Isolated container/API tests. Engine simulations are labelled separately."""
import concurrent.futures
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

IMAGE = os.environ.get('TEST_IMAGE', 'docker-get_iplayer:web-test')
ROOT = Path.cwd()
TOKEN = 'integration-token-only-1234567890'
containers = []
network = 'gip-web-test-' + uuid.uuid4().hex[:10]

def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
    if check and result.returncode:
        raise AssertionError(f'docker {args}: {result.stdout}')
    return result

def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.15)
    raise AssertionError('Timed out waiting for condition')

def run(output, *options):
    name = 'gip-web-test-' + uuid.uuid4().hex[:10]
    containers.append(name)
    docker('run', '-d', '--name', name, '--network', network,
           '--label', 'docker-get_iplayer.web-test=true',
           '-e', 'PUID=12345', '-e', 'PGID=12346',
           '-e', 'WEB_TOKEN=' + TOKEN, '-v', f'{output}:/output', *options, IMAGE)
    ip = docker('inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', name).stdout.strip()
    return name, 'http://' + ip + ':1935'

class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ''
    def call(self, path, body=None, status=200, headers=None, raw=False):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
              headers={'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf, **(headers or {})})
        try:
            response = self.opener.open(req, timeout=12)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            assert response.status == status, (path, response.status, status, data.decode())
            return data.decode() if raw else json.loads(data)
    def login(self):
        self.csrf = self.call('/api/login', {'token': TOKEN})['csrf']
    def enqueue(self, pid=None, kind='download'):
        return self.call('/api/jobs', {'kind': kind, 'pid': pid or ''}, status=201)['job']
    def job(self, job):
        return self.call('/api/jobs/' + job['id'])
    def terminal(self, job, state, seconds=20):
        wait_for(lambda: self.job(job)['state'] == state, seconds)
        return self.job(job)

def health(url):
    try:
        return urllib.request.urlopen(url + '/health', timeout=1).status == 200
    except (urllib.error.URLError, OSError):
        return False

def fixture(output):
    output.mkdir()
    profile = output / '.get_iplayer'
    (profile / 'pvr').mkdir(parents=True)
    (profile / 'options').write_text('expiry 999999\n')
    (profile / 'pvr/Existing search').write_text('search0 Fixture\ntype tv\n')
    (profile / 'download_history').write_text('b0999999|Existing recording|Episode One|tv|1700000000|hls|/output/existing.mp4|default|60|Existing history|BBC One||||||\n')
    for path in [output, *output.rglob('*')]:
        os.chown(path, 12345, 12346)
    return profile

def no_overlap(output):
    events = [json.loads(line) for line in (output / 'fixture/trace').read_text().splitlines()]
    active = None
    for event in events:
        if event['event'] == 'start':
            assert active is None, events
            active = event['target']
        else:
            assert active == event['target'], events
            active = None
    return active

with tempfile.TemporaryDirectory(prefix='gip-web-data-') as tmp:
    tmp = Path(tmp)
    tmp.chmod(0o755)
    docker('network', 'create', '--internal', network)
    try:
        # Actual bundled engine checks: offline version/syntax/history/PVR. No BBC traffic.
        output = tmp / 'real-engine'
        profile = fixture(output)
        before = {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
        name, url = run(output)
        wait_for(lambda: health(url))
        real = Client(url)
        real.call('/api/jobs', status=401)
        real.login()
        assert 'Existing recording' in json.dumps(real.call('/api/history'))
        assert 'Existing search' in json.dumps(real.call('/api/pvr'))
        real.call('/api/jobs', {'kind': 'pvr'}, status=403, headers={'X-CSRF-Token': 'wrong'})
        real.call('/api/jobs', {'kind': 'pvr'}, status=403, headers={'Origin': 'https://evil.invalid'})
        real.call('/api/jobs', {'kind': 'download', 'pid': '--refresh'}, status=400)
        assert 'Open Recorder' in real.call('/get_iplayer.cgi', status=410, raw=True)
        real.call('/get_iplayer.cgi', {'ACTION': 'record'}, status=410, raw=True)
        for filename in ('get_iplayer', 'get_iplayer.cgi'):
            installed = docker('exec', name, 'sha256sum', '/usr/local/bin/' + filename).stdout.split()[0]
            assert installed == hashlib.sha256((ROOT / 'vendor/get_iplayer' / filename).read_bytes()).hexdigest()
            docker('exec', name, 'perl', '-c', '/usr/local/bin/' + filename)
        for line in (ROOT / 'vendor/get_iplayer.sha256').read_text().splitlines():
            digest, path = line.split('  ', 1)
            assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
        docker('exec', name, 'AtomicParsley', '--version')
        docker('exec', name, 'ffmpeg', '-version')
        assert 'v3.36' in docker('logs', name).stdout
        assert all(p.read_bytes() == content for p, content in before.items())
        docker('stop', '-t', '15', name)
        assert docker('inspect', '-f', '{{.State.ExitCode}}', name).stdout.strip() == '0'
        print('PASS REAL ENGINE, OFFLINE: bundled bytes/version/dependencies, profile/history/PVR reads, auth/CSRF, disabled legacy routes', flush=True)
        # Non-default port, URL prefix and generated persistent token.
        custom_output = tmp / 'custom'
        fixture(custom_output)
        custom, custom_url = run(custom_output, '-e', 'PORT=1947', '-e', 'BASEURL=http://localhost:1947/recorder', '-e', 'WEB_TOKEN=')
        custom_url = custom_url.replace(':1935', ':1947')
        wait_for(lambda: health(custom_url))
        generated = (custom_output / '.get_iplayer-web/access-token').read_text().strip()
        assert len(generated) >= 24
        prefixed = Client(custom_url + '/recorder')
        prefixed.csrf = prefixed.call('/api/login', {'token': generated})['csrf']
        assert 'Existing recording' in json.dumps(prefixed.call('/api/history'))
        assert '/recorder/app.js' in prefixed.call('/', raw=True)
        assert (custom_output / '.get_iplayer-web/access-token').stat().st_mode & 0o777 == 0o600
        prefixed.call('/api/logout', {})
        prefixed.call('/api/jobs', status=401)
        bad = tmp / 'bad-permissions'
        (bad / '.get_iplayer').mkdir(parents=True)
        (bad / '.get_iplayer/options').write_text('expiry 3000\n')
        bad_name, _ = run(bad)
        wait_for(lambda: docker('inspect', '-f', '{{.State.Running}}', bad_name).stdout.strip() == 'false')
        assert 'not readable/writable' in docker('logs', bad_name).stdout
        assert (bad / '.get_iplayer/options').stat().st_uid == 0
        invalid, _ = run(tmp / 'invalid-uid', '-e', 'PUID=00')
        wait_for(lambda: docker('inspect', '-f', '{{.State.Running}}', invalid).stdout.strip() == 'false')
        assert 'non-root' in docker('logs', invalid).stdout
        print('PASS REAL ENGINE, OFFLINE: custom port/prefix, generated token/logout, invalid UID and existing permission checks', flush=True)


        output = tmp / 'simulation'
        profile = fixture(output)
        simulated, url = run(output, '-v', f'{ROOT / "tests/fixtures/engine.py"}:/opt/get_iplayer/get_iplayer:ro')
        wait_for(lambda: health(url))
        client = Client(url)
        client.login()
        refresh = client.enqueue(kind='refresh')
        client.terminal(refresh, 'completed')
        assert client.call('/api/search?q=coast')['items'][0]['pid'] == 'b0000002'
        assert client.call('/api/search?type=radio')['items'][0]['pid'] == 'm0000002'
        slow = client.enqueue('b0000001')
        wait_for(lambda: client.job(slow)['state'] == 'running' and client.job(slow)['progress'] == 12.5)
        wait_for(lambda: (output / 'fixture/b0000001-child-pid').exists())
        # Concurrent producers all join the same durable queue.
        def producer(index):
            peer = Client(url); peer.login()
            body = {'kind': 'pvr'} if index % 2 else {'kind': 'download', 'pid': 'b0000002'}
            req = urllib.request.Request(url + '/api/jobs', data=json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json', 'X-CSRF-Token': peer.csrf, 'Idempotency-Key': f'parallel-{index}'})
            with peer.opener.open(req) as response:
                return json.load(response)['job']
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            produced = list(pool.map(producer, range(6)))
        assert len({job['id'] for job in produced}) == 2
        cli = json.loads(docker('exec', simulated, '/start', 'run-scheduled', 'pvr').stdout)
        assert cli['id'] == next(job['id'] for job in produced if job['kind'] == 'pvr')
        # Actual scheduler insertion through the same Store, without changing the clock in production.
        scheduler_code = "import sys,datetime; sys.path.insert(0,'/usr/local/lib/get_iplayer'); from app import schedule; from store import Store; s=Store('/output/.get_iplayer-web'); print(schedule(s,datetime.datetime(2026,9,11,2,0,tzinfo=datetime.timezone.utc))[0]['id'])"
        scheduled_id = docker('exec', '--user', '12345:12346', simulated, 'python3', '-c', scheduler_code).stdout.strip()
        assert scheduled_id == cli['id']
        queued_cancel = client.enqueue('b0000010')
        client.call('/api/jobs/' + queued_cancel['id'] + '/cancel', {})
        client.terminal(queued_cancel, 'cancelled')
        started = time.monotonic()
        client.call('/api/jobs/' + slow['id'] + '/cancel', {})
        client.terminal(slow, 'cancelled')
        assert time.monotonic() - started < 10
        assert (output / 'fixture/b0000001-child-term').exists()
        assert (output / 'fixture/b0000001-parent-term').exists()
        for job in produced:
            client.terminal(job, 'completed')
        assert no_overlap(output) is None
        assert 'Simulated recording b0000002' in json.dumps(client.call('/api/history'))
        print('PASS SIMULATED: cache search, concurrent manual/CLI/scheduled requests, deduplication, serial execution, progress and scoped cancellation', flush=True)

        failed = client.enqueue('b0000004')
        client.terminal(failed, 'failed')
        retried = client.call('/api/jobs/' + failed['id'] + '/retry', {}, status=201)['job']
        assert retried['attempt'] == 2 and retried['retry_of'] == failed['id']
        client.terminal(retried, 'completed')
        fail7 = client.enqueue('b0000003')
        assert client.terminal(fail7, 'failed')['exit_code'] == 7
        noisy = client.enqueue('b0000005')
        detail = client.terminal(noisy, 'failed')
        assert detail['exit_code'] == 0 and len(detail['log'].encode()) <= 65539
        # Both graceful stop and forced termination retain queued work.
        interrupted = client.enqueue('b0000007')
        wait_for(lambda: client.job(interrupted)['state'] == 'running')
        pending = client.enqueue('b0000009')
        docker('stop', '-t', '15', simulated)
        assert docker('inspect', '-f', '{{.State.ExitCode}}', simulated).stdout.strip() == '0'
        docker('start', simulated)
        wait_for(lambda: health(url))
        client = Client(url); client.login()
        client.terminal(interrupted, 'interrupted')
        client.terminal(pending, 'completed')
        abrupt = client.enqueue('b0000007')
        wait_for(lambda: client.job(abrupt)['state'] == 'running')
        pending2 = client.enqueue('b0000011')
        docker('kill', simulated)
        docker('start', simulated)
        wait_for(lambda: health(url))
        client = Client(url); client.login()
        client.terminal(abrupt, 'interrupted')
        client.terminal(pending2, 'completed')
        # A resistant process tree must be killed before unrelated work starts.
        stubborn = client.enqueue('b0000006')
        wait_for(lambda: (output / 'fixture/b0000006-child-pid').exists())
        after = client.enqueue('b0000012')
        client.call('/api/jobs/' + stubborn['id'] + '/cancel', {})
        client.terminal(stubborn, 'cancelled', seconds=12)
        heartbeat = (output / 'fixture/b0000006-heartbeat').read_text()
        client.terminal(after, 'completed')
        time.sleep(.3)
        assert (output / 'fixture/b0000006-heartbeat').read_text() == heartbeat
        assert health(url)
        # Another application cannot own the same profile, even with a different state DB.
        duplicate, _ = run(output, '-e', 'WEB_STATE=/output/other-state')
        wait_for(lambda: docker('inspect', '-f', '{{.State.Running}}', duplicate).stdout.strip() == 'false')
        assert 'Another worker' in docker('logs', duplicate).stdout
        assert (profile / 'options').read_text() == 'expiry 999999\n'
        assert (profile / 'pvr/Existing search').read_text() == 'search0 Fixture\ntype tv\n'
        assert (profile / 'download_history').read_text().startswith('b0999999|Existing recording|')
        assert (output / '.get_iplayer-web/queue.sqlite3').stat().st_uid == 12345
        assert (output / '.get_iplayer-web/queue.sqlite3').stat().st_mode & 0o777 == 0o600
        print('PASS SIMULATED: failure recovery, retries, bounded logs, graceful/abrupt restart persistence, forced child cancellation, exclusive ownership, history compatibility', flush=True)
    finally:
        for name in containers:
            docker('rm', '-f', '-v', name, check=False)
        docker('network', 'rm', network, check=False)
print('All isolated API/container tests passed. No live BBC requests were made. Test containers, network and data removed.')

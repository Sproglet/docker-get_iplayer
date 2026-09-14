#!/usr/bin/env python3
"""Single-owner durable worker and authenticated, same-origin web application."""
import codecs
import datetime as dt
import fcntl
import hashlib
import hmac
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

from engine import Library, command, parse_pid
from store import Conflict, LOG_LIMIT, Store

HERE = Path(__file__).parent

def log(message):
    print(f'{dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")} {message}', flush=True)

def due(now):
    if now.minute == 0:
        return 'pvr'
    if now.minute == 15 and now.hour % 4 == 0:
        return 'refresh'
    return None

def schedule(store, now):
    kind = due(now)
    if kind:
        occurrence = f'{kind}:{now:%Y-%m-%dT%H:%M}Z'
        return store.enqueue(kind, title='Scheduled PVR recordings' if kind == 'pvr' else 'Scheduled catalogue refresh',
                             source='schedule', occurrence=occurrence)
    return None

def signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass

def clean_group(process, grace=5):
    signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()  # Reap the leader so that its zombie does not hold the group open.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    signal_group(process, signal.SIGKILL)
    process.wait()

class Application:
    def __init__(self):
        self.stop = threading.Event()
        self.profile = Path(os.environ['GETIPLAYER_PROFILE'])
        self.profile.mkdir(parents=True, exist_ok=True)
        output = Path(os.environ['GETIPLAYER_OUTPUT'])
        for path in [output, self.profile, *self.profile.rglob('*')]:
            if not os.access(path, os.R_OK | os.W_OK) or (path.is_dir() and not os.access(path, os.X_OK)):
                raise PermissionError(f'{path} is not readable/writable by UID {os.getuid()} GID {os.getgid()}; correct its host permissions')
        # The foundation lock now covers the entire application lifetime. It also
        # excludes an old wrapper, or another app configured with a different DB.
        self.owner = (self.profile / '.scheduled.lock').open('a')
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another worker or foundation job owns this profile. Stop it before starting this application.')
        self.store = Store(os.environ.get('WEB_STATE', str(output / '.get_iplayer-web')))
        self.library = Library(self.profile)
        token_file = self.store.directory / 'access-token'
        configured = os.environ.get('WEB_TOKEN', '')
        if configured and len(configured) < 24:
            raise ValueError('WEB_TOKEN must contain at least 24 characters')
        if configured:
            self.token = configured
        else:
            if not token_file.exists():
                fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(secrets.token_urlsafe(32) + '\n')
            self.token = token_file.read_text().strip()
            if len(self.token) < 24:
                raise ValueError(f'{token_file} is invalid; provide a valid WEB_TOKEN or restore the token file')
        self.sessions, self.login_attempts = {}, {}
        self.session_lock = threading.Lock()
        base = urlparse(os.environ.get('BASEURL', ''))
        self.prefix = base.path.rstrip('/')
        if self.prefix and not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)+', self.prefix):
            raise ValueError('BASEURL path must contain simple slash-separated letters, numbers, underscores or hyphens')
        self.secure_cookie = base.scheme == 'https'
        self.port = int(os.environ['PORT'])
        if not 1024 <= self.port <= 65535:
            raise ValueError('PORT must be between 1024 and 65535')
        recovered = self.store.recover()
        log(f'queue recovered: {recovered} interrupted job(s); queued jobs retained')
        subprocess.run(['/usr/local/bin/get_iplayer', '-V'], check=True, timeout=10)
        self.heartbeat = time.monotonic()

    def login(self, token, address):
        with self.session_lock:
            now = time.time()
            self.login_attempts = {key: value for key, value in self.login_attempts.items() if value[1] > now - 60}
            count, _ = self.login_attempts.get(address, (0, now))
            if count >= 10:
                raise Conflict('Too many sign-in attempts. Wait one minute and try again.')
            if not hmac.compare_digest(hashlib.sha256(token.encode()).digest(), hashlib.sha256(self.token.encode()).digest()):
                self.login_attempts[address] = (count + 1, now)
                return None
            self.login_attempts.pop(address, None)
            self.sessions = {key: value for key, value in self.sessions.items() if value['expires'] > now}
            if len(self.sessions) >= 100:
                self.sessions.pop(next(iter(self.sessions)))
            session = {'csrf': secrets.token_urlsafe(32), 'expires': now + 43200}
            key = secrets.token_urlsafe(32)
            self.sessions[key] = session
            return key, session

    def session(self, key):
        with self.session_lock:
            session = self.sessions.get(key)
            return session if session and session['expires'] > time.time() else None

    def work(self):
        active = None
        # Do not replay missed wall-clock occurrences on startup; persisted jobs resume.
        previous = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        try:
            while not self.stop.is_set():
                self.heartbeat = time.monotonic()
                now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
                if now != previous:
                    previous = now
                    added = schedule(self.store, now)
                    if added:
                        log(f'schedule job={added[0]["id"]} {"queued" if added[1] else "coalesced"}')
                if active is None:
                    job = self.store.claim()
                    if job:
                        try:
                            process = subprocess.Popen(command(job), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, pass_fds=(self.owner.fileno(),))
                            # Assign immediately so even a DB failure cannot orphan a download.
                            active = {'job': job, 'process': process, 'decoder': codecs.getincrementaldecoder('utf-8')('replace'),
                                      'tail': '', 'pending': '', 'progress': None, 'error': False,
                                      'deadline': None, 'last_save': 0, 'message': 'Connecting to the recording service…'}
                            os.set_blocking(process.stdout.fileno(), False)
                            self.store.update(job['id'], pid=process.pid)
                            log(f'job={job["id"]} kind={job["kind"]} running pid={process.pid}')
                        except Exception as exc:
                            if active:
                                clean_group(active['process'])
                                active['process'].stdout.close()
                                active = None
                            self.store.finish(job['id'], 'failed', None, f'Could not start the engine: {exc}. Check server logs and retry.')
                if active:
                    self.read_output(active)
                    job_id = active['job']['id']
                    current = self.store.get(job_id)
                    process = active['process']
                    if current['cancel_requested'] and active['deadline'] is None:
                        signal_group(process, signal.SIGTERM)
                        active['deadline'] = time.monotonic() + 5
                        log(f'job={job_id} cancellation requested')
                    if active['deadline'] is not None and time.monotonic() >= active['deadline']:
                        signal_group(process, signal.SIGKILL)
                    if process.poll() is not None:
                        clean_group(process, grace=1)
                        self.read_output(active, final=True)
                        state = 'failed' if process.returncode or active['error'] else 'completed'
                        message = ('The engine reported an error. Open the log, check connectivity and your saved options, then retry.' if state == 'failed'
                                   else 'Engine finished. Check history for recordings; already recorded programmes may have been skipped.')
                        self.store.finish(job_id, state, process.returncode, message)
                        log(f'job={job_id} state={self.store.get(job_id)["state"]} exit={process.returncode}')
                        process.stdout.close()
                        active = None
                self.stop.wait(.1)
        finally:
            if active:
                clean_group(active['process'])
                self.read_output(active, final=True)
                self.store.finish(active['job']['id'], 'interrupted', active['process'].returncode,
                                  'Application stopped during this job. Partial files remain; review the log and retry when ready.')
                active['process'].stdout.close()

    def read_output(self, active, final=False):
        # Bounded reads keep cancellation responsive even for a noisy child.
        chunks = []
        for _ in range(16):
            try:
                chunk = os.read(active['process'].stdout.fileno(), 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        text = active['decoder'].decode(b''.join(chunks), final=final).replace('\r', '\n')
        if text or final:
            active['tail'] = (active['tail'] + text).encode('utf-8')[-LOG_LIMIT:].decode('utf-8', 'replace')
            active['pending'] += text
            lines = active['pending'].split('\n')
            active['pending'] = lines.pop()[-4096:]
            if final and active['pending']:
                lines.append(active['pending'])
                active['pending'] = ''
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                log(f'job={active["job"]["id"]} {line[:2048]}')
                active['message'] = line[-400:]
                active['error'] |= bool(re.search(r'\bERROR:', line))
                progress = re.search(r'(?<!\d)(\d{1,3}(?:\.\d+)?)%', line)
                if progress:
                    active['progress'] = min(100, float(progress.group(1)))
        if final or time.monotonic() - active['last_save'] > .5:
            self.store.update(active['job']['id'], log=active['tail'], progress=active['progress'], message=active['message'])
            active['last_save'] = time.monotonic()

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class Handler(BaseHTTPRequestHandler):
    server_version = 'Recorder'

    @property
    def app(self):
        return self.server.app

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        # Never log passwords, cookies, request bodies or query strings.
        if len(args) > 1 and str(args[1]).startswith('5'):
            log('HTTP server error: ' + str(args[1]))

    def send(self, status, body, content='application/json; charset=utf-8', cookie=None):
        if content.startswith('application/json'):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def session_key(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
            return jar['recorder_session'].value if 'recorder_session' in jar else ''
        except cookies.CookieError:
            return ''

    def cookie(self, value, age=43200):
        return f'recorder_session={value}; HttpOnly; SameSite=Strict; Path={self.app.prefix or "/"}; Max-Age={age}' + ('; Secure' if self.app.secure_cookie else '')

    def route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if self.app.prefix and path.startswith(self.app.prefix + '/'):
            path = path[len(self.app.prefix):]
        elif self.app.prefix and path == self.app.prefix:
            path = '/'
        elif self.app.prefix and path not in ('/health', '/'):
            return '/missing', {}
        return path, parse_qs(parsed.query)

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, mutation):
        try:
            self.handle_request(mutation)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except KeyError:
            self.send(404, {'error': 'This job no longer exists. Refresh the queue.'})
        except Conflict as exc:
            self.send(409, {'error': str(exc)})
        except (ValueError, TypeError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            log(f'HTTP error: {type(exc).__name__}: {exc}')
            self.send(500, {'error': 'The server could not complete this request. Check container logs and retry.'})

    def handle_request(self, mutation):
        path, query = self.route()
        if path == '/health' and not mutation:
            healthy = not self.app.stop.is_set() and time.monotonic() - self.app.heartbeat < 15
            return self.send(200 if healthy else 503, {'ready': healthy})
        # No legacy endpoint executes engine code. POST is deliberately not redirected.
        if path.endswith('.cgi') or any(k in query for k in ('ACTION', 'NEXTPAGE')):
            return self.send(410, '<!doctype html><meta name="viewport" content="width=device-width"><title>Recorder has moved</title><h1>Recording has moved</h1><p>The old recording controls are disabled. Search, queue and manage recordings in the new interface.</p><a href="' + self.app.prefix + '/">Open Recorder</a>', 'text/html; charset=utf-8')
        if not mutation and path in ('/', '/app.js', '/app.css'):
            if path == '/' and self.app.prefix and urlparse(self.path).path == '/':
                self.send_response(302)
                self.send_header('Location', self.app.prefix + '/')
                self.end_headers()
                return
            name = 'index.html' if path == '/' else path[1:]
            types = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'app.css': 'text/css; charset=utf-8'}
            body = (HERE / 'web' / name).read_bytes()
            if name == 'index.html':
                body = body.replace(b'__PREFIX__', self.app.prefix.encode())
            return self.send(200, body, types[name])
        body = {}
        if mutation:
            origin = self.headers.get('Origin')
            if origin and (urlparse(origin).netloc != self.headers.get('Host') or urlparse(origin).scheme not in ('http', 'https')):
                return self.send(403, {'error': 'Cross-site requests are not allowed. Open Recorder directly and try again.'})
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send(403, {'error': 'Cross-site requests are not allowed.'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'Send this request as JSON.'})
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192:
                return self.send(413, {'error': 'The request is empty or too large.'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
        if path == '/api/login' and mutation:
            result = self.app.login(str(body.get('token', '')), self.client_address[0])
            if not result:
                return self.send(401, {'error': 'That access token was not recognised. Check it and try again.'})
            key, session = result
            return self.send(200, {'csrf': session['csrf']}, cookie=self.cookie(key))
        session = self.app.session(self.session_key())
        if not session:
            return self.send(401, {'error': 'Sign in to use Recorder.'})
        if mutation and not hmac.compare_digest(session['csrf'], self.headers.get('X-CSRF-Token', '')):
            return self.send(403, {'error': 'Your session could not be verified. Reload this page and try again.'})
        if path == '/api/session' and not mutation:
            return self.send(200, {'csrf': session['csrf'], 'engine': 'get_iplayer 3.36', 'schedule': 'PVR hourly · Catalogue every 4 hours · UTC'})
        if path == '/api/logout' and mutation:
            with self.app.session_lock:
                self.app.sessions.pop(self.session_key(), None)
            return self.send(200, {'ok': True}, cookie=self.cookie('', 0))
        if path == '/api/events' and not mutation:
            return self.events()
        limit = min(100, max(1, int(query.get('limit', ['40'])[0])))
        offset = min(1000000, max(0, int(query.get('offset', ['0'])[0])))
        search = query.get('q', [''])[0][:200]
        if path == '/api/search' and not mutation:
            return self.send(200, self.app.library.search(search, query.get('type', ['all'])[0], limit, offset))
        if path == '/api/history' and not mutation:
            return self.send(200, self.app.library.history(search, limit, offset))
        if path == '/api/pvr' and not mutation:
            return self.send(200, self.app.library.pvr())
        if path == '/api/jobs' and not mutation:
            return self.send(200, self.app.store.listing(limit, offset))
        if path == '/api/jobs' and mutation:
            kind = body.get('kind')
            target = parse_pid(body.get('pid', '')) if kind == 'download' else ''
            title = str(body.get('title', ''))[:200] or {'pvr': 'Saved PVR recordings', 'refresh': 'Catalogue refresh', 'download': target}.get(kind, '')
            key = self.headers.get('Idempotency-Key')
            if key and not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', key):
                raise ValueError('Invalid request identifier')
            job, created = self.app.store.enqueue(kind, target, title, key=key)
            return self.send(201 if created else 200, {'job': job, 'created': created})
        match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(cancel|retry))?', path)
        if match:
            job_id, action = match.groups()
            job = self.app.store.get(job_id)
            if not job:
                raise KeyError(job_id)
            if not mutation and not action:
                return self.send(200, job)
            if mutation and action == 'cancel':
                return self.send(200, self.app.store.cancel(job_id))
            if mutation and action == 'retry':
                if job['state'] not in ('failed', 'cancelled', 'interrupted'):
                    raise Conflict('Only failed, cancelled or interrupted jobs can be retried')
                new, created = self.app.store.enqueue(job['kind'], job['target'], job['title'], retry_of=job_id,
                    key='retry-' + job_id)
                return self.send(201 if created else 200, {'job': new, 'created': created})
        return self.send(404, {'error': 'This page or action is not available.'})

    def events(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        previous = None
        for _ in range(30):
            if self.app.stop.is_set() or not self.app.session(self.session_key()):
                return
            snapshot = json.dumps(self.app.store.listing(100), ensure_ascii=False)
            self.wfile.write(('data: ' + snapshot + '\n\n' if snapshot != previous else ': heartbeat\n\n').encode())
            self.wfile.flush()
            previous = snapshot
            self.app.stop.wait(1)

def main():
    os.umask(0o002)
    if len(sys.argv) == 3 and sys.argv[1] == '--enqueue':
        store = Store(os.environ.get('WEB_STATE', str(Path(os.environ['GETIPLAYER_OUTPUT']) / '.get_iplayer-web')))
        job, created = store.enqueue(sys.argv[2], title='Operator ' + sys.argv[2], source='operator')
        print(json.dumps({'id': job['id'], 'state': job['state'], 'created': created}))
        return 0
    app = Application()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: app.stop.set())
    server = Server(('0.0.0.0', app.port), Handler)
    server.app = app
    thread = threading.Thread(target=server.serve_forever, name='http', daemon=True)
    thread.start()
    log(f'Recorder ready port={app.port} prefix={app.prefix or "/"} uid={os.getuid()} gid={os.getgid()}; one durable worker, UTC schedules')
    log('Sign in with WEB_TOKEN or the access-token file in the persistent web state directory.')
    try:
        app.work()
    finally:
        app.stop.set()
        server.shutdown()
        server.server_close()
        app.owner.close()
        log('shutdown complete; queued jobs retained')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        log(f'ERROR: {exc}')
        sys.exit(1)

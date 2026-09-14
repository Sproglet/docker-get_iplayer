"""Durable queue, shared by HTTP, the scheduler and the operator CLI."""
import contextlib
import sqlite3
import time
import uuid
from pathlib import Path

ACTIVE = ('queued', 'running')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')
LOG_LIMIT = 65536

class Conflict(ValueError):
    pass

class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'queue.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f'Unsupported queue schema {version}; use a compatible image')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                    title TEXT NOT NULL, source TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed','cancelled','interrupted')),
                    created REAL NOT NULL, started REAL, finished REAL, pid INTEGER,
                    exit_code INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    progress REAL, message TEXT NOT NULL DEFAULT '', log TEXT NOT NULL DEFAULT '',
                    request_key TEXT UNIQUE, retry_of TEXT, attempt INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_target ON jobs(kind,target)
                    WHERE state IN ('queued','running');
                CREATE INDEX IF NOT EXISTS job_order ON jobs(state,created);
                CREATE TABLE IF NOT EXISTS occurrences (
                    occurrence TEXT PRIMARY KEY, job_id TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (request_key TEXT PRIMARY KEY, job_id TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, kind, target='', title='', source='manual', key=None, occurrence=None, retry_of=None):
        if kind not in ('download', 'pvr', 'refresh'):
            raise ValueError('Choose a download, PVR run or catalogue refresh')
        with self.connect(write=True) as db:
            if occurrence:
                row = db.execute('SELECT j.* FROM occurrences o JOIN jobs j ON j.id=o.job_id WHERE occurrence=?', (occurrence,)).fetchone()
                if row:
                    return dict(row), False
            if key:
                row = db.execute('SELECT j.* FROM requests r JOIN jobs j ON j.id=r.job_id WHERE r.request_key=?', (key,)).fetchone()
                if row:
                    if row['kind'] != kind or row['target'] != target:
                        raise Conflict('This request identifier was already used for a different job')
                    return dict(row), False
            row = db.execute("SELECT * FROM jobs WHERE kind=? AND target=? AND state IN ('queued','running')", (kind, target)).fetchone()
            created = False
            if not row:
                if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 500:
                    raise Conflict('The queue is full. Cancel queued jobs or let it finish before adding more')
                job_id = uuid.uuid4().hex
                attempt = 1
                if retry_of:
                    old = db.execute('SELECT attempt FROM jobs WHERE id=?', (retry_of,)).fetchone()
                    attempt = old['attempt'] + 1 if old else 1
                db.execute('INSERT INTO jobs(id,kind,target,title,source,state,created,request_key,retry_of,attempt) VALUES(?,?,?,?,?,\'queued\',?,?,?,?)',
                           (job_id, kind, target, title, source, time.time(), key, retry_of, attempt))
                row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
                created = True
            if key:
                db.execute('INSERT INTO requests VALUES(?,?)', (key, row['id']))
            if occurrence:
                db.execute('INSERT INTO occurrences VALUES(?,?,?)', (occurrence, row['id'], time.time()))
            return dict(row), created

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def listing(self, limit=100, offset=0):
        with self.connect() as db:
            rows = db.execute('''SELECT id,kind,target,title,source,state,created,started,finished,
                exit_code,cancel_requested,progress,message,retry_of,attempt FROM jobs
                ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                CASE WHEN state='queued' THEN created ELSE -created END LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
            counts = {r['state']: r['n'] for r in db.execute('SELECT state,count(*) n FROM jobs GROUP BY state')}
            return {'items': [dict(r) for r in rows], 'counts': counts, 'total': sum(counts.values())}

    def claim(self):
        with self.connect(write=True) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET state='running',started=?,message='Starting the recording engine' WHERE id=?", (time.time(), row['id']))
            return dict(row)

    def update(self, job_id, **values):
        allowed = {'pid', 'progress', 'message', 'log'}
        if not values or not values.keys() <= allowed:
            raise ValueError('Invalid job update')
        with self.connect(write=True) as db:
            db.execute('UPDATE jobs SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))

    def cancel(self, job_id):
        with self.connect(write=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row['state'] == 'queued':
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,message='Cancelled before starting' WHERE id=?", (time.time(), job_id))
            elif row['state'] == 'running':
                db.execute("UPDATE jobs SET cancel_requested=1,message='Stopping this job and its download processes…' WHERE id=?", (job_id,))
        return self.get(job_id)

    def finish(self, job_id, state, code, message):
        if state not in TERMINAL:
            raise ValueError('Invalid final state')
        with self.connect(write=True) as db:
            # Cancellation wins a race with normal completion, until the job is committed.
            row = db.execute('SELECT cancel_requested FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row and row['cancel_requested']:
                state, message = 'cancelled', 'Cancelled. Partial files are retained by the engine.'
            db.execute('UPDATE jobs SET state=?,exit_code=?,finished=?,pid=NULL,message=? WHERE id=?',
                       (state, code, time.time(), message, job_id))
            # Keep all metadata, but only the 200 most recent terminal job log tails.
            db.execute("UPDATE jobs SET log='' WHERE state NOT IN ('queued','running') AND id NOT IN (SELECT id FROM jobs WHERE state NOT IN ('queued','running') ORDER BY finished DESC LIMIT 200)")

    def recover(self):
        with self.connect(write=True) as db:
            count = db.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]
            db.execute("""UPDATE jobs SET state=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'interrupted' END,
                finished=?,pid=NULL,message='The application stopped during this job. Review its log and retry if needed.'
                WHERE state='running'""", (time.time(),))
            return count

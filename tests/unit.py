#!/usr/bin/env python3
"""Offline queue/adapter/schedule tests; no Docker or existing profile access."""
import concurrent.futures
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path('files/usr/local/lib/get_iplayer').resolve()))
from app import due, schedule
from engine import Library, parse_pid, command
from store import Store, Conflict

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'web')
    def tearDown(self):
        self.tmp.cleanup()
    def test_concurrent_enqueue_claim_and_idempotency(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            jobs = list(pool.map(lambda i: self.store.enqueue('download', 'b0123456', key='request-' + str(i)), range(12)))
        self.assertEqual(sum(created for _, created in jobs), 1)
        self.assertEqual(len({job['id'] for job, _ in jobs}), 1)
        first = self.store.claim()
        self.assertIsNone(self.store.claim())
        self.store.finish(first['id'], 'completed', 0, 'done')
        for i in range(12):
            again, created = self.store.enqueue('download', 'b0123456', key='request-' + str(i))
            self.assertFalse(created)
            self.assertEqual(again['id'], first['id'])
        with self.assertRaises(Conflict):
            self.store.enqueue('download', 'b9999999', key='request-0')
    def test_recovery_and_cancel_race(self):
        a, _ = self.store.enqueue('download', 'b0123456')
        b, _ = self.store.enqueue('pvr')
        self.store.claim()
        self.store.cancel(a['id'])
        self.store.finish(a['id'], 'completed', 0, 'done')
        self.assertEqual(self.store.get(a['id'])['state'], 'cancelled')
        self.store.claim()
        c, _ = self.store.enqueue('refresh')
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.get(b['id'])['state'], 'interrupted')
        self.assertEqual(self.store.get(c['id'])['state'], 'queued')
    def test_schedules_coalesce_and_persist(self):
        day = dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc)
        for minute in range(1440):
            now = day + dt.timedelta(minutes=minute)
            expected = 'pvr' if now.minute == 0 else 'refresh' if now.minute == 15 and now.hour % 4 == 0 else None
            self.assertEqual(due(now), expected)
        first = schedule(self.store, day)[0]
        self.assertFalse(schedule(Store(self.root / 'web'), day)[1])
        self.assertEqual(schedule(self.store, day + dt.timedelta(hours=1))[0]['id'], first['id'])
        self.store.claim()
        self.store.finish(first['id'], 'completed', 0, 'done')
        self.assertFalse(schedule(self.store, day)[1])
        self.assertTrue(schedule(self.store, day + dt.timedelta(hours=2))[1])
    def test_bounded_log_retention(self):
        for i in range(202):
            job, _ = self.store.enqueue('download', f'b{i:07d}')
            self.store.update(job['id'], log='output')
            self.store.finish(job['id'], 'completed', 0, 'done')
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE log != ''").fetchone()[0], 200)

class AdapterTests(unittest.TestCase):
    def test_existing_formats_and_no_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            (profile / 'tv.cache').write_text('#pid|name|type|episode|channel|desc\nb0123456|Café & <script>|tv|One|BBC One|A story\ninvalid\n')
            (profile / 'download_history').write_text('b0123456|Café & <script>|One|tv|1700000000|hls|/old/path/file.mp4|default|60|A story|BBC One||||||\n')
            (profile / 'pvr').mkdir()
            (profile / 'pvr/News').write_text('search0 News\ndisable 1\ntype tv\ncommand secret\n')
            before = {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
            lib = Library(profile)
            self.assertEqual(lib.search('café')['total'], 1)
            self.assertTrue(lib.search()['warnings'])
            self.assertEqual(lib.history()['items'][0]['filename'], '/old/path/file.mp4')
            self.assertTrue(lib.pvr()['items'][0]['disabled'])
            self.assertNotIn('command', lib.pvr()['items'][0])
            self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
    def test_pid_validation_and_no_shell(self):
        self.assertEqual(parse_pid('https://www.bbc.co.uk/iplayer/episode/b0123456/example'), 'b0123456')
        self.assertEqual(parse_pid('https://www.bbc.co.uk/sounds/play/m0123456'), 'm0123456')
        for value in ['--refresh', 'b0123456; touch /tmp/no', 'https://example.com/programmes/b0123456', '../b0123456']:
            with self.assertRaises(ValueError):
                parse_pid(value)
        self.assertEqual(command({'kind': 'download', 'target': 'b0123456'})[-3:], ['--pid', 'b0123456', '--get'])

if __name__ == '__main__':
    unittest.main(verbosity=2)

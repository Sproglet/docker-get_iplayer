#!/usr/bin/env python3
"""Simulation only: process trees, progress, failure and history; no BBC access."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

if '-V' in sys.argv:
    print('get_iplayer v3.36 (SIMULATED TEST ENGINE)')
    sys.exit(0)
root = Path(os.environ['GETIPLAYER_OUTPUT'])
profile = Path(os.environ['GETIPLAYER_PROFILE'])
markers = root / 'fixture'
markers.mkdir(exist_ok=True)
kind = 'download' if '--pid' in sys.argv else 'pvr' if '--pvr' in sys.argv else 'refresh'
pid = sys.argv[sys.argv.index('--pid') + 1] if kind == 'download' else kind

def trace(event):
    with (markers / 'trace').open('a') as stream:
        stream.write(json.dumps({'event': event, 'target': pid, 'process': os.getpid(), 'time': time.time()}) + '\n')

trace('start')
(markers / (pid + '-pid')).write_text(str(os.getpid()))
print(f'INFO: Simulated {kind} {pid}', flush=True)
print('12.5% of ~10 MB @ 1 Mb/s ETA: 00:00:10', flush=True)
slow = pid in ('b0000001', 'b0000006', 'b0000007')
child = None
if slow:
    child = subprocess.Popen(['python3', '-c', '''
import os,signal,sys,time
from pathlib import Path
root=Path(sys.argv[1]); pid=sys.argv[2]
def stop(*args):
    (root/(pid+'-child-term')).touch()
    sys.exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN if pid=='b0000006' else stop)
(root/(pid+'-child-pid')).write_text(str(os.getpid()))
while True:
    (root/(pid+'-heartbeat')).write_text(str(time.time()))
    time.sleep(.1)
''', str(markers), pid])

def stop(*args):
    trace('cancel')
    (markers / (pid + '-parent-term')).touch()
    if child:
        child.wait(timeout=3)
    sys.exit(143)

signal.signal(signal.SIGTERM, signal.SIG_IGN if pid == 'b0000006' else stop)
if slow:
    while not (markers / (pid + '-release')).exists():
        time.sleep(.1)
    child.terminate()
    child.wait(timeout=3)
else:
    time.sleep(.35)
if pid == 'b0000003':
    print('ERROR: Simulated BBC connection failure. Retry later.', flush=True)
    trace('failed')
    sys.exit(7)
if pid == 'b0000004' and not (markers / 'failed-once').exists():
    (markers / 'failed-once').touch()
    print('ERROR: Simulated temporary failure', flush=True)
    trace('failed')
    sys.exit(4)
if pid == 'b0000005':
    for _ in range(100):
        print('Test diagnostic ' + 'x' * 1000, flush=True)
    print('ERROR: Simulated zero-exit engine error without newline', end='', flush=True)
    trace('failed')
    sys.exit(0)
if kind == 'refresh':
    (profile / 'tv.cache').write_text('#index|type|name|episode|seriesnum|episodenum|pid|channel|available|expires|duration|desc|web|thumbnail|timeadded\n1|tv|The Quiet Coast|The northern shore|1|1|b0000002|BBC Two|||3600|A journey through the wild places along our coastline.|||1700000000\n2|tv|City After Dark|A different rhythm|1|2|b0000004|BBC Four|||1800|Stories from the people who keep the city moving after sunset.|||1700000000\n3|tv|A Long Journey|Across the hills|1|1|b0000001|BBC One|||3600|Explore the landscape, one unhurried step at a time.|||1700000000\n4|tv|Failure Fixture|Unavailable|1|1|b0000003|BBC One|||100|A deliberate failure for validation.|||1700000000\n')
    (profile / 'radio.cache').write_text('#pid|name|type|episode|channel|desc\nm0000002|Late Night Notes|radio|Jazz after hours|BBC Radio 3|An evening of surprising sounds and familiar favourites.\n')
if kind == 'download':
    with (profile / 'download_history').open('a') as stream:
        stream.write(f'{pid}|Simulated recording {pid}|Episode|tv|{int(time.time())}|test|/output/{pid}.mp4|default|60|Simulation|Test Channel||||||\n')
    (root / (pid + '.mp4')).write_text('SIMULATED TEST FILE - NOT A RECORDING')
print('100.0% complete', flush=True)
trace('end')

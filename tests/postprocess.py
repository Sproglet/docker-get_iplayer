#!/usr/bin/env python3
"""Real ffmpeg/ffprobe conversion checks using only temporary synthetic media."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


def inside():
    output = Path('/output')
    work = output / 'cases'
    work.mkdir()
    template = work / 'template.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=25',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(template)], check=True)
    def source(name):
        path = work / (name + '.mp4')
        shutil.copyfile(template, path)
        return path
    def convert(path, **kwargs):
        return subprocess.run(['convert-to-mkv', str(path)], capture_output=True, text=True, timeout=15, **kwargs)
    profile = output / '.get_iplayer'
    profile.mkdir()
    first = source("Programme with spaces $dollars and 'quotes'")
    history = f'b0123456|Programme|Episode|tv|1700000000|test|{first}|default|1|||||||||\n'
    (profile / 'download_history').write_text(history)
    result = subprocess.run(['convert-to-mkv', '--pid', 'b0123456'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    target = first.with_suffix('.mkv')
    assert target.exists() and not first.exists()
    assert (profile / 'download_history').read_text() == history
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)], text=True))
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
    assert (video['codec_name'], video['width'], video['height']) == ('h264', 320, 180)
    assert audio['codec_name'] == 'eac3'
    assert target.stat().st_uid == os.getuid() and target.stat().st_mode & 0o777 == 0o664
    collision = source('collision')
    collision.with_suffix('.mkv').write_bytes(b'existing recording')
    before = collision.read_bytes()
    assert convert(collision).returncode == 1
    assert collision.read_bytes() == before and collision.with_suffix('.mkv').read_bytes() == b'existing recording'
    invalid = work / 'invalid.mp4'
    invalid.write_bytes(b'not media')
    assert convert(invalid).returncode == 1 and invalid.read_bytes() == b'not media'
    link = work / 'symlink.mp4'
    link.symlink_to(template)
    assert convert(link).returncode == 1 and link.is_symlink()
    fakebin = work / 'fakebin'
    fakebin.mkdir()
    fakeffmpeg = fakebin / 'ffmpeg'
    fakeffmpeg.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nPath(sys.argv[-1]).write_bytes(b"partial")\nsys.exit(23)\n')
    fakeffmpeg.chmod(0o755)
    env = dict(os.environ, PATH=str(fakebin) + ':' + os.environ['PATH'])
    failed = source('failed')
    assert convert(failed, env=env).returncode == 1
    assert failed.exists() and not failed.with_suffix('.mkv').exists()
    assert not list(work.glob('.get-iplayer-mkv-*.partial'))
    fakeffmpeg.write_text('#!/usr/bin/env python3\nimport os,sys,time\nfrom pathlib import Path\nPath(sys.argv[-1]).write_bytes(b"partial")\nPath("/output/child-pid").write_text(str(os.getpid()))\ntime.sleep(300)\n')
    cancelled = source('cancelled')
    process = subprocess.Popen(['convert-to-mkv', str(cancelled)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if (output / 'child-pid').exists():
                break
            time.sleep(.05)
        assert (output / 'child-pid').exists()
        child = int((output / 'child-pid').read_text())
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 1 and 'cancelled' in stderr.lower(), stdout + stderr
        assert cancelled.exists() and not cancelled.with_suffix('.mkv').exists()
        assert not list(work.glob('.get-iplayer-mkv-*.partial'))
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('Conversion child survived cancellation')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    print('PASS MKV: real copied H.264/E-AC-3, spaced/quoted paths via history, permissions, no overwrite, invalid media, symlinks, failure/cancellation source retention and partial cleanup')


def main():
    if '--inside' in sys.argv:
        inside()
        return
    image = os.environ.get('TEST_IMAGE', 'docker-get_iplayer:quality-test')
    name = 'gip-postprocess-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='gip-postprocess-') as temporary:
        try:
            subprocess.run(['docker', 'run', '--rm', '--name', name, '--network', 'none',
                            '-e', 'PUID=12345', '-e', 'PGID=12346', '-v', f'{temporary}:/output',
                            '-v', f'{Path(__file__).resolve()}:/test.py:ro', image, 'python3', '/test.py', '--inside'],
                           check=True, timeout=60)
        finally:
            subprocess.run(['docker', 'rm', '-f', '-v', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()

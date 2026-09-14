#!/usr/bin/env python3
"""Verify actual bundled-engine quality defaults and override precedence offline."""
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

image = os.environ.get('TEST_IMAGE', 'docker-get_iplayer:quality-test')
containers = []
with tempfile.TemporaryDirectory(prefix='gip-quality-') as directory:
    root = Path(directory)
    root.chmod(0o755)
    def run(name, options='', pvr=False, extra=()):
        output = root / name
        profile = output / '.get_iplayer'
        profile.mkdir(parents=True)
        (profile / 'options').write_text(options)
        if pvr:
            (profile / 'pvr').mkdir()
            (profile / 'pvr/Keep SD').write_text('pid b0123456\ntvmode sd\ntype tv\n')
            (profile / 'download_history').write_text('b0123456|Already recorded|Episode|tv|1700000000|test|/output/existing.mp4|default|60|Fixture|BBC One||||||\n')
            (output / 'existing.mp4').write_text('Offline fixture')
        before = {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
        for path in [output, *output.rglob('*')]:
            os.chown(path, 12345, 12346)
        container = 'gip-quality-' + uuid.uuid4().hex[:10]
        containers.append(container)
        args = ['docker', 'run', '--rm', '--name', container, '--network', 'none',
                '-e', 'PUID=12345', '-e', 'PGID=12346', '-v', f'{output}:/output', image,
                'get_iplayer', '--nocopyright', *(['--pvr', '--verbose'] if pvr else ['--show-options']), *extra]
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
        assert result.returncode == 0, result.stdout
        assert all(p.read_bytes() == content for p, content in before.items()), 'Existing settings/history changed'
        return result.stdout
    try:
        default = run('default').split('Options Used:', 1)[1]
        assert 'modes = fhd,hd,sd,high,std' in default
        assert 'tvmode =' not in default and 'radiomode =' not in default
        assert 'fileprefix = <nameshort><.senum><.episodeshort><.quality.>iPlayer.Web-DL-Get_iPlayer' in default
        assert 'type = tv,radio' in default and 'thumbsize = 512' in default
        assert 'commandtv = /usr/local/bin/convert-to-mkv' in default
        combined = run('combined', 'quality sd,low\n').split('Options Used:', 1)[1]
        assert 'modes = sd,low' in combined and 'fhd' not in combined
        specific = run('specific', 'tv-quality hd\nradio-quality std\ntagformattitle My custom title\ncommandtv /bin/true\n').split('Options Used:', 1)[1]
        assert 'tvmode = hd' in specific and 'radiomode = std' in specific
        assert 'tag_formattitle = My custom title' in specific and 'commandtv = /bin/true' in specific
        command_line = run('cli', 'quality sd,low\n', extra=('--tv-quality', 'fhd')).split('Options Used:', 1)[1]
        assert 'tvmode = fhd' in command_line
        pvr = run('pvr', pvr=True)
        assert '\ttvmode = sd' in pvr.split('Current options:', 1)[1], pvr
        print('PASS actual engine offline: 1080p-first image default, combined/TV/radio/CLI/PVR override precedence, unchanged existing settings/history')
    finally:
        for name in containers:
            subprocess.run(['docker', 'rm', '-f', '-v', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

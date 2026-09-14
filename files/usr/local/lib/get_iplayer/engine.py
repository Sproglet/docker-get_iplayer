"""get_iplayer v3.36 adapter. Reads existing formats; never writes profile files."""
from pathlib import Path
import re
import time
from urllib.parse import urlparse

CACHE_FIELDS = 'index type name episode seriesnum episodenum pid channel available expires duration desc web thumbnail timeadded'.split()
HISTORY_FIELDS = 'pid name episode type timeadded mode filename versions duration desc channel categories thumbnail guidance web episodenum seriesnum'.split()
PID = re.compile(r'[a-z0-9]{8,16}\Z')

def parse_pid(value):
    value = str(value).strip()
    if value.startswith(('https://', 'http://')):
        url = urlparse(value)
        if url.hostname not in ('bbc.co.uk', 'www.bbc.co.uk', 'bbc.com', 'www.bbc.com'):
            raise ValueError('Use a BBC programme link or its 8–16 character programme ID')
        match = re.match(r'^/(?:iplayer/episode|sounds/play|programmes)/([a-z0-9]{8,16})(?:/|$)', url.path)
        value = match.group(1) if match else ''
    if not PID.fullmatch(value):
        raise ValueError('Enter a BBC episode link or an 8–16 character programme ID, such as b0123456')
    return value

def command(job):
    base = ['/usr/local/bin/get_iplayer', '--nocopyright', '--logprogress']
    if job['kind'] == 'download':
        return base + ['--pid', parse_pid(job['target']), '--get']
    if job['kind'] == 'refresh':
        return base + ['--refresh', '--type=tv,radio']
    if job['kind'] == 'pvr':
        return base + ['--pvr']
    raise ValueError('Unknown job kind')

def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0

class Library:
    def __init__(self, profile):
        self.profile = Path(profile)

    def read(self, path, fields, header=False):
        rows, warnings = [], []
        try:
            with path.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    line = line.rstrip('\r\n')
                    if header and line.startswith('#') and line.count('|') >= 3:
                        fields = line[1:].rstrip('|').split('|')
                        if not {'pid', 'name', 'type'} <= set(fields):
                            return [], [f'{path.name} has an unsupported header. Refresh the catalogue.']
                        continue
                    if not line or line[0].isspace() or line.startswith('#'):
                        continue
                    row = dict(zip(fields, line.split('|')))
                    if not PID.fullmatch(row.get('pid', '')):
                        warnings.append(f'{path.name}: ignored an incomplete or invalid row')
                        continue
                    rows.append(row)
        except FileNotFoundError:
            pass
        except OSError as exc:
            warnings.append(f'Cannot read {path.name}: {exc.strerror}. Check profile permissions.')
        return rows, list(dict.fromkeys(warnings))

    def search(self, query='', kind='all', limit=40, offset=0):
        if kind not in ('all', 'tv', 'radio'):
            raise ValueError('Choose TV, radio or all programmes')
        rows, warnings, caches = [], [], []
        for media in ('tv', 'radio'):
            path = self.profile / f'{media}.cache'
            try:
                modified = path.stat().st_mtime
            except FileNotFoundError:
                modified = None
            caches.append({'type': media, 'updated': modified})
            if kind in ('all', media):
                found, errors = self.read(path, CACHE_FIELDS, header=True)
                rows.extend(found)
                warnings.extend(errors)
        terms = query.casefold().split()
        matches = [row for row in rows if all(term in ' '.join(row.get(k, '') for k in ('name', 'episode', 'desc', 'channel', 'pid')).casefold() for term in terms)]
        matches.sort(key=lambda row: (row.get('name', '').casefold(), row.get('episode', ''), row['pid']))
        return {'items': matches[offset:offset + limit], 'total': len(matches), 'caches': caches, 'warnings': warnings}

    def history(self, query='', limit=50, offset=0):
        rows, warnings = self.read(self.profile / 'download_history', HISTORY_FIELDS)
        rows = [row for row in rows if query.casefold() in ' '.join(row.get(k, '') for k in ('name', 'episode', 'pid')).casefold()]
        rows.sort(key=lambda row: number(row.get('timeadded')), reverse=True)
        return {'items': rows[offset:offset + limit], 'total': len(rows), 'warnings': warnings}

    def pvr(self):
        items, warnings = [], []
        for path in sorted((self.profile / 'pvr').glob('*')):
            if not path.is_file():
                continue
            try:
                options = {}
                for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2 and not parts[0].startswith('#'):
                        options[parts[0]] = parts[1]
                items.append({'name': path.name, 'disabled': options.get('disable', '0') not in ('0', ''),
                              'type': options.get('type', 'tv'),
                              'search': options.get('pid') or ' · '.join(v for k, v in options.items() if k.startswith('search')) or 'Saved engine search'})
            except OSError:
                warnings.append(f'Cannot read saved search {path.name}. Check profile permissions.')
        return {'items': items, 'warnings': warnings}

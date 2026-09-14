# Recorder · docker-get_iplayer

A responsive programme library and recording queue around the bundled get_iplayer
engine. Search TV/radio, queue a BBC episode link, follow progress, cancel a job,
and browse existing recording history and saved PVR searches.

All application downloads, hourly PVR runs and catalogue refreshes use **one
durable queue and one worker**. The legacy CGI is no longer served. Its old
routes explain that recording has moved and link to the replacement; legacy POST
actions cannot start recordings.

The unchanged engine is get_iplayer **v3.36**, commit
`df1bc7f3b86f2e994f9a3e1140a50cb7f5d9650f`, under `vendor/get_iplayer/`.
[Provenance](vendor/get_iplayer.UPSTREAM.json) ·
[GPL licence](vendor/get_iplayer/LICENSE.txt) ·
[Contributors](vendor/get_iplayer/CONTRIBUTORS).
There is no runtime self-update and no Git submodule.

## Launch an isolated preview

From the repository root:

```sh
./scripts/preview.sh
```

This builds a separate `docker-get_iplayer:preview` image, creates a new temporary
output directory and a dedicated Docker network, and starts a uniquely named
container on an available **loopback-only port**. It prints the URL, access token,
output path and exact cleanup commands. It never mounts your existing installation
or recordings. The fresh catalogue is empty: click **Refresh catalogue**, or paste
a BBC episode link. Preview downloads remain in its temporary output directory.
Use `PUID=... PGID=... ./scripts/preview.sh` to override its default 1000:100 owner.

## Normal operation

Only after reviewing the preview, use an existing writable host output directory:

```sh
PUID=$(id -u) PGID=$(id -g) OUTPUT_DIR=/path/to/recordings docker compose up -d --build
```

Use a non-root PUID. Compose defaults to `http://localhost:1935` and passes UID/GID
as runtime variables. To obtain the generated sign-in token:

```sh
docker compose exec get_iplayer cat /output/.get_iplayer-web/access-token
```

Alternatively set `WEB_TOKEN` to a randomly generated secret of at least 24
characters. Sign-in uses a 12-hour HttpOnly, SameSite session; writes require a
per-session CSRF token. Sessions expire on application restart. No third-party
scripts, fonts or frontend build service are required.

For remote use, put an HTTPS reverse proxy in front of the loopback port. Set
`BASEURL=https://host.example/recorder` for a prefix deployment; **preserve that
prefix and the original Host header** when proxying requests. Disable proxy
buffering for `/api/events` and allow long-lived responses. HTTPS BASEURL enables
Secure cookies. Do not publish the Python HTTP listener directly to the internet.
This milestone is a single-user application, not a multi-user account system.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUID`, `PGID` | `1000`, `100` | Owner of application-created files; PUID must be non-root |
| `GETIPLAYER_OUTPUT` | `/output` | Recording directory |
| `GETIPLAYER_PROFILE` | `/output/.get_iplayer` | Existing engine configuration, caches, PVR and history |
| `WEB_STATE` | `$GETIPLAYER_OUTPUT/.get_iplayer-web` | SQLite queue and generated access token |
| `WEB_TOKEN` | generated and persisted | Optional access-token override, minimum 24 characters |
| `PORT` | `1935` | Internal HTTP port (1024–65535) |
| `BASEURL` | empty | Optional external URL and path prefix |
| `WEB_PORT` | `1935` | Compose's loopback host port |

Changing only one engine path does not change the other. Persist and back up both
the engine profile and web state. SQLite and the owner lock require local storage
with working filesystem locks; network filesystems have not been validated.

## Recording quality

The image now prefers **1080p TV**, falling back to 720p and lower resolutions
when necessary (fhd,hd,sd). Radio prefers high,std, matching `options.sample`. This
is an image-level default; explicit quality preferences in an existing profile,
preset or saved PVR search still take precedence. The bundled engine is unchanged.

Upstream get_iplayer defaults to a maximum of 720p unless 1080p is requested.
Not every programme provides a working 1080p stream. See the
[upstream quality documentation](https://github.com/get-iplayer/get_iplayer/wiki/quality).
The job log identifies the selected stream mode (`fhd` versus `hd`); this release
does not yet have per-job quality controls or automatic upgrades of old recordings.
Rebuild/recreate your preview to pick up the new default. Existing running
containers and already-recorded files are not automatically changed.

## Preferred recording settings and MKV output

[`options.sample`](options.sample) is now the single source of the image's defaults:
Docker copies it to `/etc/get_iplayer/options`. It also sets the preferred filename
pattern, excludes local/regional channels from refresh, preserves spaces, applies
the title tag and downloads 512-pixel thumbnails. Existing profile and PVR settings
are not overwritten. The original malformed `type` line has been corrected.

TV downloads run `convert-to-mkv` after the engine finishes. The helper copies the
video without re-encoding and transcodes audio to **E-AC-3**, matching the sample's
intent. It preserves text subtitles as SRT when present, validates streams, and
publishes a complete MKV atomically before removing the original MP4. It refuses
to overwrite an existing MKV. Failure or cancellation before publication retains
the MP4 and removes its temporary conversion output. A force kill can leave a
hidden `.get-iplayer-mkv-*.partial` file; the original is retained.

The hook resolves the new filename from history using the PID, so names/paths are
not substituted into shell code. History is written by the engine before the hook
and is deliberately **not rewritten**: its filename can still end in `.mp4` even
though the resulting media file is `.mkv`. The normal duplicate-recording behaviour
is preserved. A conversion failure is reported in the job log; retrying the
recording alone may skip it because it is already in engine history.

After resolving a conversion problem, an administrator can retry just that
retained MP4 while the queue is idle:

```sh
docker compose exec get_iplayer /start convert-to-mkv "/output/retained recording.mp4"
```

A profile/PVR `commandtv` overrides the default converter; set `commandtv /bin/true`
to keep MP4 instead. The sample's shell text attached to `type` and its unquoted
`rm` have been replaced by the tested helper. No notifications are sent by default.

## Queue, schedules and cancellation

* PVR runs every hour, UTC; catalogue refreshes at 00:15, 04:15, 08:15, 12:15,
  16:15 and 20:15. Both go into the same FIFO queue as manual requests.
* Repeated active requests for the same PID or job type coalesce. Scheduled
  occurrences are recorded transactionally and cannot create duplicate work.
  A long-running PVR job covers subsequent occurrences while it remains active.
* No missed wall-clock occurrences are replayed at startup. Already queued jobs
  resume automatically. In-flight jobs become **interrupted**, with an explicit
  **Retry** action. A pending cancellation becomes **cancelled** on recovery.
* States are **queued**, **running**, **completed**, **failed**, **cancelled** and
  **interrupted**. Retry creates a new linked attempt; it does not erase failures.
* Cancelling a queued job prevents it from starting. Cancelling a running job
  signals only that job's process group, including child download tools, and
  escalates after five seconds. Other queued jobs then continue. Partial files
  follow the unchanged engine's resume behaviour.
* Progress reflects the engine's current stream, not a whole-PVR-run percentage.
  A completed engine run can mean an existing recording was skipped or a PVR
  search found nothing. Download history is authoritative for recordings.
* Keep one app per profile. A lifetime lock prevents a second worker or an old
  foundation wrapper from using it concurrently. Raw, administrator-launched
  engine processes are outside the application queue: do not run manual CLI
  downloads against an active profile.

The compatibility helper now **enqueues** instead of executing work:

```sh
docker compose exec get_iplayer /start run-scheduled refresh
docker compose exec get_iplayer /start run-scheduled pvr
docker compose logs -f --tail=100 get_iplayer
```

Engine/lifecycle logs go to Docker. Each job retains its last 64 KiB of output;
only the latest 200 terminal jobs retain logs. Job metadata remains persistent.
Compose rotates Docker logs at 10 MB with three files and allows 20 seconds for
shutdown. The HTTP healthcheck verifies that the worker loop is responsive; it
does not verify BBC availability.

## Compatibility

Keep the existing directory mounted at `/output`. The app reads engine cache,
PVR and history formats without migrating them. Existing options and PVR searches
remain authoritative, including output overrides. Normal engine recording/refresh
operations can update their own caches/history and remove successful `ONCE_` PVR
searches, exactly as before. The UI itself never rewrites those files.

New/empty output directories are initialised with the chosen owner. Existing
recordings are never recursively chowned. Startup reports inaccessible profile
paths and exits. Fix the affected host permissions deliberately. Test migrations
against a **copy** first and preserve old paths where possible. Historical entries
can refer to moved/deleted files; the UI displays history, not a media file browser.

PVR search editing, schedule editing/timezones, playback and UHD are outside this
milestone. Existing saved searches can be viewed and run through the queue.

## Build, validation and updates

```sh
docker build -t docker-get_iplayer:web-test .
python3 tests/unit.py
python3 tests/importer.py
python3 tests/integration.py
```

Container tests require Docker and permission to clean temporary UID-owned files.
They use new data and internal-only Docker networks. They never use Compose's
production mounts. On a Linux Docker host, API tests connect to the isolated
container IP, with no published ports. Browser checks and optional bounded live
verification are documented in [docs/WEB-VALIDATION.md](docs/WEB-VALIDATION.md).

[Architecture and API](docs/ARCHITECTURE.md) ·
[Reviewed upstream import and image updates](docs/UPDATES.md) ·
[Original foundation findings](docs/FOUNDATION.md).
Base images, package versions and AtomicParsley source remain pinned. Update the
image through review and testing; never mutate the bundled engine in a running
container.

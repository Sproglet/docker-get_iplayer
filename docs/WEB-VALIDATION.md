# Interface validation

Final container, browser and preview-launcher checks passed on **14 September 2026**.
The unit/importer checks and the separate live run below passed on 11 September.

The first usable interface is implemented and tested against the unchanged
bundled get_iplayer v3.36 engine. This report separates deterministic simulations
from observed live BBC behaviour. No test used the existing installation's
profile or recording directories.

## Reproduce the offline checks

Run from the repository root on a Linux Docker host:

```sh
docker build -t docker-get_iplayer:web-test .
python3 tests/unit.py
python3 tests/importer.py
python3 tests/integration.py
```

`TEST_IMAGE` can override the application test image. The container tests create
unique containers and an internal-only bridge network, use only temporary data,
and publish no host ports. The host test client reaches each container's private
IP; this transport requires a Linux Docker host rather than Docker Desktop's
isolated VM. Tests require Docker access and permissions to create/clean files
owned by the configured test UID/GID.

Coverage:

* Full bundled engine tree matches the pristine import manifest; installed CLI
  and CGI bytes match their local source. Version, Perl syntax, AtomicParsley
  and ffmpeg probes execute the actual image binaries, without BBC requests.
* Existing engine configuration, PVR searches and historical filenames are read
  without migration or mutation. Normal simulated engine recordings append to
  history while retaining pre-existing entries.
* Six concurrent manual/PVR requests and the compatibility CLI coalesce through
  the same SQLite queue. An actual schedule occurrence is inserted through the
  scheduler function into that same database; engine process traces prove serial
  execution. All 1,440 UTC minute positions are checked independently.
* Queued/running/completed/failed/cancelled/interrupted states, progress parsing,
  cancellation of queued and running jobs, child-process signalling and escalation
  against a SIGTERM-resistant process tree are exercised.
* Both graceful container stop and abrupt `docker kill` preserve queued work;
  in-flight jobs are reconciled explicitly. Retry creates a linked new attempt.
  Subsequent jobs complete after a failed or cancelled predecessor.
* Idempotency keys remain effective after a coalesced job completes. Completion
  racing cancellation is resolved transactionally. Log tails and terminal-log
  retention are bounded, including a simulated zero-exit `ERROR:` diagnostic.
* A second application using the same profile is refused, even with another web
  state directory. Generated access tokens, custom port/URL prefix, sign-out,
  invalid/root-equivalent UID and existing permission failures are covered.
* Authentication, CSRF, foreign-origin and invalid PID requests are rejected.
  Old CGI download routes return 410 and cannot execute a download.
* The upstream importer is tested in a separate temporary Git repository for
  valid imports, unsafe archives/checksum failures and local-patch protection.

The concurrency, failure and restart tests use
[`tests/fixtures/engine.py`](../tests/fixtures/engine.py), explicitly a simulation.
Its “recordings” contain a text marker, not media. These checks establish queue
and process-control behaviour, not BBC availability or programme quality.

## Browser validation

Build the optional, separately pinned Chromium test image and run:

```sh
docker build -t docker-get_iplayer:browser-tests -f tests/browser.Dockerfile tests
python3 tests/browser.py
```

The browser and app run on another internal-only network using the simulated
engine and temporary data. The test image pins Playwright and its dependencies;
it is not included in the application image. Screenshots are saved under
`/tmp/gip-browser-artifacts` (`TEST_ARTIFACTS` overrides that location).

The browser scenario covers sign-in failure/success, the empty catalogue,
refresh/search, enqueueing, completed history, live progress and log details,
cancellation, actionable engine failures, saved PVR searches, sign-out and
390-pixel mobile layout. It asserts no horizontal mobile overflow and no uncaught
JavaScript errors. Desktop and mobile screenshots were inspected. The screenshot
programmes are synthetic test fixtures, not live BBC catalogue evidence.

## Live BBC verification

An opt-in live check was performed on **11 September 2026**, in a separate
container and temporary profile with outbound access:

* Catalogue refresh completed with exit 0: **8,885 TV** and **13,524 radio**
  programmes were added to the engine's caches.
* The application's radio search read the refreshed catalogue successfully.
* A queued sample of **'Round Midnight**, PID **`m0030wf7`**, completed with exit
  0, was converted/tagged as M4A, and appeared as **one history entry**.
* The temporary profile requested a ten-second stop offset. The engine reported
  about **thirteen seconds / 0.54 MB**, consistent with stream-segment boundaries.
* The live container, network and all temporary profile/media data were removed.

To deliberately repeat a bounded live check using a new temporary profile:

```sh
python3 tests/live.py --execute
```

It limits catalogue refresh to 100 seconds and the sample attempt to 90 seconds,
then requests cancellation if needed. It chooses a radio programme from the
actual refreshed cache rather than guessing a PID. The temporary `stop 10` option
is only for this test; normal application recordings are not shortened. Live
programme availability and access vary, so a future run need not produce the same
counts or programme.

## Limits and operational implications

The initial live result establishes one short radio recording; the later short
1080p/MKV check below supplements it. Neither establishes full-length TV,
long-running PVR output, every BBC region, or all possible engine errors. Engine
progress is a current-stream percentage, and a successful PVR job can have no
new matches. History is the authority for recorded items. Conservative `ERROR:`
matching may mark an otherwise partly successful engine run failed; review its
log and history before retrying.

The UI displays existing history rather than proving every historical media file
still exists. Saved-search and schedule editing, playback, multi-user accounts
and UHD are not part of this milestone. Use a local filesystem for SQLite and
locking; NFS/SMB and architectures other than linux/amd64 were not validated.
Run only one application against a profile and do not start raw administrator
CLI downloads outside its queue. Remote access should use an HTTPS reverse proxy.

## Isolated preview

```sh
cd /opt/Docker/docker-get_iplayer
./scripts/preview.sh
```

The script builds a separately tagged preview, creates a temporary output path,
uses a dedicated network and chooses an available loopback port. It prints the
exact URL, sign-in token and cleanup commands. It does not mount, stop, recreate
or change an existing installation. Preview downloads remain solely in the
printed temporary directory until you choose to remove them.

The preview launcher was also exercised: its temporary-only bind mount, random
loopback port, generated-token sign-in, health endpoint and empty history were
verified. That validation preview, its network and temporary data were removed.

## Preferred settings and 1080p/MKV follow-up

On 14 September 2026, the image was updated to take its defaults directly from
`options.sample`. The intended TV preference (fhd,hd,sd) and radio preference
(high,std) are represented as a combined default so that explicit existing
combined or per-type preferences retain precedence. Internal engine option names
are used to avoid its repeated-file alias parsing issue; the engine source remains
unchanged. Filename, channel-exclusion, title, thumbnail and whitespace preferences
are also applied. The malformed type setting and unsafe deletion command were fixed.

Checks for this follow-up:

```sh
docker build -t docker-get_iplayer:quality-test .
python3 tests/quality.py
python3 tests/postprocess.py
TEST_IMAGE=docker-get_iplayer:quality-test python3 tests/integration.py
```

`quality.py` uses the actual engine in network-disabled temporary containers to
check defaults, existing quality/title/custom-command overrides and a saved PVR
search. `postprocess.py` generates one-second synthetic H.264/AAC media and checks
copied H.264/E-AC-3 output, PID-to-history filename resolution, spaces/quotes,
permissions, no overwrite, invalid media, symlink rejection, failed conversion and
cancellation. Conversion failure tests simulate only ffmpeg's failure/long-running
process; successful conversion and media inspection use real ffmpeg/ffprobe.

A separate live queued TV sample of **Asia Specific**, episode **Why NE Asia is
Wary of Immigration**, PID **m0030hh9**, completed with the bundled engine and the
new sample defaults. ffprobe confirmed **1920×1080 H.264 video, E-AC-3 audio and
Matroska format**, duration **11.52 seconds**. The job log confirmed successful
MKV publication and removal of the temporary profile's original MP4. One engine
history entry remained. The temporary live container, network and media were
removed; no existing user recordings were accessed or changed.

The live helper can now select TV as well as radio and reports media format/codecs:

```sh
TEST_IMAGE=docker-get_iplayer:quality-test python3 tests/live.py --execute \
  --programme-type tv --query "BBC News at Ten"
```

The search matches title/episode/description, so it may select a different programme
that contains those terms (as it did in this run). No particular future PID or
1080p availability is guaranteed. Full-length TV/PVR conversion is still untested.

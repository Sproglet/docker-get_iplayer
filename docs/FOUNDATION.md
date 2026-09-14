# Foundation review and validation

Historical record of the first phase. The interface milestone supersedes the
legacy UI and timer described below; see [current architecture](ARCHITECTURE.md)
and [current validation](WEB-VALIDATION.md).

Inspected from the repository root. No AGENTS.md instructions were present in
the repository or its ancestor directories. The initial working tree was clean.
`modernise/cronupdates` and `CronUpdates` both started at
`eb839f39036d4505c8ddf2e2fde63d5136e8f9b3`; no branch switch or production changes
were needed.

## Original behaviour and concrete problems

| Component | Original behaviour | Consequence |
| --- | --- | --- |
| Dockerfile | Alpine latest; GitHub latest-release lookups for both engine and AtomicParsley | Same Git commit could build different code and dependencies; no engine source available for joint development |
| Startup | Network updater, syslog and background cron, then an infinite UI restart loop | Offline startup depended on updater behaviour; crashes could spin; background process lifetime was not supervised |
| Startup engine call | `get_iplayer --verbose` as root before every UI launch | Could refresh/write persistent caches as root and delay UI startup |
| Scheduled jobs | Hourly PVR as PUID/PGID, refresh as root every four hours | Inconsistent ownership; no common exclusion between PVR and refresh |
| Updater | Nightly install into `/usr/local/bin`, process-name parsing to kill CGI | Could erase local engine changes, interrupt downloads and leave a partially updated runtime |
| Logs | PVR redirected to an internal file with filtering, refresh output handled separately | Operational failures hard to see in Docker logs; pipelines obscured engine exit status |
| Compose | UID/GID passed as unused build arguments; published all interfaces; no stop grace or log limits | Runtime ownership settings ineffective; legacy CGI broadly exposed; lifecycle/log policy implicit |

The profile defaults already pointed at `/output/.get_iplayer`; keeping those
paths and engine formats is the compatibility boundary. The inherited
`files/etc/get_iplayer/options` (`expiry 3000`) remains unchanged.

## Implemented scope

The complete, unchanged get_iplayer v3.36 upstream tree is vendored with its
GPL licence and contributor attribution. Provenance records its exact source
commit, archive URL and archive checksum. A per-file manifest verifies the
pristine import. The image copies that tree and links the normal executable
paths to it. No engine functionality was modified.

Alpine is pinned by digest, both APK dependency graphs are version-locked, and
AtomicParsley's pinned, checksummed source archive is retained locally. Builds
make no latest-release lookups. Long-term APK availability and byte-for-byte
build reproducibility remain distinct limitations documented in UPDATES.md.

A small Python standard-library supervisor replaces the cron/syslog/restart-loop
arrangement. It retains hourly PVR/four-hourly refresh times in UTC, reports job
failures, supervises the legacy UI and shuts down process groups. One advisory
profile lock protects all wrapper-scheduled operations, including across
cooperating containers. Occurrences while busy are skipped visibly, with no
unbounded queue or startup catch-up. The daily and startup updater is removed.

Application processes run as the configured non-root UID/GID with umask 002.
Only an empty output directory is initialised; existing profile/recording
contents are not rewritten or recursively chowned. Startup validates profile
write access. Logs go to Docker, Compose supplies rotation and a shutdown grace
period, and the image has a configurable-port TCP healthcheck.

## Validation performed

On linux/amd64 with Docker Engine 29.8.0:

* Successful clean `docker build --no-cache -t docker-get_iplayer:foundation-test .`.
* `docker compose config --quiet` and `git diff --check` passed.
* `python3 tests/integration.py` passed using unique containers with disabled
  networking, no host ports and temporary output mounts.
* Confirmed both installed engine executables match local source SHA-256 values;
  startup reports v3.36. Perl syntax and AtomicParsley/ffmpeg execution passed.
* Actual HTTP history page rendered the synthetic existing recording with a
  custom port/base URL. Engine PVR listing and history search read the fixtures;
  options, PVR files and history remained byte-identical through restart.
* Verified profile UID/GID and directory mode; lock files use group-writable
  mode 0664. Inaccessible existing profiles and invalid/root-equivalent UIDs
  failed rather than changing existing ownership.
* A test-only injected clock triggered an actual scheduled job. A simulated
  downloader and child process received SIGTERM on container stop. A concurrent
  refresh in the same container and another container sharing the profile was
  skipped; after shutdown the lock was reusable. Engine failure status 7 was
  preserved and logged. UI exit status 23 caused container failure, not a loop.
* All 1,440 minute positions in a UTC day matched the intended schedule.
* `python3 tests/importer.py` passed in a temporary Git repository: successful
  verified import, modes/provenance, checksum and unsafe-path rejection, and
  protection against both dirty and committed local engine patches.

Test containers and temporary outputs were removed. The separately tagged test
image remains available for review. No running installation, existing recording
or production profile was inspected or changed. No live BBC search/download,
long-running recording, non-amd64 build or network-filesystem lock semantics were
validated. The current CGI/direct engine commands still bypass the scheduled-job
lock; the proposed queue-backed interface resolves that limitation.

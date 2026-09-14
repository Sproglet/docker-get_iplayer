# Implemented interface milestone

The application now consists of four small Python standard-library modules and a
static, responsive browser UI. There are no new production package dependencies
beyond the foundation's locked Python runtime and no frontend package manager.
The bundled get_iplayer source remains unchanged.

| Component | Responsibility |
| --- | --- |
| `app.py` | Authenticated HTTP API, session/CSRF controls, SSE, UTC scheduler and worker lifecycle |
| `store.py` | SQLite schema v1, transactional queue, deduplication, cancellation requests, recovery and log retention |
| `engine.py` | Fixed-argument engine commands; read-only v3.36 cache/history/PVR adapters |
| `convert-to-mkv` | Post-recording TV hook: copied video/E-AC-3 audio, validated atomic MKV publication, source removal only after success |
| `web/` | Search, direct-link enqueue, live queue/progress/logs, cancellation/retry, history and saved searches |

`files/start` checks identity and initialises only an empty output directory,
then drops to `PUID:PGID` and starts the app under tini. The legacy CGI is no longer
started. Requests to CGI or old ACTION/NEXTPAGE URLs return HTTP 410 with a link
to Recorder, so no browser route bypasses coordination. The upstream executable
remains in the source/image for attribution and engine development, not as a
second service.

## Durable coordination

One process holds the profile's `.scheduled.lock` for its lifetime, excluding
other applications and the old foundation's scheduled wrappers even when their
web-state paths differ. The lock descriptor is inherited by engine subprocesses,
so a surviving engine cannot immediately be overlapped by a replacement worker.

SQLite is in `/output/.get_iplayer-web/queue.sqlite3`, with WAL, FULL synchronous
writes and `BEGIN IMMEDIATE` transactions. `jobs` records identity, PID, type,
target, source, title, timestamps, state, progress, exit status, cancellation,
bounded log text and retry ancestry. A partial unique index prevents duplicate
active targets. `requests` records idempotency keys even for coalesced requests;
`occurrences` records schedule occurrences. `user_version` explicitly identifies
schema v1; unknown future versions fail rather than silently downgrading.

The worker alone claims the oldest queued row and starts an engine session using
an argument array and no shell. HTTP requests and the scheduler only insert jobs
or cancellation flags. Refresh and PVR jobs are also single queue entries. A PVR
entry delegates its existing search behaviour to get_iplayer; that engine run
may contain multiple recordings.

Queued work is durable. On normal shutdown the active process group is stopped
and its job becomes interrupted; following an abrupt container death, startup
reconciles running rows to interrupted (or cancelled if cancellation was already
requested). It never signals a persisted PID, which could now belong to another
process. Interrupted jobs are not automatically retried; the user sees a clear
reason and can create a new attempt. Queued jobs continue automatically.

Cancellation signals only the in-memory active process group, waits five seconds,
then kills remaining descendants before claiming another job. Completion and
cancellation are resolved in a transaction: a cancellation requested before the
terminal update wins. Exit-code failures and explicit `ERROR:` engine messages
are reported as failed; some recordings in a failed PVR run may still succeed.
This conservative message detection is tested, but is not a full semantic parser
of every possible engine diagnostic.

Schedules retain the foundation's UTC boundaries. Each occurrence is durable and
deduplicated; existing active jobs of the same kind coalesce. Missed wall-clock
occurrences are not replayed after downtime. The operator `run-scheduled` command
now inserts into this same queue and can enqueue while the app is stopped.

## Profile compatibility and browser behaviour

Search reads the TV/radio caches, including their header-defined column order;
it never triggers an uncoordinated cache refresh. An empty/stale catalogue can be
refreshed through a queued operation. History displays the original pipe-delimited
engine file. The PVR view reads only a selected set of saved-search fields, not
custom command secrets. The application does not migrate or rewrite engine files.
All actual recording operations still use the same bundled engine and environment.
`options.sample` supplies the image defaults. Its TV hook invokes `convert-to-mkv`
inside the active engine process group, so queue cancellation also stops conversion.
The helper reads the new MP4 path from history by PID and never rewrites history.

The browser uses authenticated SSE snapshots for live jobs, with reconnection,
and fetches a bounded log tail when the details dialog is open. It renders text
through DOM text nodes, never untrusted HTML. Responsive layouts, labelled forms,
keyboard controls, live announcements and actionable error/empty states are part
of the delivered interface. Percentage is explicitly current-stream progress.

Sign-in accepts a configured or persistently generated access token. Random
12-hour sessions are in memory and invalidate on restart. Session cookies are
HttpOnly/SameSite=Strict, with Secure enabled for HTTPS BASEURL. Mutations require
JSON, same-origin checks and a per-session CSRF header. Failed sign-in attempts
are throttled per peer address. Static assets have a restrictive CSP and are all
served locally. This is one trusted user's application; use an HTTPS reverse proxy
for remote access. Prefix deployments must preserve Host and the configured path.

## HTTP contract

All `/api` routes except login require a session. Mutations require
`Content-Type: application/json` and `X-CSRF-Token`. Routes live beneath the
optional BASEURL prefix; `/health` is always available without it.

| Method/path | Payload or result |
| --- | --- |
| `POST /api/login` | `{token}` → session cookie and `{csrf}` |
| `GET /api/session` | CSRF token and engine/schedule metadata |
| `POST /api/logout` | `{}` → invalidate session |
| `GET /api/search?q=&type=all&limit=40&offset=0` | Cache programmes, total, cache timestamps and warnings |
| `GET /api/history?q=&limit=40&offset=0` | Engine history entries, total and warnings |
| `GET /api/pvr` | Existing saved searches, enabled state and warnings |
| `POST /api/jobs` | `{kind: download, pid: BBC_ID_OR_LINK, title?: text}` or `{kind: pvr\|refresh}`; optional Idempotency-Key; 201 new/200 coalesced |
| `GET /api/jobs?limit=40&offset=0` | Jobs, state counts and total; active jobs first |
| `GET /api/jobs/{id}` | Full job details and bounded log tail |
| `POST /api/jobs/{id}/cancel` | `{}` → cancellation state/flag |
| `POST /api/jobs/{id}/retry` | `{}` → idempotent new attempt for failed/cancelled/interrupted job |
| `GET /api/events` | SSE job snapshots and heartbeats, reconnectable |
| `GET /health` | 200 while ready/worker responsive, otherwise 503 |

Pagination is bounded at 100 entries/request. Active queue capacity is 500.
Finished job metadata is retained; log tails are capped at 64 KiB/job and only
200 terminal jobs retain their tails. SQLite/lock semantics on NFS or SMB have
not been validated; use a local persistent filesystem.

## Next milestone

Add saved-search editing and schedule creation/timezone display through the same
API/worker. Specify catch-up policy before adding editable schedules. Then consider
media browsing/playback and richer structured engine progress. Multi-user accounts,
UHD and engine functional changes remain outside this release.

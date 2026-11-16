# get_iplayer PVR Docker image

This Docker image hosts the [get_iplayer](https://github.com/get-iplayer/get_iplayer/wiki) PVR. It installs the latest release and automatically applies `get_iplayer.diff` **only to version 3.36**. Releases 3.37 and later install without this workaround, assuming the fix is included upstream.

The PVR recording feature runs hourly. get_iplayer updates at startup and daily; its showlist refreshes every 4 hours. Updates are patched and syntax-checked before installation.

## Build the patched image

Set your download directory in `docker-compose.yaml`, then run:

```sh
docker compose up -d --build get_iplayer
```

The local image tag keeps the custom build separate from the published unpatched image. If you use another Compose file, set its image to `local/get_iplayer:patched` and its build context to this directory.

The patch supports DASH manifests using `SegmentTemplate@duration` or uniform `SegmentTimeline` durations (with a possibly shorter final segment). Missing durations/timescales and unsupported variable timelines are skipped with a warning.

To run the offline manifest regression tests after building:

```sh
docker run --rm --entrypoint perl -v "$PWD/tests:/tests:ro" local/get_iplayer:patched /tests/dash-manifests.t /usr/local/bin/get_iplayer
```

## Image configuration

The PVR can be accessed by default on port `1935/tcp`. Set the environment variable `PORT` to override.

Downloaded TV & radio files will be placed in the `/output` bind mount.

Environment variables `PUID` and `PGID` can be set to dictate the owner and group of downloaded files (useful if you're putting these files in a shared folder).

If you're running behind a reverse proxy set the environment variable `BASEURL` to the full proxy URL.

## Example invocation

This makes the PVR available on port 1935, and makes downloaded files owned by the current user:

```sh
docker run -d -e PUID=`id -u` -e PGID=`id -g` -p 1935:1935 -v /destination/on/host:/output local/get_iplayer:patched
```
Replace `/destination/on/host` with the path of a folder on the host machine where you would like the downloaded files to be placed.

## get_iplayer configuration

get_iplayer's configuration and cache will be written to the `.get_iplayer` subfolder under the `/output` bind mount. You can set get_iplayer [options](https://github.com/get-iplayer/get_iplayer/wiki/options) in the file `/destination/on/host/.get_iplayer/options`.

Refer to the file [options.sample](https://raw.githubusercontent.com/Sproglet/docker-get_iplayer/master/options.sample) for examples, including how to setup to send a Slack notification after a download.

## Migrating an existing get_iplayer installation

1. Copy your existing `.get_iplayer` folder over to `/destination/on/host/.get_iplayer` .

2. If you have a `.get_iplayer/options` file, open it in a text editor and remove any `output`, `outputradio` and/or `outputtv` statements.

3. Open the file `.get_iplayer/download_history` in a text editor, search for "/old/destination/folder/" and globally replace with "`/output/`".

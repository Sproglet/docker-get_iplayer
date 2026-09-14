#!/bin/sh
# Run from the repository root. Never mounts an existing profile or recording path.
set -eu
[ -f Dockerfile ] && [ -d vendor/get_iplayer ] || { echo 'Run this script from the repository root.' >&2; exit 1; }
docker build -t docker-get_iplayer:preview .
preview_output=$(mktemp -d /tmp/get-iplayer-preview.XXXXXX)
preview_name="get-iplayer-preview-$(date +%s)-$$"
preview_network="$preview_name-net"
preview_started=false
cleanup_failure() {
    if [ "$preview_started" != true ]; then
        docker rm -f -v "$preview_name" >/dev/null 2>&1 || true
        docker network rm "$preview_network" >/dev/null 2>&1 || true
        echo "Preview did not start. Its temporary output is at $preview_output" >&2
    fi
}
trap cleanup_failure EXIT
docker network create "$preview_network" >/dev/null
docker run -d --name "$preview_name" --network "$preview_network" --stop-timeout 20 --label docker-get_iplayer.preview=true \
    -p 0.0.0.0::1935 -e PUID="${PUID:-1000}" -e PGID="${PGID:-100}" \
    --mount "type=bind,source=$preview_output,target=/output" \
    --log-opt max-size=10m --log-opt max-file=3 docker-get_iplayer:preview >/dev/null
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if docker exec "$preview_name" /usr/local/bin/healthcheck >/dev/null 2>&1; then
        preview_started=true
        break
    fi
    sleep 1
done
if [ "$preview_started" != true ]; then
    docker logs "$preview_name" >&2
    exit 1
fi
printf '\nPreview URL: http://%s\n' "$(docker port "$preview_name" 1935/tcp)"
printf 'Access token: '
docker exec "$preview_name" cat /output/.get_iplayer-web/access-token
printf '\nTemporary recordings and state: %s\n' "$preview_output"
printf 'Logs: docker logs -f %s\n' "$preview_name"
printf 'Stop and remove preview: docker rm -f -v %s\n' "$preview_name"
printf 'Remove its network afterwards: docker network rm %s\n' "$preview_network"
printf 'Keep or delete the temporary output directory separately after reviewing its contents.\n'

FROM alpine:latest AS atomicparsleybuild

RUN apk --update --no-cache add cmake g++ jq linux-headers make zlib-dev

RUN wget -qO - `wget -qO - "https://api.github.com/repos/wez/atomicparsley/releases/latest" | jq -r .tarball_url` | tar -zx --strip-components=1 && cmake . && cmake --build . --config Release --target install/strip


FROM alpine:latest
#MAINTAINER Sproglet <losprog@gmail.com>
LABEL org.opencontainers.image.authors="losprog@gmail.com"
LABEL org.opencontainers.image.source=https://github.com/sproglet/get_iplayer
ENV GETIPLAYER_OUTPUT=/output GETIPLAYER_PROFILE=/output/.get_iplayer PUID=1000 PGID=100 PORT=1935 BASEURL=
EXPOSE $PORT
VOLUME "$GETIPLAYER_OUTPUT"

RUN apk --update --no-cache add ffmpeg perl-cgi perl-mojolicious perl-lwp-protocol-https perl-xml-libxml jq logrotate su-exec tini curl patch

COPY --from=atomicparsleybuild /usr/local/bin/AtomicParsley /usr/local/bin/AtomicParsley

COPY get_iplayer.diff /usr/local/share/get_iplayer/get_iplayer.diff
COPY files/usr/local/bin/patch-get-iplayer /usr/local/bin/patch-get-iplayer

RUN set -eu; \
    mkdir /tmp/get_iplayer-src; \
    wget -qO /tmp/latest.json "https://api.github.com/repos/get-iplayer/get_iplayer/releases/latest"; \
    wget -qO /tmp/get_iplayer.tar.gz "$(jq -er .tarball_url /tmp/latest.json)"; \
    tar -xzf /tmp/get_iplayer.tar.gz --strip-components=1 -C /tmp/get_iplayer-src; \
    patch-get-iplayer /tmp/get_iplayer-src; \
    perl -c /tmp/get_iplayer-src/get_iplayer; \
    install -m 755 -t /usr/local/bin /tmp/get_iplayer-src/get_iplayer /tmp/get_iplayer-src/get_iplayer.cgi; \
    rm -rf /tmp/get_iplayer-src /tmp/get_iplayer.tar.gz /tmp/latest.json

COPY files/ /

#ENTRYPOINT ["/sbin/tini", "--"]
#CMD /start
ENTRYPOINT ["/sbin/tini", "/start"]

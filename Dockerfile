# Both stages use the same immutable multi-platform Alpine base.
FROM alpine:3.23.5@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40 AS atomicparsleybuild
COPY docker/build.packages /tmp/packages
RUN xargs apk add --no-cache < /tmp/packages
WORKDIR /src
COPY vendor/atomicparsley/source.tar.gz /tmp/source.tar.gz
RUN echo "b93a937fe21b2e67fb65df7c2ac2e47effba05b30c9af1490f015979976ad5cd  /tmp/source.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target install/strip

FROM alpine:3.23.5@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40
LABEL org.opencontainers.image.authors="losprog@gmail.com" \
      org.opencontainers.image.source="https://github.com/Sproglet/docker-get_iplayer"
ENV GETIPLAYER_OUTPUT=/output GETIPLAYER_PROFILE=/output/.get_iplayer PUID=1000 PGID=100 PORT=1935 BASEURL= PYTHONUNBUFFERED=1
COPY docker/runtime.packages /tmp/packages
RUN xargs apk add --no-cache < /tmp/packages && rm /tmp/packages
COPY --from=atomicparsleybuild /usr/local/bin/AtomicParsley /usr/local/bin/AtomicParsley
COPY vendor/get_iplayer/ /opt/get_iplayer/
COPY vendor/get_iplayer.UPSTREAM.json /opt/get_iplayer.UPSTREAM.json
COPY vendor/atomicparsley/ /usr/local/share/doc/atomicparsley/
RUN chmod -R a-w /opt/get_iplayer \
    && ln -s /opt/get_iplayer/get_iplayer /usr/local/bin/get_iplayer \
    && ln -s /opt/get_iplayer/get_iplayer.cgi /usr/local/bin/get_iplayer.cgi
COPY files/ /
# One reviewed source of recording defaults; user profiles are never rewritten.
COPY options.sample /etc/get_iplayer/options
RUN chmod 755 /start /usr/local/bin/run-scheduled /usr/local/bin/healthcheck /usr/local/lib/get_iplayer/app.py
EXPOSE 1935
VOLUME ["/output"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]
ENTRYPOINT ["/sbin/tini", "--", "/start"]

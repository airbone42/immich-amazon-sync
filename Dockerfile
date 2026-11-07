# alpine:3.24, pinned by multi-arch index digest. Update both tag and digest together.
FROM alpine:3.24@sha256:3f4d6b32e26703c6082839234f43769d5046b3ac7c910033911d1e71d8cd630a

# apk upgrade: the digest pin freezes base packages (zlib, musl, ...), security fixes from the
# Alpine repo only arrive with an upgrade at build time. Build with --pull --no-cache.
RUN apk upgrade --no-cache \
    && apk add --no-cache python3 \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app -s /sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data \
    && chmod 700 /data

WORKDIR /app
COPY src/immich_sync.py entrypoint.sh ./
RUN chmod 0755 entrypoint.sh && chmod 0644 immich_sync.py

ENV STATE_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001
VOLUME ["/data"]

ENTRYPOINT ["./entrypoint.sh"]

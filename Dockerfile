# alpine:3.24, pinned by multi-arch index digest. Update both tag and digest together.
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

RUN apk add --no-cache python3 \
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

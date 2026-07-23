# News Harness V1 — Docker image
FROM python:3.12-alpine

WORKDIR /app

ARG APP_UID=10001
ARG APP_GID=10001

RUN apk add --no-cache ca-certificates chromium nodejs npm \
    && npm install --omit=dev playwright-core \
    && addgroup -g "${APP_GID}" -S news-harness \
    && adduser -u "${APP_UID}" -S -D -H -G news-harness news-harness

# Copy application code
COPY news_harness/ ./news_harness/
COPY pyproject.toml .
COPY configs/ ./configs/
COPY fixtures/ ./fixtures/
COPY schemas/ ./schemas/
COPY web/ ./web/
COPY scripts/docker_entrypoint.sh ./docker_entrypoint.sh
COPY scripts/reddit_headless_export.mjs ./scripts/reddit_headless_export.mjs
COPY scripts/xueqiu_headless_export.mjs ./scripts/xueqiu_headless_export.mjs

RUN chmod +x docker_entrypoint.sh \
    && mkdir -p /app/artifacts/manual_smoke/latest /app/web/data/radar-timeline \
    && chown -R news-harness:news-harness /app/artifacts /app/web/data

# Python app is stdlib-only; Xueqiu headless detail reads use system Chromium.

EXPOSE 8765

USER news-harness:news-harness

ENTRYPOINT ["./docker_entrypoint.sh"]

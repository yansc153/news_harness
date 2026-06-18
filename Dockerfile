# News Harness V1 — Docker image
FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache ca-certificates chromium nodejs npm \
    && npm install --omit=dev playwright-core

# Copy application code
COPY news_harness/ ./news_harness/
COPY pyproject.toml .
COPY configs/ ./configs/
COPY fixtures/ ./fixtures/
COPY schemas/ ./schemas/
COPY web/ ./web/
COPY scripts/docker_entrypoint.sh ./docker_entrypoint.sh
COPY scripts/x_list_headless_export.mjs ./scripts/x_list_headless_export.mjs
COPY scripts/reddit_headless_export.mjs ./scripts/reddit_headless_export.mjs
COPY scripts/xueqiu_headless_export.mjs ./scripts/xueqiu_headless_export.mjs

RUN chmod +x docker_entrypoint.sh

# Python app is stdlib-only; Xueqiu headless detail reads use system Chromium.

EXPOSE 8765

ENTRYPOINT ["./docker_entrypoint.sh"]

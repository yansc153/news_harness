# Security / Ops Hardening Checklist

Date: 2026-07-23

## Completed In Source

- [x] App container runs as non-root user `news-harness` from the Docker image.
- [x] App compose service uses a read-only root filesystem.
- [x] Writable app paths are explicit: `/app/artifacts` and `/app/web/data/radar-timeline`.
- [x] Secret files are mounted read-only at `/run/news-harness/secrets`.
- [x] App compose service drops Linux capabilities and sets `no-new-privileges`.
- [x] App compose healthcheck calls `http://127.0.0.1:8765/api/health`.
- [x] Caddy sidecar uses read-only root, `no-new-privileges`, and explicit data/config volumes.
- [x] Retention settings are present in compose as dry-run defaults.
- [x] `python3 -m news_harness janitor --dry-run` provides a non-deleting retention plan.
- [x] Operations docs cover healthcheck inspection, Docker mount permissions, and retention dry-run/apply paths.

## Operator Checklist

- [ ] Create host writable mounts before starting Docker: `docker/artifacts`, `docker/feed`, and `docker/secrets`.
- [ ] Keep `docker/secrets` mode `0700` or stricter and never commit its contents.
- [ ] Confirm `docker compose ps` shows the app healthcheck as healthy after the first real cycle.
- [ ] Run `python3 -m news_harness janitor --dry-run` before any retention maintenance.
- [ ] Back up persistent artifact/media volumes before any `janitor --apply`.

## External Secret Rotation Still Required

- [ ] Rotate `deepseek-api-key` in the repo-external secret store.
- [ ] Rotate `reddit-reader-cookie` and confirm Reddit source health.
- [ ] Refresh `xueqiu-storage-state.json` through a real login flow and confirm Xueqiu source health.
- [ ] Rotate `export-token` when `/api/export/v1/*` is exposed outside the private network.

Do not record raw secret values in tickets, docs, logs, or artifacts. Record only
the external secret path, rotation time, operator, and post-rotation healthcheck.

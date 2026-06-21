# News Harness MCP Connector

This repository includes a read-only MCP stdio server for local trusted
cross-project access to radar copy and image evidence.

## Command

```bash
python3 -m news_harness mcp \
  --feed web/radar-timeline/timeline_feed.json \
  --artifact-dir artifacts/manual_smoke/latest
```

## Tools

- `get_latest_feed` returns the current MCP export projection.
- `list_radar_items` returns item summaries and supports `limit` and `source`.
- `get_radar_item` returns one item by `item_id`.
- `get_image_refs` returns export-safe image references.
- `get_health` returns local artifact chain health.

## Boundaries

- Read-only only.
- Stdio MCP is local-trusted only; remote clients should use the tokened
  `/api/export/v1/*` HTTPS surface.
- Public remote clients that only need copy/image evidence can use the
  no-token `/api/public/v1/*` HTTPS surface. It uses the same evidence-only
  projection as MCP/export and does not expose scores or rule internals.
- No source crawling.
- No model calls.
- No promotion.
- No raw cookies, API keys, or session material.
- Local paths, private refs, and internal artifact refs are not exposed as
  openable public links.

## Example Client Config Shape

```json
{
  "mcpServers": {
    "news-harness": {
      "command": "python3",
      "args": [
        "-m",
        "news_harness",
        "mcp",
        "--feed",
        "/opt/news_harness/web/radar-timeline/timeline_feed.json",
        "--artifact-dir",
        "/opt/news_harness/artifacts/manual_smoke/latest"
      ]
    }
  }
}
```

Use this connector when another project needs stable access to crawled copy,
source URLs, and image evidence refs. Scores, revisit/eval status, rulebook
internals, secrets, cookies, and session material are intentionally not part of
the MCP projection.

## Public Evidence API

```text
GET /api/public/v1/items?source=xueqiu,reddit&limit=500
GET /api/public/v1/items/{item_id}
GET /api/public/v1/items/{item_id}/images
```

Check `/api/health` first and require `status: "ok"` before consuming a batch
when freshness matters. The response items include only `object_type`, `id`,
`source`, `published_at`, `copy_text`, `source_url`, and `image_refs`.

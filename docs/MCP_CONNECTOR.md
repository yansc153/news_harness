# News Harness MCP Connector

This repository now includes a read-only MCP stdio server for stable cross-project
access to radar copy and image evidence.

## Command

```bash
python3 -m news_harness mcp \
  --feed web/radar-timeline/timeline_feed.json \
  --artifact-dir artifacts/manual_smoke/latest
```

## Tools

- `get_latest_feed` returns the current public feed projection.
- `list_radar_items` returns item summaries and supports `limit` and `source`.
- `get_radar_item` returns one item by `item_id`.
- `get_image_refs` returns original image references and local asset refs.
- `get_health` returns artifact chain health.

## Boundaries

- Read-only only.
- No source crawling.
- No model calls.
- No promotion.
- No raw cookies, API keys, or session material.
- `fixture://` and internal references are preserved as artifact refs but are
  not exposed as openable public links.

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

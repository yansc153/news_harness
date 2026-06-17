"""Minimal read-only MCP stdio server for News Harness artifacts.

This implements the small JSON-RPC surface needed by MCP clients without adding
external dependencies. It is intentionally read-only: no source fetching, no
scoring, no scheduler mutation, and no promotion.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import artifact_api


JSON = dict[str, Any]


def _text_content(payload: Any) -> list[JSON]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}]


class NewsHarnessMcpServer:
    def __init__(self, feed_path: Path, artifact_dir: Path):
        self.feed_path = feed_path
        self.artifact_dir = artifact_dir
        self.tools: dict[str, tuple[str, JSON, Callable[[JSON], Any]]] = {
            "get_latest_feed": (
                "Return the latest timeline feed with evidence-only MCP fields (copy_text, image_refs, source_url).",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _args: artifact_api.latest_feed(self.feed_path, projection="mcp"),
            ),
            "list_radar_items": (
                "List radar items with evidence-only MCP fields, optionally filtered by source.",
                {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "source": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                lambda args: artifact_api.list_items(self.feed_path, limit=int(args.get("limit", 50)), source=args.get("source"), projection="mcp"),
            ),
            "get_radar_item": (
                "Return one radar item with evidence-only MCP fields (copy_text, image_refs, source_url).",
                {
                    "type": "object",
                    "required": ["item_id"],
                    "properties": {"item_id": {"type": "string"}},
                    "additionalProperties": False,
                },
                lambda args: artifact_api.get_item(str(args["item_id"]), self.feed_path, projection="mcp"),
            ),
            "get_image_refs": (
                "Return original image references for one radar item.",
                {
                    "type": "object",
                    "required": ["item_id"],
                    "properties": {"item_id": {"type": "string"}},
                    "additionalProperties": False,
                },
                lambda args: artifact_api.image_refs(str(args["item_id"]), self.feed_path, projection="mcp"),
            ),
            "get_health": (
                "Return read-only artifact health for website/API/MCP consumers.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _args: artifact_api.artifact_health(self.feed_path, self.artifact_dir),
            ),
        }

    def handle(self, message: JSON) -> JSON | None:
        method = message.get("method")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                return self._result(
                    message,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {"name": "news-harness", "version": "0.1.0"},
                    },
                )
            if method == "tools/list":
                return self._result(
                    message,
                    {
                        "tools": [
                            {"name": name, "description": description, "inputSchema": schema}
                            for name, (description, schema, _handler) in self.tools.items()
                        ]
                    },
                )
            if method == "tools/call":
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                name = params.get("name")
                args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                if name not in self.tools:
                    return self._error(message, -32602, f"unknown tool: {name}")
                payload = self.tools[str(name)][2](args)
                return self._result(message, {"content": _text_content(payload), "isError": False})
            if method == "resources/list":
                return self._result(
                    message,
                    {
                        "resources": [
                            {
                                "uri": "news-harness://latest-feed",
                                "name": "Latest News Harness feed",
                                "mimeType": "application/json",
                            },
                            {
                                "uri": "news-harness://health",
                                "name": "News Harness artifact health",
                                "mimeType": "application/json",
                            },
                        ]
                    },
                )
            if method == "resources/read":
                uri = (message.get("params") or {}).get("uri")
                if uri == "news-harness://latest-feed":
                    payload = artifact_api.latest_feed(self.feed_path, projection="mcp")
                elif uri == "news-harness://health":
                    payload = artifact_api.artifact_health(self.feed_path, self.artifact_dir)
                else:
                    return self._error(message, -32602, f"unknown resource: {uri}")
                return self._result(
                    message,
                    {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}]},
                )
            return self._error(message, -32601, f"method not found: {method}")
        except artifact_api.ArtifactReadError as exc:
            return self._error(message, -32004, str(exc))
        except Exception as exc:  # pragma: no cover - defensive protocol boundary
            return self._error(message, -32000, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _result(message: JSON, result: Any) -> JSON:
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}

    @staticmethod
    def _error(message: JSON, code: int, text: str) -> JSON:
        return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": code, "message": text}}


def run_stdio(feed_path: Path, artifact_dir: Path) -> None:
    server = NewsHarnessMcpServer(feed_path=feed_path, artifact_dir=artifact_dir)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = server.handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only News Harness MCP stdio server")
    parser.add_argument("--feed", type=Path, default=artifact_api.DEFAULT_FEED)
    parser.add_argument("--artifact-dir", type=Path, default=artifact_api.DEFAULT_ARTIFACT_DIR)
    args = parser.parse_args(argv)
    run_stdio(args.feed, args.artifact_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Production-shaped static website and read-only JSON API server."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import artifact_api
from .fixtures import ROOT


def json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


class NewsHarnessSiteHandler(SimpleHTTPRequestHandler):
    server: "NewsHarnessSiteServer"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "public, max-age=60")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed.path, parse_qs(parsed.query))
            return
        if parsed.path in {"", "/"}:
            self._redirect_to_timeline()
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            self._redirect_to_timeline()
            return
        super().do_HEAD()

    def _redirect_to_timeline(self) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/web/radar-timeline/")
        self.end_headers()

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        clean_parts = [part for part in unquote(parsed.path).split("/") if part and part not in {".", ".."}]
        candidate = self.server.root_dir.joinpath(*clean_parts) if clean_parts else self.server.root_dir
        try:
            candidate.resolve().relative_to(self.server.root_dir)
        except ValueError:
            return str(self.server.root_dir / "__not_found__")
        return str(candidate)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/timeline":
                self._send_json(artifact_api.latest_feed(self.server.feed_path, projection="web"))
                return
            if path == "/api/items":
                limit = int((query.get("limit") or ["50"])[0])
                source = (query.get("source") or [None])[0]
                self._send_json(artifact_api.list_items(self.server.feed_path, limit=limit, source=source, projection="web"))
                return
            if path.startswith("/api/items/") and path.endswith("/images"):
                item_id = unquote(path.removeprefix("/api/items/").removesuffix("/images").strip("/"))
                self._send_json(artifact_api.image_refs(item_id, self.server.feed_path, projection="web"))
                return
            if path.startswith("/api/items/"):
                item_id = unquote(path.removeprefix("/api/items/").strip("/"))
                self._send_json(artifact_api.get_item(item_id, self.server.feed_path, projection="web"))
                return
            if path == "/api/health":
                self._send_json(artifact_api.artifact_health(self.server.feed_path, self.server.artifact_dir))
                return
            self._send_json({"status": "error", "error": "not_found", "path": path}, HTTPStatus.NOT_FOUND)
        except artifact_api.ArtifactReadError as exc:
            self._send_json({"status": "error", "error": str(exc)}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._send_json({"status": "error", "error": type(exc).__name__, "message": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


class NewsHarnessSiteServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], root_dir: Path, feed_path: Path, artifact_dir: Path):
        super().__init__(server_address, NewsHarnessSiteHandler)
        self.root_dir = root_dir.resolve()
        self.feed_path = feed_path.resolve()
        self.artifact_dir = artifact_dir.resolve()


def run_server(host: str, port: int, root_dir: Path, feed_path: Path, artifact_dir: Path) -> None:
    server = NewsHarnessSiteServer((host, port), root_dir, feed_path, artifact_dir)
    print(f"news_harness site serving http://{host}:{port}/")
    print(f"timeline feed: {feed_path}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve News Harness website and read-only JSON API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--feed", type=Path, default=artifact_api.DEFAULT_FEED)
    parser.add_argument("--artifact-dir", type=Path, default=artifact_api.DEFAULT_ARTIFACT_DIR)
    args = parser.parse_args(argv)
    run_server(args.host, args.port, args.root, args.feed, args.artifact_dir)
    return 0

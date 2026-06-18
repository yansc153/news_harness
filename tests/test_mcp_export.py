from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from news_harness import artifact_api, mcp_server


class McpExportTests(unittest.TestCase):
    def _feed_path(self, tmp: Path) -> Path:
        feed = {
            "feed_id": "test-feed",
            "feed_version": "v1",
            "generated_at": "2026-06-18T00:00:00Z",
            "items": [
                {
                    "id": "item-1",
                    "source": "x_list",
                    "source_label": "X list",
                    "author": "reader",
                    "published_at": "2026-06-18T00:00:00Z",
                    "topic_or_hook": "Evidence item",
                    "copy_text": "full source text",
                    "source_url": "https://example.com/post",
                    "image_status": "available",
                    "image_refs": [{
                        "original_image_ref": "https://example.com/image.png",
                        "page_context_ref": "/tmp/should-not-leak",
                    }],
                    "asset_refs": [{"asset_ref": "/tmp/local-image.png"}],
                    "hotness_score": 0.91,
                    "radar_score": 91,
                    "hotness_series": [0.1, 0.9],
                    "eval_status": "joined",
                    "revisit_status": "collected",
                    "non_investment_advice": True,
                }
            ],
        }
        path = tmp / "timeline_feed.json"
        path.write_text(json.dumps(feed), encoding="utf-8")
        return path

    def test_mcp_projection_is_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            item = artifact_api.latest_feed(self._feed_path(Path(name)), projection="mcp")["items"][0]
        self.assertEqual(item["object_type"], "McpExportItem")
        self.assertEqual("full source text", item["copy_text"])
        self.assertEqual([{"original_image_ref": "https://example.com/image.png"}], item["image_refs"])
        self.assertEqual([], artifact_api.validate_mcp_export(item))
        for key in artifact_api.FORBIDDEN_MCP_KEYS:
            self.assertNotIn(key, item)

    def test_mcp_tool_call_uses_mcp_projection(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            server = mcp_server.NewsHarnessMcpServer(self._feed_path(tmp), tmp)
            response = server.handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_radar_item", "arguments": {"item_id": "item-1"}},
            })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("McpExportItem", payload["object_type"])
        self.assertEqual([], artifact_api.validate_mcp_export(payload))

    def test_stdio_supports_content_length_framing(self) -> None:
        def frame(message: dict) -> bytes:
            payload = json.dumps(message).encode("utf-8")
            return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload

        def parse_frames(raw: bytes) -> list[dict]:
            frames: list[dict] = []
            cursor = 0
            while cursor < len(raw):
                header_end = raw.find(b"\r\n\r\n", cursor)
                self.assertNotEqual(-1, header_end)
                header = raw[cursor:header_end].decode("ascii")
                length = int(header.split(":", 1)[1].strip())
                start = header_end + 4
                frames.append(json.loads(raw[start:start + length].decode("utf-8")))
                cursor = start + length
            return frames

        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            feed_path = self._feed_path(tmp)
            requests = b"".join([
                frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                frame({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_radar_items", "arguments": {"limit": 1}},
                }),
            ])
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "news_harness",
                    "mcp",
                    "--feed",
                    str(feed_path),
                    "--artifact-dir",
                    str(tmp),
                ],
                input=requests,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=True,
            )

        responses = parse_frames(completed.stdout)
        self.assertEqual("news-harness", responses[0]["result"]["serverInfo"]["name"])
        payload = json.loads(responses[1]["result"]["content"][0]["text"])
        self.assertEqual("McpExportItem", payload["items"][0]["object_type"])


if __name__ == "__main__":
    unittest.main()

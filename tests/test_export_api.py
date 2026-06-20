from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from news_harness.site_server import NewsHarnessSiteServer


class ExportApiTests(unittest.TestCase):
    def test_export_api_is_tokened_and_narrow(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            root = tmp / "web"
            root.mkdir()
            feed = tmp / "timeline_feed.json"
            feed.write_text(json.dumps({
                "feed_id": "test",
                "feed_version": "v1",
                "generated_at": "2026-06-18T00:00:00Z",
                "items": [
                    {
                        "id": "item-1",
                        "source": "x_list",
                        "published_at": "2026-06-18T00:00:00Z",
                        "copy_text": "full copy",
                        "source_url": "https://example.com/post",
                        "image_refs": [{
                            "original_image_ref": "https://example.com/image.png",
                            "page_context_ref": "/tmp/private",
                        }],
                        "hotness_score": 0.99,
                    },
                    {"id": "item-2", "source": "xueqiu_hot", "source_label": "雪球热门", "copy_text": "xueqiu copy", "image_refs": []},
                    {"id": "item-3", "source": "reddit", "source_label": "r/stocks", "copy_text": "reddit copy", "image_refs": []},
                ],
            }), encoding="utf-8")

            os.environ["NEWS_HARNESS_EXPORT_TOKEN"] = "secret"
            server = NewsHarnessSiteServer(("127.0.0.1", 0), root, feed, tmp)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(HTTPError) as exc:
                    urlopen(f"{base}/api/export/v1/items", timeout=5)
                self.assertEqual(401, exc.exception.code)
                exc.exception.close()

                request = Request(
                    f"{base}/api/export/v1/items",
                    headers={"Authorization": "Bearer secret"},
                )
                payload = json.loads(urlopen(request, timeout=5).read().decode("utf-8"))
                request = Request(
                    f"{base}/api/export/v1/items?source=xueqiu,reddit&limit=10",
                    headers={"Authorization": "Bearer secret"},
                )
                filtered = json.loads(urlopen(request, timeout=5).read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                os.environ.pop("NEWS_HARNESS_EXPORT_TOKEN", None)

        item = payload["items"][0]
        self.assertEqual("full copy", item["copy_text"])
        self.assertNotIn("hotness_score", item)
        self.assertEqual([{"original_image_ref": "https://example.com/image.png"}], item["image_refs"])
        self.assertEqual(["xueqiu_hot", "reddit"], [item["source"] for item in filtered["items"]])


if __name__ == "__main__":
    unittest.main()

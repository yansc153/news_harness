"""S3 — Reddit source connector TDD (RED first).

Covers:
- pure mapper `reddit_observation_to_content_item`
- `RedditSourceConnector.fetch()` orchestration with an injected fake fetcher
"""

import unittest

from news_harness.connectors.source.reddit import (
    RedditSourceConnector,
    reddit_observation_to_content_item,
)
from news_harness.models import ContentItem


def _sample_obs(observation_id="obs_1", copy_text=None, image_refs=None):
    if copy_text is None:
        copy_text = ("This is a sufficiently long Reddit post about a stock thesis. " * 20).strip()
    if image_refs is None:
        image_refs = [{
            "image_ref_id": "img_x",
            "original_image_ref": "https://example.com/a.png",
            "thumbnail_ref": "https://example.com/a.png",
            "page_context_ref": "https://reddit.com/r/wallstreetbets/comments/abc",
            "context_position": "preview_image",
            "ownership_scope": "source_content_body",
            "image_role": "original_content_image",
            "evidence_eligible": True,
            "filter_status": "accepted_content_image",
            "dimensions": {"width": 800, "height": 600},
            "alt": "chart",
            "caption": "chart",
            "access_status": "public_candidate_unverified",
            "download_status": "pending_policy_check",
        }]
    return {
        "observation_id": observation_id,
        "source": "reddit",
        "source_label": "r/wallstreetbets",
        "source_url": "https://reddit.com/r/wallstreetbets/comments/abc",
        "canonical_url": "https://reddit.com/r/wallstreetbets/comments/abc",
        "author": "some_redditor",
        "published_at": "2026-07-22T00:00:00",
        "copy_text": copy_text,
        "image_refs": image_refs,
        "engagement": {"likes": 120, "comments": 30, "views": 5000},
        "topic_or_hook": "GME moon",
    }


class TestRedditMapper(unittest.TestCase):
    def test_maps_core_fields(self):
        obs = _sample_obs()
        item = reddit_observation_to_content_item(obs)
        self.assertIsInstance(item, ContentItem)
        self.assertEqual(item.id, "obs_1")
        self.assertEqual(item.platform, "reddit")
        self.assertEqual(item.source_label, "r/wallstreetbets")
        self.assertEqual(item.source_url, "https://reddit.com/r/wallstreetbets/comments/abc")
        self.assertEqual(item.author.name, "some_redditor")
        self.assertEqual(item.author.handle, "some_redditor")
        self.assertEqual(item.author_type, "unknown")
        self.assertEqual(item.published_at, "2026-07-22T00:00:00")
        self.assertEqual(item.char_count, len(item.copy_text.strip()))
        self.assertEqual(item.content_kind, "post")
        self.assertEqual(item.ingest_gate, "passed")  # Reddit 无闸门（闸门在 S4 雪球）
        self.assertEqual(item.processing_status, "raw")
        self.assertEqual(item.evidence_status, "observed")
        self.assertEqual(item.rights_status, "ok")

    def test_maps_image_refs_into_media(self):
        item = reddit_observation_to_content_item(_sample_obs())
        self.assertEqual(len(item.image_refs), 1)
        self.assertEqual(item.image_refs[0].url, "https://example.com/a.png")
        self.assertEqual(item.image_refs[0].dimensions, "800x600")
        self.assertEqual(item.media_kind, "image")
        self.assertEqual(item.video_refs, [])

    def test_no_image_yields_text_media_kind(self):
        item = reddit_observation_to_content_item(_sample_obs(image_refs=[]))
        self.assertEqual(item.image_refs, [])
        self.assertEqual(item.media_kind, "text")

    def test_strips_whitespace_for_char_count(self):
        item = reddit_observation_to_content_item(_sample_obs(copy_text="  hello world  "))
        self.assertEqual(item.char_count, 11)


class _FakeFetcher:
    def __init__(self, observations, errors=None):
        self._observations = observations
        self._errors = errors or []
        self.called_with = None

    def __call__(self, source_config, availability):
        self.called_with = (source_config, availability)
        return (self._observations, self._errors)


class TestRedditSourceConnector(unittest.TestCase):
    def test_fetch_maps_observations_and_skips_empty_text(self):
        good = _sample_obs("obs_good")
        empty = _sample_obs("obs_empty", copy_text="   ")
        fetcher = _FakeFetcher([good, empty])
        conn = RedditSourceConnector(fetcher=fetcher)
        items = conn.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "obs_good")

    def test_fetch_passes_config_and_availability_to_fetcher(self):
        fetcher = _FakeFetcher([_sample_obs()])
        conn = RedditSourceConnector(fetcher=fetcher, config={"subreddits": ["wallstreetbets"]})
        conn.fetch(availability={"rdt": {"command": "/usr/bin/rdt"}})
        self.assertEqual(fetcher.called_with[0], {"subreddits": ["wallstreetbets"]})
        self.assertEqual(fetcher.called_with[1], {"rdt": {"command": "/usr/bin/rdt"}})

    def test_fetch_exposes_errors_and_empty_on_failure(self):
        fetcher = _FakeFetcher([], errors=[{"code": "auth_or_challenge_required"}])
        conn = RedditSourceConnector(fetcher=fetcher)
        items = conn.fetch()
        self.assertEqual(items, [])
        self.assertEqual(conn.last_errors, [{"code": "auth_or_challenge_required"}])

    def test_registry_knows_reddit_source(self):
        from news_harness.connectors.registry import ConnectorRegistry
        reg = ConnectorRegistry()
        reg.register(RedditSourceConnector)
        self.assertIn("reddit", reg.list_sources())
        self.assertIs(reg.get_source("reddit"), RedditSourceConnector)


if __name__ == "__main__":
    unittest.main()

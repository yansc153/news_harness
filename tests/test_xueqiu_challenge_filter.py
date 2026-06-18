from __future__ import annotations

import unittest

from news_harness.direct_cli_backend import (
    _looks_like_auth_or_challenge_text,
    _looks_like_truncated_xueqiu_text,
    _xueqiu_source_quality,
)
from news_harness.manual_smoke import _looks_like_challenge


class XueqiuChallengeFilterTests(unittest.TestCase):
    def test_access_verification_text_is_blocked(self) -> None:
        text = (
            "Access Verification For better experience, please slide to complete "
            "the verification process before accessing the web page. TraceID: abc"
        )

        self.assertTrue(_looks_like_auth_or_challenge_text(text))
        self.assertTrue(_looks_like_challenge(text))

    def test_xueqiu_ellipsis_text_is_not_full_text(self) -> None:
        text = "今天有几个客户和我聊天，对基金今年亏损表达了担忧。在经济的下行阶段，整个大盘..."

        self.assertTrue(_looks_like_truncated_xueqiu_text(text))
        quality = _xueqiu_source_quality(
            {"full_text_observed": True, "detail_fetch_status": "api_full_text_observed"},
            text,
        )

        self.assertEqual("detail_attempt_incomplete", quality["full_text_status"])
        self.assertIn("xueqiu_full_text_not_confirmed", quality["source_quality_risk_flags"])

    def test_xueqiu_complete_text_can_be_full_text(self) -> None:
        text = "第一段完整正文。第二段补充细节，最后有明确句号。"

        self.assertFalse(_looks_like_truncated_xueqiu_text(text))
        quality = _xueqiu_source_quality(
            {"full_text_observed": True, "detail_fetch_status": "api_full_text_observed"},
            text,
        )

        self.assertEqual("full_text_observed", quality["full_text_status"])


if __name__ == "__main__":
    unittest.main()

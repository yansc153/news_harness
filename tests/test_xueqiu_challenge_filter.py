from __future__ import annotations

import unittest

from news_harness.direct_cli_backend import _looks_like_auth_or_challenge_text
from news_harness.manual_smoke import _looks_like_challenge


class XueqiuChallengeFilterTests(unittest.TestCase):
    def test_access_verification_text_is_blocked(self) -> None:
        text = (
            "Access Verification For better experience, please slide to complete "
            "the verification process before accessing the web page. TraceID: abc"
        )

        self.assertTrue(_looks_like_auth_or_challenge_text(text))
        self.assertTrue(_looks_like_challenge(text))


if __name__ == "__main__":
    unittest.main()

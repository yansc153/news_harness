from news_harness import direct_cli_backend


def test_xueqiu_headless_timeout_covers_80_item_detail_window() -> None:
    assert direct_cli_backend.XUEQIU_HEADLESS_TIMEOUT_SECONDS >= 420

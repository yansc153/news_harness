from news_harness import health


def test_health_defaults_do_not_require_x_list(monkeypatch) -> None:
    monkeypatch.setattr(health, "_load_json", lambda path: {})

    result = health.run_healthcheck()

    assert "x_list" not in result["counts_by_source"]
    assert "source_x_list_present" not in result["failed_checks"]
    assert "source_x_list_run_ok" not in result["failed_checks"]

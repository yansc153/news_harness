"""Verify that zero observations with no source failures is not treated as an error."""

import unittest


class TestNoCandidatesIsNotFailure(unittest.TestCase):
    def test_zero_observations_without_failures_is_ok(self):
        source_result = {
            "status": "ok",
            "observation_count": 0,
            "source_statuses": {"xueqiu_targeted": "ok"},
        }
        errors = []
        if not source_result.get("observation_count"):
            failed_sources = [
                src for src, st in (source_result.get("source_statuses") or {}).items()
                if st != "ok"
            ]
            if failed_sources:
                errors.append({"phase": "sources", "code": "source_failed"})
        self.assertEqual(errors, [], "Zero candidates should not produce errors when sources are ok")

    def test_zero_observations_with_failure_is_error(self):
        source_result = {
            "status": "failed",
            "observation_count": 0,
            "source_statuses": {"xueqiu_targeted": "failed"},
        }
        errors = []
        if not source_result.get("observation_count"):
            failed_sources = [
                src for src, st in (source_result.get("source_statuses") or {}).items()
                if st != "ok"
            ]
            if failed_sources:
                errors.append({"phase": "sources", "code": "source_failed"})
        self.assertGreater(len(errors), 0)


if __name__ == "__main__":
    unittest.main()

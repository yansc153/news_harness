"""Validate that every enabled KPL endpoint has a complete request contract."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_PATH = ROOT / "interfaces" / "providers" / "kaipanla" / "news-harness-cases.v1.json"

REQUIRED_ENDPOINTS = [
    "KPL-41", "KPL-46", "KPL-47", "KPL-49", "KPL-50", "KPL-51",
    "KPL-52", "KPL-53", "KPL-78", "KPL-79", "KPL-80", "KPL-94", "KPL-115",
]


class TestKaipanlaContracts(unittest.TestCase):
    """Every enabled endpoint must have server, method, path, and query template."""

    @classmethod
    def setUpClass(cls):
        if not CONTRACTS_PATH.exists():
            raise unittest.SkipTest(f"Contract file missing: {CONTRACTS_PATH}")
        raw = json.loads(CONTRACTS_PATH.read_text(encoding="utf-8"))
        cls.contracts = {c["endpoint_id"]: c for c in raw["contracts"]}

    def test_all_required_endpoints_present(self):
        missing = set(REQUIRED_ENDPOINTS) - set(self.contracts.keys())
        self.assertEqual(missing, set(), f"Missing contracts for: {missing}")

    def test_every_contract_has_server_and_path(self):
        for eid, c in sorted(self.contracts.items()):
            with self.subTest(endpoint=eid):
                self.assertTrue(c.get("server", "").startswith("https://"), f"{eid}: bad server")
                self.assertTrue(c.get("path", "").startswith("/"), f"{eid}: bad path")
                self.assertEqual(c.get("method"), "GET", f"{eid}: must be GET-only")

    def test_no_secret_material_in_query(self):
        forbidden_keys = {"token_value", "api_key", "password", "secret"}
        for eid, c in sorted(self.contracts.items()):
            q = c.get("query_template", {})
            for key in q:
                with self.subTest(endpoint=eid, key=key):
                    self.assertNotIn(key.lower(), forbidden_keys, f"{eid}: suspicious key {key}")
                    val = str(q[key])
                    self.assertLessEqual(len(val), 40, f"{eid}.{key}: value too long, may be a secret")

    def test_no_env_placeholders_remain(self):
        raw = CONTRACTS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("{env:", raw, "Env placeholder not resolved")

    def test_identity_values_are_not_committed(self):
        for eid, contract in sorted(self.contracts.items()):
            query = contract.get("query_template", {})
            with self.subTest(endpoint=eid):
                self.assertEqual(query.get("DeviceID", ""), "")
                self.assertEqual(query.get("Token", ""), "")
                self.assertEqual(query.get("UserID", ""), "")

    def test_query_template_has_action_parameter(self):
        for eid, c in sorted(self.contracts.items()):
            q = c.get("query_template", {})
            with self.subTest(endpoint=eid):
                self.assertIn("a", q, f"{eid}: missing action param 'a'")

    def test_every_required_endpoint_has_response_schema(self):
        schema_dir = CONTRACTS_PATH.parent / "schemas"
        missing = [eid for eid in REQUIRED_ENDPOINTS if not (schema_dir / f"{eid}.schema.json").exists()]
        self.assertEqual(missing, [], f"Missing response schemas for: {missing}")


if __name__ == "__main__":
    unittest.main()

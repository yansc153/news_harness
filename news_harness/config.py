"""Configuration and redaction helpers for preflight checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .fixtures import ROOT, load_json


DEFAULT_PREFLIGHT_CONFIG = ROOT / "configs" / "preflight.example.json"
REFERENCE_ONLY_KEYS = {
    "api_key_ref",
    "secret_ref",
    "session_ref",
    "session_state_ref",
    "credentials_ref",
    "credential_ref",
}
REFERENCE_NAME_KEYS = {
    "api_key_env_var_name",
    "api_key_file_path_ref",
    "cookie_file_env_var_name",
    "secret_env_var_name",
    "cookie_bundle_env_var_name",
    "session_state_env_var_name",
    "secrets_env_example_ref",
    "secret_file_path_ref",
    "session_state_file_path_ref",
}
SECRET_POLICY_KEYS = {
    "auth_material_storage",
    "raw_api_key_allowed",
    "raw_cookie_allowed",
    "raw_secret_values_allowed",
    "raw_token_allowed",
    "secret_refs_only",
    "session_state_refs_only",
}
HASH_VALUE_KEYS = {
    "content_hash",
    "context_hash",
    "config_hash",
    "output_hash",
    "prompt_hash",
    "scoring_path_hash",
    "sha256",
}
RAW_SECRET_KEY_FRAGMENTS = {
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "bearer",
    "cookie",
    "ct0",
    "credential",
    "pat",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_cookie",
    "token",
    "twid",
}
SECRET_VALUE_PREFIXES = ("AKIA", "sk-", "pk-", "ghp_", "gho_", "github_pat_", "xoxb-", "eyJ", "ya29.", "sk_live_")
RAW_SECRET_VALUE_MARKERS = ("auth_token=", "ct0=", "twid=", "cookie:", "set-cookie:", "authorization: bearer")
LONG_BASE64_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")


def load_preflight_config(config_path: Path = DEFAULT_PREFLIGHT_CONFIG) -> dict[str, Any]:
    return load_json(config_path)


def find_raw_secret_material(data: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child_path = f"{path}.{key}"
            normalized = key.lower()
            if normalized in SECRET_POLICY_KEYS:
                findings.extend(find_raw_secret_material(value, child_path))
                continue
            if normalized in HASH_VALUE_KEYS:
                continue
            if normalized in REFERENCE_ONLY_KEYS:
                if value not in (None, "") and not (isinstance(value, str) and value.startswith(("secret_ref:", "session_ref:", "credential_ref:"))):
                    findings.append(child_path)
                continue
            if normalized in REFERENCE_NAME_KEYS:
                if not isinstance(value, str) or _looks_like_secret_value(value):
                    findings.append(child_path)
                continue
            if normalized.endswith("_config_ref"):
                if not isinstance(value, str) or _looks_like_secret_value(value):
                    findings.append(child_path)
                continue
            if _looks_like_secret_key(normalized):
                if isinstance(value, (dict, list)):
                    findings.extend(find_raw_secret_material(value, child_path))
                elif isinstance(value, str) and value:
                    findings.append(child_path)
                continue
            findings.extend(find_raw_secret_material(value, child_path))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            findings.extend(find_raw_secret_material(item, f"{path}[{index}]"))
    elif isinstance(data, str):
        if _looks_like_secret_value(data):
            findings.append(path)
    return findings


def _looks_like_secret_value(value: str) -> bool:
    lowered = value.lower()
    return (
        value.startswith(SECRET_VALUE_PREFIXES)
        or any(marker in lowered for marker in RAW_SECRET_VALUE_MARKERS)
        or LONG_BASE64_TOKEN_RE.fullmatch(value) is not None
    )


def _looks_like_secret_key(normalized_key: str) -> bool:
    for fragment in RAW_SECRET_KEY_FRAGMENTS:
        if fragment == "pat":
            tokens = [token for token in re.split(r"[^a-z0-9]+", normalized_key) if token]
            if fragment in tokens:
                return True
            continue
        if fragment in normalized_key:
            return True
    return False


def check_preflight_config(config: dict[str, Any], required_guardrails: list[str]) -> list[str]:
    issues: list[str] = []
    if config.get("object_type") != "PreflightConfig":
        issues.append("object_type must be PreflightConfig")
    if not config.get("config_version"):
        issues.append("config_version is required")
    if config.get("runtime_stage") != "local_fixture":
        issues.append("runtime_stage must remain local_fixture")
    configured_guardrails = set(config.get("required_guardrails", []))
    missing_guardrails = sorted(set(required_guardrails) - configured_guardrails)
    if missing_guardrails:
        issues.append(f"required_guardrails missing {missing_guardrails}")
    secret_findings = find_raw_secret_material(config)
    if secret_findings:
        issues.append(f"raw secret material present at {secret_findings}")
    return issues

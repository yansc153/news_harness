"""Direct CLI backed manual smoke source runner.

This backend uses source-specific CLIs instead of treating Agent-Reach as a
unified collection API: twitter-cli for X list reads, rdt-cli for Reddit
subreddit reads, and OpenCLI for Xueqiu when its Browser Bridge is connected.
It still writes the same redacted manual-smoke artifacts consumed by timeline
and DeepSeek scoring.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .config import find_raw_secret_material
from .constants import X_LIST_URL
from .events import sha256_json
from .fixtures import ROOT, load_json
from .manual_smoke import (
    IMAGE_ASSET_ARTIFACT,
    SOURCE_RUN_ARTIFACT,
    build_image_asset_manifest,
    _check_manual_env,
    _fetch_xueqiu,
    _image_ref_record,
    _observation,
    _read_optional_secret_file,
    _read_secret_file,
    _rel,
    _run_id,
    _source_artifact,
    _source_summary,
    _structured_error,
    _utc_now,
    _write_manual_json,
)
from .paths import write_json_artifact


DIRECT_CLI_DIR = ROOT / "artifacts" / "direct_cli" / "latest"
DIRECT_CLI_PROCESSING_ARTIFACT = DIRECT_CLI_DIR / "processing.json"
DIRECT_CLI_INSTALL_TIMEOUT_SECONDS = 180
DIRECT_CLI_READ_TIMEOUT_SECONDS = 60
OPENCLI_READ_TIMEOUT_SECONDS = 45
XUEQIU_HEADLESS_TIMEOUT_SECONDS = 75


def run_direct_cli_sources(config_path: Path) -> dict[str, Any]:
    """Run a small read-only direct-CLI manual smoke and write standard artifacts."""

    started = time.monotonic()
    config = load_json(config_path)
    run_id = _run_id("direct_cli_source")
    env_check = _check_manual_env()
    if env_check["status"] != "ok":
        artifact = _source_artifact(
            run_id=run_id,
            config_path=config_path,
            sources=[],
            observations=[],
            structured_errors=[env_check["structured_error"]],
            started=started,
            env_check=env_check,
        )
        artifact["backend"] = "direct-cli"
        _write_manual_json(SOURCE_RUN_ARTIFACT, artifact)
        return {**_source_summary(artifact), "backend": "direct-cli"}

    availability = _ensure_direct_cli_available()
    source_results: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    structured_errors: list[dict[str, Any]] = []

    for source_config in config.get("sources", []):
        source = str(source_config.get("source", "unknown"))
        source_started = time.monotonic()
        source_observations: list[dict[str, Any]]
        errors: list[dict[str, Any]]
        try:
            if source == "x_list":
                source_observations, errors = _fetch_x_list_with_twitter_cli(source_config, availability)
            elif source == "reddit":
                source_observations, errors = _fetch_reddit_with_rdt_cli(source_config, availability)
            elif source.startswith("xueqiu_"):
                source_observations, errors = _fetch_xueqiu_with_opencli(source_config, availability)
                for observation in source_observations:
                    observation.setdefault(
                        "connector_identity",
                        {
                            "connector_id": f"direct_cli.{source}.manual_smoke.v1",
                            "tool_id": "opencli.xueqiu",
                            "tool_version": "0.1.0",
                        },
                    )
            else:
                source_observations, errors = [], [_structured_error("backend_unsupported", f"direct-cli backend does not support source {source!r}")]
        except Exception as exc:  # noqa: BLE001 - converted to structured smoke error
            source_observations = []
            errors = [_structured_error("source_fetch_failed", str(exc))]

        observations.extend(source_observations)
        structured_errors.extend({**error, "source": error.get("source", source)} for error in errors)
        source_results.append(
            {
                "source": source,
                "backend": "direct-cli",
                "status": "ok" if source_observations else "failed",
                "item_count": len(source_observations),
                "requested_item_count": _requested_item_count(source_config),
                "refresh_interval_seconds": source_config.get("refresh_interval_seconds"),
                "batch_limit": source_config.get("max_items_per_subreddit_per_run") if source == "reddit" else source_config.get("batch_limit"),
                "structured_errors": errors,
                "duration_seconds": round(time.monotonic() - source_started, 3),
                "rate_limit": {"backoff_seconds": 0, "retry_after": None},
                "redaction_status": "passed",
            }
        )

    image_manifest = build_image_asset_manifest(run_id, observations)
    _write_manual_json(IMAGE_ASSET_ARTIFACT, image_manifest)
    processing_artifact = {
        "object_type": "DirectCliRealProcessingRun",
        "run_id": run_id,
        "mode": "manual_smoke",
        "backend": "direct-cli",
        "created_at": _utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "official_docs_checked": [
            "https://github.com/public-clis/twitter-cli",
            "https://github.com/public-clis/rdt-cli",
            "opencli xueqiu --help",
        ],
        "availability": availability,
        "source_run_ref": _rel(SOURCE_RUN_ARTIFACT),
        "image_asset_ref": _rel(IMAGE_ASSET_ARTIFACT),
        "sources": [{k: v for k, v in source.items() if k != "structured_errors"} for source in source_results],
        "observation_count": len(observations),
        "structured_error_count": len(structured_errors),
        "production_connector_ready": False,
        "read_only": True,
        "redaction_status": "passed",
    }
    processing_artifact["output_hash"] = sha256_json({k: v for k, v in processing_artifact.items() if k != "output_hash"})
    _write_direct_cli_json(DIRECT_CLI_PROCESSING_ARTIFACT, processing_artifact)

    artifact = _source_artifact(
        run_id=run_id,
        config_path=config_path,
        sources=source_results,
        observations=observations,
        structured_errors=structured_errors,
        started=started,
        env_check=env_check,
    )
    artifact["backend"] = "direct-cli"
    artifact["direct_cli_artifact_ref"] = _rel(DIRECT_CLI_PROCESSING_ARTIFACT)
    artifact["image_asset_artifact_ref"] = _rel(IMAGE_ASSET_ARTIFACT)
    _write_manual_json(SOURCE_RUN_ARTIFACT, artifact)

    summary = _source_summary(artifact)
    return {
        **summary,
        "backend": "direct-cli",
        "direct_cli_status": availability["status"],
        "direct_cli_artifact_ref": _rel(DIRECT_CLI_PROCESSING_ARTIFACT),
    }


def _fetch_x_list_with_twitter_cli(source_config: dict[str, Any], availability: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    command = availability.get("twitter", {}).get("command")
    if not command:
        return [], [_structured_error("direct_cli_unavailable", "twitter-cli is unavailable")]
    cookie = _read_secret_file(os.environ["NEWS_HARNESS_X_COOKIE_FILE"], "x_cookie")
    if cookie["status"] != "ok":
        return [], [cookie["structured_error"]]
    tokens = _extract_x_cookie_tokens(str(cookie["value"]))
    if not tokens:
        return [], [_structured_error("auth_or_challenge_required", "X cookie file did not contain auth_token and ct0")]
    list_id = _x_list_id(str(source_config.get("source_entry_url") or X_LIST_URL))
    env = {
        "TWITTER_AUTH_TOKEN": tokens["auth_token"],
        "TWITTER_CT0": tokens["ct0"],
        "OUTPUT": "json",
    }
    result = _run_command(
        [command, "list", list_id, "-n", str(source_config.get("batch_limit", 10)), "--json"],
        timeout=DIRECT_CLI_READ_TIMEOUT_SECONDS,
        env=env,
    )
    return _observations_from_cli_result(
        result,
        source_config,
        source="x_list",
        label=str(source_config.get("source_label") or "X list"),
        default_url=str(source_config.get("source_entry_url") or X_LIST_URL),
        tool_id="twitter-cli",
        connector_prefix="direct_cli",
    )


def _fetch_reddit_with_rdt_cli(source_config: dict[str, Any], availability: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    command = availability.get("rdt", {}).get("command")
    if not command:
        return [], [_structured_error("direct_cli_unavailable", "rdt-cli is unavailable")]
    reddit_cookie = _read_optional_secret_file("NEWS_HARNESS_REDDIT_COOKIE_FILE", "reddit_cookie")
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if reddit_cookie["status"] == "ok":
        errors.append(_structured_error("reddit_cookie_file_not_consumed_by_rdt_cli", "rdt-cli uses browser/saved cookies; repo-external cookie file was not copied into CLI config"))
    per_subreddit = int(source_config.get("max_items_per_subreddit_per_run", 10))
    subreddits = [str(subreddit) for subreddit in source_config.get("subreddits", [])]

    def fetch_subreddit(subreddit_name: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        default_url = f"https://www.reddit.com/r/{urllib.parse.quote(subreddit_name)}/hot/"
        result = _run_command([command, "sub", subreddit_name, "-n", str(per_subreddit), "--json"], timeout=DIRECT_CLI_READ_TIMEOUT_SECONDS, env={"OUTPUT": "json"})
        obs, errs = _observations_from_cli_result(
            result,
            source_config,
            source="reddit",
            label=f"r/{subreddit_name}",
            default_url=default_url,
            tool_id="rdt-cli",
            connector_prefix="direct_cli",
            source_label_override=f"r/{subreddit_name}",
            max_items=per_subreddit,
        )
        return subreddit_name, obs, errs

    workers = min(5, len(subreddits) or 1)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_subreddit, subreddit): subreddit for subreddit in subreddits}
        results = []
        for future in as_completed(futures):
            results.append(future.result())
    for subreddit_name, obs, errs in sorted(results, key=lambda item: subreddits.index(item[0])):
        if obs:
            observations.extend(obs)
        else:
            errors.extend({**error, "subreddit": subreddit_name} for error in errs)
    return observations, errors


def _fetch_xueqiu_with_opencli(source_config: dict[str, Any], availability: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read Xueqiu through OpenCLI when its browser bridge is available.

    Xueqiu's useful sections are browser/session-assisted in OpenCLI. We do not
    hide that behind a fake success: Browser Bridge errors become explicit
    structured source failures.
    """

    command = availability.get("opencli", {}).get("command")
    source = str(source_config.get("source", "xueqiu"))
    if not command:
        fallback_observations, fallback_errors = _fetch_xueqiu(source_config)
        return fallback_observations, [
            {
                **_structured_error(
                    "opencli_unavailable",
                    "opencli is unavailable; builtin Xueqiu parser fell back to homepage reach only",
                ),
                "source": source,
                "fallback_errors": fallback_errors,
            }
        ]
    command_args = _xueqiu_opencli_args(command, source, int(source_config.get("batch_limit", 10)))
    if command_args is None:
        return [], [
            {
                **_structured_error(
                    "xueqiu_section_backend_unsupported",
                    f"opencli has no exact read-only backend for {source}; do not map this section to fake data",
                ),
                "source": source,
            }
        ]
    bridge = availability.get("opencli", {}).get("browser_bridge", {})
    if bridge.get("status") == "failed":
        headless_observations, headless_errors = _fetch_xueqiu_from_headless(source_config)
        if headless_observations:
            return headless_observations, headless_errors
        chrome_observations, chrome_errors = _fetch_xueqiu_from_chrome_export(source_config)
        if chrome_observations:
            return chrome_observations, chrome_errors
        return [], [
            {
                **_structured_error(
                    "opencli_browser_bridge_required",
                    "OpenCLI daemon is installed, but Browser Bridge extension is not connected; Xueqiu hot/feed cannot be read yet.",
                ),
                "source": source,
                "doctor_ref": "artifacts/direct_cli/latest/processing.json#availability/opencli/browser_bridge",
                "headless_status": headless_errors,
                "chrome_export_status": chrome_errors,
            }
        ]
    result = _run_command(command_args, timeout=OPENCLI_READ_TIMEOUT_SECONDS, env={"OUTPUT": "json"})
    return _observations_from_cli_result(
        result,
        source_config,
        source=source,
        label=str(source_config.get("source_label") or human_xueqiu_label(source)),
        default_url=str(source_config.get("source_entry_url") or "https://xueqiu.com/"),
        tool_id="opencli-xueqiu",
        connector_prefix="direct_cli",
        source_label_override=str(source_config.get("source_label") or human_xueqiu_label(source)),
        max_items=int(source_config.get("batch_limit", 10)),
    )


def _xueqiu_opencli_args(command: str, source: str, limit: int) -> list[str] | None:
    if source == "xueqiu_hot":
        return [command, "xueqiu", "hot", "--limit", str(limit), "-f", "json", "--window", "background"]
    if source == "xueqiu_daren":
        return [command, "xueqiu", "feed", "--limit", str(limit), "-f", "json", "--window", "background"]
    return None


def _fetch_xueqiu_from_chrome_export(source_config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    export_path = os.environ.get("NEWS_HARNESS_XUEQIU_CHROME_EXPORT_FILE")
    if not export_path:
        return [], [_structured_error("xueqiu_chrome_export_missing", "NEWS_HARNESS_XUEQIU_CHROME_EXPORT_FILE is not set")]
    return _fetch_xueqiu_from_chrome_export_file(source_config, Path(export_path), "chrome")


def _fetch_xueqiu_from_chrome_export_file(source_config: dict[str, Any], export_path: Path, export_kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = str(source_config.get("source", "xueqiu"))
    path = export_path.expanduser().resolve()
    try:
        path.relative_to(ROOT.resolve())
        return [], [_structured_error(f"xueqiu_{export_kind}_export_in_repo", "Xueqiu DOM export must live outside the repo")]
    except ValueError:
        pass
    if not path.exists():
        return [], [_structured_error(f"xueqiu_{export_kind}_export_missing", "Xueqiu DOM export file does not exist")]
    try:
        export = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [_structured_error(f"xueqiu_{export_kind}_export_unreadable", str(exc))]
    if find_raw_secret_material(export):
        return [], [_structured_error(f"xueqiu_{export_kind}_export_secret_leak", "Xueqiu DOM export contains raw secret-like material")]
    sources = export.get("sources") if isinstance(export, dict) else None
    rows = sources.get(source) if isinstance(sources, dict) else None
    if not isinstance(rows, list) or not rows:
        return [], [_structured_error(f"xueqiu_{export_kind}_export_no_rows", f"Xueqiu DOM export has no rows for {source}")]
    observations = []
    limit = int(source_config.get("batch_limit", 10))
    default_url = str(source_config.get("source_entry_url") or "https://xueqiu.com/")
    label = str(source_config.get("source_label") or human_xueqiu_label(source))
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        text = _row_copy_text(row)
        if not text or _looks_like_auth_or_challenge_text(text):
            continue
        observation_url = _row_url(row, default_url, source)
        observation = _observation(
            source=source,
            source_label=label,
            source_url=observation_url,
            canonical_url=observation_url,
            author=_row_author(row, source, label),
            published_at=_row_published_at(row),
            copy_text=text[:2000],
            image_refs=_image_refs_from_row(row, observation_url),
            engagement=_engagement_from_row(row),
            topic_or_hook=str(row.get("title") or row.get("topic") or label)[:300],
            structured_error=None,
        )
        observation["connector_identity"] = {
            "connector_id": f"direct_cli.{source}.{export_kind}_dom_manual_smoke.v1",
            "tool_id": f"{export_kind}-dom-xueqiu",
            "tool_version": str(export.get("export_schema_version") or "xueqiu_chrome_dom.v1") if isinstance(export, dict) else "xueqiu_chrome_dom.v1",
        }
        observation.update(_xueqiu_source_quality(row, text))
        observation["fetch_status"] = f"{export_kind}_dom_manual_smoke_success"
        observation["evidence_ref"] = f"artifacts/manual_smoke/latest/source_run.json#observations/{observation['observation_id']}"
        observations.append(observation)
    if not observations:
        return [], [_structured_error(f"xueqiu_{export_kind}_export_parse_failed", f"Xueqiu DOM export rows for {source} had no readable post text")]
    return observations, []


def _fetch_xueqiu_from_headless(source_config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = str(source_config.get("source", "xueqiu"))
    if os.environ.get("NEWS_HARNESS_XUEQIU_HEADLESS") != "1":
        return [], [_structured_error("xueqiu_headless_disabled", "NEWS_HARNESS_XUEQIU_HEADLESS is not enabled")]
    if source == "xueqiu_dispute":
        return [], [_structured_error("xueqiu_section_backend_unsupported", "No exact read-only headless DOM section for Xueqiu dispute")]
    python_script = ROOT / "scripts" / "xueqiu_headless_export.py"
    node_script = ROOT / "scripts" / "xueqiu_headless_export.mjs"
    if python_script.exists():
        command = os.sys.executable
        script = python_script
    else:
        node = _find_command("node")
        if not node:
            return [], [_structured_error("node_unavailable", "Node.js is required for the Xueqiu headless export script")]
        command = node
        script = node_script
    if not script.exists():
        return [], [_structured_error("xueqiu_headless_script_missing", "scripts/xueqiu_headless_export.py or .mjs is missing")]
    export_dir = Path(os.environ.get("NEWS_HARNESS_XUEQIU_EXPORT_DIR", "/tmp/news-harness-secrets")).expanduser().resolve()
    try:
        export_dir.relative_to(ROOT.resolve())
        return [], [_structured_error("xueqiu_headless_export_dir_in_repo", "Xueqiu headless export directory must live outside the repo")]
    except ValueError:
        pass
    export_path = export_dir / f"{source}_headless_export.json"
    args = [
        command,
        str(script),
        "--source",
        source,
        "--limit",
        str(int(source_config.get("batch_limit", 10))),
        "--out",
        str(export_path),
    ]
    storage_state = os.environ.get("NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE")
    if storage_state:
        storage_path = Path(storage_state).expanduser().resolve()
        try:
            storage_path.relative_to(ROOT.resolve())
            return [], [_structured_error("xueqiu_storage_state_in_repo", "Xueqiu storage-state file must live outside the repo")]
        except ValueError:
            pass
        args.extend(["--storage-state", str(storage_path)])
    result = _run_command(args, timeout=XUEQIU_HEADLESS_TIMEOUT_SECONDS, env={"OUTPUT": "json"})
    if result["status"] != "ok":
        parsed = _parse_json_payload(result.get("stdout", ""))
        if isinstance(parsed, dict) and parsed.get("code"):
            return [], [_structured_error(_normalize_error_code(str(parsed.get("code"))), str(parsed.get("message") or parsed.get("code")))]
        return [], [_structured_error(_direct_cli_error_code(result), _command_failure_message(result))]
    return _fetch_xueqiu_from_chrome_export_file(source_config, export_path, "headless")


def human_xueqiu_label(source: str) -> str:
    return {
        "xueqiu_hot": "雪球热门",
        "xueqiu_daren": "雪球达人",
        "xueqiu_dispute": "雪球争议讨论",
    }.get(source, "雪球")


def _ensure_direct_cli_available() -> dict[str, Any]:
    install_results = []
    twitter = _find_command("twitter")
    rdt = _find_command("rdt")
    opencli = _find_command("opencli")
    if not twitter or not rdt:
        install_result = _run_command(
            [os.sys.executable, "-m", "pip", "install", "--user", "--index-url", "https://pypi.org/simple", "twitter-cli", "rdt-cli"],
            timeout=DIRECT_CLI_INSTALL_TIMEOUT_SECONDS,
        )
        install_results.append(_redacted_command_result(install_result))
        twitter = _find_command("twitter")
        rdt = _find_command("rdt")
    opencli_probe = _probe_opencli(opencli) if opencli else {"status": "failed", "command": None, "browser_bridge": {"status": "failed", "code": "opencli_unavailable"}}
    availability = {
        "status": "ok" if twitter and rdt else "failed",
        "twitter": _probe_cli(twitter, "twitter") if twitter else {"status": "failed", "command": None},
        "rdt": _probe_cli(rdt, "rdt") if rdt else {"status": "failed", "command": None},
        "opencli": opencli_probe,
        "install_attempted": bool(install_results),
        "install_results": install_results,
        "production_connector_ready": False,
    }
    return availability


def _requested_item_count(source_config: dict[str, Any]) -> int:
    source = str(source_config.get("source", "unknown"))
    if source == "reddit":
        per_subreddit = int(source_config.get("max_items_per_subreddit_per_run") or source_config.get("batch_limit") or 10)
        subreddits = source_config.get("subreddits", [])
        return per_subreddit * len(subreddits) if isinstance(subreddits, list) else per_subreddit
    return int(source_config.get("batch_limit") or 10)


def _probe_opencli(command: str) -> dict[str, Any]:
    version = _probe_cli(command, "opencli")
    doctor = _run_command([command, "doctor"], timeout=15)
    doctor_text = _sanitize_text(f"{doctor.get('stdout','')}\n{doctor.get('stderr','')}")
    bridge_ok = doctor["status"] == "ok" and "[OK] Connectivity" in doctor_text and "Extension: not connected" not in doctor_text
    return {
        **version,
        "browser_bridge": {
            "status": "ok" if bridge_ok else "failed",
            "code": "ok" if bridge_ok else _normalize_error_code(doctor_text),
            "message": "connected" if bridge_ok else "Browser Bridge extension is not connected",
            "doctor_returncode": doctor.get("returncode"),
            "duration_seconds": doctor.get("duration_seconds"),
        },
    }


def _probe_cli(command: str, name: str) -> dict[str, Any]:
    version = _run_command([command, "--version"], timeout=10)
    return {
        "status": "ok" if version["status"] == "ok" else "failed",
        "command": command,
        "name": name,
        "version": _first_line(version.get("stdout") or version.get("stderr")) or "unknown",
    }


def _find_command(name: str) -> str | None:
    command = shutil.which(name)
    if command:
        return command
    try:
        user_base = subprocess.run([os.sys.executable, "-m", "site", "--user-base"], text=True, capture_output=True, check=False, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        user_base = ""
    if user_base:
        candidate = Path(user_base) / "bin" / name
        if candidate.exists():
            return str(candidate)
    return None


def _run_command(args: list[str], *, timeout: int, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    try:
        completed = subprocess.run(args, text=True, capture_output=True, check=False, timeout=timeout, env=command_env)
        status = "ok" if completed.returncode == 0 else "failed"
        return {
            "status": status,
            "argv": _redacted_argv(args),
            "returncode": completed.returncode,
            "stdout": _redact_text(completed.stdout),
            "stderr": _redact_text(completed.stderr)[-4000:],
            "duration_seconds": round(time.monotonic() - started, 3),
            "message": "command completed" if status == "ok" else f"command returned {completed.returncode}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "argv": _redacted_argv(args),
            "returncode": None,
            "stdout": _redact_text((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr": _redact_text((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            "duration_seconds": round(time.monotonic() - started, 3),
            "message": f"command timed out after {timeout}s",
            "timeout": True,
        }
    except OSError as exc:
        return {
            "status": "failed",
            "argv": _redacted_argv(args),
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "duration_seconds": round(time.monotonic() - started, 3),
            "message": str(exc),
        }


def _observations_from_cli_result(
    result: dict[str, Any],
    source_config: dict[str, Any],
    *,
    source: str,
    label: str,
    default_url: str,
    tool_id: str,
    connector_prefix: str,
    source_label_override: str | None = None,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if result["status"] != "ok":
        return [], [{**_structured_error(_direct_cli_error_code(result), _command_failure_message(result)), "source": source}]
    parsed = _parse_json_payload(result.get("stdout", ""))
    error = _error_from_envelope(parsed)
    if error:
        return [], [{**error, "source": source}]
    rows = _extract_rows(parsed)
    if not rows:
        text = _sanitize_text(result.get("stdout", ""))
        if _looks_like_auth_or_challenge_text(text):
            return [], [{**_structured_error("auth_or_challenge_required", "direct CLI output indicates auth/challenge/risk-control state"), "source": source}]
        return [], [{**_structured_error("parse_failed", "direct CLI returned no readable rows"), "source": source}]

    observations = []
    row_errors: list[dict[str, Any]] = []
    limit = int(max_items or source_config.get("batch_limit") or 10)
    for row in rows[:limit]:
        source_quality: dict[str, Any] = {
            "source_material_role": "original_source_candidate",
            "source_quality_status": "source_row_observed",
            "source_quality_risk_flags": [],
        }
        if source == "x_list":
            quote = _x_quote_context(row, default_url)
            if quote["status"] == "quote_repost_original_unresolved":
                row_errors.append(
                    {
                        **_structured_error(
                            "x_quote_repost_original_unresolved",
                            "X list row is a quote repost wrapper, but the quoted original post was not available in CLI output",
                        ),
                        "source": source,
                        "wrapper_url": _row_url(row, default_url, source),
                    }
                )
                continue
            if quote["status"] == "quoted_original_traced":
                row = quote["quoted_row"]
                source_quality = {
                    "source_material_role": "original_from_quote_repost",
                    "source_quality_status": "quoted_original_traced",
                    "source_quality_risk_flags": ["quote_repost_wrapper_rewritten_to_original"],
                    "quote_wrapper_url": quote.get("wrapper_url"),
                    "quoted_original_url": quote.get("quoted_original_url"),
                }
        text = _row_copy_text(row)
        if not text or _looks_like_auth_or_challenge_text(text):
            continue
        observation_url = _row_url(row, default_url, source)
        observation = _observation(
            source=source,
            source_label=source_label_override or _row_source_label(row, label, source),
            source_url=observation_url,
            canonical_url=observation_url,
            author=_row_author(row, source, label),
            published_at=_row_published_at(row),
            copy_text=text[:2000],
            image_refs=_image_refs_from_row(row, observation_url),
            engagement=_engagement_from_row(row),
            topic_or_hook=str(row.get("title") or row.get("topic") or label)[:300],
            structured_error=None,
        )
        observation["connector_identity"] = {
            "connector_id": f"{connector_prefix}.{source}.manual_smoke.v1",
            "tool_id": tool_id,
            "tool_version": _cli_version(tool_id),
        }
        observation.update(source_quality)
        if source.startswith("xueqiu_"):
            observation.update(_xueqiu_source_quality(row, text))
        observation["fetch_status"] = "direct_cli_manual_smoke_success"
        observation["evidence_ref"] = f"artifacts/manual_smoke/latest/source_run.json#observations/{observation['observation_id']}"
        observations.append(observation)
    if not observations:
        if row_errors:
            return [], row_errors
        return [], [{**_structured_error("parse_failed", "direct CLI rows did not contain readable source text"), "source": source}]
    return observations, row_errors


def _x_quote_context(row: dict[str, Any], default_url: str) -> dict[str, Any]:
    if not _is_x_quote_repost(row):
        return {"status": "not_quote_repost"}
    quoted = _quoted_original_row(row)
    wrapper_url = _row_url(row, default_url, "x_list")
    if not quoted or not _row_copy_text(quoted):
        return {"status": "quote_repost_original_unresolved", "wrapper_url": wrapper_url}
    quoted_url = _row_url(quoted, default_url, "x_list")
    return {
        "status": "quoted_original_traced",
        "wrapper_url": wrapper_url,
        "quoted_original_url": quoted_url,
        "quoted_row": quoted,
    }


def _is_x_quote_repost(row: dict[str, Any]) -> bool:
    if row.get("is_quote_status") is True or row.get("quoted_status_id") or row.get("quoted_tweet_id"):
        return True
    return any(key in row for key in ("quoted_status", "quoted_tweet", "quoted", "quotedStatus", "quotedTweet"))


def _quoted_original_row(row: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("quoted_status", "quoted_tweet", "quoted", "quotedStatus", "quotedTweet"):
        value = row.get(key)
        if isinstance(value, dict):
            return _unwrap_row(value)
    result = row.get("quoted_status_result")
    if isinstance(result, dict):
        nested = result.get("result") or result.get("tweet") or result.get("data")
        if isinstance(nested, dict):
            return _unwrap_row(nested)
    return None


def _xueqiu_source_quality(row: dict[str, Any], text: str) -> dict[str, Any]:
    detail_status = str(row.get("detail_fetch_status") or "").strip()
    if detail_status:
        full_text_status = "full_text_observed" if detail_status in {"full_text_observed", "api_full_text_observed"} else "detail_attempt_incomplete"
    elif row.get("full_text_observed") is True or len(text) >= 600:
        detail_status = "full_text_observed"
        full_text_status = "full_text_observed"
    else:
        detail_status = "detail_click_required_not_attempted"
        full_text_status = "summary_or_list_excerpt_only"
    risk_flags = []
    if full_text_status != "full_text_observed":
        risk_flags.append("xueqiu_full_text_not_confirmed")
    return {
        "source_material_role": "original_article",
        "source_quality_status": full_text_status,
        "full_text_status": full_text_status,
        "detail_fetch_status": detail_status,
        "article_detail_url": row.get("url"),
        "source_quality_risk_flags": risk_flags,
    }


def _extract_x_cookie_tokens(cookie: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for key in ("auth_token", "ct0"):
        match = re.search(rf"(?:^|;\s*){re.escape(key)}=([^;]+)", cookie)
        if match:
            tokens[key] = urllib.parse.unquote(match.group(1).strip().strip('"'))
    return tokens if {"auth_token", "ct0"} <= set(tokens) else {}


def _parse_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if not cleaned:
        return None
    for candidate in (cleaned, _extract_json_substring(cleaned)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _extract_json_substring(text: str) -> str | None:
    starts = [index for index in (text.find("["), text.find("{")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    end = max(text.rfind("]"), text.rfind("}"))
    if end <= start:
        return None
    return text[start : end + 1]


def _error_from_envelope(parsed: Any) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    ok = parsed.get("ok")
    if ok is False:
        error = parsed.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "direct_cli_read_failed")
            message = str(error.get("message") or error)
        else:
            code = "direct_cli_read_failed"
            message = str(error or "direct CLI returned ok=false")
        return _structured_error(_normalize_error_code(code), message)
    return None


def _extract_rows(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if not isinstance(parsed, dict):
        return []
    rows: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if rows:
            return
        if isinstance(node, list):
            rows.extend(item for item in node if isinstance(item, dict))
            return
        if not isinstance(node, dict):
            return
        for key in ("items", "posts", "tweets", "results", "rows", "children", "entries"):
            value = node.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
                return
        if any(key in node for key in ("text", "title", "id", "url", "permalink", "selftext")):
            rows.append(node)
            return
        data = node.get("data")
        if isinstance(data, (dict, list)):
            visit(data)

    visit(parsed.get("data"))
    visit(parsed)
    return [_unwrap_row(row) for row in rows]


def _unwrap_row(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data")
    if isinstance(data, dict):
        merged = {**data, **{k: v for k, v in row.items() if k != "data"}}
        return merged
    return row


def _row_copy_text(row: dict[str, Any]) -> str:
    parts = []
    for key in ("title", "text", "full_text", "long_text", "note_tweet_text", "content", "body", "selftext", "message", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    note_tweet = row.get("note_tweet") or row.get("noteTweet")
    if isinstance(note_tweet, dict):
        for key in ("text", "full_text", "content"):
            value = note_tweet.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    article = row.get("article") or row.get("card")
    if isinstance(article, dict):
        for key in ("title", "description", "summary"):
            value = article.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(dict.fromkeys(parts))


def _row_url(row: dict[str, Any], default_url: str, source: str) -> str:
    for key in ("url", "permalink", "link", "source_url", "tweet_url"):
        value = row.get(key)
        if isinstance(value, str) and value:
            if value.startswith("/"):
                return "https://www.reddit.com" + value if source == "reddit" else urllib.parse.urljoin(default_url, value)
            if value.startswith("http"):
                return value
    tweet_id = row.get("id") or row.get("tweet_id")
    if source == "x_list" and tweet_id:
        return f"https://x.com/i/web/status/{tweet_id}"
    return default_url


def _row_author(row: dict[str, Any], source: str, label: str) -> str:
    author = row.get("author") or row.get("user") or row.get("username") or row.get("screen_name") or row.get("screenName")
    if isinstance(author, dict):
        author = author.get("username") or author.get("screen_name") or author.get("screenName") or author.get("name")
    if isinstance(author, str) and author:
        return author
    return label if source == "reddit" else source


def _row_source_label(row: dict[str, Any], label: str, source: str) -> str:
    subreddit = row.get("subreddit")
    if source == "reddit" and isinstance(subreddit, str) and subreddit:
        return f"r/{subreddit}"
    return label


def _row_published_at(row: dict[str, Any]) -> str | None:
    for key in ("createdAtISO", "created_at", "createdAt", "published_at", "time", "date", "created_utc"):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            from datetime import datetime, timezone

            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        text = str(value).strip()
        if _looks_like_machine_date(text):
            return text
    return None


def _looks_like_machine_date(text: str) -> bool:
    if not text:
        return False
    if re.match(r"^\d{4}-\d{2}-\d{2}([T\s]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$", text):
        return True
    if re.match(r"^\d{10}(\.\d+)?$", text):
        return True
    return False


def _engagement_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = {}
    nested_metrics = row.get("metrics")
    if isinstance(nested_metrics, dict):
        metrics.update(nested_metrics)
    for key in ("likes", "like_count", "retweets", "retweet_count", "bookmarks", "replies", "reply_count", "comments", "num_comments", "score", "upvotes", "views", "view_count", "quotes"):
        if key in row and row.get(key) is not None:
            metrics[key] = row.get(key)
    return {"status": "observed_at_fetch" if metrics else "real_engagement_unavailable", "metrics": metrics, "metrics_are_fixture": False}


def _image_refs_from_row(row: dict[str, Any], page_url: str) -> list[dict[str, Any]]:
    candidates: list[tuple[str, str, Any, Any]] = []
    for key in ("preview_image_url", "url_overridden_by_dest", "image_url", "thumbnail", "media_url", "media_url_https", "card_image", "article_image"):
        value = row.get(key)
        if isinstance(value, str):
            candidates.append((key, value, row.get("width"), row.get("height")))
    article = row.get("article") or row.get("card")
    if isinstance(article, dict):
        for key in ("image", "image_url", "thumbnail", "preview_image_url"):
            value = article.get(key)
            if isinstance(value, str):
                candidates.append(("card_image", value, article.get("width"), article.get("height")))
    for key in ("media", "photos", "images", "media_urls", "gallery_urls"):
        value = row.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    candidates.append((key, item, None, None))
                elif isinstance(item, dict):
                    for nested_key in ("url", "media_url", "media_url_https", "preview_image_url", "image_url"):
                        nested = item.get(nested_key)
                        if isinstance(nested, str):
                            candidates.append((key, nested, item.get("width"), item.get("height")))
    refs = []
    seen = set()
    for source_field, url, width, height in candidates:
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        if not re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE):
            continue
        seen.add(url)
        record = _image_ref_record(
            url,
            page_url=page_url,
            alt=str(row.get("title") or row.get("text") or "")[:200],
            caption=str(row.get("title") or row.get("text") or "")[:200],
            source_field=source_field,
            width=width,
            height=height,
        )
        if not record["evidence_eligible"]:
            continue
        refs.append(record)
        if len(refs) >= 3:
            break
    return refs


def _direct_cli_error_code(result: dict[str, Any]) -> str:
    return _normalize_error_code(f"{result.get('stdout', '')}\n{result.get('stderr', '')}\n{result.get('message', '')}")


def _normalize_error_code(text: str) -> str:
    lowered = text.lower()
    if "browser_connect" in lowered or "browser bridge extension not connected" in lowered:
        return "opencli_browser_bridge_required"
    if "401" in lowered:
        return "http_401"
    if "403" in lowered or "forbidden" in lowered:
        return "http_403"
    if "rate" in lowered and "limit" in lowered:
        return "rate_limited"
    if any(marker in lowered for marker in ("captcha", "challenge", "login", "auth", "session expired", "no twitter cookies found", "no reddit cookies found")):
        return "auth_or_challenge_required"
    if "not found" in lowered or "unknown command" in lowered:
        return "backend_unsupported"
    if "timed out" in lowered:
        return "network_timeout"
    return "direct_cli_read_failed"


def _command_failure_message(result: dict[str, Any]) -> str:
    text = _sanitize_text("\n".join(str(result.get(key) or "") for key in ("stdout", "stderr", "message")))
    return text[:800] or "direct CLI command failed"


def _looks_like_auth_or_challenge_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("captcha", "login required", "please log in", "challenge", "verify you are human", "请登录", "验证码", "no twitter cookies found", "session expired"))


def _x_list_id(url: str) -> str:
    match = re.search(r"/lists/(\d+)", url)
    return match.group(1) if match else "2056032482127175889"


def _cli_version(tool_id: str) -> str:
    name = "twitter" if tool_id == "twitter-cli" else "rdt" if tool_id == "rdt-cli" else tool_id
    command = _find_command(name)
    if not command:
        return "unavailable"
    result = _run_command([command, "--version"], timeout=10)
    return _first_line(result.get("stdout") or result.get("stderr")) or "unknown"


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return _sanitize_text(text).splitlines()[0][:120] if _sanitize_text(text) else ""


def _sanitize_text(text: str) -> str:
    redacted = _redact_text(text)
    lines = [line.strip() for line in redacted.splitlines() if line.strip()]
    return "\n".join(lines)[:4000]


def _redacted_command_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {key: _redacted_mapping(value) for key, value in result.items()}


def _redacted_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redacted_mapping(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_mapping(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redacted_argv(args: list[str]) -> list[str]:
    return [_redact_text(arg) for arg in args]


def _redact_text(text: str) -> str:
    from .manual_smoke import _redact_text as manual_redact

    return manual_redact(text)


def _write_direct_cli_json(path: Path, data: Any) -> None:
    findings = find_raw_secret_material(data)
    if findings:
        raise ValueError(f"direct-cli artifact failed redaction scan at {findings}")
    write_json_artifact(path, data)

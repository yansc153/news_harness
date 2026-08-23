"""Kaipanla market-signal discovery module.

Calls KPL endpoints to discover today's active themes and their constituent
stocks, then produces an HourlyTargetSet for downstream Xueqiu collection.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "hourly_targeting.v1.json"
DEFAULT_CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "interfaces" / "providers" / "kaipanla" / "news-harness-cases.v1.json"
DEFAULT_SCHEMAS_DIR = DEFAULT_CONTRACTS_PATH.parent / "schemas"

# Environment variable overrides for KPL identity fields. When set, they
# replace the placeholder values in the contract file. This keeps real
# device/user identifiers outside the repo.
KPL_DEVICE_ID_ENV = "NEWS_HARNESS_KPL_DEVICE_ID"
KPL_TOKEN_ENV = "NEWS_HARNESS_KPL_TOKEN"
KPL_USER_ID_ENV = "NEWS_HARNESS_KPL_USER_ID"
KPL_DEVICE_ID_FILE_ENV = "NEWS_HARNESS_KPL_DEVICE_ID_FILE"
KPL_TOKEN_FILE_ENV = "NEWS_HARNESS_KPL_TOKEN_FILE"
KPL_USER_ID_FILE_ENV = "NEWS_HARNESS_KPL_USER_ID_FILE"


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or DEFAULT_CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def load_contracts(path: Path | None = None) -> dict[str, dict]:
    p = path or DEFAULT_CONTRACTS_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return {c["endpoint_id"]: c for c in data["contracts"]}


def normalize_symbol(raw_code: str, market_hint: str = "") -> str:
    """Convert bare stock code to full symbol like SH600000 / SZ000001."""
    code = str(raw_code).strip()
    if code.upper().startswith(("SH", "SZ", "BJ")):
        normalized = code.upper()
        return normalized if len(normalized) == 8 and normalized[2:].isdigit() else ""
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) != 6:
        return ""
    if digits.startswith("6"):
        return f"SH{digits}"
    elif digits.startswith(("0", "3")):
        return f"SZ{digits}"
    elif digits.startswith(("4", "8")):
        return f"BJ{digits}"
    return f"SZ{digits}"


def normalize_kpl_response(endpoint_id: str, raw: dict) -> list[dict]:
    """Normalize a KPL endpoint response into a unified list of themes or stocks."""
    errcode = str(raw.get("errcode", "1"))
    if errcode != "0":
        raise ValueError(f"KPL {endpoint_id} returned errcode={errcode}")

    if endpoint_id == "KPL-41":
        rows = raw.get("list") or []
        themes = []
        for row in rows[:20]:
            if not isinstance(row, list) or len(row) < 2:
                continue
            # Current W41 layout: [plate_id, plate_name, stock_count, pct_change, ...].
            # Historical fixture layout: [plate_name, pct_change, limit_up_count].
            current_layout = str(row[0]).isdigit() and isinstance(row[1], str)
            if current_layout:
                theme = {
                    "theme_id": str(row[0]),
                    "theme_name": str(row[1]),
                    "pct_change": float(row[3]) if len(row) > 3 and row[3] is not None else 0.0,
                    "constituent_count": int(row[2]) if row[2] is not None else 0,
                    "limit_up_count": None,
                }
            else:
                theme = {
                    "theme_name": str(row[0]),
                    "pct_change": float(row[1]) if row[1] is not None else 0.0,
                    "limit_up_count": int(row[2]) if len(row) > 2 and row[2] is not None else 0,
                }
            themes.append(theme)
        return themes

    elif endpoint_id == "KPL-47":
        items = raw.get("List") or []
        stocks = []
        for item in items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("StockID", "")).strip()
            name = str(item.get("StockName", "")).strip()
            reason = str(item.get("Reason", "")).strip()
            if not code:
                continue
            stocks.append({"symbol": normalize_symbol(code), "stock_code": code, "stock_name": name, "reason": reason})
        return stocks

    elif endpoint_id in ("KPL-49", "KPL-50", "KPL-51", "KPL-52", "KPL-53"):
        level_map = {"KPL-49": 1, "KPL-50": 2, "KPL-51": 3, "KPL-52": 4, "KPL-53": 5}
        level = level_map[endpoint_id]
        info = raw.get("info") or []
        if isinstance(info, list) and info and isinstance(info[0], list) and info[0] and isinstance(info[0][0], list):
            info = info[0]
        stocks = []
        for row in info:
            if not isinstance(row, list) or len(row) < 2:
                continue
            code = str(row[0]).strip() if row[0] else ""
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            if not code:
                continue
            stocks.append({
                "symbol": normalize_symbol(code),
                "stock_code": code,
                "stock_name": name,
                "board_level": level,
                "reason": str(row[5]).strip() if len(row) > 5 and row[5] else "",
                "concepts": str(row[12]).strip() if len(row) > 12 and row[12] else "",
                "upstream_theme_id": str(row[19]).strip() if len(row) > 19 and row[19] else "",
            })
        return stocks

    elif endpoint_id == "KPL-79":
        stocks_list = raw.get("List3") or []
        results = []
        for row in stocks_list:
            if not isinstance(row, list) or len(row) < 2:
                continue
            code = str(row[0]).strip() if row[0] else ""
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            if not code:
                continue
            results.append({"symbol": normalize_symbol(code), "stock_code": code, "stock_name": name})
        return results

    elif endpoint_id == "KPL-80":
        rows = raw.get("List") or []
        results = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            code = str(row[0]).strip() if row[0] else ""
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            if not code:
                continue
            results.append({"symbol": normalize_symbol(code), "stock_code": code, "stock_name": name})
        return results

    elif endpoint_id == "KPL-46":
        codes = raw.get("Stocks") or []
        results = []
        for code in codes:
            if not isinstance(code, str) or not code.strip():
                continue
            results.append({"symbol": normalize_symbol(code.strip()), "stock_code": code.strip()})
        return results

    elif endpoint_id == "KPL-115":
        rows = raw.get("info") or []
        results = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            code = str(row[0]).strip() if row[0] else ""
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            if not code:
                continue
            results.append({"symbol": normalize_symbol(code), "stock_code": code, "stock_name": name})
        return results

    elif endpoint_id == "KPL-94":
        rows = raw.get("ListJX") or raw.get("List") or []
        results = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            results.append({
                "theme_id": str(row[0]),
                "theme_name": str(row[1]),
                "pct_change": float(row[2]) if len(row) > 2 and row[2] is not None else None,
            })
        return results

    return []


def validate_kpl_response(endpoint_id: str, raw: dict, schemas_dir: Path | None = None) -> None:
    """Validate the observed top-level response contract and report the exact drift path."""
    schema_path = (schemas_dir or DEFAULT_SCHEMAS_DIR) / f"{endpoint_id}.schema.json"
    if not schema_path.exists():
        raise ValueError(f"KPL {endpoint_id} response schema missing: {schema_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"KPL {endpoint_id} schema drift at $: expected object")
    for key in schema.get("required", []):
        if key not in raw:
            raise ValueError(f"KPL {endpoint_id} schema drift at $.{key}: required field missing")
    type_map = {"string": str, "integer": int, "number": (int, float), "array": list, "object": dict}
    for key, field_schema in (schema.get("properties") or {}).items():
        if key not in raw or not isinstance(field_schema, dict):
            continue
        expected = field_schema.get("type")
        expected_names = expected if isinstance(expected, list) else [expected]
        allowed = tuple(type_map[name] for name in expected_names if name in type_map)
        if allowed and not isinstance(raw[key], allowed):
            raise ValueError(f"KPL {endpoint_id} schema drift at $.{key}: expected {expected_names}")


def deduplicate_stocks(themes_with_stocks: list[dict], *, max_total: int = 12) -> list[dict]:
    """Deduplicate stocks across themes, preserving first-seen order, cap at max_total."""
    seen: dict[str, dict] = {}
    result: list[dict] = []
    for theme_block in themes_with_stocks:
        theme_id = theme_block.get("theme_id", "")
        for stock in theme_block.get("stocks", []):
            sym = stock.get("symbol", "")
            if not sym:
                continue
            if sym in seen:
                existing = seen[sym]
                existing["theme_ids"] = list(dict.fromkeys([
                    *(existing.get("theme_ids") or []),
                    *(stock.get("theme_ids") or []),
                    *([theme_id] if theme_id else []),
                ]))
                existing["signal_sources"] = list(dict.fromkeys([
                    *(existing.get("signal_sources") or []),
                    *(stock.get("signal_sources") or []),
                ]))
                continue
            entry = {**stock}
            if theme_id:
                entry["theme_ids"] = list(dict.fromkeys([*(entry.get("theme_ids") or []), theme_id]))
            result.append(entry)
            seen[sym] = entry
            if len(result) >= max_total:
                return result
    return result


def _limit_stocks_per_theme(stocks: list[dict], *, max_per_theme: int, max_total: int) -> list[dict]:
    """Keep upstream order while preventing one identified theme from taking the whole target set."""
    selected: list[dict] = []
    theme_counts: dict[str, int] = {}
    for stock in stocks:
        theme_ids = stock.get("theme_ids") or []
        if theme_ids and all(theme_counts.get(theme_id, 0) >= max_per_theme for theme_id in theme_ids):
            continue
        selected.append(stock)
        for theme_id in theme_ids:
            theme_counts[theme_id] = theme_counts.get(theme_id, 0) + 1
        if len(selected) >= max_total:
            break
    return selected


def _fetch_endpoint(
    contract: dict,
    *,
    timeout_seconds: int = 15,
    query_overrides: dict[str, str] | None = None,
) -> dict:
    """Make a single GET call to a KPL endpoint using its contract."""
    server = contract["server"]
    path = contract["path"]
    env_overrides = {
        "DeviceID": _identity_override(KPL_DEVICE_ID_ENV, KPL_DEVICE_ID_FILE_ENV),
        "Token": _identity_override(KPL_TOKEN_ENV, KPL_TOKEN_FILE_ENV),
        "UserID": _identity_override(KPL_USER_ID_ENV, KPL_USER_ID_FILE_ENV),
    }
    params = {}
    for k, v in contract.get("query_template", {}).items():
        val = str(v)
        for env_key, env_val in env_overrides.items():
            if k == env_key and env_val is not None:
                val = env_val
                break
        params[k] = val
    for key, value in (query_overrides or {}).items():
        if key not in params:
            raise ValueError(f"KPL query override is not registered in contract: {key}")
        params[key] = str(value)
    qs = urllib.parse.urlencode(params)
    url = f"{server}{path}?{qs}"
    headers = contract.get("headers", {})
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise ConnectionError(f"KPL fetch failed: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        end = body.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(body[start:end])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"KPL response not parseable as JSON")


def _identity_override(value_env: str, file_env: str) -> str | None:
    """Resolve a KPL identity from a direct env value or an external secret file."""
    direct = os.environ.get(value_env)
    if direct is not None:
        return direct
    file_ref = os.environ.get(file_env)
    if not file_ref:
        return None
    path = Path(file_ref)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read KPL identity file from {file_env}: {path}") from exc


def build_target_set(config: dict) -> dict:
    """Build an HourlyTargetSet from live KPL API calls."""
    contracts = load_contracts()
    enabled = config.get("enabled_endpoints", [])
    max_themes = config.get("max_themes", 5)
    max_per_theme = config.get("max_stocks_per_theme", 3)
    max_total = config.get("max_stocks_total", 12)
    timeout_seconds = int(config.get("timeout_seconds", 15))
    request_delay_seconds = float(config.get("request_delay_seconds", 1))

    all_themes: list[dict] = []
    all_stock_sources: dict[str, list[dict]] = {}
    errors: list[dict] = []
    endpoint_results: list[dict] = []
    dynamic_endpoints = {"KPL-46", "KPL-47", "KPL-78", "KPL-80", "KPL-94"}

    for endpoint_index, eid in enumerate(enabled):
        if eid in dynamic_endpoints:
            continue
        contract = contracts.get(eid)
        if not contract:
            errors.append({"source": eid, "error": "contract_missing"})
            endpoint_results.append({"endpoint_id": eid, "status": "failed", "error_code": "contract_missing"})
            continue
        endpoint_started = time.monotonic()
        try:
            raw = _fetch_endpoint(contract, timeout_seconds=timeout_seconds)
            validate_kpl_response(eid, raw)
            normalized = normalize_kpl_response(eid, raw)
            if eid == "KPL-41":
                for t in normalized[:max_themes]:
                    t.setdefault("theme_id", t["theme_name"])
                    all_themes.append(t)
            else:
                all_stock_sources[eid] = normalized[:30]
            endpoint_results.append({
                "endpoint_id": eid,
                "status": "ok",
                "row_count": len(normalized),
                "duration_seconds": round(time.monotonic() - endpoint_started, 3),
                "response_hash": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
            })
        except Exception as exc:
            errors.append({"source": eid, "error": str(exc)[:300]})
            endpoint_results.append({
                "endpoint_id": eid,
                "status": "failed",
                "error_code": type(exc).__name__,
                "duration_seconds": round(time.monotonic() - endpoint_started, 3),
            })
        if request_delay_seconds > 0 and endpoint_index < len(enabled) - 1:
            time.sleep(request_delay_seconds)

    # Build a flat stock pool from direct sources, preserving upstream order.
    flat_pool: list[dict] = []
    pool_by_symbol: dict[str, dict] = {}
    for eid in enabled:
        for stock in all_stock_sources.get(eid, []):
            sym = stock.get("symbol", "")
            if not sym:
                continue
            entry = {**stock}
            entry["signal_sources"] = list(dict.fromkeys([*(entry.get("signal_sources") or []), eid]))
            reason = str(entry.get("reason") or "")
            upstream_theme_id = str(entry.get("upstream_theme_id") or "")
            entry["theme_ids"] = [
                theme["theme_id"] for theme in all_themes[:max_themes]
                if theme["theme_id"] == upstream_theme_id
                or (theme["theme_name"] and theme["theme_name"] in reason)
            ]
            if sym in pool_by_symbol:
                current = pool_by_symbol[sym]
                current["signal_sources"] = list(dict.fromkeys([*current.get("signal_sources", []), eid]))
                current["theme_ids"] = list(dict.fromkeys([*current.get("theme_ids", []), *entry["theme_ids"]]))
                if not current.get("reason") and entry.get("reason"):
                    current["reason"] = entry["reason"]
                continue
            pool_by_symbol[sym] = entry
            flat_pool.append(entry)

    flat_pool.sort(key=lambda stock: (len(stock.get("signal_sources") or []), bool(stock.get("theme_ids")), stock.get("board_level") or 0), reverse=True)
    for stock in flat_pool:
        stock["selection_reasons"] = [f"signal:{source}" for source in stock.get("signal_sources", [])]
        if stock.get("theme_ids"):
            stock["selection_reasons"].append("active_theme_match")
        if len(stock.get("signal_sources") or []) > 1:
            stock["selection_reasons"].append("multi_signal_intersection")

    flat_pool = _limit_stocks_per_theme(flat_pool, max_per_theme=max_per_theme, max_total=max_total)
    theme_blocks = [{"theme_id": "", "stocks": flat_pool}] if flat_pool else []

    if not theme_blocks and all_stock_sources:
        flat = []
        for stocks in all_stock_sources.values():
            flat.extend(stocks)
        theme_blocks = [{"theme_id": "_direct", "stocks": flat}]

    final_stocks = deduplicate_stocks(theme_blocks, max_total=max_total)

    if "KPL-78" in enabled:
        contract = contracts.get("KPL-78")
        for theme in all_themes[:max_themes]:
            started = time.monotonic()
            try:
                raw = _fetch_endpoint(contract, timeout_seconds=timeout_seconds, query_overrides={"PlateID": theme["theme_id"]})
                validate_kpl_response("KPL-78", raw)
                endpoint_results.append({
                    "endpoint_id": "KPL-78", "status": "ok", "theme_id": theme["theme_id"],
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "response_hash": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                })
            except Exception as exc:
                errors.append({"source": "KPL-78", "theme_id": theme["theme_id"], "error": str(exc)[:300]})
                endpoint_results.append({"endpoint_id": "KPL-78", "status": "failed", "theme_id": theme["theme_id"], "error_code": type(exc).__name__})

    if "KPL-94" in enabled:
        contract = contracts.get("KPL-94")
        for stock in final_stocks:
            started = time.monotonic()
            try:
                raw = _fetch_endpoint(contract, timeout_seconds=timeout_seconds, query_overrides={"StockID": stock["stock_code"]})
                validate_kpl_response("KPL-94", raw)
                concepts = normalize_kpl_response("KPL-94", raw)
                stock["theme_ids"] = list(dict.fromkeys([*stock.get("theme_ids", []), *(row["theme_id"] for row in concepts)]))
                stock["theme_names"] = list(dict.fromkeys(row["theme_name"] for row in concepts))
                endpoint_results.append({
                    "endpoint_id": "KPL-94", "status": "ok", "symbol": stock["symbol"], "row_count": len(concepts),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "response_hash": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                })
            except Exception as exc:
                errors.append({"source": "KPL-94", "symbol": stock["symbol"], "error": str(exc)[:300]})
                endpoint_results.append({"endpoint_id": "KPL-94", "status": "failed", "symbol": stock["symbol"], "error_code": type(exc).__name__})

    if "KPL-47" in enabled:
        contract = contracts.get("KPL-47")
        for stock in final_stocks:
            started = time.monotonic()
            try:
                raw = _fetch_endpoint(contract, timeout_seconds=timeout_seconds, query_overrides={"StockID": stock["stock_code"]})
                validate_kpl_response("KPL-47", raw)
                reasons = normalize_kpl_response("KPL-47", raw)
                matching = next((row for row in reasons if row.get("stock_code") == stock["stock_code"]), None)
                if matching and matching.get("reason"):
                    stock["limit_up_reason"] = matching["reason"]
                    stock["selection_reasons"] = list(dict.fromkeys([*stock.get("selection_reasons", []), "limit_up_reason_confirmed"]))
                endpoint_results.append({
                    "endpoint_id": "KPL-47", "status": "ok", "symbol": stock["symbol"], "row_count": len(reasons),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "response_hash": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                })
            except Exception as exc:
                errors.append({"source": "KPL-47", "symbol": stock["symbol"], "error": str(exc)[:300]})
                endpoint_results.append({"endpoint_id": "KPL-47", "status": "failed", "symbol": stock["symbol"], "error_code": type(exc).__name__})

    generated_at = datetime.now(timezone.utc)
    cycle_id = f"target_{generated_at.strftime('%Y%m%d_%H%M%S')}"
    target_set = {
        "object_type": "HourlyTargetSet",
        "cycle_id": cycle_id,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "config_used": {k: config[k] for k in ("max_themes", "max_stocks_per_theme", "max_stocks_total") if k in config},
        "endpoint_ids_called": enabled,
        "endpoint_results": endpoint_results,
        "themes_discovered": all_themes[:max_themes],
        "target_stocks": final_stocks,
        "stock_count": len(final_stocks),
        "structured_errors": errors,
        "status": "partial_failure" if errors else ("ok" if final_stocks else "no_targets"),
    }
    payload_for_hash = {k: v for k, v in target_set.items()}
    target_set["output_hash"] = hashlib.sha256(json.dumps(payload_for_hash, sort_keys=True).encode()).hexdigest()
    return target_set


def validate_hourly_target_set(target_set: dict, *, max_stocks: int = 12) -> None:
    """Fail before artifact write when the Target Set violates its runtime contract."""
    if target_set.get("object_type") != "HourlyTargetSet":
        raise ValueError("HourlyTargetSet schema drift at $.object_type")
    stocks = target_set.get("target_stocks")
    if not isinstance(stocks, list):
        raise ValueError("HourlyTargetSet schema drift at $.target_stocks")
    if len(stocks) > max_stocks or target_set.get("stock_count") != len(stocks):
        raise ValueError("HourlyTargetSet schema drift at $.stock_count")
    symbols = [stock.get("symbol") for stock in stocks if isinstance(stock, dict)]
    if len(symbols) != len(stocks) or any(not _is_normalized_symbol(symbol) for symbol in symbols):
        raise ValueError("HourlyTargetSet schema drift at $.target_stocks[*].symbol")
    if len(symbols) != len(set(symbols)):
        raise ValueError("HourlyTargetSet schema drift: duplicate target symbol")
    if target_set.get("status") not in {"ok", "no_targets", "partial_failure"}:
        raise ValueError("HourlyTargetSet schema drift at $.status")
    if not isinstance(target_set.get("endpoint_results"), list):
        raise ValueError("HourlyTargetSet schema drift at $.endpoint_results")
    if not target_set.get("output_hash"):
        raise ValueError("HourlyTargetSet schema drift at $.output_hash")


def _is_normalized_symbol(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit()

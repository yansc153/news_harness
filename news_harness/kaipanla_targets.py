"""Kaipanla market-signal discovery module.

Calls KPL endpoints to discover today's active themes and their constituent
stocks, then produces an HourlyTargetSet for downstream Xueqiu collection.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "hourly_targeting.v1.json"
DEFAULT_CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "interfaces" / "providers" / "kaipanla" / "news-harness-cases.v1.json"


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
        return code.upper()
    digits = "".join(ch for ch in code if ch.isdigit())
    if not digits:
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
            if not isinstance(row, list) or len(row) < 1:
                continue
            theme = {
                "theme_name": str(row[0]),
                "pct_change": float(row[1]) if len(row) > 1 and row[1] else 0.0,
                "limit_up_count": int(row[2]) if len(row) > 2 and row[2] else 0,
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
        stocks = []
        for row in info:
            if not isinstance(row, list) or len(row) < 2:
                continue
            code = str(row[0]).strip() if row[0] else ""
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            if not code:
                continue
            stocks.append({"symbol": normalize_symbol(code), "stock_code": code, "stock_name": name, "board_level": level})
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

    return []


def deduplicate_stocks(themes_with_stocks: list[dict], *, max_total: int = 12) -> list[dict]:
    """Deduplicate stocks across themes, preserving first-seen order, cap at max_total."""
    seen: set[str] = set()
    result: list[dict] = []
    for theme_block in themes_with_stocks:
        theme_id = theme_block.get("theme_id", "")
        for stock in theme_block.get("stocks", []):
            sym = stock.get("symbol", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            entry = {**stock}
            if theme_id:
                entry["theme_ids"] = [theme_id]
            result.append(entry)
            if len(result) >= max_total:
                return result
    return result


def _fetch_endpoint(contract: dict) -> dict:
    """Make a single GET call to a KPL endpoint using its contract."""
    server = contract["server"]
    path = contract["path"]
    params = {k: str(v) for k, v in contract.get("query_template", {}).items()}
    qs = urllib.parse.urlencode(params)
    url = f"{server}{path}?{qs}"
    headers = contract.get("headers", {})
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
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


def build_target_set(config: dict) -> dict:
    """Build an HourlyTargetSet from live KPL API calls."""
    contracts = load_contracts()
    enabled = config.get("enabled_endpoints", [])
    max_themes = config.get("max_themes", 5)
    max_per_theme = config.get("max_stocks_per_theme", 3)
    max_total = config.get("max_stocks_total", 12)

    all_themes: list[dict] = []
    all_stock_sources: dict[str, list[dict]] = {}
    errors: list[dict] = []

    for eid in enabled:
        contract = contracts.get(eid)
        if not contract:
            errors.append({"source": eid, "error": "contract_missing"})
            continue
        try:
            raw = _fetch_endpoint(contract)
            normalized = normalize_kpl_response(eid, raw)
            if eid == "KPL-41":
                for t in normalized[:max_themes]:
                    t["theme_id"] = t["theme_name"]
                    all_themes.append(t)
            else:
                all_stock_sources[eid] = normalized[:30]
        except Exception as exc:
            errors.append({"source": eid, "error": str(exc)[:300]})

    # Build a flat stock pool from direct sources, preserving upstream order.
    flat_pool: list[dict] = []
    seen_in_pool: set[str] = set()
    for eid in enabled:
        for stock in all_stock_sources.get(eid, []):
            sym = stock.get("symbol", "")
            if not sym or sym in seen_in_pool:
                continue
            seen_in_pool.add(sym)
            entry = {**stock}
            if all_themes:
                entry["theme_ids"] = [t["theme_id"] for t in all_themes[:max_themes]]
            flat_pool.append(entry)

    if all_themes:
        theme_blocks = [{"theme_id": "_discovered", "stocks": flat_pool}]
    elif flat_pool:
        theme_blocks = [{"theme_id": "_direct", "stocks": flat_pool}]
    else:
        theme_blocks = []

    if not theme_blocks and all_stock_sources:
        flat = []
        for stocks in all_stock_sources.values():
            flat.extend(stocks)
        theme_blocks = [{"theme_id": "_direct", "stocks": flat}]

    final_stocks = deduplicate_stocks(theme_blocks, max_total=max_total)

    cycle_id = f"target_{time.strftime('%Y%m%d_%H%M%S')}"
    target_set = {
        "object_type": "HourlyTargetSet",
        "cycle_id": cycle_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config_used": {k: config[k] for k in ("max_themes", "max_stocks_per_theme", "max_stocks_total") if k in config},
        "endpoint_ids_called": enabled,
        "themes_discovered": all_themes[:max_themes],
        "target_stocks": final_stocks,
        "stock_count": len(final_stocks),
        "structured_errors": errors,
        "status": "ok" if final_stocks else ("no_targets" if not errors else "partial_failure"),
    }
    payload_for_hash = {k: v for k, v in target_set.items()}
    target_set["output_hash"] = hashlib.sha256(json.dumps(payload_for_hash, sort_keys=True).encode()).hexdigest()
    return target_set

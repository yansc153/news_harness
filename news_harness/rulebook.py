"""Rulebook: internal rule discovery, validation, and promotion (V2).

V2: Rulebook only consumes OutcomeEvaluation records (not raw growth_scores).
V1 shadow-only: rules are compute-and-log, never applied to production scores.
Activation requires promotion gate, which V1 blocks.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .fixtures import ROOT
from .paths import write_json_artifact


class RulebookPromotionBlocked(Exception):
    """Raised when a rule promotion is blocked by version policy."""


RULEBOOK_VERSION = 2
RULEBOOK_PATH = ROOT / "artifacts" / "manual_smoke" / "latest" / "rulebook.json"
CASE_MANUAL_DIR = ROOT / "artifacts" / "manual_smoke" / "latest" / "case_manual"

MIN_CASES_HYPOTHESIS = 3
MIN_CASES_SHADOW = 3
MIN_CASES_VERIFIED = 5
LIFT_THRESHOLD = 1.5
REJECT_LIFT_THRESHOLD = 1.0
EXPIRY_DAYS = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _empty_rulebook() -> dict[str, Any]:
    return {
        "object_type": "Rulebook",
        "rulebook_version": RULEBOOK_VERSION,
        "updated_at": _utc_now(),
        "rules": [],
        "hypotheses": [],
        "case_manual_refs": [],
    }


def load(path: Path = RULEBOOK_PATH) -> dict[str, Any]:
    if not path.exists():
        return _empty_rulebook()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("object_type") == "Rulebook":
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return _empty_rulebook()


def save(rulebook: dict[str, Any], path: Path = RULEBOOK_PATH) -> str:
    rulebook["updated_at"] = _utc_now()
    out_path = str(path)
    write_json_artifact(out_path, rulebook)
    return out_path


def _rule_key(content_format: str, hook_types: tuple[str, ...], emotion: str) -> str:
    hooks = "_".join(sorted(hook_types)) if hook_types else "none"
    return f"{content_format}__{hooks}__{emotion}"


def _emotion_bucket(emotion: dict[str, Any]) -> str:
    primary = str(emotion.get("primary", "neutral")) if isinstance(emotion, dict) else "neutral"
    intensity = float(emotion.get("intensity", 0)) if isinstance(emotion, dict) else 0
    if intensity >= 0.6:
        return f"high_{primary}"
    return f"low_{primary}"


def discover_rules_from_evaluations(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Discover rules from OutcomeEvaluation records (V2).

    Only consumes evaluations with learning_eligibility in
    ('learn_neutral', 'learn_negative', 'learn_positive_shadow').
    Groups by structure_tags and computes lift from relative_growth
    and platform_normalized_growth. Max rule status is 'shadow' in V1.
    """
    eligible = [
        e for e in evaluations
        if e.get("learning_eligibility") in ("learn_neutral", "learn_negative", "learn_positive_shadow")
    ]
    if not eligible:
        return []

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in eligible:
        st = e.get("structure_tags", {})
        if not isinstance(st, dict):
            continue
        content_format = st.get("content_format", "unknown")
        hook_types = tuple(sorted(st.get("hook_types", ["none_detectable"])))
        emotion_raw = st.get("emotion", "neutral")
        emotion_bucket = _emotion_bucket(emotion_raw) if isinstance(emotion_raw, dict) else str(emotion_raw)
        key = _rule_key(content_format, hook_types, emotion_bucket)
        groups[key].append(e)

    now = _utc_now()
    rules: list[dict[str, Any]] = []
    for key, group in groups.items():
        n = len(group)
        if n < MIN_CASES_HYPOTHESIS:
            continue
        relative_growths = [float(e["relative_growth"]) for e in group if isinstance(e.get("relative_growth"), (int, float))]
        platform_growths = [float(e["platform_normalized_growth"]) for e in group if isinstance(e.get("platform_normalized_growth"), (int, float))]
        avg_relative = mean(relative_growths) if relative_growths else 0.0
        avg_platform = mean(platform_growths) if platform_growths else 0.0
        lift = avg_platform if avg_platform > 0 else avg_relative
        if lift <= LIFT_THRESHOLD and n < MIN_CASES_VERIFIED:
            continue
        if lift < REJECT_LIFT_THRESHOLD and n >= MIN_CASES_VERIFIED:
            continue

        status = "shadow" if n >= MIN_CASES_SHADOW else "hypothesis"
        sample = group[0]
        st = sample.get("structure_tags", {})
        emotion_raw = st.get("emotion", "neutral")
        emotion_bucket = _emotion_bucket(emotion_raw) if isinstance(emotion_raw, dict) else str(emotion_raw)

        rules.append({
            "rule_id": key,
            "condition": {
                "content_format": [st.get("content_format", "unknown")],
                "hook_types_any": list(st.get("hook_types", ["none_detectable"])),
                "emotion_bucket": emotion_bucket,
            },
            "action": {"type": "boost_topic_score", "adjustment": round(min(0.20, max(0.0, (lift - 1.0) * 0.15)), 4)},
            "evidence": {
                "verified_case_count": n,
                "avg_growth_lift": round(lift, 2),
                "avg_relative_growth": round(avg_relative, 2),
                "avg_platform_normalized_growth": round(avg_platform, 2),
                "sources": sorted({str(e.get("platform", "")) for e in group}),
                "first_seen_at": now,
                "last_verified_at": now,
            },
            "status": status,
            "created_at": now,
        })
    return rules


def apply_rules_shadow(items: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute hypothetical adjusted scores from shadow rules (V2, compute-and-log)."""
    shadow_rules = [r for r in rules if r.get("status") == "shadow"]
    adjustments: list[dict[str, Any]] = []
    for item in items:
        item_id = item.get("id", item.get("item_id", "unknown"))
        original_score = float(item.get("hotness_score", item.get("score", 0)))
        st = item.get("structure_tags", item.get("structure_analysis", {}))
        if not isinstance(st, dict):
            st = {}
        content_format = st.get("content_format", "")
        hook_types = set(st.get("hook_types", []))
        emotion_raw = st.get("emotion", {})
        emotion_bucket = _emotion_bucket(emotion_raw) if isinstance(emotion_raw, dict) else str(emotion_raw or "neutral")
        matching_rule_ids: list[str] = []
        shadow_adjustment = 0.0
        reasons: list[str] = []
        for rule in shadow_rules:
            cond = rule.get("condition", {})
            if cond.get("content_format") and content_format not in cond["content_format"]:
                continue
            if cond.get("hook_types_any") and not hook_types & set(cond["hook_types_any"]):
                continue
            if cond.get("emotion_bucket") and emotion_bucket != cond["emotion_bucket"]:
                continue
            adj = float(rule.get("action", {}).get("adjustment", 0))
            if rule.get("action", {}).get("type") == "boost_topic_score":
                shadow_adjustment += adj
            elif rule.get("action", {}).get("type") == "penalize_topic_score":
                shadow_adjustment -= adj
            matching_rule_ids.append(str(rule.get("rule_id", "")))
            reasons.append(f"Rule {rule.get('rule_id')}: adjustment={adj}")
        adjustments.append({
            "item_id": item_id,
            "original_score": original_score,
            "shadow_adjusted_score": round(original_score + shadow_adjustment, 4),
            "shadow_adjustment": round(shadow_adjustment, 4),
            "matching_rule_ids": matching_rule_ids,
            "adjustment_reason": "; ".join(reasons) if reasons else "no matching shadow rules",
        })
    return {
        "object_type": "CalibrationReport",
        "generated_at": _utc_now(),
        "shadow_rules_count": len(shadow_rules),
        "items_evaluated": len(items),
        "adjustments": adjustments,
    }


def _discover_rules_legacy(
    items: list[dict[str, Any]],
    rulebook: dict[str, Any],
    baseline_growth: float | None = None,
) -> dict[str, Any]:
    """DEPRECATED: Legacy rule discovery (rulebook.v1). Gated behind rulebook_version < 2.
    Use discover_rules_from_evaluations() for V2+ rulebooks."""
    if not items:
        return rulebook

    if baseline_growth is None:
        scores = [float(i.get("growth_score", 0)) for i in items if isinstance(i.get("growth_score"), (int, float))]
        baseline_growth = mean(scores) if scores else 0.01

    baseline = max(baseline_growth, 0.01)
    existing_rules = {r.get("rule_id"): r for r in rulebook.get("rules", [])}
    existing_hypotheses = {h.get("hypothesis_id"): h for h in rulebook.get("hypotheses", [])}

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        sa = item.get("structure_analysis", {})
        if not isinstance(sa, dict):
            continue
        content_format = sa.get("content_format", "unknown")
        hook_types = tuple(sorted(sa.get("hook_types", ["none_detectable"])))
        emotion_bucket = _emotion_bucket(sa.get("emotion", {}))
        key = _rule_key(content_format, hook_types, emotion_bucket)
        groups[key].append(item)

    now = _utc_now()

    for key, group in groups.items():
        growth_scores = [
            float(g.get("growth_score", 0))
            for g in group
            if isinstance(g.get("growth_score"), (int, float))
        ]
        if not growth_scores:
            continue
        avg_growth = mean(growth_scores)
        lift = avg_growth / baseline
        n = len(group)
        sources = sorted({str(g.get("source", "")) for g in group})

        existing_rule = existing_rules.get(key)
        existing_hyp = existing_hypotheses.get(key)

        if n < MIN_CASES_HYPOTHESIS:
            if existing_hyp is None:
                existing_hypotheses[key] = {
                    "hypothesis_id": key,
                    "description": f"Pattern: {key}, n={n}",
                    "condition": {
                        "content_format": group[0].get("structure_analysis", {}).get("content_format"),
                        "hook_types_any": list(group[0].get("structure_analysis", {}).get("hook_types", [])),
                    },
                    "observations": n,
                    "min_observations_for_shadow": MIN_CASES_HYPOTHESIS,
                    "status": "collecting",
                    "created_at": now,
                }
            else:
                existing_hyp["observations"] = n
            continue

        if lift > LIFT_THRESHOLD:
            if existing_rule is None:
                rulebook.setdefault("rules", []).append({
                    "rule_id": key,
                    "condition": {
                        "content_format": [group[0].get("structure_analysis", {}).get("content_format")],
                        "hook_types_any": list(group[0].get("structure_analysis", {}).get("hook_types", [])),
                        "emotion_bucket": _emotion_bucket(group[0].get("structure_analysis", {}).get("emotion", {})),
                    },
                    "action": {"type": "boost_topic_score", "adjustment": min(0.20, (lift - 1.0) * 0.15)},
                    "evidence": {
                        "verified_case_count": n,
                        "avg_growth_lift": round(lift, 2),
                        "baseline_growth": round(baseline, 4),
                        "sources": sources,
                        "first_seen_at": now,
                        "last_verified_at": now,
                    },
                    "status": "hypothesis" if n < MIN_CASES_SHADOW else "shadow" if n < MIN_CASES_VERIFIED else "verified",
                    "created_at": now,
                })
            else:
                existing_rule["evidence"]["verified_case_count"] = n
                existing_rule["evidence"]["avg_growth_lift"] = round(lift, 2)
                existing_rule["evidence"]["last_verified_at"] = now
                if n >= MIN_CASES_VERIFIED and existing_rule["status"] in ("hypothesis", "shadow"):
                    existing_rule["status"] = "verified"
                elif n >= MIN_CASES_SHADOW and existing_rule["status"] == "hypothesis":
                    existing_rule["status"] = "shadow"
        elif lift < REJECT_LIFT_THRESHOLD and n >= MIN_CASES_VERIFIED:
            if existing_rule and existing_rule["status"] != "rejected":
                existing_rule["status"] = "rejected"
                existing_rule["evidence"]["rejected_at"] = now

    rulebook["hypotheses"] = list(existing_hypotheses.values())
    return rulebook


def discover_rules(
    items: list[dict[str, Any]],
    rulebook: dict[str, Any],
    baseline_growth: float | None = None,
) -> dict[str, Any]:
    """Backward-compat wrapper. Delegates to _discover_rules_legacy."""
    return _discover_rules_legacy(items, rulebook, baseline_growth)


def apply_rules(
    structure_analysis: dict[str, Any],
    rulebook: dict[str, Any],
) -> dict[str, Any]:
    """Apply verified/active rules to a structure analysis (V2: shadow rules excluded).

    V2: Only rules with status 'verified' or 'active' are applied to production scores.
    Shadow rules are NEVER applied to production scores -- use apply_rules_shadow() for
    compute-and-log calibration reports.
    """
    content_format = structure_analysis.get("content_format", "")
    hook_types = set(structure_analysis.get("hook_types", []))
    emotion_bucket = _emotion_bucket(structure_analysis.get("emotion", {}))

    matched: list[dict[str, Any]] = []
    total_adjustment = 0.0

    for rule in rulebook.get("rules", []):
        if rule.get("status") not in ("active", "verified"):
            continue
        cond = rule.get("condition", {})
        if cond.get("content_format") and content_format not in cond["content_format"]:
            continue
        if cond.get("hook_types_any") and not hook_types & set(cond["hook_types_any"]):
            continue
        if cond.get("emotion_bucket") and emotion_bucket != cond["emotion_bucket"]:
            continue
        adjustment = rule.get("action", {}).get("adjustment", 0)
        if rule.get("action", {}).get("type") == "boost_topic_score":
            total_adjustment += adjustment
        elif rule.get("action", {}).get("type") == "penalize_topic_score":
            total_adjustment -= adjustment
        matched.append({
            "rule_id": rule.get("rule_id"),
            "status": rule.get("status"),
            "adjustment": adjustment,
            "evidence_lift": rule.get("evidence", {}).get("avg_growth_lift"),
        })

    return {
        "matched_rules": matched,
        "total_adjustment": round(total_adjustment, 4),
    }


def promote_rules(
    rules: list[dict[str, Any]],
    target_status: str,
) -> list[dict[str, Any]]:
    """Promote rules to a target status. V1 blocks promotion past 'shadow'."""
    if target_status in ("verified", "active"):
        raise RulebookPromotionBlocked(
            f"V1 rulebook (version {RULEBOOK_VERSION}) does not allow promotion "
            f"past 'shadow' status. Target '{target_status}' is blocked."
        )
    status_order = ["collecting", "hypothesis", "shadow"]
    try:
        target_idx = status_order.index(target_status)
    except ValueError:
        return rules
    for rule in rules:
        current = rule.get("status", "hypothesis")
        try:
            current_idx = status_order.index(current)
        except ValueError:
            continue
        if target_idx > current_idx:
            rule["status"] = target_status
    return rules


def write_case_manual(
    observation: dict[str, Any],
    structure_analysis: dict[str, Any],
    outcomes: list[dict[str, Any]],
    growth_score: float,
    matched_rule_ids: list[str] | None = None,
) -> str:
    """Write a case manual markdown file for a verified growing case."""
    CASE_MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    case_id = structure_analysis.get("structure_analysis_id", "case_unknown")
    case_path = CASE_MANUAL_DIR / f"{case_id}.md"

    emotion = structure_analysis.get("emotion", {})
    richness = structure_analysis.get("material_richness", {})
    units = structure_analysis.get("content_units", {})

    lines = [
        f"# Case: {structure_analysis.get('topic_or_hook', 'Untitled')}",
        "",
        "## 基本信息",
        f"- Source: {observation.get('source')}",
        f"- URL: {observation.get('source_url')}",
        f"- Published: {observation.get('published_at')}",
        f"- Author: {observation.get('author')}",
        "",
        "## 原文",
        str(observation.get("copy_text", ""))[:2000],
        "",
        "## Structure Analysis",
        f"- Hook 类型: {structure_analysis.get('hook_types')}",
        f"- 内容形式: {structure_analysis.get('content_format')}",
        f"- 情绪: {emotion.get('primary')} (intensity={emotion.get('intensity')})",
        f"- 素材丰富度: {richness.get('overall', 'unknown')}",
        "",
        "## 内容单元",
    ]
    for unit_key in ("QST", "OPI", "CON", "CAS", "SOL"):
        lines.append(f"- {unit_key}: {units.get(unit_key)}")
    lines.extend([
        "",
        "## 增长数据",
        f"- Growth Score: {growth_score:.4f}",
    ])
    for outcome in outcomes[:5]:
        lines.append(f"- {outcome.get('window')}: growth_score={outcome.get('engagement_growth', {}).get('growth_score', 'N/A')}")
    lines.extend([
        "",
        "## 分析：为什么涨了",
        structure_analysis.get("rationale", "No analysis available."),
        "",
        "## 图片引用",
    ])
    for img in observation.get("image_refs", []):
        lines.append(f"- {img.get('original_image_ref', 'N/A')}")
    lines.extend([
        "",
        "## 关联规则",
        f"- {', '.join(matched_rule_ids) if matched_rule_ids else 'None'}",
    ])

    case_path.write_text("\n".join(lines), encoding="utf-8")
    return str(case_path)

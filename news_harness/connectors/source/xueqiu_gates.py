"""v2 雪球摄入筛选（ARCHITECTURE.md §4.4，Gate A/B/C，D-10~D-16）。

全部为纯函数 + 数据驱动，便于单测与 offline 运行：
- Gate A：账号级排除（硬剔除，确定性）—— 块列表命中即跳过，不进素材池。
- Gate B：硬性门槛（字数/点赞/评论/配图）—— 四道全过才进素材池。
- Gate C：个人账号优先 —— 推导 author_type（personal / institutional / unknown）。
- `filter_batch`：拉 batch_limit 条，过 A→B，保底 floor 条（不足则按阶梯放宽，D-16）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from news_harness.models import ContentItem

# Gate A 机构/媒体识别标记（软启发式；块列表为主闸门）
_INSTITUTIONAL_MARKERS = (
    "官方", "机构", "媒体", "券商", "日报", "通讯社", "财经新闻",
    "财联社", "新华社", "证券时报", "券商中国",
)


def load_blocklist(path) -> list[dict]:
    """读取 configs/xueqiu_blocklist.json → accounts 列表（数据文件，不发版即可增删）。"""
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("accounts", []))


def _obs_identity_values(obs: dict) -> set[str]:
    """收集 observation 中可用于块列表命中的身份标识（id / screen_name / handle / author）。"""
    vals: set[str] = set()
    user = obs.get("user") or {}
    for key in ("id", "screen_name", "handle"):
        v = user.get(key)
        if isinstance(v, str) and v:
            vals.add(v)
    author = obs.get("author")
    if isinstance(author, str) and author:
        vals.add(author)
    return vals


def _blocklist_values(entry: dict) -> set[str]:
    vals: set[str] = set()
    for key in ("user_id", "screen_name", "handle", "id"):
        v = entry.get(key)
        if isinstance(v, str) and v:
            vals.add(v)
    return vals


def apply_gate_a(obs: dict, blocklist: list[dict]) -> tuple[bool, str]:
    """Gate A：账号级排除。命中块列表 → (False, 'dropped_blocklist')。"""
    obs_vals = _obs_identity_values(obs)
    if not obs_vals:
        return True, "passed"
    for entry in blocklist:
        if obs_vals & _blocklist_values(entry):
            return False, "dropped_blocklist"
    return True, "passed"


def apply_gate_b(item: ContentItem, thresholds: dict) -> tuple[bool, str]:
    """Gate B：硬性门槛。返回 (通过, 未通过原因)。"""
    min_chars = int(thresholds.get("min_chars", 0))
    min_likes = int(thresholds.get("min_likes", 0))
    min_comments = int(thresholds.get("min_comments", 10))
    require_image = bool(thresholds.get("require_image", False))

    if item.char_count < min_chars:
        return False, "dropped_short"
    eng = item.engagement or {}
    likes = int(eng.get("likes") or eng.get("like_count") or 0)
    comments = int(eng.get("comments") or eng.get("num_comments") or eng.get("reply_count") or 0)
    if likes < min_likes or comments < min_comments:
        return False, "dropped_low_engagement"
    if require_image and len(item.image_refs) < 1:
        return False, "dropped_no_image"
    return True, "passed"


def derive_author_type(obs: dict, blocklist: list[dict]) -> str:
    """Gate C：推导 author_type。块列表命中或身份标记含机构词 → institutional；否则 personal。"""
    passed, _ = apply_gate_a(obs, blocklist)
    if not passed:
        return "institutional"
    user = obs.get("user") or {}
    identity = " ".join(str(user.get(k, "")) for k in ("identity", "screen_name", "name", "handle"))
    if any(marker in identity for marker in _INSTITUTIONAL_MARKERS):
        return "institutional"
    return "personal"


def filter_batch(
    observations: list[dict],
    blocklist: list[dict],
    thresholds: dict,
    *,
    batch_limit: int = 20,
    floor: int = 0,
    relax: bool = False,
    mapper=None,
) -> tuple[list[ContentItem], dict]:
    """Fixed-threshold batch filter. No relaxation. floor kept for interface compatibility but ignored."""
    from news_harness.connectors.source.xueqiu import xueqiu_observation_to_content_item

    _mapper = mapper or xueqiu_observation_to_content_item
    stats = {
        "total_in": len(observations),
        "gate_a_dropped": 0,
        "gate_b_dropped": {},
        "passed": 0,
        "floor": floor,
    }

    window = observations[:batch_limit]
    passed_items: list[ContentItem] = []
    dropped: dict[str, int] = {}

    for obs in window:
        ok, _ = apply_gate_a(obs, blocklist)
        if not ok:
            stats["gate_a_dropped"] += 1
            continue
        item = _mapper(obs)
        item.author_type = derive_author_type(obs, blocklist)
        ok_b, reason = apply_gate_b(item, thresholds)
        if ok_b:
            passed_items.append(item)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1

    stats["gate_b_dropped"] = dropped
    stats["passed"] = len(passed_items)
    return passed_items, stats

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
    min_chars = int(thresholds.get("min_chars", 500))
    min_likes = int(thresholds.get("min_likes", 50))
    min_comments = int(thresholds.get("min_comments", 10))
    require_image = bool(thresholds.get("require_image", True))

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


# filter_batch 放宽阶梯：从 level 1 起逐档去掉最严门槛
_RELAX_LEVELS = [
    {},                                            # level 0: 全门槛
    {"min_comments": 0},                           # level 1: 去掉评论门槛
    {"min_comments": 0, "min_likes": 0},           # level 2: 再去点赞门槛
    {"min_comments": 0, "min_likes": 0, "require_image": False},  # level 3: 再去配图
    {"min_comments": 0, "min_likes": 0, "require_image": False, "min_chars": 0},  # level 4: 去字数
]


def _relaxed_thresholds(base: dict, level: int) -> dict:
    return {**base, **_RELAX_LEVELS[level]}


def filter_batch(
    observations: list[dict],
    blocklist: list[dict],
    thresholds: dict,
    *,
    batch_limit: int = 20,
    floor: int = 5,
    relax: bool = True,
    mapper=None,
) -> tuple[list[ContentItem], dict]:
    """摄入编排：拉 batch_limit 条 → Gate A → 映射 → Gate B（不足 floor 则阶梯放宽）。

    返回 (passed_items, stats)。stats 含各闸门丢弃计数与 relaxation_level / floor_met。
    """
    from news_harness.connectors.source.xueqiu import xueqiu_observation_to_content_item

    _mapper = mapper or xueqiu_observation_to_content_item
    stats = {
        "total_in": len(observations),
        "gate_a_dropped": 0,
        "gate_b_dropped": {},
        "passed": 0,
        "relaxation_level": 0,
        "floor": floor,
        "floor_met": False,
    }

    window = observations[:batch_limit]
    survivors: list[tuple[dict, ContentItem]] = []
    for obs in window:
        ok, _ = apply_gate_a(obs, blocklist)
        if not ok:
            stats["gate_a_dropped"] += 1
            continue
        item = _mapper(obs)
        item.author_type = derive_author_type(obs, blocklist)
        survivors.append((obs, item))

    def evaluate(level: int) -> list[ContentItem]:
        th = _relaxed_thresholds(thresholds, level)
        passed: list[ContentItem] = []
        dropped = {}
        for _, item in survivors:
            ok, reason = apply_gate_b(item, th)
            if ok:
                passed.append(item)
            else:
                dropped[reason] = dropped.get(reason, 0) + 1
        stats["gate_b_dropped"] = dropped
        return passed

    passed = evaluate(0)
    level = 0
    while relax and len(passed) < floor and level < len(_RELAX_LEVELS) - 1:
        level += 1
        stats["relaxation_level"] = level
        passed = evaluate(level)

    stats["passed"] = len(passed)
    stats["floor_met"] = len(passed) >= floor
    return passed, stats

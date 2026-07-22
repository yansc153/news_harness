# ARCHITECTURE.md — news_harness v2：金融爆款搬运工具

> 状态：v2 设计文档（金融聚焦版，2026-07-22 第二轮修订）
> 生成日期：2026-07-22
> 关联：`.taiyi/changes/arch-review-repack-tool/`（taiyiforge 规划工件，5/9 阶段通过，停在 dev 门前）

---

## 0. 决策记录（Decision Log）

| ID | 决策点 | 结论 | 来源 |
|----|--------|------|------|
| D-01 | 整体重构方向 | **Option A：分层重构**（connectors/ + store/ + mcp_v2，删预测内核） | 用户确认 2026-07-22 |
| D-02 | MCP 视频就绪时机 | **先图文，视频阶段再扩**（video_refs/media_kind 作预留 seam） | 用户确认 2026-07-22 |
| D-03 | 下一步动作 | **先落 ARCHITECTURE.md，再进 dev 实现** | 用户确认 2026-07-22 |
| D-04 | 平台发现机制（Discovery 聚合层） | **已撤销** —— 被 D-06 取代（金融聚焦，去掉泛流量聚合） | 2026-07-22 第二轮 |
| D-05 | 平台最终清单（泛流量版） | **已撤销** —— 被 D-06 金融版取代 | 2026-07-22 第二轮 |
| D-06 | 范围聚焦 | **聚焦金融板块，去掉泛流量平台与 Discovery 聚合层**；connectors 只放金融源 | 用户确认 2026-07-22 第二轮 |
| D-07 | 雪球抓取目标 | **保留 30min 节奏（已是 1800s），把目标从「热门」改为「最新」**；需新接 `xueqiu_latest` / 改 headless 目标 | 用户确认 2026-07-22 第二轮 |
| D-08 | Reddit 线 | **不变**：crawl → translate（机翻）→ LLM（结构化/重写），作为 repack 处理链 | 用户确认 2026-07-22 第二轮 |
| D-09 | 核心增值 | **新增 Processing 层（translate + LLM）**，取代旧 predictor，是 v2 的 repack 价值所在 | 2026-07-22 第二轮 |
| D-10 | 雪球摄入筛选闸门 | **两道闸门**：(A) **账号级排除**——财经新闻类媒体号（如 财联社/新华社/券商中国/证券时报/「XX财经新闻」）直接不扒，走可配 `configs/xueqiu_blocklist.json` 账号块列表；(B) 互动门槛——`likes >= X AND comments >= Y`，复用 `_engagement_from_row` 已有数据；X/Y 可配常量 | 用户确认 2026-07-22 |
| D-11 | 「最新」vs「高赞」矛盾化解 | **摄入源=雪球「最新」tab；加 recency 窗口**：仅保留近 `XUEQIU_FRESH_WINDOW_HOURS` 内发布且越过互动门槛的帖；不引入回访循环（保持去 harness） | 用户确认 2026-07-22 |
| D-12 | Gate B 硬性门槛升级（D-10 细化） | **四道硬门槛**：① `char_count >= XUEQIU_MIN_CHARS(500)`；② `likes >= XUEQIU_MIN_LIKES(50)`；③ `comments >= XUEQIU_MIN_COMMENTS(10)`；④ `len(image_refs) >= 1`（有配图）。**个人账号优先**：机构/媒体号走 Gate A 块列表剔除，personal 账号为收录主体 | 用户确认 2026-07-22 |
| D-13 | 历史筛选澄清 | **旧版雪球无专属筛选**：500字来自 `REDDIT_MIN_ANALYSIS_CHARS`(Reddit 阈值)、点赞≥50/评论≥15/配图是 `manual_smoke` 全平台「软信号」(喂 DeepSeek 打分)，均非雪球硬门槛；雪球唯一硬删选是 `<28字` 通用规则 | 代码核实 2026-07-22 |
| D-14 | 雪球批次与保底 | **30 条/30min → 20 条/30min，且每批保底 ≥5 条通过闸门**：`batch_limit = 20`（原 `configs/all_source_runner.json` 为 30，v2 重新定）。拉 20 条过 Gate A/B/C 后**至少 5 条入库**；不足 5 条触发阈值渐进放宽兜底（见 D-16） | 用户确认 2026-07-22 |
| D-15 | 「最新」tab 刷新机制约束 | **雪球「最新」feed 不能靠网页刷新加载，必须手动点击「最新」tab 才会刷新内容**。headless 抓取不能只 load 默认页/刷新页面，必须**显式定位并点击「最新」tab 元素**（或命中其底层接口/URL）才能拿到最新流；这是对 S4 实现的关键约束（待与 OpenCLI/headless 桥实测验证） | 用户确认 2026-07-22 |
| D-16 | 批次保底兜底 | **每批须保底 ≥5 条通过 Gate B**；若 <5 条，按序渐进放宽：① 降评论地板 `MIN_COMMENTS`(10→5)；② 降点赞地板 `MIN_LIKES`(50→25→10)；③ 放宽配图要求(`REQUIRE_IMAGE` 临时 false)；④ 仍不足则回退摄入源到「热门 + recency 窗口」(D-11)。记录实际生效的放宽档位供下游排序降权。避免「最新+24h+高赞」叠加导致 0 条 | 用户确认 2026-07-22 |

**明确删除（harness 遗产）**：`evaluator.py` / `baseline.py` / `rulebook.py` / `loop_driver.py`、以及 `manual_smoke.py` 的预测/打分/回访链路；**泛流量平台（微博/B站/小红书/抖音）与 Discovery 聚合层（tophub）从范围移除**。
**明确保留（用户指定不变）**：credential 层（`config.py` + `direct_cli_backend.py` 的 auth/session）、Reddit 线、雪球线、artifact_api 的投影分层思路、MCP 只读契约、证据保留（原始 source_url / 图片引用不下载替换）。

---

## 1. 产品定位（v2，金融聚焦）

从「结果优先的预测 harness」进化为「**金融爆款搬运工具**」：

```
金融源抓取  →  翻译(机翻)  →  LLM 结构化/重写  →  统一素材池(文案+图片+未来视频)  →  MCP 导出给下游
(Reddit/雪球)   (外文→中文)    (搬运稿/摘要)        (SQLite+哈希媒体库)              (只读)
```

- **核心价值**：金融内容的低成本采集 + 翻译 + LLM 重写，产出「搬运就绪」的中文素材（文案 + 图片）。
- **不是**：新闻聚合器、内容农场、投资建议产品、发布机器人（发布走独立受控路径）、泛流量搬运。
- **节奏**：雪球每 30 分钟拉一批最新；Reddit 按金融 subreddit 节奏同步。
- **未来扩展**：视频原生（如后续加 B站/抖音金融号），MCP 接口已留 seam。

---

## 2. 设计原则（继承与变更）

| 原则 | v1(harness) | v2(金融搬运) |
|------|-------------|--------------|
| 凭证/secret 外部 | ✅ 保留 | ✅ 不变 |
| Reddit 线 | ✅ | ✅ 不变（crawl→translate→LLM） |
| 雪球线 | ✅（热门） | ✅（改最新，30min） |
| MCP 只读 | ✅ | ✅ 不变（发布走 publish.py） |
| 证据保留原始 URL/图片引用 | ✅ | ✅ 不变 |
| 原子写入 / 结构化失败 | ✅ | ✅ 不变 |
| 预测/评估/回访自循环 | ✅ 核心 | ❌ 删除 |
| 每轮 dump JSON | ✅ | ❌ 改 SQLite + 哈希媒体库 |
| 模型自评/晋升规则 | ✅（受限） | ❌ 删除 |
| LLM 翻译+重写处理链 | ❌ | ✅ 新增（v2 核心增值） |

---

## 3. 整体架构（分层）

```
┌─────────────────────────────────────────────────────────────┐
│  CLI / serve (news_harness.cli, news_harness.serve)          │
├─────────────────────────────────────────────────────────────┤
│  Pipeline Orchestrator                                       │
│   fetch → translate → llm → normalize → store → export       │
├──────────────────────┬──────────────────────────────────────┤
│  connectors/         │  store/                              │
│   source/            │   db.py      (SQLite 元数据索引)      │
│     reddit.py        │   media.py    (哈希媒体库+manifest)   │
│     xueqiu.py        │   cache.py    (响应缓存+TTL+容量)     │
│   processing/        │   janitor.py  (配额/LRU/TTL 清理)     │
│     translate.py     ├──────────────────────────────────────┤
│     llm.py           │  models.py (ContentItem/Author/      │
│   registry.py        │            MediaRef/ProcessedContent)│
└──────────────────────┴──────────────────────────────────────┘
        │                                              │
        └────────────── mcp_v2.py (导出投影, 只读) ────┘
```

**零新第三方依赖**（沿用标准库 + 现有 JS 看板 + 既有 LLM/translate 调用点）。

---

## 4. Connector 模型（Source + Processing）

### 4.1 Source 层（金融平台抓取）
- **职责**：按平台拉取原文 + 媒体（图片/图表）。
- **输出**：`ContentItem`（见 §7）。
- **金融源（核心 + 待确认扩展，见 §5）**：Reddit（金融 subreddit）、雪球（最新）。
- **复用**：credential 层与 `direct_cli_backend.py` 的 auth/session 逻辑原样保留。
- **雪球现状与改动（D-07 / D-15）**：当前 `xueqiu_hot` 走 `opencli xueqiu hot`（热门），`xueqiu_daren` 走 `opencli xueqiu feed`，刷新间隔已是 1800s（30min）。改「最新」= 新接 `xueqiu_latest`（若 OpenCLI 支持）或把 headless 目标换到「最新」tab；`_xueqiu_opencli_args` 当前只有 `hot`/`feed` 两种，需补 `latest`。雪球抓取为 browser/session-assisted（OpenCLI 桥 + headless DOM 导出），非公开 JSON。**关键约束（D-15）**：雪球「最新」feed **不是靠网页刷新就能刷新**的——必须**手动点击「最新」tab** 才会加载最新内容。因此 headless 抓取**不能只 load 默认页/刷新页面**，必须显式定位并点击「最新」tab 元素（或命中其底层接口/URL）才能拿到最新流；这是 S4 实现时必须解决的，待与 OpenCLI/headless 桥实测验证「点击 tab」这条路径是否可达。

### 4.2 Processing 层（翻译 + LLM，v2 核心 —— D-08/D-09）
- **translate.py**：外文→中文机翻（Reddit 英文帖）。保留原文，生成 `translated_text`。
- **llm.py**：基于翻译/原文做结构化与重写，产出 `llm_summary`（搬运稿/摘要）。LLM 仅做处理，**不参与抓取决策、不自评**。
- **输出**：`ProcessedContent`（见 §7），供 store 与 MCP 导出。
- **调度**：雪球每 30min 批后整批过 Processing；Reddit 抓取后逐条过 Processing。

### 4.3 Registry
- 配置驱动自动发现：`connectors/source/<name>.py`、`connectors/processing/<name>.py`，按 `configs/platforms.v2.json` 启用。
- 雪球 `refresh_interval_seconds = 1800`（保持，见 preflight/validator 现有断言）。

### 4.4 雪球摄入筛选（D-10 / D-11）

雪球「最新」批**不是**拉到就全收，过两道闸门后才进素材池。**批次与节奏（D-14 / D-16）**：`batch_limit = 20`、`refresh_interval_seconds = 1800`（**30 分钟 20 条**）。注意：现有 `configs/all_source_runner.json` + `tests/test_xueqiu_headless_limit.py` 仍断言 `batch_limit = 30`，v2 重新定为 20；S4 实现时需同步更新该配置与测试断言。拉 20 条过 Gate A/B/C 后**保底至少 5 条入库**（D-16）——既保证「精选」质量，又避免高门槛叠加导致空批。

**Gate A — 账号级排除（硬剔除，确定性，主闸门）**：财经新闻类媒体号直接不扒。
- 维护**账号块列表** `configs/xueqiu_blocklist.json`（可配，种子含 财联社 / 新华社 / 券商中国 / 证券时报 / 各类「XX财经新闻」机构号），按作者 `user.id` / `screen_name` / `handle` 命中即跳过该帖，**不进入素材池、不消耗处理配额**。
- 这是 S4 必须补的能力：当前 `_xueqiu_source_quality`（`direct_cli_backend.py:1161`）硬编码 `source_material_role: "original_article"`，**完全无账号/内容过滤**——雪球线上跑的其实是把媒体号内容也一并收进来了。
- 块列表做成**数据文件而非代码**：新增/移除媒体号只改 JSON，不发版；connector 启动时加载，O(1) 查表。
- 可选二级信号（不强依赖）：`is_repost` / `retweeted_status` 存在 → 转载，可一并跳过；正文以「【」开头或含「来源：/快讯」等作软提示，不单独据此判删。

**Gate B — 硬性门槛（可配常量，D-12）**：四道全过才进素材池。
- ① **字数**：`char_count >= XUEQIU_MIN_CHARS`（默认 **500**）——与 `REDDIT_MIN_ANALYSIS_CHARS = 500`（`direct_cli_backend.py:58`）对齐语义，保证「有料可搬」；`char_count` 新抽自 `len(copy_text.strip())`。
- ② **点赞**：`likes >= XUEQIU_MIN_LIKES`（默认 **50**）——沿用旧版全平台「爆款信号」阈值（manual_smoke.py:321），作为雪球硬地板。
- ③ **评论**：`comments >= XUEQIU_MIN_COMMENTS`（默认 **10**）——讨论度下限（旧版软信号是 15，硬门槛取更宽松的 10 以保 yield）。
- ④ **配图**：`len(image_refs) >= 1`——用户明确要求「有配图」，无图帖直接跳过（图表/截图也算 image_ref）。
- raw 数据来源：`_engagement_from_row`（`direct_cli_backend.py:1354`）已抽 `likes/like_count/retweets/reply_count/comments/num_comments/views`，**无需新抓**；`image_refs` 已由 `_image_refs_from_row` 解析。
- 新增常量（默认建议，config 可调）：`XUEQIU_MIN_CHARS = 500`、`XUEQIU_MIN_LIKES = 50`、`XUEQIU_MIN_COMMENTS = 10`、`XUEQIU_REQUIRE_IMAGE = true`。

**Gate C — 个人账号优先（D-12，正信号）**：
- Gate A 已剔除机构/媒体号；v2 进一步把**个人账号（personal）作为收录主体**。
- 新增 `author_type` 字段（`personal` / `institutional` / `unknown`）：由 Gate A 块列表 + 简单启发式（如 `user.identity` 含「官方/机构/媒体」→ institutional）推导；`unknown` 默认放行但降权（不影响入库，影响下游排序）。
- 不强制 `author_type == personal` 才收（避免误杀），但**机构号已被 Gate A 挡在门外**，因此实际入库以个人为主。

**「最新」vs「高赞」矛盾化解（D-11）**：
- 矛盾：刚发的帖还没攒到赞，对「最新」批做绝对高赞硬门槛，一轮可能剩 0 条。
- 化解：**摄入源 = 雪球「最新」tab（保证时效性）+ recency 窗口**——仅保留 `published_at` 在 `XUEQIU_FRESH_WINDOW_HOURS`（默认 24h）内、且已越过 Gate B 门槛的帖。这样既「新」又「有讨论度」，且**不引入回访/预测循环**（保持去 harness 原则）。
- **无样本先行策略**（回应「爬的时候才看得到」）：Gate A 块列表种子先置空 + Gate C 启发式上线；**首次抓取时把命中 institutional 模式的账号自动追加进块列表候选**（落 `configs/xueqiu_blocklist.candidates.json`，人工复核后转正），不阻塞落地。
- 兜底开关：若实测「最新」yield 过低，可把摄入源切到「热门 + 24h recency 窗口」（语义即「近期热门」），仍是单轮硬过滤、无状态。

---

## 5. 平台范围与优先级（金融聚焦 — D-06）

| 平台 | 层 | 批次 | 抓取难度 | 内容形态 | 备注 |
|------|----|------|---------|---------|------|
| **Reddit（金融 sub）** | Source+Processing | 核心 | ★★ 中 | 图文 | crawl→translate→LLM；credential 不变 |
| **雪球（最新）** | Source | 核心 | ★★★ browser-assisted | 图文+图表 | 30min/20条批·保底5条（D-07/D-14/D-16）；热门→最新；须显式点击「最新」tab 而非网页刷新（D-15）；Gate A 账号块列表剔财经新闻号 + Gate B 四道硬门槛(≥500字/≥50赞/≥10评/有配图) + Gate C 个人账号优先（D-10/D-11/D-12） |
| 东方财富股吧 | Source | 二批（待确认） | ★★ 中 | 图文 | 纯金融讨论 |
| 金十数据 | Source | 二批（待确认） | ★ 易 | 快讯 | 金融快讯 |
| 财联社 | Source | 二批（待确认） | ★ 易 | 快讯 | 金融快讯 |
| 同花顺 / 微博财经号 | Source | 三批（待确认） | ★★ 中 | 图文 | 需按金融属性严格筛 |

**选型逻辑（金融聚焦）**：只保留有金融信息密度的源；Reddit 覆盖全球宏观/个股讨论（需翻译），雪球覆盖 A股/基金实时讨论（最新优先）。泛流量平台（微博/B站/小红书/抖音）与 tophub 聚合层**移出范围**。

---

## 6. Store 层（治磁盘 / 缓存 / 释放）

解决用户点名的「脚本文件乱、缓存释放、磁盘爆」。

| 模块 | 职责 |
|------|------|
| `db.py` | **SQLite 元数据索引**：去重、发布状态、处理状态(processing_status)、历史、rights_status。O(1) 查询，不再扫 JSON。 |
| `media.py` | **内容哈希媒体库**：`media/{sha256[:2]}/{sha256}.ext`，天然去重；`manifest` 记录 size / refcount / last_accessed / rights_status。 |
| `cache.py` | 抓取响应缓存，带 **TTL + 容量上限**，避免无限增长。 |
| `janitor.py` | **配额守护**：超配额（默认 20GB）按 LRU 淘汰；未发布超 TTL（默认 7 天）清理；refcount=0 立即删。 |

**关键参数（建议，可在 config 调）**：
- `MEDIA_QUOTA_GB = 20`
- `UNPUBLISHED_TTL_DAYS = 7`
- `CACHE_TTL = 3600s`，`CACHE_MAX_MB = 512`
- 雪球 30min 批天然限流；janitor 由 `loop` / cron 触发，或 `news_harness janitor --dry-run` 手动。

---

## 7. 数据模型（`models.py`）

```python
@dataclass
class Author:
    name: str
    handle: str | None
    avatar_url: str | None
    follower_count: int | None

@dataclass
class MediaRef:
    url: str
    mime: str | None            # image/jpeg, video/mp4 ...
    dimensions: str | None      # "1080x1920"
    byte_size: int | None
    sha256: str | None          # 媒体库去重键
    thumbnail_ref: str | None   # 视频缩略图（视频阶段用）
    rights_status: str          # ok | unknown | restricted

@dataclass
class ContentItem:
    id: str
    platform: str               # reddit / xueqiu ...
    source_label: str
    source_url: str
    canonical_url: str | None
    author: Author | None
    author_type: str            # personal | institutional | unknown（Gate C，D-12）
    copy_text: str              # 原始文案
    char_count: int             # len(copy_text.strip())，Gate B①门槛（D-12）
    published_at: str | None
    observed_at: str
    engagement: dict | None     # {likes, comments, ...} 来自 _engagement_from_row
    content_kind: str           # original | news | repost（Gate A 判定，D-10）
    ingest_gate: str            # passed | dropped_blocklist | dropped_low_engagement | dropped_no_image | dropped_short
    media_kind: str             # text | image | video | mixed
    image_refs: list[MediaRef]
    video_refs: list[MediaRef]  # 视频阶段填充，当前空
    evidence_status: str        # observed | missing
    rights_status: str

@dataclass
class ProcessedContent:        # Processing 层输出（D-08/D-09）
    item_id: str
    translated_text: str | None   # 机翻稿（Reddit 外文→中文）
    llm_summary: str | None       # LLM 结构化/重写搬运稿
    processing_status: str        # raw | translated | llm_done
    model_ref: str | None         # 使用的 LLM/translate 标识（可追溯，不泄 secret）
```

---

## 8. MCP 导出（`mcp_v2.py`，先图文）

**当前（v2 首批）** —— 白名单/禁令牌测试照旧，secret 永不外泄；导出同时带原文与处理稿：

```json
{
  "object_type": "McpExportItem",
  "id": "...",
  "platform": "reddit",
  "source_label": "Reddit",
  "author": { "name": "...", "handle": "...", "avatar_url": "...", "follower_count": 123456 },
  "copy_text": "...",
  "translated_text": "...",
  "llm_summary": "...",
  "source_url": "...",
  "canonical_url": "...",
  "image_refs": [ { "url": "...", "dimensions": "1080x1440", "rights_status": "ok" } ],
  "evidence_status": "observed",
  "public_url_available": true
}
```

**视频 seam（D-02，不现在 breaking change）**：
- 预留 `media_kind` 字段（已在上游 ContentItem）+ `video_refs: MediaRef[]`。
- 升级为 optional 扩展，下游首次见到 `video_refs` 时再适配，避免二次 breaking change。
- **MCP 保持只读**；真正「发」走独立 `publish.py` 受控路径。

---

## 9. Pipeline 流程

```
news_harness fetch --platform xueqiu     →  雪球最新 30min 批  →  ContentItem[]
news_harness fetch --platform reddit     →  Reddit 金融 sub   →  ContentItem[]
news_harness translate                   →  机翻(外文→中文)    →  ProcessedContent
news_harness llm                         →  LLM 重写/摘要      →  ProcessedContent
news_harness store                       →  归一化 + SQLite + 媒体库  →  落盘
news_harness export                      →  mcp_v2 投影        →  下游
news_harness janitor                     →  配额/LRU/TTL 清理
```

---

## 10. CLI / 命令（v2）

```
python3 -m news_harness fetch --platform xueqiu|reddit
python3 -m news_harness translate [--item <id>]
python3 -m news_harness llm [--item <id>]
python3 -m news_harness store --item <path>
python3 -m news_harness export [--platform ...]   # MCP 投影
python3 -m news_harness janitor [--dry-run]
python3 -m news_harness serve                       # 看板(只读)
python3 -m news_harness healthcheck --auto
```

（对比旧 `run-cycle`：拆除自循环，改为显式、可组合的阶段命令；雪球 30min 节奏由 scheduler/loop 触发。）

---

## 11. 落地阶段（S1–S7，对应 taiyiforge TASK.md，金融版）

| 切片 | 内容 | 依赖 |
|------|------|------|
| S1 | 骨架 + 接口（connectors/base、models.py、registry） | — |
| S2 | store 层（db/media/cache/janitor）+ 配额参数 | S1 |
| S3 | Reddit connector 迁移到新框架 + Processing(translate/llm) 链 | S1 |
| S4 | **雪球 connector**：保留 30min（1800s），`batch_limit=20` + 每批保底≥5条通过闸门（D-14/D-16）；改「最新」目标——**须显式点击「最新」tab 而非网页刷新**（D-15，先验证 headless 桥可达性）；接 Gate A/B/C 三道闸门 | S2, S3 |
| S5 | mcp_v2 导出（图文优先，带 translated_text/llm_summary） | S1, S2 |
| S6 | 删预测内核（evaluator/baseline/rulebook/loop_driver + manual_smoke 链路）+ 移除泛流量/Discovery 代码 | S4, S5 |
| S7 | 二批金融源（东方财富股吧/金十/财联社，待确认）+ 视频 seam | S4 |

TDD：每切片先红后绿，沿用现有 `tests/` 框架（零新依赖）。

---

## 12. 开放问题（Open Questions）

1. **雪球「最新」实现（D-15）**：OpenCLI 是否有 `xueqiu latest` 子命令？没有则需改 headless 目标到「最新」tab。关键约束：雪球「最新」feed **靠手动点击「最新」tab 才会刷新**，仅网页刷新/load 默认页拿不到最新流——需验证 headless 桥能否**显式点击 tab 元素**或命中其底层接口（如 timeline 带 sort/最新参数的 URL）。这是 S4 必须先落地的可达性验证。
2. **Gate A 块列表种子（无样本先行）**：用户暂无真实导出样本，账号标识（`user.id` / `screen_name`）待爬时校准；**采用「空种子 + 运行时增量补全」策略**——首次抓取自动把命中 institutional 模式的账号写入 `xueqiu_blocklist.candidates.json`，人工复核后转正，不阻塞落地。当前代码完全无账号/内容过滤（D-13）。
3. **Gate B 阈值与保底（D-14/D-16）**：`XUEQIU_MIN_CHARS=500` / `XUEQIU_MIN_LIKES=50` / `XUEQIU_MIN_COMMENTS=10` / `XUEQIU_REQUIRE_IMAGE=true` 为建议默认，需按雪球「最新」批（20/30min，D-14）实测 yield 调参；`XUEQIU_FRESH_WINDOW_HOURS=24` 同理。点赞地板可能偏高（最新+24h 窗口下新帖赞少），需实测后下调。**保底机制（D-16）**：每批须 ≥5 条通过，否则按序渐进放宽（评论 10→5 → 点赞 50→25→10 → 放开配图 → 回退热门+窗口），记录生效档位；这正好化解「最新+24h+高赞」三重叠加可能 0 条的旧风险。
4. **速率 / 风控**：雪球 30min 批之外的并发/退避；Reddit 多 sub 节奏与账号隔离。
5. **搬运合规**：转码 / 水印 / 署名 / `rights_status` 过滤的执行点（建议 janitor + publish 双闸）。
6. **LLM 处理策略**：translate 与 llm 是否串联固定管线，还是按平台可配置（Reddit 需翻译，雪球中文可直接 LLM）。
7. **视频阶段 schema 升级时机**：`video_refs` 在 S7 还是独立阶段引入。
8. **看板 UI**：是否按新方向用 DESIGN.md 规范重做（独立流程）。

---

_本文件为 v2 架构基线（金融聚焦版）。任何 D-01~D-09 之外的新决策，更新 §0 决策记录并同步 `.taiyi/changes/arch-review-repack-tool/` 工件。_

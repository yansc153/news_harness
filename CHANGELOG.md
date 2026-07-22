# Changelog

## 2026-06-16: Topic Discovery Architecture Evolution

Status: architecture evolution, new modules added, existing production modules
unchanged.

### Added

- `news_harness/structure_analysis.py`: dbskill × DeepSeek structure analysis
  module. Analyzes source content using dbs-hook (6 hook types), dbs-content
  (content formats, emotions), and dbs-content-system (QST/OPI/CON/CAS/SOL
  content units) frameworks. Falls back to heuristic keyword-based analysis when
  DeepSeek is unavailable.

- `news_harness/rulebook.py`: internal rule discovery, validation, and promotion
  module. Discovers rules from revisit-verified growing content by grouping
  structure analysis tags × growth outcomes. Rule lifecycle: hypothesis → shadow
  → verified → active. Writes Case Manual markdown files for verified growing
  cases.

- `tests/test_structure_and_rulebook.py`: 14 tests covering structure analysis
  heuristic detection, rule discovery, rule application, case manual generation.

- `docs/architecture-evolution.md`: documents the evolution from fixture-first
  MVP to topic discovery harness with two-level crawling, Rulebook, and dbskill
  integration.

- `docs/rulebook-design.md`: detailed Rulebook data structure, lifecycle,
  discovery algorithm, and dbskill framework mapping.

### Changed

- `configs/all_source_runner.example.json`: added `two_level_crawl: true` flags
  for X list, Reddit, and all Xueqiu sources. Added `js_render_required: true`
  for Xueqiu sources. Added Reddit `subreddit_tiers` with differentiated
  frequency/batch per tier (Tier 1: 30min/15 items, Tier 2: 1h/10, Tier 3:
  2h/5).

- `news_harness/loop_driver.py`: `_refetch_engagement` now attempts direct-cli
  refetch for auth-gated sources (X list via twitter-cli, Xueqiu via degraded
  browser bridge state) instead of silently returning None. Reddit uses public
  JSON API refetch.

### Architecture Understanding (corrected)

- News Harness is a **topic discovery infrastructure** for content operators,
  not a generic prediction engine.
- MCP output is **raw content + evidence only**. No recommendations, no
  structure tags, no Rulebook content in MCP output.
- Rulebook is **internal**: guides Level 1 filtering and topic scoring.
- Revisit feedback comes from **source platform engagement growth**, not
  downstream account performance.
- Two-level crawling: Level 1 (list page, fast) → Level 2 (detail page, deep,
  only for items that pass Level 1 screening).

### Known Gaps

- `manual_smoke.py` scoring still uses engagement absolute values as primary
  signal. Structure-based topic scoring integration is designed but not yet
  wired into the real data flow.
- Xueqiu dispute section still lacks a connector.
- Structure analysis DeepSeek integration tested via fixture, not yet wired into
  the `run-cycle` pipeline.
- Rulebook rule discovery tested in isolation, not yet triggered automatically
  by revisit outcomes.

## 2026-06-16: VPS Candidate Readiness

Status: candidate deployable, production confidence still gated.

### Added

- `python3 -m news_harness quickstart` for local and VPS command discovery.
- Root `index.html` operator start page.
- `docs/index.html` documentation map.
- `docs/OPERATIONS.md` for healthcheck and scheduler troubleshooting.

### Changed

- Timeline UI exposes source-quality and image-evidence risks instead of hiding
  stale or missing contract fields.
- Prediction feedback windows are now `15m / 1h / 3h / 6h / 24h`.
- X quote repost wrappers are skipped unless the quoted original can be traced.
- Xueqiu observations record full-text/detail-fetch status.

### Known Gaps

- Existing live artifacts may predate the new prediction contract until the
  first VPS manual-smoke cycle runs.
- `healthcheck --max-age-minutes 90` must pass on VPS before the run is called
  healthy.
- Real 24h labels require elapsed time after deployment.
- Xueqiu dispute still lacks an exact connector.

<!-- taiyi:arch-review-repack-tool --> 2026-07-22

# Changelog

## [v2.0.0] - 2026-07-22

### Added

- 金融内容搬运工具框架：connector 注册表 + base/source/processing 抽象层
- Reddit source connector（沿用 rdt-cli cookie 凭证路径，凭证层不变）
- 雪球 source connector：显式点击「最新」tab，批次 20、底线 5 + 保底阶梯
- 双闸门过滤：Gate A 账号黑名单 / Gate B 内容阈值（≥500字·≥50赞·≥10评·有配图）/
  Gate C 个人优先
- translate + llm 处理链（外文翻译后 LLM 改写；中文直出 LLM），含离线降级
- SQLite store（db / media / cache / janitor）
- 只读 MCP v2 导出（白名单字段 + forbidden key 校验）
- batch 编排（fetch → translate → llm → store → mcp_v2
  export），端到端保留视频 seam

### Changed

- CLI 裁剪为 v2 repack 命令集（移除 prediction 子命令）

### Removed

- 预测 harness（evaluator / baseline / rulebook / loop_driver）
- 泛流量平台与 Discovery / 聚合代码

### Fixed

- connector registry 递归 bug（单一 classmethod + 类级共享 dict）

### Security

- MCP 导出严格白名单；secret / cookie / token / session / model_ref /
  user_id 永不外泄

---

### Migration

- 环境变量无变化；`configs/secrets.env`（本地）/
  `configs/secrets.vps.env`（VPS）指向的凭证文件路径不变。
- 数据库：SQLite 由 store 层自动初始化（首次运行建表），无手动 DDL。
- 配置：`configs/all_source_runner.json` 批次上限 20、雪球批次 20（已更新）。

### Rollback

- 触发：MCP 导出出现 forbidden key 泄漏，或 96 测试套件回归失败
- 操作：1. `git revert` 本次变更 commit 2. 重启服务（或 `docker compose up -d`）
- 时间：< 5 分钟

### Post-Launch Watch

- 观察期：7 天
- 指标：抓取成功率、MCP 导出条目数、96 测试回归、cookie 过期告警
- 退出标准：连续 7 天抓取成功率 ≥ 95% 且无 secret 泄漏
- 异常：cookie 过期 → 重新粘贴凭证文件后重启

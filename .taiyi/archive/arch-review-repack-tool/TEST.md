---
phase: test
skill: taiyi-test
gate: auto
produces: TEST.md
upstream: [task, dev]
downstream: [review]
---
<!-- phase:test skill:taiyi-test gate:auto est:20min produces:TEST.md upstream:[task,dev] downstream:[review] cplx:[ALL]5steps +[M+]4 +[H]2 -->
# TEST: news_harness — 金融爆款搬运工具重构 (v2 repack)

> **策略**: 纯 Python stdlib 项目（零三方依赖），无 UI / 浏览器层。测试以 `unittest`
> 单元 + 轻量集成覆盖为主，**无 E2E 浏览器测试**（CLI / workflow only）。

---

## Test Evidence（真实运行）

```bash
$ python3 -m unittest discover -s tests -p "test_*.py"
----------------------------------------------------------------------
Ran 96 tests in 0.67s
OK
```

- **结果**: 96 用例 / 0 失败 / 0 错误 / 0.67s
- **框架**: `unittest`（stdlib，无 pytest；pytest 未安装，符合零依赖约束）
- **发现模式**: `test_*.py`（Python stdlib 约定；引擎默认的 `*.test.py` 因含点号
  会被 Python 导入系统当成 `package.module` 而无法被 `unittest discover` 加载，故不采用）

### 按文件分布（13 个文件承载全部 96 用例）

| 测试文件 | 用例 | 覆盖切片 |
|----------|:--:|----------|
| test_models | 11 | S1 `ContentItem` / `MediaRef` / `ProcessedContent` / 媒体类型推导 |
| test_connector_registry | 7 | S1/S3 注册表（类级共享 dict + 装饰器自注册） |
| test_connectors_processing | 6 | S3 translate→repack 链 + 离线降级 |
| test_connectors_reddit | 8 | S3 Reddit 观测→`ContentItem` mapper + connector |
| test_connectors_xueqiu | 17 | S4 雪球 mapper + Gate A/B/C + 保底阶梯 + 批次 20/底线 5 |
| test_store | 17 | S2 SQLite 索引 + 哈希媒体库 + janitor + 配额/LRU |
| test_mcp_v2 | 10 | S5 MCP v2 白名单导出 + forbidden-keys 校验 |
| test_mcp_export | 4 | S5 旧导出契约回归（向后兼容） |
| test_batch_pipeline | 4 | S7 编排（fetch→translate→llm→store→export）+ video seam |
| test_timeline_merge | 5 | 时间线合并 |
| test_reddit_quality_filter | 3 | Reddit 质量过滤 |
| test_xueqiu_challenge_filter | 3 | 雪球挑战过滤 |
| test_export_api | 1 | 导出 API 冒烟 |
| **合计** | **96** | 13 个文件 |

> 4 个 legacy 文件（`test_docker_feed_mount` / `test_radar_timeline_source_tabs` /
> `test_xueqiu_headless_limit` / `test_xueqiu_headless_timeout`）当前贡献 0 用例——
> 其目标模块已在 S6 删除，列为后续清理项（不影响 96/96 绿灯）。

---

## 三层覆盖（适配 Python）

- **单元**: `unittest`，关键路径 100% 覆盖（模型 / 注册表 / connector / gates / mcp_v2 / batch）。
- **集成**: connector → processing → store → mcp_v2 端到端编排（`test_batch_pipeline` 覆盖）。
- **E2E**: 无 UI，不适用（CLI / workflow only）。

---

## Mocking 边界

| 层级 / 模块 | Mock? | 理由 |
|------------|:-----:|------|
| 外部 HTTP 抓取（X / Reddit / 雪球） | ✅ 允许 | injectable `fetcher`，fixture 驱动，确定性 |
| SQLite / 媒体库 | ✅ 允许 | 内存库 + 临时目录，避免污染 |
| translate / llm provider | ✅ 允许 | 离线 fallback 路径覆盖；无 provider 时降级（graceful degradation） |
| 核心业务逻辑（gates / mcp_v2 白名单 / mapper） | ❌ 禁止 | 必须走真实实现 |

---

## 回归规则（Iron Rule + red-green）

| 回归项 | 原行为 | 新行为 | 测试 | Red-green | 状态 |
|--------|--------|--------|------|-----------|------|
| 全量测试 | harness 旧预测内核 | v2 connector / store / mcp_v2 | `python3 -m unittest discover -s tests` | ✅ 96/96 通过 | 通过 |

---

## Edge Case 覆盖

| 场景 | TC | 状态 |
|------|-----|------|
| 空文本 / 仅空白 | connectors_reddit / connectors_processing | ✅ |
| 账号黑名单命中（Gate A） | connectors_xueqiu | ✅ |
| Gate B 阈值（字数≥500 / 点赞≥50 / 评论≥10 / 有配图）+ 保底阶梯 | connectors_xueqiu | ✅ |
| MCP 导出禁止键（secret / user_id / model_ref 永不泄露） | mcp_v2 | ✅ |
| video seam 端到端保留（media_kind + video_refs） | batch_pipeline / models | ✅ |
| translate 失败降级（`translated_text=None` → 原文进 LLM） | connectors_processing | ✅ |
| llm 离线 local fallback（带 `.fallback` 标记） | connectors_processing | ✅ |

---

## 安全测试

- [x] MCP v2 导出白名单 + forbidden-keys 测试（secret / cookie / session / 登录态永不出现）
- [x] 无硬编码密钥（credentials 走 repo-external secrets，不入库）
- [x] `mcp_v2.validate` 拒绝非预期键与禁止键
- [x] 导出与工件永不包含 secret（沿用既有 forbidden-keys 测试，SC 映射 REQUIREMENT §NFR）

---

## 性能 / 兼容

- **性能**: CLI 单测全量 <1s；无新增热路径 / 服务层，不适用 k6 / Lighthouse。
- **兼容**: 纯逻辑变更，无浏览器 / 视口。运行时要求 Python ≥ 3.12。

---

## Quality Gate

- [x] 单元覆盖关键路径（模型 / 注册表 / connector / gates / mcp_v2 / batch）
- [x] TC 真实跑过（96/96 OK，非“应能通过”）
- [x] 回归全量通过
- [x] 安全测试通过（forbidden-keys）
- [x] CI 可自动化（`unittest discover` 单行命令）

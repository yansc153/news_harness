---
phase: change
skill: taiyi-change
gate: human
produces: CHANGE.md
upstream: []
downstream: [requirement]
---
<!-- phase:change skill:taiyi-change gate:human est:15min produces:CHANGE.md upstream:[] downstream:[requirement] cplx:[ALL]5steps +[UI]1 +[M+]4 +[H]1 -->
# CHANGE: news_harness 架构评审：从 harness 转向国内爆款搬运工具

> **一句话**: 现有骨架是「预测引擎」，新方向是「爆款搬运工具」，需评审架构对错并定下重构契约 | **Status**: active | **Slug**: arch-review-repack-tool

---

## Step 1: Problem Statement
> **[ALL]** Goal: 证明值得做

**当前状态**: news_harness 是 outcome-first 预测 harness（Fetch→Predict(DeepSeek)→Revisit→Evaluate→Rulebook）。新方向是「国内爆款内容搬运工具」（多平台抓取→统一素材池→可发布输出），预测/评估/回访链路全部不再需要。manual_smoke.py（2338 行）编排器、evaluator/baseline/rulebook 成为负担；产物是散落 JSON 脚本文件，无缓存淘汰/磁盘配额，媒体（图片/未来视频）积累会撑爆磁盘。

**不改的代价**: 在错误骨架上打补丁——connector 无法插拔、媒体无去重/清理、MCP 无视频字段；未来接抖音/视频时重构成本翻倍。

**目标状态**: 完成架构评审，给出「对/错」结论 + ≥2 套重构方案对比 + 决策建议；明确模块删/留/建；为磁盘/缓存/MCP-v2 定下设计契约。

## Step 2: Boundary Definition
> **[ALL]** Goal: 画清边界防蔓延

### In Scope
- 架构评审：当前程序架构对新方向是否正确
- connector 可插拔框架设计（微博 / B站 / 小红书 / 抖音 + 保留 Reddit）
- 存储层设计：SQLite 元数据索引 + 哈希去重媒体库 + janitor（配额/TTL/LRU）
- MCP v2 schema 设计（含 video_refs，为视频铺路）
- 模块删/留/建清单（去预测内核、留凭证与 Reddit、建 connectors/store/mcp_v2）

### Out of Scope
- 实际代码实现（归入 dev 阶段）
- 具体平台 connector 的抓取实现
- 发布/转码/水印逻辑
- 网页看板 UI 重做（后续单独 DESIGN.md 流程）

## Step 3: Visual Direction
> **[UI]** 前端项目必填，纯后端 skip。

- **调性**: 无 — CLI/workflow only；无可视表面（UI 后续单独流程）

## Step 4: Premise Challenge
> **[ALL]** Goal: 确认这是正确的问题

- **换个角度**: 是否真要「全删 harness」？爆款判定（engagement 阈值）也许有用 → 但应简化为规则而非 ML 预测，不在本次 scope。
- **不做代价**: 保留旧代码结构 = 持续技术债，接视频时重构翻倍。
- **已有复用**: credential 层、Reddit connector、artifact_api 投影分层、MCP 只读契约 → 全部保留复用。
- **Scrap it?**: 不 scrap；要「去预测内核、留可插拔外壳」。

## Step 5: Impact Map
> **[ALL]** Goal: 知道改了谁受影响

- `news_harness/manual_smoke.py` — 删预测/打分/回访链路，仅留抓取骨架
- `news_harness/evaluator.py` / `baseline.py` / `rulebook.py` / `loop_driver.py` — 删除
- `news_harness/direct_cli_backend.py` — 保留凭证/auth，重构为 connector
- `news_harness/artifact_api.py` — 投影分层复用，扩展 MCP v2
- `schemas/v1/mcp_export_item.schema.json` — v2 扩展（加 video_refs）
- 新增：`connectors/`（base+各平台+registry）、`store/`（db+media+cache+janitor）、`models.py`、`mcp_v2.py`

## Step 6: Success Criteria
> **[ALL]** Goal: 定义"做完"的客观标准

- [x] **SC-01**: 输出明确架构结论（对/错）及理由
- [x] **SC-02**: 给出 ≥2 套重构方案并对比（优劣/工作量/风险）
- [x] **SC-03**: 产出模块删/留/建清单，与现有代码文件一一对应
- [x] **SC-04**: 定义 MCP v2 schema（含 video_refs），且不破坏白名单/禁令牌测试（secret 不外泄）
- [x] **SC-05**: 定义存储层契约（SQLite 索引 + 哈希媒体库 + janitor 配额/TTL/LRU），能解决磁盘/缓存问题

## Step 7: Dream State
> **[MEDIUM+]** Goal: 确认在正确方向

```
  CURRENT(预测 harness)  --->  THIS(爆款搬运骨架: connector+素材池+MCP-v2)  --->  12月(多平台+视频+一键发布)
```

## Step 8: Risk Assessment
> **[MEDIUM+]** Goal: 识别可能出错的地方

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 反爬升级导致 connector 失效 | 中 | 高 | 接口抽象 + 配置化 + 限速退避 |
| 媒体库磁盘配额配置不当丢数据 | 低 | 中 | TTL/refcount 双保护 + 只读快照 |
| 过度设计（先上微服务） | 中 | 中 | 单进程 + SQLite 起步，按需拆分 |

## Step 9: Innovation Token Check
> **[MEDIUM+]** Goal: 不为新而新

| 技术决策 | Token? | 不选成熟方案的理由 |
|---------|:--:|-------------------|
| 沿用 Python stdlib + SQLite + 现有 credential | 否 | 零依赖、可移植、复用已有 |

**已花费: 0/3**

## Step 10: Migration & Rollback
> **[HIGH]** Goal: 上线回退有预案

**迁移**: 分阶段——先建 connectors/store 骨架 + MCP v2，再删旧模块；旧 manual_smoke 可 git revert。
**回滚触发**: 新存储层出现数据损坏或 connector 大面积失效。
**回滚操作**: git revert change 分支；SQLite/媒体库为新增，删除目录即可。
**回滚时间**: ≤ 15 min。

## Step 11: Stakeholder Sign-off
> **[MEDIUM+]** Goal: 该知道的人都知道了

| 角色 | 姓名 | 诉求 |
|------|------|------|
| 产品/技术决策者 | oxjames | 架构方向正确、磁盘可控、视频可扩展 |
| 运维（未来） | — | 介质清理/配额可观测 |

---
## Quality Gate

✅ S1 有量化数据（2338 行编排器、散落 JSON、无配额）
✅ S2 边界清晰（In/Out 互斥）
⬜ [UI] S3 视觉调性已选定（纯后端，skip）
✅ S4 挑战≥2假设 + 有Scrap-it备选
✅ S5 影响模块无遗漏
✅ S6 每条SC可度量
⬜ [H]  S10 回滚方案可执行（已填，待确认）
⬜ **TODOS.md**: 延期项已记录

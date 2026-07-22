---
phase: design
skill: taiyi-design
gate: human
produces: DESIGN.md
upstream: [requirement]
downstream: [task, ui-design]
---
<!-- phase:design skill:taiyi-design gate:human est:30min produces:DESIGN.md upstream:[requirement] downstream:[task,ui-design] cplx:[ALL]4steps +[M+]6 +[H]1 (+opt:1) -->
# DESIGN: news_harness 架构评审：从 harness 转向国内爆款搬运工具

> **一句话**: Python 3.12（沿用 stdlib-only，零第三方依赖）+ SQLite（元数据索引）+ 本地文件系统（哈希媒体库）。与现有 direct_cli_backend.py 凭证层、Reddit connector 同栈。

---

## Step 1: Context & Constraints
> **[ALL]** Goal: 框定设计边界 | Inputs: REQUIREMENT.md §2, §4, §8
<!-- Action: 技术栈全貌 + 约束条件 -->

- **选定**: Python 3.12（沿用 stdlib-only，零第三方依赖）+ SQLite（元数据索引）+ 本地文件系统（哈希媒体库）。与现有 direct_cli_backend.py 凭证层、Reddit connector 同栈。
  理由: 零依赖保证可移植与 VPS 部署简单；SQLite 解决『散落 JSON + 扫文件』问题；复用已有 credential / Reddit / artifact_api 投影，不引入新基础设施（Innovation Token = 0）。
- **约束**: 

<!-- Validate: 约束覆盖技术/性能/兼容性/时间/团队？ -->

## Step 1a: Current State
> **[ALL]** Goal: 变更前基线，ADR 强制覆写模式 | Inputs: CHANGE.md §1
<!-- Action: 记录变更前的架构/行为状态。ADR 模式：强制覆写 DESIGN.md，不准 append-only -->

**当前架构/行为**:

现有 news_harness 是 outcome-first 预测 harness：manual_smoke.py(2338行) 编排 Fetch→Predict(DeepSeek)→Revisit→Evaluate→Rulebook，围绕『预测爆款』打造，含 baseline/rulebook/evaluator/loop_driver。产物是每轮 dump 的 JSON（artifacts/），无缓存淘汰、无磁盘配额；Media 仅 image_refs 引用，无本地下载/去重。结论：这套骨架对『搬运工具』是错的——①不需要预测/评估/回访；②散落 JSON + 无配额会撑爆磁盘；③MCP 无视频字段；④connector 不可插拔（X/Reddit/Xueqiu 硬编码）。

> ⚠️ **ADR 覆写规则**: 此 DESIGN.md 是当前变更的设计真源，**强制覆写**而非追加。每次设计变更请覆写/更新相关节段，不要保留过时的旧设计 —— 半年后的 Agent 从此文档拼出系统全貌，不看历史版本。变更记录由 CHANGELOG.md 和 git log 承担。

<!-- Validate: 基线状态可度量？下一次变更能从此出发？ -->

## Step 1b: Dependency Sandbox
> **[ALL]** Goal: 每个依赖有版本/用途/替代方案/过时检查 | Inputs: package.json / 项目配置
<!-- Action: 列出所有新增/变更的依赖，标注版本范围、用途、替代方案、npm 最新版 -->

| 依赖 | 版本范围 | 用途 | 考虑过的替代 | npm 最新 | 过时检查 |
|------|---------|------|------------|:-------:|:--------:|
| Python stdlib | `3.12` | 全部运行时，零第三方依赖 | 引入 requests/playwright（增加依赖与部署复杂度） | `n/a` | ✅ |
| SQLite (sqlite3) | `内置` | 元数据索引，替代散落 JSON | Postgres（过度，需独立服务） | `n/a` | ✅ |
| credential 层（沿用） | `现有` | NEWS_HARNESS_* 环境变量 + json 配置管理密钥 | 引入 vault（当前规模不必要） | `n/a` | ✅ |

> 💡 写模板时 `npm view <pkg> version` 检查最新版本；若有 major bump 警告需说明。
> SSOT 规则：依赖变更的真源在 `package.json` / lockfile，此表为设计视角的验证清单。

<!-- Validate: 每个依赖有最新版本确认？替代方案已搜索？→如果依赖陈旧则应在此说明已在最近的 minor 上 -->

## Step 2: Architecture Overview
> **[ALL]** Goal: 一眼看清整体结构 | Inputs: Step1+REQUIREMENT.md §2
<!-- Action: Mermaid图 + 模块清单(新增/修改/删除) -->

```mermaid
```

| 模块 | 操作 | 路径 | 说明 |
|------|------|------|------|

### 既有架构对齐（brownfield）
<!-- Action: 三表 — 触碰模块 / 抽象沿用 / 模式对比 -->

**触碰模块**:
- `news_harness/manual_smoke.py（删预测/打分/回访链路，仅留抓取骨架）`（既有 · 本次修改）
- `news_harness/artifact_api.py（投影分层复用，扩展 MCP v2）`（既有 · 本次修改）
- `news_harness/direct_cli_backend.py（保留凭证/auth，重构为 connector）`（既有 · 本次修改）
- `schemas/v1/mcp_export_item.schema.json（升 v2 扩展）`（既有 · 本次修改）
- `news_harness/connectors/（base.py + 各平台 connector + registry.py）`（新增）
- `news_harness/store/（db.py + media.py + cache.py + janitor.py）`（新增）
- `news_harness/models.py（ContentItem 规范模型）`（新增）
- `news_harness/mcp_v2.py（视频就绪导出）`（新增）
- `news_harness/publish.py（未来受控发布层）`（新增）

<!-- Validate: 禁动清单是否从 CONTEXT 复用？新增模块有没有侵入禁动区？ -->

## Step 3: Options

> **[ALL]** Goal: ≥2方案含对照 | Inputs: Step1+2
<!-- Action: 每个方案: 思路/优点/缺点/代价。A=不改/最小改动 -->

| 方案 | 名称 | 思路 | 优点 | 缺点 | 代价 |
|------|------|------|------|------|------|
| A | 分层重构：connector 框架 + 存储层 + MCP v2（推荐） | 分层重构：connector 框架 + 存储层 + MCP v2（推荐） | 可插拔：加新平台只写一个 connector 模块，不动核心<br>SQLite + 哈希媒体库根治磁盘/缓存问题（配额/TTL/LRU）<br>MCP v2 视频就绪，未来接视频零改造<br>删预测内核，代码量大幅下降（去掉 evaluator/baseline/rulebook/loop_driver）<br> | 前期设计成本较高<br>需把 manual_smoke 编排逻辑重写为 connector 驱动<br> | — |
| B | 最小改动：保留 manual_smoke 编排器，只换抓取源 + 补媒体库 | 最小改动：保留 manual_smoke 编排器，只换抓取源 + 补媒体库 | 改动最小，最快出活<br>复用现有 orchestrator 心智模型<br> | 编排器仍是 2338 行巨石，加平台要改核心<br>无统一 ContentItem，各平台字段散落<br>媒体库只是补丁，缓存/配额仍是脚本逻辑<br>未来视频/发布要再重构一次，技术债翻倍<br> | — |
| C | 微服务：抓取/存储/MCP 独立服务 | 微服务：抓取/存储/MCP 独立服务 | 各服务独立伸缩<br>故障隔离彻底<br> | 引入进程间通信/队列/部署复杂度<br>零依赖优势丧失，VPS 运维成本陡增<br>对当前规模严重过度设计（Innovation Token 超支）<br> | — |

<!-- Validate: ≥2方案？含"不改"对照？代价量化？ -->

## Step 4a: Reuse Analysis

> **[ALL]** Goal: 显式声明复用既有代码 / 模块 / 模式
<!-- Action: 列出本次会复用的现有模块、新增/修改的边界 -->

**复用既有模块**（existing / 可复用）:
- 无新增依赖 — 沿用既有 `WorkflowEngine` / `artifact-validator` / `template-seed` 等基础设施，零额外成本（性能 / 复杂度中性）。

**新增模块**（仅当确实需要）: 无

**不重写**: 复用既有 helper（这是来自 现有 模块的一种 trade-off 决策，避免 复杂度 漂移）。

## Step 4b: Decision

> **[ALL]** Goal: 选定方案并说清理由 | Inputs: Step3
<!-- Action: 基于数据/约束决策，不写"感觉这个好" -->

- **Chosen:** A
- **Reason:** 新方向本质是『多源抓取 → 统一素材池 → 可发布输出』，可插拔 connector + 统一存储层是最小正确抽象。最小改动(B)把技术债留到视频阶段，微服务(C)过度设计。A 在删除预测内核的同时一次性解决磁盘/缓存/视频扩展三件事，且零新依赖。

<!-- Validate: 理由基于数据/约束而非主观？ -->

## Step 5: Detailed Design
> **[MEDIUM+]** Goal: 落地细节完整 | Inputs: Step4
<!-- Action: DDL+API契约+时序图 -->

### 数据模型
```sql
统一 ContentItem（取代 RadarTimelineItem）：{id, platform, source_label, author{id,name,handle,avatar_url,follower_count?}, published_at, observed_at, copy_text, source_url, canonical_url, media:[{kind:'image'|'video', url|local_ref, mime, dimensions, duration_s?, thumbnail_ref?, byte_size?, hash, rights_status}], engagement{likes,comments,shares,views}, dedup_hash, raw_refs}。媒体以 content hash 去重，local_ref 指向哈希媒体库。
```

### API 设计
```
MCP v1→v2：McpExportItem 增加 author 对象、video_refs[]、media_kind 字段；image_refs 保留。保持白名单(ALLOWED_MCP_KEYS) + 禁令牌(FORBIDDEN_MCP_KEYS) 测试，secret/cookie/session 永不外泄。MCP 保持只读；发布走独立 publish.py 受控层。schemas/v1/mcp_export_item.schema.json 升 v2。
```

### 关键流程
1) scheduler/loop 触发 → 2) registry 解析启用的 connector → 3) connector.fetch_feed() 拉平台热帖 → 4) 归一化为 ContentItem → 5) store.db 去重+写索引，媒体落哈希库(media.py) → 6) janitor 按配额/TTL/LRU 清理 → 7) mcp_v2 只读导出（文案+图片+视频）→ 8)（未来）publish.py 受控发布。

<!-- Validate: DDL有索引？API有rate limit？流程有错误路径？ -->

## Step 6: Blast Radius
> **[MEDIUM+]** Goal: 每个决策的最坏情况 | Inputs: Step2+4
<!-- Action: 决策→爆炸半径→最坏情况→隔离措施 -->

| 决策 | 半径 | 最坏情况 | 隔离 |
|------|:--:|---------|------|
| 删除预测内核(evaluator/baseline/rulebook/loop_driver) | 中 | 历史评估数据/规则书失效 | 旧模块独立，删除不影响 connector/store |
| 重构 manual_smoke → connector 驱动 | 中 | 抓取逻辑短暂回归 | 先建新模块再切，旧文件 git revert 可回滚 |
| direct_cli_backend 重构为 connector | 低 | X/Reddit 抓取临时中断 | Reddit 线保留，X 非首批重点 |
| artifact_api 扩展 MCP v2 | 低 | 下游 MCP 消费方需适配 v2 | v1 字段保留，向后兼容 |
| mcp_export_item.schema 升 v2 | 低 | 校验不通过 | 独立 schema 文件 |
| 新增 connectors/store/models/mcp_v2 | 低 | 新代码 bug | 独立包，单测覆盖 |

<!-- Validate: 有没有一个变更能影响所有用户？半径可控？ -->

> 📎 **SSOT 规则**: 风险真源见 [CHANGE.md §Risks](CHANGE.md)。Blast Radius 从架构视角验证已声明的业务风险，不重复定义。

## Step 7: Innovation Token Accounting
> **[MEDIUM+]** Goal: 不浪费创新额度 | Inputs: Step2+5
<!-- Action: 新技术/新Infra必须说明理由。每公司约3token -->


**累计: 0/3**

<!-- Validate: ≤3？每个"是"有充分理由？ -->

## Step 8: Trade-off Analysis
> **[MEDIUM+]** Goal: 诚实面对取舍 | Inputs: Step4+5
<!-- Action: 选择了什么/代价是什么/为什么接受 -->

| 权衡点 | 选择 | 接受理由 |
|--------|------|---------|
| 删预测内核 | 用 engagement 阈值规则替代 ML 预测 | 搬运工具不需预测爆款，简单规则足够，去掉 ML 依赖 |
| 存储选型 | SQLite 单文件 | 当前单进程够用；多进程并发写用 WAL 即可，避免过度设计 |
| 媒体库位置 | 本地哈希文件系统 | 简单可移植；未来可换对象存储，接口抽象隔离 |

<!-- Validate: 每个权衡都说清了"接受代价的理由"？ -->

## Step 9: Distribution & Deployment
> **[MEDIUM+]** Goal: 确保能发布 | Inputs: Step5
<!-- Action: 新artifact类型？CI/CD变更？回滚方式？ -->

- **新artifact**: 无
- **CI/CD变更**: 
- **回滚方式**: 

<!-- Validate: 新artifact的build/publish/update流程完整？ -->

## Step 10: Security Model
> **[HIGH]** Goal: 威胁建模仿真 | Inputs: Step5+REQUIREMENT.md §9
<!-- Action: STRIDE威胁建模+缓解 -->

| 威胁 | 攻击向量 | 缓解 |
|------|---------|------|
| Secret 泄露 | 导出/工件含 cookie/session | 复用 forbidden-keys 测试，白名单校验，secret 永不序列化 |
| 版权/风控内容搬运 | 搬运未授权内容 | rights_status 字段发布前过滤 |
| 磁盘写满 | 媒体无限堆积 | janitor 配额 + TTL + LRU 兜底 |
| 反爬封号 | 高频抓取触发风控 | connector 限速/退避/配置化 |

<!-- Validate: OWASP Top10全覆盖？敏感数据加密+日志脱敏？ -->

> 📎 **SSOT 规则**: 安全策略真源见 [CHANGE.md §Risks](CHANGE.md) + [REQUIREMENT.md §Non-Functional Security](REQUIREMENT.md)。STRIDE 威胁建模从此派生，不独立重评估。

## Step 11: Rollout Strategy
> **[MEDIUM+]** Goal: 上线有计划 | Inputs: Step6+9
<!-- Action: 灰度比例+观察时间+回滚触发 -->

- 1. 建 connectors/ store/ models.py mcp_v2.py 骨架（接口 + 空实现）
- 2. 实现 store 层 + janitor，接 SQLite + 哈希媒体库
- 3. 迁移 manual_smoke 抓取逻辑到 Reddit connector（保留线）
- 4. 加微博 / B站 connector（第一批，易爬 + 视频就绪）
- 5. 扩展 artifact_api → mcp_v2 + schema v2
- 6. 删 evaluator/baseline/rulebook/loop_driver + manual_smoke 预测链路
- 7. 小红书 connector（第二批）；抖音/快手（三期，反爬方案成熟后）

> 📎 **SSOT 规则**: 回滚真源见 [CHANGE.md §Risks](CHANGE.md)。此处为部署视角的灰度/上线步骤，与 CHANGE 的 rollback_{trigger,ops,time} 互不重复。若此处的回滚方式 != CHANGE 声明的，即视为 SSOT 违规。

## Step 12: Architecture Evolution
- [reusable-abstraction] 媒体库抽象可换 S3/对象存储
- [reusable-abstraction] 加感知哈希相似度去重，防搬运撞车
- [tech-decision] publish.py 支持多平台一键发布 + 水印
- [cross-module-contract] 网页看板按 DESIGN.md 重做，消费 mcp_v2

---
## Step 13: Code Generation Contract
> **[ALL]** Goal: DESIGN→TASK→DEV 三阶段代码生成链 | Inputs: Step 2+5
<!-- Action: 结构化文件清单 → TASK 按文件拆分 Slice → DEV 逐文件生成 -->


<!-- Validate: module_manifest 覆盖所有模块？每模块 pattern 匹配实际代码结构？ -->

---
## Quality Gate
<!-- Evidence-first: 每项通过需要可验证证据，不是"感觉对了"。ECC verification-loop 取代 Superpowers verification-before-completion -->

- [ ] S1 约束完整
- [ ] S2 架构图+模块清单清晰
- [ ] S3 ≥2方案含对照
- [ ] S4 决策基于数据
- [ ] [M+] S5 含DDL+API+流程
- [ ] [M+] S6 Blast Radius已评估
- [ ] [M+] S8 权衡分析诚实
- [ ] [M+] S9 部署流程完整
- [ ] [H]  S10 STRIDE已建模
- [ ] [M+] S11 灰度+回滚明确
- [ ] **2-week smell**: 合格工程师2周内能交付一个小feature？cognitive#11
- [ ] **Refactor-first**: 重构和功能改动分开了吗？cognitive#13: 先让改动变简单，再做简单改动

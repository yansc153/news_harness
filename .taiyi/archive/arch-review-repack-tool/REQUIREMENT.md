---
phase: requirement
skill: taiyi-requirement
gate: auto
produces: REQUIREMENT.md
upstream: [change]
downstream: [design, ui-design]
---
<!-- phase:requirement skill:taiyi-requirement gate:auto est:20min produces:REQUIREMENT.md upstream:[change] downstream:[design,ui-design] cplx:[ALL]5steps +[M+]4 +[H]1 -->
# REQUIREMENT: news_harness 架构评审：从 harness 转向国内爆款搬运工具

> **一句话**: 对现有预测 harness 骨架做架构评审，给出『对/错』结论与重建契约（connector 框架 + 存储层 + MCP v2），不动业务代码。

---

> ⛔ **Out of Scope — 本变更明确不覆盖以下事项**
> <!-- 放置在最顶部，让读者第一眼知道什么不做。与 Step 2 的 scope_out 内容一致无需重复详述，此处为硬性提醒 -->
> - 预测 / 评分 / 回访 / 规则书等 harness 能力
> - 具体平台反爬破解细节
> - 转码 / 水印 / 多平台一键发布逻辑
> - UI 实现（归入后续 DESIGN.md 流程）
>
> 📌 *完整范围切分见下方 §Step 2 Scope Partitioning*

---

## Step 1: User Stories
> **[ALL]** Goal: 从用户视角说清需求 | Inputs: CHANGE.md §1, §2
<!-- Action: As a [角色] I want [功能] so that [价值]. 覆盖所有角色 -->

- **As a** 内容运营/创作者, **I want** 一个能从国内爆款平台抓取并统一沉淀素材的工具, **so that** 我可以挑选题、搬运发布，不用手动逐个平台扒 (P0)
- **As a** 项目维护者, **I want** 可插拔的 connector 框架和统一存储层, **so that** 加新平台/视频时不用重写整套骨架 (P0)
- **As a** 下游自动化（通过 MCP）, **I want** 只读拿到『文案+图片+视频』的标准化内容, **so that** 我做二次创作/发布时格式稳定 (P1)

<!-- Validate: 所有用户角色都覆盖了？ -->

## Step 2: Scope Partitioning
> **[ALL]** Goal: 分版本切范围，防 TASK 阶段误判 | Inputs: CHANGE.md §2
<!-- Action: v1=本次必做, v2=下次, out=永不. 至少 v2+out 各 ≥1 条 -->

### v1（本次必做）
- 架构评审结论：当前 harness 骨架对新方向是否正确（对/错 + 理由）
- connector 可插拔框架设计（接口 / 注册表 / 配置驱动）
- 存储层设计：SQLite 元数据索引 + 哈希去重媒体库 + janitor（配额/TTL/LRU）
- MCP v2 schema 设计（含 video_refs，视频就绪），且不破坏白名单/禁令牌
- 模块删/留/建清单，与现有代码文件一一对应

### v2（下次）
- 实现 connectors（微博 / B站 / 小红书 / 抖音 + 保留 Reddit）
- 实现 store 层与 janitor 守护进程
- 实现 mcp_v2 导出 + 受控发布适配层
- 网页看板按新 DESIGN.md 重做

### out（永不）
- 预测 / 评分 / 回访 / 规则书等 harness 能力
- 具体平台反爬破解细节
- 转码 / 水印 / 多平台一键发布逻辑
- UI 实现（归入后续 DESIGN.md 流程）

<!-- Validate: v2 和 out 各 ≥ 1 条？v1 不包含 out 项？ -->

## Step 3: Functional Requirements
> **[ALL]** Goal: 拆成可测试的功能点 | Inputs: Step1
<!-- Action: FR-XX编号，分模块。涉及UI标注(UI)→触发Phase4 -->

### 架构评审
- **FR-01**: 输出明确结论（当前 harness 骨架对新方向是否正确）及理由
- **FR-02**: 给出 ≥2 套重构方案并对比（优劣 / 工作量 / 风险）
### connector 框架
- **FR-03**: 定义 Connector 接口：fetch_feed(opts) / fetch_by_url(url) → 统一 ContentItem
- **FR-04**: 定义 registry：按平台名自动发现 connector，配置驱动启用
- **FR-05**: 定义 ContentItem 规范模型：platform / author / copy_text / media[](image|video) / engagement / urls / timestamps / raw_refs / dedup_hash
### 存储层
- **FR-06**: SQLite 元数据索引：内容去重、发布状态、历史，替代每轮 dump JSON
- **FR-07**: 哈希媒体库：内容哈希去重落盘 + manifest（size/refcount/last_accessed/rights_status）
- **FR-08**: janitor 守护：磁盘配额上限 + TTL + LRU 淘汰，refcount=0 即删
### MCP v2
- **FR-09**: 导出含 copy_text / image_refs / video_refs + author，media_kind 区分 text/image/video/mixed
- **FR-10**: 保持白名单 + 禁令牌测试：secret/cookie/session 永不外泄
- **FR-11**: MCP 保持只读；真正『发布』走独立受控适配层（publish.py），不并入导出
### 模块清单
- **FR-12**: 列出删（evaluator/baseline/rulebook/loop_driver + manual_smoke 预测链路）/ 留（credential 层 / Reddit / artifact_api 投影）/ 建（connectors/ store/ models.py mcp_v2.py）

<!-- Validate: 每个FR可独立测试？编号连续？ -->

## Step 4: Acceptance Criteria
> **[ALL]** Goal: 每个FR都有客观验收标准 | Inputs: Step3
<!-- Action: Given/When/Then，AC-XX对应FR-XX。verify=可执行验证命令 -->

- [ ] **AC-01**: SC-01：输出明确架构结论（对/错）及理由
  - **验证**: 
- [ ] **AC-02**: SC-02：给出 ≥2 套重构方案并对比（优劣/工作量/风险）
  - **验证**: 
- [ ] **AC-03**: SC-03：产出模块删/留/建清单，与现有代码文件一一对应
  - **验证**: 
- [ ] **AC-04**: SC-04：定义 MCP v2 schema（含 video_refs），且不破坏白名单/禁令牌测试
  - **验证**: 
- [ ] **AC-05**: SC-05：定义存储层契约（SQLite 索引 + 哈希媒体库 + janitor 配额/TTL/LRU）
  - **验证**: 
- [ ] **AC-06**: 给出 connector 接口签名与 ContentItem 字段表
  - **验证**: 
- [ ] **AC-07**: 给出 janitor 配额/TTL/LRU 参数与清理语义
  - **验证**: 

<!-- Validate: 每个AC可独立验收？Given/When/Then完整？验证命令可执行？ -->

## Step 5: Non-Functional Requirements
> **[ALL]** Goal: 性能/安全/可用性有硬指标 | Inputs: Step2
<!-- Action: NFR-XX编号，每个带数值 -->

  ### 性能
  - **NFR-P01**: 媒体库支持磁盘配额上限与 LRU 淘汰，单进程 SQLite 读写，索引查询 O(1) 而非扫 JSON
  - **NFR-P02**: connector 失败隔离：单平台故障/限速不影响整体抓取循环
  
  ### 安全
  - **NFR-S01**: 导出与工件永不包含 secret/cookie/session/登录态（沿用现有 forbidden-keys 测试）
  - **NFR-S02**: 媒体 rights_status 字段用于在搬运前过滤版权/风控内容
  

<!-- Validate: 每个指标有具体数字？ -->

> 📎 **SSOT 规则**: NFR-S* 安全要求应基于 [CHANGE.md §Risks](CHANGE.md) 做非功能性拆解，不独立重评估。每条 NFR-S 应与 CHANGE 的 risks[] 可追溯。

## Step 6: Error & Rescue Map
> **[MEDIUM+]** Goal: 每个错误都有名字和恢复路径 | Inputs: Step2+3
<!-- Action: 触发条件→捕获位置→用户看到→恢复路径 -->


<!-- Validate: 所有可能的错误都有名字？恢复路径可执行？ -->



## Step 9: Dependencies
> **[MEDIUM+]** Goal: 外部依赖不阻塞 | Inputs: CHANGE.md §4
<!-- Action: 技术约束/第三方/跨团队+状态+风险 -->


<!-- Validate: 第三方SLA确认？跨团队排期对齐？ -->

## Step 10: Security & Compliance
> **[HIGH]** Goal: 安全不出事 | Inputs: Step4+5
<!-- Action: OWASP Top10 + GDPR/PIPL. user/auth/payment/PII场景必填 -->

- [ ] npm audit 无 critical/high
- [ ] 无硬编码密钥/令牌
- [ ] PII/GDPR 合规检查（若涉及用户数据）

<!-- Validate: threat modeling过了？PII合规？ -->

---
## Quality Gate
<!-- Evidence-first: 每个需求可追溯到CHANGE.md的SC，ECC 替代 Superpowers 需求逐条对账 -->

- [ ] S1 用户角色全覆盖
- [ ] S2 版本切分 v1/v2/out 各≥1条
- [ ] S3 每个FR可独立测试
- [ ] S4 AC用Given/When/Then + 验证命令
- [ ] S5 非功能需求有数值
- [ ] [H]  S10 安全合规已覆盖
- [ ] 无[NEEDS CLARIFICATION]残留

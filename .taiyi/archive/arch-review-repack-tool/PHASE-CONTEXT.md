# Change Graph: arch-review-repack-tool

## Phases
### change (5 nodes)
**acceptance_criterion** (5)
  - 输出明确架构结论（对/错）及理由
  - 给出 ≥2 套重构方案并对比（优劣/工作量/风险）
  - 产出模块删/留/建清单，与现有代码文件一一对应
  - ... +2 more

### requirement (23 nodes)
**acceptance_criterion** (7)
  - SC-01：输出明确架构结论（对/错）及理由
  - SC-02：给出 ≥2 套重构方案并对比（优劣/工作量/风险）
  - SC-03：产出模块删/留/建清单，与现有代码文件一一对应
  - ... +4 more
**nfr** (4)
  - 导出与工件永不包含 secret/cookie/session/登录态（沿用现有 forbidden-keys 测试）
  - 媒体 rights_status 字段用于在搬运前过滤版权/风控内容
  - 媒体库支持磁盘配额上限与 LRU 淘汰，单进程 SQLite 读写，索引查询 O(1) 而非扫 JSON
  - ... +1 more
**unknown** (12)
  - 输出明确结论（当前 harness 骨架对新方向是否正确）及理由
  - 给出 ≥2 套重构方案并对比（优劣 / 工作量 / 风险）
  - 定义 Connector 接口：fetch_feed(opts) / fetch_by_url(url) → 统一...
  - ... +9 more

### design (21 nodes)
**threat** (4)
  - Secret 泄露
  - 版权/风控内容搬运
  - 磁盘写满
  - ... +1 more
**risk** (6)
  - 删除预测内核(evaluator/baseline/rulebook/loop_driver)
  - 重构 manual_smoke → connector 驱动
  - direct_cli_backend 重构为 connector
  - ... +3 more
**design_decision** (4)
  - 删预测内核
  - 存储选型
  - 媒体库位置
  - ... +1 more
**deployment_step** (7)
  - 1. 建 connectors/ store/ models.py mcp_v2.py 骨架（接口 + 空实现）
  - 2. 实现 store 层 + janitor，接 SQLite + 哈希媒体库
  - 3. 迁移 manual_smoke 抓取逻辑到 Reddit connector（保留线）
  - ... +4 more

### ui-design (1 nodes)
**design_decision** (1) CLI/workflow only; no user interface surfaces. UI 重做归入后续独...

### task (23 nodes)
**slice** (7)
  - 建立 connectors/store/models/mcp_v2 骨架与接口
  - 实现 store 层：SQLite 索引 + 哈希媒体库 + janitor
  - 迁移 Reddit connector（保留线）
  - ... +4 more
**risk** (4)
  - janitor 误删未发布媒体
  - Reddit 抓取逻辑回归
  - 反爬触发封号
  - ... +1 more
**rollback** (7)
  - git revert
  - git revert + 删 media 库目录
  - git revert
  - ... +4 more
**unknown** (5)
  - 1 (基础)
  - 2 (依赖 S1, 可并行)
  - 3 (依赖 S1/S2)
  - ... +2 more

### test (1 nodes)
**test_case** (1) (empty)

### review (15 nodes)
**unknown** (12)
  - rights_status 同时出现在 ALLOWED 与 FORBIDDEN 集合，ALLOWED 中冗余；bu...
  - 4 个 legacy 测试文件（test_docker_feed_mount / test_radar_timel...
  - manual_smoke.py 仍保留 dead 预测辅助函数（score_manual_deepseek / r...
  - ... +9 more
**test_case** (3) FORBIDDEN_MCP_V2_KEYS 覆盖 secret/cookie/token/session/mode... / credentials 经 repo-external secrets，无硬编码密钥入库。 / unit

### integration (1 nodes)
**unknown** (1) (empty)

## Cross-Cutting Concerns
**12** SSOT violations: 4 high, 4 medium, 4 low
- [LOW] design_decision (design vs task): design_decision 跨阶段不一致: "删预测内核" ≠ "建立 connectors/store/models/mcp_v2 骨架与接口"
- [LOW] design_decision (design vs task): design_decision 跨阶段不一致: "存储选型" ≠ "实现 store 层：SQLite 索引 + 哈希媒体库 + janitor"
- [LOW] design_decision (design vs task): design_decision 跨阶段不一致: "媒体库位置" ≠ "迁移 Reddit connector（保留线）"
- [LOW] design_decision (design vs task): design_decision 跨阶段不一致: "A" ≠ "微博 / B站 connector（第一批，易爬 + 视频就绪）"
- [HIGH] nfr (requirement vs design): nfr 跨阶段不一致: "connector 失败隔离：单平台故障/限速不影响整体抓取循环" ≠ "Secret 泄露"
- [HIGH] nfr (requirement vs design): nfr 跨阶段不一致: "导出与工件永不包含 secret/cookie/session/登录态（沿用现有 forbidden-keys 测试）" ≠ "版权/风控内容搬运"
- [HIGH] nfr (requirement vs design): nfr 跨阶段不一致: "媒体 rights_status 字段用于在搬运前过滤版权/风控内容" ≠ "磁盘写满"
- [HIGH] nfr (requirement vs design): nfr 跨阶段不一致: "媒体库支持磁盘配额上限与 LRU 淘汰，单进程 SQLite 读写，索引查询 O(1) 而非扫 JSON" ≠ "反爬封号"
- [MEDIUM] risk (task vs design): risk 跨阶段不一致: "误删仍在用代码" ≠ "删除预测内核(evaluator/baseline/rulebook/loop_driver)"
- [MEDIUM] risk (task vs design): risk 跨阶段不一致: "janitor 误删未发布媒体" ≠ "重构 manual_smoke → connector 驱动"
- [MEDIUM] risk (task vs design): risk 跨阶段不一致: "Reddit 抓取逻辑回归" ≠ "direct_cli_backend 重构为 connector"
- [MEDIUM] risk (task vs design): risk 跨阶段不一致: "反爬触发封号" ≠ "artifact_api 扩展 MCP v2"

## Stats
- Total nodes: 90
- Total edges: 35
- Phases with nodes: 8/8


## review (✓)
**评审**:
- [x] **Approve**（机器评审通过：无阻断项，96/96 绿灯，secret 处理合规，架构对齐 ARCHITECTURE.md）
> ⚠️ **人工门**：最终放行需用户作为审批人执行 `taiyi continue --approver "名字"`。
> 上述非阻断项（rights_status 冗余、4 个 legacy 测试、manual_smoke dead code、README 过时）可在 integration 前或之后处理，不阻塞本门。


---

**当前**: integration · Skill: @taiyi-integration · 工件: INTEGRATION.md
**复杂度**: medium | Profile: full
**下一步**: 加载 @taiyi-integration，编辑 INTEGRATION.md

*引擎生成 · Agent 读此文件即可*

<!-- ⚠️ SSOT 声明: 以下摘要仅作快速参考。各阶段真源始终是对应的上游工件 (CHANGE.md / DESIGN.md / TASK.md 等)。
     版本发生变更或阶段有冲突时，请直接读取工件文件而非本摘要。 -->
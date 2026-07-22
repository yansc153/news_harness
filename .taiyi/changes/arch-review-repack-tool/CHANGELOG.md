# Changelog

## [v2.0.0] - 2026-07-22

### Added
- 金融内容搬运工具框架：connector 注册表 + base/source/processing 抽象层
- Reddit source connector（沿用 rdt-cli cookie 凭证路径，凭证层不变）
- 雪球 source connector：显式点击「最新」tab，批次 20、底线 5 + 保底阶梯
- 双闸门过滤：Gate A 账号黑名单 / Gate B 内容阈值（≥500字·≥50赞·≥10评·有配图）/ Gate C 个人优先
- translate + llm 处理链（外文翻译后 LLM 改写；中文直出 LLM），含离线降级
- SQLite store（db / media / cache / janitor）
- 只读 MCP v2 导出（白名单字段 + forbidden key 校验）
- batch 编排（fetch → translate → llm → store → mcp_v2 export），端到端保留视频 seam

### Changed
- CLI 裁剪为 v2 repack 命令集（移除 prediction 子命令）

### Removed
- 预测 harness（evaluator / baseline / rulebook / loop_driver）
- 泛流量平台与 Discovery / 聚合代码

### Fixed
- connector registry 递归 bug（单一 classmethod + 类级共享 dict）

### Security
- MCP 导出严格白名单；secret / cookie / token / session / model_ref / user_id 永不外泄

---

### Migration
- 环境变量无变化；`configs/secrets.env`（本地）/ `configs/secrets.vps.env`（VPS）指向的凭证文件路径不变。
- 数据库：SQLite 由 store 层自动初始化（首次运行建表），无手动 DDL。
- 配置：`configs/all_source_runner.json` 批次上限 20、雪球批次 20（已更新）。

### Rollback
- 触发：MCP 导出出现 forbidden key 泄漏，或 96 测试套件回归失败
- 操作：1. `git revert` 本次变更 commit  2. 重启服务（或 `docker compose up -d`）
- 时间：< 5 分钟

### Post-Launch Watch
- 观察期：7 天
- 指标：抓取成功率、MCP 导出条目数、96 测试回归、cookie 过期告警
- 退出标准：连续 7 天抓取成功率 ≥ 95% 且无 secret 泄漏
- 异常：cookie 过期 → 重新粘贴凭证文件后重启

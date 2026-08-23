# News Harness

选题 harness：每小时从 A 股市场信号中定向发现讨论内容，预测其传播潜力，验证后通过 MCP 导出给下游自动化。Web dashboard 仅用于监控。

## Language

### 信号与目标

**Theme（题材）**:
A 股市场中的一个板块或概念（如 AI 算力、创新药），由开盘啦接口返回，带有当日强度信号。
_Avoid_: 板块、概念、赛道（口语可用，代码和 artifact 统一用 theme）

**Target Stock（目标股票）**:
本小时被选入雪球抓取清单的一只 A 股个股。每只股票有 symbol、名称、所属题材和入选原因。
_Avoid_: watchlist stock、candidate stock

**Target Set（目标集）**:
一个小时内从开盘啦生成的全部目标股票集合，最多 12 只。每个 cycle 恰好对应一个 Target Set。
_Avoid_: watchlist、stock pool

### 内容与证据

**Observation（观察）**:
一条来自雪球的原始帖子记录，包含正文全文、图片引用、互动数据和原文链接。
_Avoid_: post、article、item（在 source 层统一叫 observation）

**Candidate（候选）**:
通过评论数门槛的 Observation，进入 DeepSeek 预测环节。
_Avoid_: qualified post、passed item

**Timeline Item（时间线条目）**:
Candidate 经评分后进入时间线的最终形态，是下游和用户看到的东西。
_Avoid_: feed entry、radar item

### 质量与门槛

**Comment Threshold（评论门槛）**:
固定值 10。Observation 的回复数必须 ≥ 10 才能成为 Candidate。不可动态放宽。
_Avoid_: quality score、engagement gate（后者太宽泛）

**Structured Failure（结构化失败）**:
带错误码、错误来源和上下文的失败记录。系统不允许空成功或静默降级。
_Avoid_: error log、warning

### 流程阶段

**Discovery（发现）**:
调用开盘啦接口生成 Target Set 的阶段。只产出股票清单，不产出内容。

**Collection（采集）**:
对 Target Set 中每只股票调用雪球讨论接口并应用评论门槛的阶段。产出 Observation 或 Structured Failure。

**Prediction（预测）**:
DeepSeek 对 Candidate 做 1h/4h 传播潜力打分的阶段。

**Revisit（回看）**:
在发布后 1h/4h/24h 回到原帖重新采集互动数据，验证预测是否命中的阶段。

**Export（导出）**:
通过 MCP 白名单把 Timeline Item 的正文、图片引用和来源链接交给下游的阶段。

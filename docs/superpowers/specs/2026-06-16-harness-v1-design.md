# News Harness V1 — Outcome-First Harness Architecture

**Date:** 2026-06-16
**Revision:** 2026-06-17
**Status:** 架构修订，待实施
**Scope:** 从 fixture-first MVP 重构为 outcome-first 最小预测 harness

---

## 目标

News Harness V1 的目标不是先学习“爆款规则”，而是先建立一个可审计、可回放、不会从噪声里学习的预测 harness。

V1 必须跑通这条最小链路：

```text
RawEvidence
  -> CandidateRecord
  -> StructureAnalysis
  -> PredictionRecord
  -> RevisitSchedule
  -> OutcomeRecord
  -> ConnectorQualityReport
  -> BaselineSnapshot + BaselineValidation
  -> OutcomeEvaluation
  -> Rulebook shadow memory
  -> WebProjection / MCP export
```

成功标准：

- 保留原文、原始 URL、图片引用、source、connector、抓取时间、互动快照。
- 对候选内容输出 1h early momentum 和 4h primary spread 预测。
- 到期后回访真实 outcome，记录事实观测和结构化失败。
- 用确定性 `OutcomeEvaluation` 判断预测是否命中、偏差在哪里、是否可用于学习。
- Baseline 和 connector quality 必须先通过验证，否则只能观察，不能学习。
- Rulebook 只消费 evaluation 后的可学习样本，V1 默认 shadow-only。
- Web 和 MCP 使用不同 projection；MCP 不输出 rulebook、内部学习标签、promotion 结果。

---

## 核心修正

旧设计的问题是把 “observed delta” 当成 “传播成功”：

```text
delta > 0 -> win -> rulebook 学习
```

这不成立。点赞 `1 -> 2` 是事实增长，但不是传播成功，也不能驱动 rulebook 学习。

新设计：

```text
revisit 只采集事实
OutcomeEvaluation 才裁决
rulebook 只学习 learnable outcome
```

---

## 架构分层

```text
┌────────────────────────────────────────────┐
│              OUTPUT SURFACE                 │
│  WebProjection / McpExportItem              │
│  原文、图片引用、source URL、evidence status │
└────────────────────────────────────────────┘
                       ▲
┌────────────────────────────────────────────┐
│                LEARNING LOOP                │
│  prediction -> revisit -> outcome eval      │
│  -> shadow rulebook -> calibration report   │
└────────────────────────────────────────────┘
                       ▲
┌────────────────────────────────────────────┐
│              OUTCOME HARNESS                │
│  1 Evidence     raw source truth            │
│  2 Features     deterministic + model tags  │
│  3 Prediction   1h / 4h score               │
│  4 Outcome      factual revisit record      │
│  5 Evaluation   deterministic verdict       │
│  6 Memory       rulebook + rolling store    │
│  7 Guardrails   schema / replay / tests     │
│  8 Ops          health / retry / retention  │
└────────────────────────────────────────────┘
```

Harness 不直接学习互动增量。Harness 学习的是：经过 evidence、baseline、risk、window、leakage gate 后的 outcome evaluation。

---

## 数据流

```text
source connector
  -> RawEvidence
      source_url / canonical_url / title / author / published_at
      fetched_at / evaluated_at / copy_text / image_refs
      engagement_snapshot / connector identity / content_hash
      evidence_status / rights_risk_flags

  -> CandidateRecord
      candidate_id / evidence_ref / dedupe status / risk flags

  -> StructureAnalysis
      hook / format / angle / visual / emotion / audience / risk tags
      注意：structure tags 是 model/rule inference，不是 ground truth

  -> PredictionRecord
      1h_score / 4h_score / confidence / uncertainty
      rationale / feature_contributions / strategy_version
      scoring_version / model_version / rule_version

  -> RevisitSchedule
      +1h early_momentum 观察，+4h primary_outcome 裁决
      24h 只做抽样 audit，不进入默认 rolling queue

  -> OutcomeRecord
      raw_delta / current_snapshot / observation_status
      metrics_source / source_availability / failure_state

  -> ConnectorQualityReport
      required field presence / metric completeness / item count drift
      duplicate rate / truncation signal / quality_status

  -> OutcomeEvaluation
      relative_growth / platform_normalized_growth / author_lift
      confidence_band / outcome_labels / success_grade
      hit_or_miss / learning_eligibility / quality_gates

  -> Rulebook
      collecting -> hypothesis -> shadow -> verified -> active
      V1 只允许 collecting / hypothesis / shadow

  -> WebProjection
      dashboard fields: scores, series, outcome status, evidence refs

  -> MCP Export
      evidence fields only: copy_text / image_refs / source_url
      canonical_url / source metadata / evidence status
```

---

## OutcomeEvaluation Contract

`OutcomeEvaluation` 是 rulebook 学习的唯一入口。Rulebook 不允许直接读取 `delta > 0`。

`OutcomeRecord` 最小字段：

```json
{
  "object_type": "OutcomeRecord",
  "outcome_id": "outcome_...",
  "candidate_id": "candidate_...",
  "source_evidence_ref": "artifacts/.../evidence.json",
  "window": "4h",
  "window_role": "primary_outcome",
  "scheduled_for": "2026-06-16T04:00:00Z",
  "collected_at": "2026-06-16T04:00:00Z",
  "observation_status": "observed",
  "metrics_source": "same_connector_same_url",
  "source_availability": "available",
  "baseline_snapshot": {"likes": 1, "comments": 0, "shares": 0, "views": null},
  "current_snapshot": {"likes": 2, "comments": 0, "shares": 0, "views": null},
  "raw_delta": {"likes": 1, "comments": 0, "shares": 0, "views": null},
  "connector_quality_ref": "artifacts/.../connector_quality.json",
  "failure_state": null
}
```

最小 JSON artifact：

```json
{
  "object_type": "OutcomeEvaluation",
  "evaluation_id": "outcome_eval_...",
  "evaluation_version": "outcome_evaluator.v1",
  "prediction_id": "prediction_...",
  "outcome_id": "outcome_...",
  "candidate_id": "candidate_...",
  "source_evidence_ref": "artifacts/.../evidence.json",
  "window": "4h",
  "window_role": "primary_outcome",
  "evaluated_at": "2026-06-16T00:00:00Z",
  "collected_at": "2026-06-16T04:00:00Z",
  "platform": "xueqiu",
  "metric_map_version": "platform_metrics.v1",
  "threshold_version": "outcome_thresholds.v1",
  "raw_delta": {
    "likes": 1,
    "comments": 0,
    "shares": 0,
    "views": null
  },
  "relative_growth": 1.0,
  "platform_normalized_growth": 0.0,
  "author_normalized_growth": null,
  "baseline_status": "author_baseline_missing",
  "confidence_band": "low",
  "outcome_labels": ["insufficient_denominator", "weak_signal"],
  "success_grade": "not_meaningful",
  "hit": false,
  "calibration_error": 0.18,
  "quality_gates": {
    "evidence_complete": true,
    "metrics_available": true,
    "source_available": true,
    "not_duplicate": true,
    "risk_allowed": true,
    "no_holdout_leakage": true
  },
  "learning_eligibility": "not_learnable",
  "failure_state": null,
  "input_refs": {
    "prediction_ref": "artifacts/.../prediction.json",
    "outcome_ref": "artifacts/.../outcome.json",
    "strategy_version": "strategy.v1",
    "rule_version": "rulebook.v1"
  }
}
```

失败状态必须显式记录：

- `missing_outcome`
- `metrics_unavailable`
- `auth_failed`
- `source_deleted`
- `window_missed`
- `metric_invalid`
- `leakage_detected`
- `risk_blocked`
- `connector_unsupported`

---

## Evaluator 公式

V1 不用 z-score，不做复杂统计。用平台 metric map + JSON baseline artifact 计算可回放分数。

基础公式：

```text
weighted_baseline = sum(baseline_metric[metric] * weight[platform][metric])
weighted_delta = sum(max(0, current_metric - baseline_metric) * weight[platform][metric])

relative_growth = weighted_delta / max(weighted_baseline, platform_min_denominator[platform][window])
platform_normalized_growth = clamp(
  weighted_delta / max(platform_p75_delta[platform][window], platform_min_delta[platform][window]),
  0,
  3
)
author_normalized_growth = null unless author_baseline_status == baseline_ready
author_normalized_growth = clamp(
  weighted_delta / max(author_p75_delta[author][window], platform_min_delta[platform][window]),
  0,
  3
)
```

`platform_normalized_growth = 1.0` 表示达到同平台同窗口 p75 或最小有效增量，两者取更高者。`>= 2.0` 视为强信号候选。baseline 缺失时不要猜；输出 `baseline_missing`，并禁止 rulebook 晋级。

V1 metric weights：

| Platform | likes/score | comments/replies | reposts/quotes/shares | views/reads | hotlist/rank |
|----------|-------------|------------------|------------------------|-------------|--------------|
| X | 1 | 4 | 6 | 0.05 | 0 |
| Reddit | 1 | 5 | 0 | 0 | 3 |
| Xueqiu | 1 | 4 | 6 | 0.03 | 5 |

V1 最小分母和最小有效增量：

| Platform | Window | `platform_min_denominator` | `platform_min_delta` |
|----------|--------|----------------------------|----------------------|
| X | 1h | 20 | 8 |
| X | 4h | 30 | 15 |
| Reddit | 1h | 15 | 6 |
| Reddit | 4h | 25 | 12 |
| Xueqiu | 1h | 20 | 8 |
| Xueqiu | 4h | 30 | 15 |

这些是 `outcome_evaluator.v1` 的启动常量，必须带 `metric_map_version` 和 `threshold_version` 写入 artifact。后续只能通过版本化 eval 更改，不能静默调参。

---

## Outcome 标签

标签分三层，不能混用。

### 事实观测标签

来自 connector 或 fixture 明确声明：

- `source_available`
- `source_deleted`
- `metrics_available`
- `metrics_unavailable`
- `raw_growth_observed`
- `hotlist_observed`
- `cross_source_observed`

### 派生 outcome 标签

由确定性 evaluator 计算：

- `insufficient_denominator`
- `weak_signal`
- `baseline_normal`
- `platform_local_lift`
- `author_baseline_outperform`
- `early_momentum_1h`
- `primary_spread_4h`
- `cross_source_spread`
- `sustained_spread`
- `false_positive`
- `late_bloom`

### 学习标签

供 rulebook 使用：

- `not_learnable`
- `learn_neutral`
- `learn_negative`
- `learn_positive_shadow`
- `learn_positive_verified`

V1 只能产生 `not_learnable`、`learn_neutral`、`learn_negative`、`learn_positive_shadow`。`learn_positive_verified` 留给后续 promotion gate。

---

## Hit 和等级定义

`success_grade` 枚举：

- `not_measurable`：缺 outcome、缺 metrics、source 不可访问或窗口错过。
- `not_meaningful`：有事实增长，但低基数、低质量或低于平台最小有效增量。
- `weak`：超过最小有效增量，但低于平台 p75，或缺作者基线。
- `meaningful`：达到平台 p75，且通过 evidence/risk/duplicate gate。
- `breakout`：达到平台 p90 或同时出现强互动质量、热榜、跨源扩散之一。

`hit` 定义：

```text
predicted_positive = prediction_score[window] >= prediction_threshold[window]
actual_positive = success_grade in ["meaningful", "breakout"]
hit = predicted_positive == actual_positive
```

V1 默认阈值：

| Window | Role | `prediction_threshold` |
|--------|------|------------------------|
| 1h | early_momentum | 0.60 |
| 4h | primary_outcome | 0.65 |

`false_positive`：`predicted_positive=true` 且 `actual_positive=false`。
`false_negative` 不作为 outcome label 写入主标签；它进入 evaluation error report，避免标签爆炸。

`failure_state -> learning_eligibility` 映射：

| failure_state | learning_eligibility |
|---------------|----------------------|
| `missing_outcome` | `not_learnable` |
| `metrics_unavailable` | `not_learnable` |
| `auth_failed` | `not_learnable` |
| `source_deleted` | `not_learnable` |
| `window_missed` | `not_learnable` |
| `metric_invalid` | `not_learnable` |
| `leakage_detected` | `not_learnable` |
| `risk_blocked` | `not_learnable` |
| `connector_unsupported` | `not_learnable` |
| `null` + `success_grade=not_meaningful` | `not_learnable` |
| `null` + `success_grade=weak` | `learn_neutral` |
| `null` + `success_grade=meaningful` | `learn_positive_shadow` |
| `null` + `success_grade=breakout` | `learn_positive_shadow` |

High-confidence prediction misses with clean evidence become `learn_negative`; noisy or failed measurements never become negative learning.

---

## 成功判定

传播成功不能是 `delta > 0`。V1 至少要同时考虑：

- 绝对增量：新增互动是否超过平台最小噪声下限。
- 相对增幅：增长比例是否超过冷启动噪声。
- 平台基线：是否超过同平台、同窗口、同 metric 的历史分布。
- 作者基线：是否超过该作者或 source_channel 的历史常态。
- 时间窗口：1h 是 early momentum，4h 是 V1 主裁决窗口。
- 互动质量：评论、转发、引用、收藏通常比单纯点赞更强。
- 跨源扩散：是否被其他平台、账号、独立来源引用或讨论。
- 证据质量：source、metrics、image refs、connector identity 是否完整。

低基数保护：

- baseline 小于平台最小分母时，只能输出 `insufficient_denominator` 或 `weak_signal`。
- `1 -> 2`、`0 -> 1` 这类结果可以记录事实，但默认 `learning_eligibility = not_learnable`。
- 平台或作者基线缺失时，可以预测和观察，但不能晋级 rulebook。
- 24h 只做 top-K、高置信误判和随机小样本 audit，不进入默认 rolling queue。

---

## 平台和作者基线

每个平台必须有 `metric_map_version`，不能把不同平台指标粗暴相加。

示例：

| Platform | Core metrics | Stronger signals |
|----------|--------------|------------------|
| X | likes, replies, reposts, quotes, views | reposts, quotes |
| Reddit | score, comments, upvote_ratio | comments, rank movement |
| Xueqiu | likes, comments, reposts, reads, hotlist | comments, reposts, hotlist |

作者/source_channel 基线状态：

- `baseline_ready`：有足够历史样本，可用于 lift。
- `baseline_cold_start`：样本不足，只能观察。
- `baseline_missing`：不可用于学习。

V1 的懒实现：用 JSON artifact 保存平台/作者历史中位数、p75、p90 和样本数。先不用数据库。

Baseline 必须是版本化快照，不是可变真相文件：

```json
{
  "object_type": "BaselineSnapshot",
  "baseline_version": "baseline.v1.20260617T000000Z",
  "created_at": "2026-06-17T00:00:00Z",
  "input_refs": ["artifacts/.../outcome.json"],
  "metric_map_version": "platform_metrics.v1",
  "threshold_version": "outcome_thresholds.v1",
  "platform_windows": {
    "xueqiu:4h": {
      "sample_count": 42,
      "p50_delta": 8,
      "p75_delta": 15,
      "p90_delta": 31,
      "missing_metric_rate": 0.02
    }
  },
  "validation": {
    "status": "ok",
    "checks": ["sample_count", "quantile_order", "missing_metric_rate", "future_leakage"]
  },
  "content_hash": "sha256:..."
}
```

Baseline validation gate:

- Platform/window baseline needs `sample_count >= 30` before it can mark `baseline_ready`.
- Author/source_channel baseline needs `sample_count >= 10` before it can mark `baseline_ready`.
- Quantiles must be ordered: `p50 <= p75 <= p90`.
- `missing_metric_rate` must be `<= 0.20`.
- Baseline inputs must be older than the prediction being evaluated; future leakage fails closed.
- Evaluator must record `baseline_version` and `content_hash` in every `OutcomeEvaluation`.
- If baseline validation fails, output `baseline_invalid` and `learning_eligibility = not_learnable`.

No evaluator may silently use a modified baseline file. Update by writing a new snapshot and moving a `latest` pointer only after validation passes.

---

## Evaluator Meta-Evaluation

`OutcomeEvaluation` also needs its own audit. Otherwise evaluator bias becomes invisible.

Every rolling day writes `EvaluatorMetaReport`:

- label distribution by platform/window.
- rate of `not_measurable`, `not_meaningful`, `weak`, `meaningful`, `breakout`.
- false positive / false negative rate on 24h audit sample.
- platform skew: one platform dominating `meaningful` or `not_learnable`.
- baseline drift: p75/p90 moving more than 2x from previous valid snapshot.
- learning starvation: 7 rolling days with zero `learn_positive_shadow`.

Meta-eval actions:

- `ok`: continue.
- `watch`: keep predicting, but do not promote rules.
- `freeze_learning`: predictions and MCP may continue; rulebook stops consuming new samples for affected platform/window.

Learning starvation is a watch condition, not an automatic window change. If 4h produces no learnable samples for 7 rolling days, freeze promotion and review thresholds/window choice in a new versioned evaluator.

24h audit remains sampled only: top-K, high-confidence misses, and random small sample. It does not enter the default rolling queue for every item.

---

## API Boundaries

The architecture must exist in code, not only in the diagram.

- Loop driver calls public phase functions only: fetch, predict, revisit, evaluate, export.
- No module may import another module's private helpers for cross-layer work.
- WebProjection and McpExportItem are separate functions or modules.
- Rulebook reads `OutcomeEvaluation` or `RuleLearningInput`, never raw `OutcomeRecord`.
- Score path may write `calibration_report`; it must not apply shadow adjustments to production score.
- Healthcheck reads artifacts and quality reports; it must not mutate source, score, or rulebook state.

---

## 确定性规则 vs 模型辅助

确定性规则负责：

- 计算 raw delta。
- 计算 relative growth。
- 应用低基数保护。
- 应用平台/作者基线。
- 生成 outcome labels。
- 判断 hit / miss / calibration error。
- 判断 evidence、risk、duplicate、holdout、regression gate。
- 决定 `learning_eligibility`。
- 晋级或拒绝 rulebook proposal。

模型辅助负责：

- 抽取 hook、format、emotion、visual、topic framing。
- 给 prediction rationale。
- 总结传播机制。
- 解释 false positive / false negative。
- 提出 rule hypothesis 草案。

模型不得：

- 伪造 URL、发布时间、图片引用、互动指标。
- 把 rationale 当 outcome。
- 判定自己的预测是否命中。
- 直接设置 rulebook `verified` 或 `active`。
- 绕过 `OutcomeEvaluation` 写入学习结果。

Model 提出的 rule hypothesis 必须带 `source_evaluation_ids`。没有通过 `OutcomeEvaluation` 的样本只能进入 scratch/proposal log，不能进入 rulebook。进入 rulebook 的最小状态也是 `collecting`，并且必须由确定性 gate 写入。

---

## Connector Quality Gate

Connector 成功不等于数据可学习。每次 source run 必须产出 `ConnectorQualityReport`。

最小字段：

```json
{
  "object_type": "ConnectorQualityReport",
  "connector_id": "direct_cli.xueqiu_hot.v1",
  "run_id": "source_run_...",
  "quality_status": "ok",
  "item_count": 10,
  "item_count_vs_rolling_median": 0.91,
  "required_field_presence": {
    "source_url": 1.0,
    "copy_text": 1.0,
    "published_at": 0.8,
    "engagement_snapshot": 1.0
  },
  "metric_completeness": 0.95,
  "duplicate_rate": 0.0,
  "truncation_suspected": false,
  "structured_error_count": 0
}
```

Quality gate:

- `quality_status=ok` 才允许 outcome 进入 learning。
- `degraded` 可以进入 Web/MCP read model，但 `learning_eligibility = not_learnable`。
- `blocked` 不得产生成功 outcome。
- `item_count_vs_rolling_median < 0.5`、required field presence 低于 0.9、metric completeness 低于 0.8、truncation suspected，都至少是 `degraded`。

这个 gate 用来区分 data drift 和 model drift。

---

## Rulebook 学习门槛

Rulebook 记录的是“可验证结构假设”，不是“爆款真理”。

Rulebook 只消费满足以下条件的 `OutcomeEvaluation`：

- evidence 完整，或缺失字段已显式标记 unavailable。
- metrics available。
- connector quality status is `ok`。
- source 没有 deleted / auth_failed / window_missed。
- 非 duplicate。
- 非 high-risk / financial amplification blocked。
- 没有 holdout 泄漏。
- 同时具备 structure tags、prediction、outcome、evaluation。
- learning label 不是 `not_learnable`。

V1 晋级边界：

- `collecting`：记录样本，不影响评分。
- `hypothesis`：形成规则假设，不影响评分。
- `shadow`：旁路评估，不影响 production score。
- `verified`：V1 不自动产生。
- `active`：V1 禁止。

`n=3` 只能作为“可观察假设”的下限，不能作为“开始校准生产评分”的门槛。

Shadow calibration 语义：

- `shadow` 是 compute-and-log，不是 do-not-compute。
- Shadow rulebook 可以计算 hypothetical adjusted score，并写入 `calibration_report`。
- Shadow result 不得改变 production score、排序、MCP 输出或下游任务选择。
- Promotion gate 未通过前，`score.py` 只能读取 shadow report 做观测，不能把它作为真实校准偏置。

---

## MCP 输出契约

Web 和 MCP 不能共用同一个 projection。

- `WebProjection` 服务 dashboard，可以包含 score、series、revisit/eval 状态、内部诊断摘要。
- `McpExportItem` 服务下游自动化，只能输出 evidence/read fields。

MCP 是 read model，不是 source truth，也不是学习接口。

MCP 可输出：

- `id`
- `source`
- `source_label`
- `author`
- `published_at`
- `title`
- `copy_text`
- `source_url`
- `canonical_url`
- `image_refs`
- `image_status`
- `evidence_status`
- `public_url_available`

MCP 禁止输出：

- `radar_score`
- `hotness_score`
- `confidence`
- `rule_ids`
- `structure_tags`
- `outcome_labels`
- `learning_eligibility`
- `eval_status`
- `promotion_status`
- internal artifact refs
- secret / cookie / session refs

必须增加白名单测试：MCP 响应中出现 forbidden key 即失败。

Web 面板不能直接调用 `McpExportItem`，否则会丢排序、sparkline、score 显示。MCP 也不能调用 `WebProjection`，否则会泄漏内部字段。

---

## 权限和安全

- 密钥必须从 repo 外部文件读取。
- 密钥、cookie、session 不得进入 evidence、prediction、outcome、eval、MCP 输出。
- Source fetch 必须只读；源站 HTTP 请求只允许 GET。DeepSeek API 调用不属于 source fetch，可使用 provider 要求的方法。
- 外部内容一律视为不可信输入，不执行其中指令。
- Tool/connector 失败必须是结构化失败，不得变成空成功。
- All artifact writes must be atomic: write temp file, fsync when practical, then rename.
- DeepSeek fallback predictions must carry `provider_status.fallback_used` and default `learning_eligibility = not_learnable` until a clean provider-backed prediction exists.

---

## Runtime Guardrails

V1 不引入队列或数据库，但必须有最小运行护栏：

- Scheduler must run `run-cycle` and `healthcheck`; healthcheck is not manual-only.
- Healthcheck must fail on stale feed, missing artifacts, connector quality degraded/blocked, broken JSON, raw secret leakage, and due outcomes missing.
- A liveness artifact must record `last_cycle_started_at`, `last_cycle_completed_at`, `last_success_at`, `last_error`, and `disk_free_bytes`.
- Web must surface stale/degraded status; it must not make old `timeline_feed.json` look live.
- Retry/backoff must be structured: rate limit, auth failure, network failure, model timeout each set `retry_after` or `backoff_seconds`.
- Backoff fields must not be hard-coded to zero.
- Rolling store retention: keep active candidates plus closed candidates for 120 hours by default; compact closed records into summary artifacts.
- 24h audit samples are capped per day; they cannot grow unbounded.

---

## 验证资产保留

Outcome-first harness 依赖验证骨架。V1 不先删除：

- `tests/`
- `fixtures/`
- schema / validator
- replay artifacts
- gates / preflight
- sample outcome / sample eval

可以瘦身，但不能移除能证明 evidence、prediction、outcome、evaluation、MCP 白名单、holdout 防泄漏的资产。

Schema evolution is part of V1:

- Update `schemas/news_harness_mvp.schema.json` or add versioned schema coverage for `OutcomeRecord`, `OutcomeEvaluation`, `BaselineSnapshot`, `ConnectorQualityReport`, `EvaluatorMetaReport`, `WebProjection`, and `McpExportItem`.
- Validator must reject new artifacts that omit required version fields, quality gates, failure states, or forbidden MCP keys.
- Replay fixtures must include at least one clean positive, one false positive, one `1 -> 2` weak signal, one connector degraded sample, and one fallback prediction.

---

## 执行顺序（7 workstreams）

1. **契约 + schema** — 定义并验证 `OutcomeRecord`、`OutcomeEvaluation`、`BaselineSnapshot`、`ConnectorQualityReport`、`EvaluatorMetaReport`、`WebProjection`、`McpExportItem`。
2. **负例 fixtures** — 增加 `1 -> 2` weak signal、connector degraded、fallback prediction、false positive、clean positive。
3. **Baseline + evaluator** — 实现 baseline snapshot validation、确定性 outcome evaluator、failure/learning 映射。
4. **Connector + runtime gates** — 增加 connector quality report、automatic healthcheck、retry/backoff、atomic write、retention/compaction。
5. **Projection split** — 分离 WebProjection 和 McpExportItem，MCP 加 forbidden-key 白名单测试。
6. **Rulebook shadow** — rulebook 只读取 `OutcomeEvaluation`，shadow compute-and-log，不影响生产评分。
7. **Closed-loop verification** — 跑通 fixture evidence -> prediction -> outcome -> evaluation -> baseline/meta-eval -> shadow report；真实源只验证采集和结构化失败。

并行规则：Workstream 5 只依赖 Workstream 1，可与 2/3 并行。Workstream 6 依赖 3。Workstream 7 依赖 1-6 的最小闭环。

---

## 不变约束

- Python 3.12+，标准库为主。
- JSON artifact 优先，不引入数据库、队列、平台化编排。
- DeepSeek 输出是模型推断，不是事实。
- 预测是概率和不确定性，不是确定结论。
- 不提供投资建议。
- 原始图片证据保留 URL / reference；V1 不下载、不缓存、不替换源图片。
- 自改进必须可审计、可回放、可回滚。

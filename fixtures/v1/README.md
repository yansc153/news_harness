# V1 OutcomeEvaluation Fixtures

Sample fixtures for the OutcomeEvaluation schema (`schemas/v1/outcome_evaluation.schema.json`), covering the minimum set of negative and positive outcome verdicts required by the V1 replay verification spec.

- **`outcome_eval_weak_signal.json`** — Proves that `delta > 0` (e.g. 1→2 likes) is not a "win": insufficient_denominator + weak_signal tags force not_meaningful and block learning.
- **`outcome_eval_connector_degraded.json`** — Connector quality degradation (low item count vs median, missing required fields) caps success_grade at not_meaningful and forces not_learnable regardless of raw numbers.
- **`outcome_eval_fallback_prediction.json`** — A DeepSeek fallback prediction scored high but the actual outcome was trivial; the evaluator tags it false_positive and blocks learning because fallback predictions are never learnable.
- **`outcome_eval_false_positive.json`** — A clean (non-fallback) false positive: the model was confident (0.78) but the real outcome stayed below the minimum effective delta, yielding hit=false and not_learnable.
- **`outcome_eval_clean_positive.json`** — The canonical positive case: strong engagement growth exceeding platform p75, baseline_ready, meaningful success_grade, hit=true, and learn_positive_shadow eligibility.

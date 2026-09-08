# chat-bi-agent

![CI](https://github.com/Zsyyxrs/chat-bi-agent/actions/workflows/ci.yml/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![LLM: Qwen3.7](https://img.shields.io/badge/LLM-Qwen3.7--max-7c3aed.svg)](https://dashscope.aliyun.com/)

[中文](./README.md) | **English**

> **A conversational BI agent for banking scenarios** — compresses the traditional "file a request → wait in the queue → build a report → read the report → dig for numbers → attribute by hand" pipeline down to **"ask in one sentence → get numbers directly → get automatic attribution → follow up freely"**.

---

## ✨ Three Capability Tracks

| Track | Capability | Sample Question |
|---|---|---|
| **P1 Precise Retrieval** | Natural language → SQL → data → auto-chart | "What was the deposit balance of HNW customers in the Shanghai branch in May?" |
| **P2 Multi-Step Analysis** | Decompose → multi-step retrieval → fact extraction → synthesized insight | "How did cash withdrawal behavior change around Chinese New Year?" |
| **P3 RCA Attribution** | Anchor fact → drill by dimension → match events → synthesize root cause | "Shanghai branch deposits dropped 8% on 2026-05-14 — why?" |

**Entry points**: the Streamlit UI (three tabs), plus an **MCP server** that exposes P1 to
Claude Desktop and other MCP clients. The caller's identity is pinned by server-side
configuration — the tool schema carries no identity field, so the model cannot declare who
it is. With no identity configured the server fails closed, and a refusal never falls back
to a path without row-level control. See [docs/RUNBOOK.md §4bis](docs/RUNBOOK.md).

![P1 demo: natural language → SQL → result table → auto-chart](docs/assets/demo_p1.gif)

<sub>P1 tab, recorded live and **sped up**: ask "monthly transaction-amount trend for 2026" → generate and run SQL → result table → chart type inferred automatically. The 👍 at the end sends this (question, sql) pair to the few-shot pool candidates. The "耗时 18043 ms" at the bottom is the **real** end-to-end latency of this run, not compressed; across all 8 questions avg is 17.7s / p50 12.0s (<a href="results/baseline_p1_eval_2026-08-15.json">baseline JSON</a>). No recording for P2 / P3 yet.</sub>

---

## 📊 Evaluation Results

### In-house three-track evaluation

| Track | Questions | Passed | Avg Score | Baseline | Notes |
|---|---:|---:|---:|---|---|
| **P1 NL2SQL** | 8 / 8 | 8 | **0.977** | 2026-08-15 (restated 2026-09-08) | Full question set; multi-table JOIN, time windows, aggregation, branch filters — all pass. Originally reported 0.965, understated by a scorer bug — see below |
| **P2 Multi-Step Analysis** | 3 / 8 | 1 | **0.626** | 2026-08-17 | 3 scored dims (insight 45% + rubric LLM judge 35% + metric coverage 20%); reasoning/business/step-completeness demoted to diagnostics |
| **P3 RCA Attribution** | 7 / 7 | 7 | **0.900** · event_hit **7/7** | 2026-06-29 | 4-dim rubric, all events matched, zero hallucination |

The denominator in "Questions" is the total in each evaluation set. **P2 has only ever scored the
first 3 of its 8 questions**; q004–q008 have never been scored. At 300–500s per question,
completing them takes 40–70 minutes — deferred on cost.

**P2 is the only score in this project that has been repeatedly revised downward**, because much of
the earlier figure was unearned. Four steps (2026-08-15/17), each one a case of "this dimension
claims to measure A but actually computes B":

| Step | Change | Avg | Passed |
|---|---|---:|---:|
| Start | — | 0.798 | 3/3 |
| ① Fix two silently broken dims | The insight dim split Chinese on whitespace (i.e. did not tokenize at all); metric coverage matched **individual characters** — '长' and '户' appear in almost any banking narrative | 0.798 | 3/3 |
| ② Delete the two dims with no ground truth | Reasoning quality and business relevance have **nothing to compare against**; their only possible criterion was counting keywords, and they sat at exactly 1.000 — 35% of the weight was a constant, not a measurement | 0.655 | 2/3 |
| ③ Add a rubric LLM judge | 4-dim G-Eval modelled on P3, each dim anchored to **this question's** YAML fields | 0.601 | 0/3 |
| ④ Demote step completeness to diagnostic | It computed `len(plan nodes)/len(YAML steps)` — **plan granularity**, not whether the steps were performed | **0.626** | **1/3** |

Deleting in ② and adding in ③ is not self-contradictory: the deleted dims were anchored to a generic
word list (identical for every question, hence guaranteed to saturate), whereas the judge's four dims
are anchored to per-question `analysis_steps` / `expected_insights` / `evaluation_criteria`, written
by hand before the agent ever ran. See [ADR-015](./DESIGN_DECISIONS.md#adr-015) and
[ADR-016](./DESIGN_DECISIONS.md#adr-016).

**④ is a benefit the judge paid out immediately.** On q001 the agent planned only 2 steps (the YAML
lists 5) → step completeness 0.40, while the judge, anchored to **the same** `analysis_steps`, scored
content fidelity at 1.00. Manual review of the full answer confirms all five steps' substance is
present — the judge was right, and the old dimension was penalising "did all five steps in two".
**Two dimensions sharing an anchor but reaching opposite conclusions act as a control on each other**,
which is impossible when only one measurement exists.

**The substantive defect the judge exposed is `quantification`: 0.50 / 0.00 / 0.00.** But the accurate
statement is not "the agent fails to report figures" — it reports plenty (q001 contains 4 percentages
and 13 large numbers). The real gap is that it **does not compute the derived rates the gold expects**:
on q003 it retrieved both numerator and denominator (9141 / 5265 / 720) yet never divided them into a
redemption or continuation rate. q001 proves it *can* (319% / −70.7%); on q003 it simply did not.
Nothing could see this before (`insight_accuracy` measures content-word recall, so saying "growth"
counts as a hit whether the reported figure is +25% or +3%).

**Do not read 1/3 as a precise scale**: q001 scored 0.700 and 0.679 across two agent runs under the
old weights, straddling the pass line. The table's figure comes from
`baseline_p2_analysis_2026-08-17_rescored.json` — the same agent answers **rescored** under the new
scorer, with the two provenance chains (agent run / scorer) recorded separately in `run_metadata` and
`rescorer_metadata`, both `commit_dirty=false`. Re-running the agent after a scorer change would
conflate "the scorer changed" with "the agent ran differently", so rescoring is deliberate.

**Still unresolved — and all of it blocked on the same thing: `n=3`**:

- `multi_metric_coverage` (20% weight) sits at 1.000 across these three questions — **zero
  discrimination**; the candidate terms are too generic. It scored 0.500 on q001 previously, so it is
  not a pure constant, and three questions cannot settle whether to delete or repair it. It is now the
  only scored dimension that has never been scrutinised.
- `causal_reasoning` / `business_actionability` have a **floor at 0.50** (never observed lower),
  compressing their effective range to [0.5, 1.0]. This is **not saturation** — they do vary across
  questions, unlike the deleted dims which sat at exactly 1.000 — but it is impossible to tell
  whether the criteria are lenient or these three answers are genuinely mid-quality.
- **q004–q008 have never been scored** (300–500s per question; 40–70 minutes to complete). That is
  the prerequisite for both points above: only at n=8 can a dimension be shown to discriminate.
  Deferred on cost, so those two items **lack evidence, not effort**.

**Restated 2026-09-08: 0.965 → 0.977.** The scorer's `table_score` was counting CTE names as
tables — its regex `(?:FROM|JOIN)\s+(\w+)` matches `FROM txn_agg`, a reference to a CTE. This is
not random noise but a **systematic bias**, and an asymmetric one: gold SQL here favours derived
subqueries (`FROM (` does not match), while generated SQL using `WITH` gets penalised. q008's table
selection should have been perfect and was scored 0.5, costing it a flat 0.1. After the fix,
[`scripts/replay_p1_scoring.py`](./scripts/replay_p1_scoring.py) re-scored the stored artifacts
offline (**zero LLM calls** — the recorded SQL is re-executed against the real database): the
8-question average goes 0.9646 → **0.9771**, with the other seven questions unchanged.

**This also corrects an earlier claim.** This paragraph used to say the 0.965–0.977 spread came
entirely from run-to-run noise on q008. After restatement the 2026-08-14 and 08-15 runs are **both
0.9771** — that regression never existed. What actually changed between runs was that the model
rewrote q008 using a CTE, and the scorer docked 0.1 for that **equivalent** form. The real
run-to-run variation was in SQL *shape*, not in quality — which is itself another angle on why
determinism is worth paying for: the model produced two equivalent queries and my scorer gave them
two different scores.

Run-to-run variation still exists (q008 is the hardest of the eight: two windows + conditional
aggregation + percent change + Top-N), and changes should still be judged per-question rather than
by fractions of a point on the average. See [ADR-013](./DESIGN_DECISIONS.md#adr-013).

<details>
<summary>Why P1 changed from "6 questions / 1.000" to the full 8-question set</summary>

The previously published **6 questions / 1.000** needed two corrections, both made on 2026-08-14:

1. **Only 6 of 8 questions were ever run.** `run_p1_eval` carried a hardcoded `HAPPY_PATH_IDS`
   allowlist that excluded q005 and q008. Running them revealed **the gold was defective, not the
   agent**: q005 summed a stock metric (balance) across 28 daily snapshots, yielding 27.4× the
   month-end truth; q008's prompt says "term deposits" but its gold SQL had no `account_type`
   filter. Both golds are fixed and the allowlist is deleted.
2. **Gold row counts had drifted from the seed data.** `expected_result_count` for q001/q003/q004
   still held values from the original seed; after a reseed they were stale, so character-identical
   correct SQL was still docked the 0.15 `result_count` weight — silently lowering scores rather
   than raising an error. Backfilled from measured values.

After the fixes the **6-question figure returns to 1.000** (and reproduces); the full 8-question
average is 0.977 (restated 2026-09-08; originally reported 0.965) with 8/8 passing. The gap comes
from q005, where the agent genuinely dropped the "term deposits" constraint.

Editing gold risks fitting it to the agent, so a boundary was drawn: fix only golds that violate
business semantics, or that fail to implement a constraint their own prompt states. Differences of
interpretation are left alone. A guard —
[`tests/eval/test_gold_sql_row_counts.py`](tests/eval/test_gold_sql_row_counts.py), 42 cases run
against live Postgres — now makes row-count drift a red test instead of a silent deduction.
Full reasoning in [DESIGN_DECISIONS.md#adr-014](./DESIGN_DECISIONS.md).

</details>

Evaluation methodology in [EVALUATION_FRAMEWORK.md](./EVALUATION_FRAMEWORK.md); raw baseline JSONs under [`results/`](./results/) (latest P1: [`baseline_p1_eval_2026-08-15.json`](results/baseline_p1_eval_2026-08-15.json)); three-track markdown report at [`results/eval_report_2026-08-15.md`](./results/eval_report_2026-08-15.md).

P1 runs all 8 questions by default (`python -m chat_bi_agent.runners.run_p1_eval`). Before
2026-08-14 a `HAPPY_PATH_IDS` allowlist limited it to 6; it was removed along with the gold fixes.

One-click rerun:

```bash
python scripts/run_all_evals.py              # run all three tracks + generate markdown report
python scripts/run_all_evals.py --only p3    # P3 only
python scripts/eval_diff.py --phase p3       # diff latest two P3 baselines
```

### Public benchmarks

- **BIRD-financial dev subset** (n=106, model `qwen3.7-max-2026-05-20`):

  We ran **three variants** side by side — lean baseline measures the **LLM/prompt-substrate ceiling**; P1 pipeline shows what the live system does when **dropped onto a foreign schema unchanged**; P1 (dialect fix) adds **dialect parameterization** to SQLGenerator/Validator/Reflector — together they attribute the delta to specific mechanisms:

  | Difficulty | n | Lean baseline<br/>(BIRD-specific prompt) | P1 pipeline<br/>(pre-fix, dialect=postgres) | P1 pipeline<br/>(dialect=sqlite) | Δ dialect vs pre |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | simple | 62 | 64.52% (40/62) | 50.00% (31/62) | 59.68% (37/62) | **+9.68** |
  | moderate | 37 | 48.65% (18/37) | 37.84% (14/37) | 37.84% (14/37) | 0 |
  | challenging | 7 | 28.57% (2/7) | 28.57% (2/7) | 14.29% (1/7) | −14.28 (n=7 noise) |
  | **overall** | **106** | **56.60%** (60/106) | **44.34%** (47/106) | **49.06%** (52/106) | **+4.72** |

  Error & efficiency:

  | | syntax errors | avg attempts | avg latency |
  | --- | ---: | ---: | ---: |
  | Lean baseline | 0 | 1.00 | 28.6s |
  | P1 pre-fix (postgres) | 27 | 1.58 | 45.4s |
  | P1 dialect fix (sqlite) | **0** | **1.04** | **30.1s** |

  **How to read the three numbers**:
  - **Lean 56.60%** — LLM + prompt substrate ceiling (English SQLite-aware prompt built for BIRD).
  - **P1 pre-fix 44.34%** — production P1 stack unchanged. PostgreSQL dialect assumptions baked into the SQLGenerator prompt (`EXTRACT(YEAR FROM ...)`, `ILIKE`, `DATE 'YYYY-MM-DD'`) collapse on SQLite. 27 syntax errors, Reflector burns 3 attempts on each without recovery.
  - **P1 dialect fix 49.06%** — added a `dialect` parameter across SQLGenerator (SYSTEM_PROMPT variant that mandates STRFTIME / plain-string date / LOWER LIKE instead of ILIKE), SQLValidator (sqlglot dialect switch), and Reflector (upgrades SYNTAX_ERROR → DIALECT_MISMATCH on prev_sql inspection and injects a targeted rewrite hint). Result: **27 syntax errors → 0, avg attempts 1.58 → 1.04, avg latency 45.4s → 30.1s, EX +4.72 points**.
  - **The Reflector DIALECT_MISMATCH branch fired 0 times in the actual run** — all 4 retry events were plain SYNTAX_ERROR. The SYSTEM_PROMPT rules alone got the LLM to emit correct-dialect SQL on the first shot; the Reflector safety net is defence in depth and stayed dormant here.
  - **Gap closed from 12.26 → 7.54 points (38% recovered)**. The remaining gap is dominated by semantic errors: even with correct dialect, some date-arithmetic and multi-join questions are inherently hard.

  We chose the `financial` subset (real Czech bank data, 8 tables) because it matches this project's domain and difficulty. Runners: [`scripts/run_bird_financial.py`](scripts/run_bird_financial.py) (lean) and [`scripts/run_bird_financial_p1.py`](scripts/run_bird_financial_p1.py) (P1; `--dialect {postgres,sqlite}` toggles the variant). Results: [`results/bird_financial_2026-07-01.json`](results/bird_financial_2026-07-01.json) / [`results/bird_financial_p1_2026-07-01.json`](results/bird_financial_p1_2026-07-01.json) / [`results/bird_financial_p1_dialect_2026-07-02.json`](results/bird_financial_p1_dialect_2026-07-02.json). EX semantics follow BIRD's official `evaluation.py` (row-set equivalence plus the 42-entry `dev_tied_append.json` patch). Dataset provenance: [`benchmarks/README.md`](benchmarks/README.md).

- **Q-SQL few-shot retrieval (2026-07-06, and same-domain follow-up 2026-08-14)** — a vector pool
  of 1,427 non-`financial` BIRD dev questions (financial strictly excluded to prevent leakage),
  with top-k similar Q-SQL pairs injected into the SQLGenerator prompt. **Honest conclusion: net
  effect ≈ 0.** On BIRD the apparent +3.77 EX did not survive per-question analysis — all 8 flipped
  questions had `retrieved_example_ids = []`, so few-shot never fired and the gain was day-to-day
  model noise. The same-domain A/B on 34 questions likewise showed no benefit once an approximate
  leakage guard was added: the surface gain came from near-duplicate questions in the pool.
  **Shipped but off by default.** Full analysis: [ADR-012](./DESIGN_DECISIONS.md#adr-012).

- **Same-domain feedback loop (2026-07-07)** — every answer in all three Streamlit tabs carries
  👍/👎, recorded via Langfuse `score(name="user_feedback")` on the current trace. A nightly cron
  ([`scripts/nightly_promote.sh`](scripts/nightly_promote.sh) or `make promote-pool`) appends
  thumbs-up P1 `(question, sql)` pairs to a production few-shot pool, deduplicated by
  `sha1(q||sql)[:12]` so reruns are idempotent. The production P1 agent hot-loads that pool at
  `min_sim=0.7` and falls back at zero cost when it is empty.

- **Semantic layer / Metric Resolver (2026-07-08 → 2026-08-14)** — the largest body of work since
  July. [`config/metrics.yaml`](config/metrics.yaml) now defines **18 banking metrics** across
  deposit/loan/AUM/holding/risk/campaign/transaction domains. `MetricRouter`
  ([`metric_resolver.py`](src/chat_bi_agent/agents/p1/metric_resolver.py)) runs an embedding cosine
  prefilter ahead of SchemaLinker; on a hit it extracts a `{metric_id, dims, filters, time_window}`
  spec via LLM, renders template SQL, and returns `route="metric"` while skipping the Reflect loop.
  Anything that fails to resolve falls back to the original NL2SQL path, with `metric_fail_reason`
  recording why. Four rounds of A/B on a 34-question routing yardstick settled the default
  threshold at **0.63**, yielding routing **precision 1.000 (zero false positives)**, recall 0.75,
  F1 0.857. Two scalability changes followed: a global join registry (26 inline join clauses
  collapsed to 4 templates) and top-k candidate pruning, which **decouples prompt size from catalog
  size** (18 metrics: 6,380 → 3,713 chars; at 300 metrics the pruned prompt stays 3,713 while the
  full one would reach ~83,000). Detail: [ADR-013](./DESIGN_DECISIONS.md#adr-013).

- **Evaluation-integrity work (2026-08-14/15)** — a sweep for the failure family "docs claim X, code
  does Y, and the mismatch is silent" found and fixed: stale gold row counts silently docking
  correct SQL, a hardcoded allowlist hiding two questions with defective gold, two scripts globbing
  a baseline filename the runner no longer writes, and a Reflect budget that contradicted its own
  ADR (3 attempts where ADR-006 specified 1 retry; the third attempt had succeeded 0 times in 27
  occurrences). Guards were added for each. Method and boundaries — in particular where "fixing
  defective gold" ends and "fitting gold to the agent" begins — are documented in
  [ADR-014](./DESIGN_DECISIONS.md#adr-014).

### Recent work (2026-08-20 → 09-02)

- **Metric catalog grew 6 → 21, and "calibration" became statically checkable.** The three silent
  wrong answers found along the way are all the same shape — SQL valid, query runs, number wrong:
  "current deposits" was computed as current + savings (**2×**); balance, a **stock** metric, was
  summed across daily snapshots (27× the true month-end figure); holdings had the same bug. Metrics
  now carry `semantic: stock | flow`, and point-in-time semantics support one-snapshot-per-period,
  taking the latest snapshot in the window. Three static gates back this
  (`scripts/check_metric_catalog.py`): column existence (previously only a real DB hit exposed a
  typo), **dim↔filter symmetry** (a column you can GROUP BY must also be filterable), and joins
  attached only where referenced. Also added `ORDER BY / LIMIT` (ranking questions no longer fall
  back to NL2SQL), `op='IN'`, value retrieval (nothing had told the model that "Shanghai" lives in
  a `province` column), and dual-path prefilter recall (whole-sentence embeddings get diluted by
  time modifiers). See the ADR-013 updates.

- **P3 drill-down candidate dimensions now come from the metric catalog** instead of a global
  hardcoded allowlist — the allowlist knows nothing about whether a given metric can be split by a
  given dimension, and the catalog already holds that answer.

- **Row-level access control (RLAC)**: `row_policies` are declared on a metric and injected **at
  render time** via `render_sql_from_spec(spec, catalog, session_props={"branch_id": "BR001"})`,
  which is what "a branch sees only itself, HQ sees everything" actually requires — the previous
  option was static modeling-time pruning, identical for everyone. Three deliberate departures from
  the reference implementation (WrenAI): **fail-closed** (a declared policy with a missing session
  property raises, never silently returns the full table), the policy condition sorts first in the
  `WHERE` clause so a human can spot it, and policies are checked by the **static catalog gate**
  rather than blowing up when a user with that attribute finally runs a query. The test that
  matters most is `test_rlac_never_reaches_the_llm_prompt` — the model never sees the policy
  condition, so it cannot be talked into bypassing it. The registry and value-domain gate for
  session attributes are [ADR-017](./DESIGN_DECISIONS.md#adr-017) (Proposed).

- **RLAC wired up and exercised for the first time (2026-09-04).** Until then this path had
  **zero real usage** — no metric declared `row_policies`, no caller passed `session_props`:
  fully implemented and tested, never run against a real query. Two campaign metrics now declare
  policies, and `session_props` flows from the Streamlit "current identity" selector all the way
  to render time. Asking "how many people did the Spring Festival savings campaign reach?" returns
  **31992** for HQ, **808** for the Hangzhou branch and **754** for Nanjing — all three verified
  against the database directly — and refuses outright with no identity. Campaign metrics went
  first because fail-closed is a global switch: putting a policy on `deposit_balance` would make
  every batch job that passes no identity fall back to NL2SQL silently, moving the published P1
  baseline with it. The very first run also cashed in the known gap recorded in ADR-017: after the
  semantic layer refused to render, the agent handed the question to NL2SQL and **answered 13618**
  — that path has no row-level access control, so refusing and then answering via an unguarded
  route is not refusing. `rlac_denied` now terminates the query (`route="metric_denied"`) while
  every other failure reason still falls back. `_classify_metric_error` had also been bucketing
  permission denials into the catch-all `unknown_dim`, hiding them from every dashboard; they now
  have their own bucket.
  **The value-existence probe now covers session values too.** It previously guarded only the
  filters the LLM extracts; the values bound by policy conditions had nothing. A typo'd `branch_id`
  produces legal SQL that runs and returns 0 — indistinguishable from "this branch genuinely
  reached nobody" (`BR_CITY_00OO` does exactly that). A miss now attaches a diagnostic but **does
  not block**: falling back to NL2SQL is no safe alternative for a policy condition (that path has
  no row-level access control), and refusing outright would punish a legitimate branch that simply
  has no rows.
  **Stated plainly:** in the demo the identity is picked by the user from a dropdown — choose "HQ"
  and you see everything, so **RLAC is bypassable in the demo**. What closes that is an
  authentication layer, not the semantic layer: `session_props` should be derived by auth from a
  verified identity token. That is also why [ADR-017](./DESIGN_DECISIONS.md#adr-017) stays
  Proposed — the registry and value-domain gate are triggered by "authentication/multi-tenancy
  lands", and building them before that is motion without traction.

- **SQL validation gained a function-level blacklist.** "SELECT only" was a check on the
  **top-level statement type** and said nothing about what the SELECT calls —
  `SELECT pg_read_file(...)` passed both it and the table/column check. Coverage was then extended
  from PG/DuckDB to MySQL/MSSQL/Oracle (`dialect` is a constructor argument, so those were
  unguarded the moment it pointed elsewhere), plus a qualifier-prefix rule for Oracle's
  `utl_http.request(...)`, which sqlglot parses as `Dot(Identifier, Anonymous)` and which
  name-based matching structurally cannot catch. See the ADR-005 update.

- **Observability: from "instrumented" to "instrumentation you can trust."** None of these four
  raised an error or moved a score — the traces were there and the numbers came out, just wrong:
  P3's parallel drills had always been disconnected traces (`ThreadPoolExecutor` does not propagate
  contextvars, so each drill became its own root trace); P2's step metadata was overwritten every
  round (no per-step span, so only the last step survived); adding breakdown keys to
  `usage_details` made the server double-count `reasoning`/`cached` against their parents, doubling
  token counts; and spans gained a `local_operation` dimension separating model time from local
  time (total duration alone could not say whether to optimize the prompt or the SQL). All 20 bare
  `@observe` decorators moved to `observe_local` / `observe_llm`, with a guard test that turns
  "new path forgot to wire the gate" into a CI failure. See the ADR-003 update.

---

## 🏗 Architecture

```
                       ┌─────────────────────────────┐
                       │  Streamlit Web UI (3 Tabs)  │
                       │  P1 Retrieval / P2 Analysis / P3 RCA │
                       └──────────────┬──────────────┘
                                      │
       ┌──────────────────────────────┼──────────────────────────────┐
       │                              │                              │
       ▼                              ▼                              ▼
┌─────────────┐              ┌─────────────────┐            ┌───────────────────┐
│ P1 NL2SQL   │              │ P2 Multi-Step   │            │ P3 RCA Agent      │
│ Agent       │              │ Analysis Agent  │            │ (5-step pipeline) │
│             │              │                 │            │                   │
│ SchemaLink  │◄──reuse──────┤  Planner        │            │ 1. fact_anchor    │
│ SQLGen      │              │  ↓              │            │    (via P1)       │
│ SQLValidate │              │  P1 Agent (×N)  │◄──reuse────┤ 2. drill_select   │
│ SQLExecute  │              │  ↓              │            │ 3. drill_run      │
│ Reflector   │              │  FactExtractor  │            │    (Pareto Top-K) │
│ (×1 retry)  │              │  ↓              │            │ 4. event_match    │
│             │              │  InsightSynth   │            │    (YAML window)  │
│             │              │  ↓              │            │ 5. synthesize     │
│             │              │  ReportWriter   │            │    (narrative)    │
└──────┬──────┘              └────────┬────────┘            └────────┬──────────┘
       │                              │                              │
       └──────────────────┬───────────┴──────────────────────────────┘
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
┌─────────────┐  ┌────────────────┐  ┌──────────────────┐
│ Qwen3.7     │  │ PostgreSQL 16  │  │ Langfuse v3      │
│ (DashScope) │  │ (read-only     │  │ (self-hosted)    │
│ + Embedding │  │  user enforced)│  │ full trace tree  │
└─────────────┘  └────────────────┘  └──────────────────┘
```

**Architecture highlights**:
- **Three independent agents**, one per track (no forced single super-agent)
- **P2/P3 reuse P1 as the atomic retrieval layer** (both FactAnchor and each planned step call P1)
- **Orchestration is a plain function chain + Langfuse `@observe` decorators** — **no LangGraph** (fixed flow doesn't need a graph)
- **Single LLM source** (Qwen for both generation and judge) — no separate judge model
- **P3 ground truth via YAML event library + propagation engine** (controllable, replayable, quantifiable)

Full design trade-offs in [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md).

---

## 🚀 Quick Start

### A. Docker Compose (recommended)

```bash
git clone https://github.com/Zsyyxrs/chat-bi-agent.git
cd chat-bi-agent

# 1. Configure
cp .env.example .env
cp config/local.example.yaml config/local.yaml   # copy it to reproduce the README scores
# Edit .env and fill in DASHSCOPE_API_KEY (required)

# 2. Bring up the full stack (Postgres + Langfuse stack + Streamlit app)
docker compose up -d

# 3. Seed data + plant events (one-off job)
docker compose --profile seed run --rm seed

# 4. On first launch, create a Langfuse API key
#    Visit http://localhost:3001 → sign in with admin@chatbi.local / admin12345
#    Settings → API Keys → create a pair → fill LANGFUSE_PUBLIC_KEY / SECRET_KEY in .env
#    Then: docker compose restart app

# 5. Open Streamlit
open http://localhost:8501
```

If you get stuck, see [docs/RUNBOOK.md](./docs/RUNBOOK.md) (Chinese) — health checks,
nine common failure modes with fixes, and what `down -v` destroys.

Service ports:
- Streamlit App: `http://localhost:8501`
- Langfuse UI: `http://localhost:3001`
- pgAdmin: `http://localhost:5050`
- Postgres: `localhost:5433` (5432 inside the container)

### B. Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Bring up Postgres + Langfuse only (skip the app container)
docker compose up -d postgres langfuse pgadmin

# Seed data (seed does not read PG_PORT; it defaults to 5432 while the
# container is mapped to 5433, so pass --port explicitly)
python -m chat_bi_agent.data.seed --port 5433 --truncate --with-events

# Run Streamlit locally
streamlit run streamlit_app/app.py
```

### Run evaluations

```bash
python scripts/run_all_evals.py                    # run all three tracks
python scripts/run_all_evals.py --only p1          # P1 only
python scripts/run_all_evals.py --skip p2,p3       # skip P2/P3
python scripts/run_all_evals.py --p3-limit 2       # P3: first 2 questions only (save tokens)
python scripts/run_all_evals.py --report-only      # regenerate report from latest baselines, no rerun

python scripts/eval_diff.py --phase p3             # diff the latest two P3 baselines
python scripts/eval_diff.py --phase p3 \
    --base results/baseline_p3_rca_2026-06-28.json \
    --head results/baseline_p3_rca_2026-06-29.json
```

---

## 🎬 Demo

The P1 recording sits under the "Three Capability Tracks" table at the top. **There is no recording for
P2 / P3** — a single P2 question takes 300–500s and P3 is in the same range, so a real-time capture is
unwatchable while a fast-forwarded one misrepresents the actual latency. Both tracks output long-form
attribution prose, which static screenshots serve better; those are still to come.

To see the full set yourself, follow Quick Start A and launch Streamlit. **Each tab opens with a
"What can I ask here?" panel** covering what the tab is and isn't for, how long a run takes, and
one-click example questions — all drawn from the evaluated question sets, so they are known to work.
These three are the representative ones:

- **P1 tab**: "What was the total deposit balance of HNW customers in the Shanghai branch in May 2026?"
- **P2 tab**: "How did cash withdrawal behavior change around Chinese New Year?"
- **P3 tab**: "Deposits from HNW customers at the Shanghai branch dropped 8% on 2026-05-14 — what caused it?"

Every question leaves a full trace in Langfuse (`http://localhost:3001`, live).

---

## 🧱 Project Structure

```
chat-bi-agent/
├── src/chat_bi_agent/
│   ├── agents/                # Three agents + shared components
│   │   ├── p1/                #   nl2sql_agent · sql_generator · sql_validator · reflector
│   │   ├── p2/                #   p2_analysis_agent · planner · fact_extractor · insight_synthesizer · report_writer
│   │   ├── p3/                #   p3_rca_agent · fact_anchor · drilldown_selector · drill_executor · event_matcher · synthesizer
│   │   └── shared/            #   schema_linker · sql_executor
│   ├── runners/               # P1/P2/P3 evaluation runners
│   ├── llm/                   # qwen_client.py + langfuse_setup.py
│   ├── obs/                   # span_kind.py (observe_local / observe_llm decorators)
│   ├── viz/                   # chart_inference (rule-based) + plotly_renderer
│   ├── eval/                  # precision / multi-step / rca evaluators
│   ├── data/
│   │   ├── seed.py            #   seed data generation CLI
│   │   └── events/            #   YAML event library (4 real-world scenarios)
│   ├── schema/                # table/column metadata loader
│   └── config.py              # YAML + defaults merge
│
├── streamlit_app/
│   ├── app.py                 # 3-tab entry point
│   ├── tabs/{p1_nl2sql,p2_analysis,p3_rca}.py
│   └── components/{chart,dataframe,sql,insight}_block.py
│
├── scripts/
│   ├── run_all_evals.py       # One-click: run P1+P2+P3 + generate markdown report
│   ├── eval_diff.py           # Baseline regression detector
│   ├── verify_events.py       # Verify event propagation
│   ├── rejudge_baseline.py    # Re-run LLM judge
│   ├── check_metric_catalog.py     # metrics.yaml static gate (columns/symmetry/joins/RLAC)
│   ├── sweep_prefilter_threshold.py # Re-sweep routing threshold (mandatory after an embedding model swap)
│   ├── verify_ab.py           # A/B guard (commit/model treated as CRITICAL fields)
│   └── calibrate_magnitudes.py
│
├── config/
│   ├── local.yaml             # Runtime config (model names, retrieval top_k, PG timeout, ...)
│   └── metrics.yaml           # Semantic-layer metric catalog (21 metrics)
├── tests/                     # 847 tests, organized by p1/p2/p3/shared/data/viz/eval/schema
├── results/                   # Evaluation baseline JSONs + markdown reports
├── docker-compose.yml         # Postgres + Langfuse stack + App + Seed
├── Dockerfile                 # Streamlit image
├── EVALUATION_FRAMEWORK.md    # Three-track evaluation methodology (Chinese)
├── DESIGN_DECISIONS.md        # Tech choices + evolution + ADRs (Chinese)
└── CONTRIBUTING.md
```

---

## 🛠 Tech Stack

| Category | Choice | Notes |
|---|---|---|
| LLM (generation + judge) | currently `qwen3.7-max` (DashScope; ADR-001 was written for Qwen3.6-max-preview) | Single source, Chinese banking domain → ADR-001 |
| Embeddings | text-embedding-v4 (DashScope, dim=1024) | For schema retrieval |
| Observability | Langfuse v3 (self-hosted) | Full trace tree + LLM judge score writeback; spans split `local_operation` from model time → ADR-003 |
| Agent orchestration | In-house function chain + `@observe` | Fixed flow, no LangGraph → ADR-002 |
| SQL parse/validate | sqlglot | AST rewriting + multi-dialect |
| Chinese tokenization | jieba | Preprocessing for schema retrieval |
| Database | PostgreSQL 16 | Isolated read-only user (chatbi_readonly) |
| Web UI | Streamlit | Demo-oriented, ~3× dev speed → ADR-009 |
| Visualization | Plotly | 6 chart types auto-inferred (rule-based) |
| Testing | pytest (847 tests) + ruff | CI on GitHub Actions |

Full rationale and alternatives in [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md).

---

## 📖 Documentation

- [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md) — Tech-choice comparison, architecture evolution, 17 ADRs (Chinese)
- [EVALUATION_FRAMEWORK.md](./EVALUATION_FRAMEWORK.md) — Three-track methodology, question sets, rubrics, ground truth (Chinese)
- [金融 data agent 架构设计](./docs/金融data%20agent架构设计.md) — Original business-domain design (Chinese)
- [docs/RUNBOOK.md](./docs/RUNBOOK.md) — Deployment runbook: prerequisites, health checks, troubleshooting, teardown (Chinese)
- [CONTRIBUTING.md](./CONTRIBUTING.md) — Dev environment and contribution flow

---

## 🧪 Tests & Code Quality

```bash
pytest -m "not integration" -v             # unit tests (no Postgres needed)
pytest -v                                  # everything; integration tests skip cleanly if PG is down
pytest tests/p3 -v                         # P3 only
pytest --cov=src/chat_bi_agent --cov-report=html   # coverage → htmlcov/

ruff check src/ tests/ streamlit_app/ scripts/
ruff format src/ tests/ streamlit_app/ scripts/
```

Integration tests (`@pytest.mark.integration`, 50 of them) need a **running Postgres with the
full seed data**. The gate is an actual `SELECT 1` probe, **not the presence of `PG_HOST`** — that
variable is always set in `.env`, so using it as the switch makes anyone without Docker hit a pile
of connection errors instead of a clean skip.

### CI (`.github/workflows/ci.yml`) — three jobs

| job | What it does |
|---|---|
| `test` | ruff + unit tests on a Python 3.11/3.12 matrix, coverage gate `--cov-fail-under=72` (actual 76) |
| `integration` | Starts a `postgres:16-alpine` service → applies schema → `seed --rows 100000 --seed 42 --with-events` → runs the 50 integration tests |
| `audit` | `pip-audit --skip-editable` dependency vulnerability audit |

`--seed 42` is a hard requirement: 43 gold-SQL row-count guards assert **exact row counts**
(e.g. 674), so a different seed turns them all red. These guards had never run in CI before
2026-08-18 — and gold row counts were the root cause of the P1 score distortion on 2026-08-14,
making them exactly what CI most needed to catch. See
[ADR-014](./DESIGN_DECISIONS.md#adr-014).

The coverage gate covers `src/chat_bi_agent` only: `streamlit_app` is a thin rendering shell whose
real logic (`viz/` at 96%, `llm/langfuse_feedback.py` at 100%) is already covered inside the core
package; including it would merely encode a known gap as a lower threshold.

---

## 📄 License / Author

MIT License · Shangyi Zhu · zhusayi1994@gmail.com

Questions or feedback welcome via email or Issue.

---

**Last updated**: 2026-09-02

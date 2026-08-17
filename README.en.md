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

---

## 📊 Evaluation Results

### In-house three-track evaluation

| Track | Questions | Passed | Avg Score | Baseline | Notes |
|---|---:|---:|---:|---|---|
| **P1 NL2SQL** | 8 / 8 | 8 | **0.965** | 2026-08-15 | Full question set; multi-table JOIN, time windows, aggregation, branch filters — all pass |
| **P2 Multi-Step Analysis** | 3 / 8 | 0 | **0.601** | 2026-08-17 | 4 scored dims (insight 35% + steps 25% + metric coverage 15% + rubric LLM judge 25%); reasoning/business demoted to diagnostics |
| **P3 RCA Attribution** | 7 / 7 | 7 | **0.900** · event_hit **7/7** | 2026-06-29 | 4-dim rubric, all events matched, zero hallucination |

The denominator in "Questions" is the total in each evaluation set. **P2 has only ever scored the
first 3 of its 8 questions**; q004–q008 have never been scored. At 300–500s per question,
completing them takes 40–70 minutes — deferred on cost.

**P2's 0.601 is the only score in this project that has been repeatedly revised downward**, because
much of the earlier figure was unearned. Three steps (2026-08-15/17):

| Step | Change | Avg | Passed |
|---|---|---:|---:|
| Start | — | 0.798 | 3/3 |
| ① Fix two silently broken dims | The insight dim split Chinese on whitespace (i.e. did not tokenize at all); metric coverage matched **individual characters** — '长' and '户' appear in almost any banking narrative | 0.798 | 3/3 |
| ② Delete the two dims with no ground truth | Reasoning quality and business relevance have **nothing to compare against**; their only possible criterion was counting keywords, and they sat at exactly 1.000 — 35% of the weight was a constant, not a measurement | 0.655 | 2/3 |
| ③ Add a rubric LLM judge | 4-dim G-Eval modelled on P3, each dim anchored to **this question's** YAML fields, weighted 25% | **0.601** | **0/3** |

Deleting in ② and adding in ③ is not self-contradictory: the deleted dims were anchored to a generic
word list (identical for every question, hence guaranteed to saturate), whereas the judge's four dims
are anchored to per-question `analysis_steps` / `expected_insights` / `evaluation_criteria`, written
by hand before the agent ever ran. See [ADR-015](./DESIGN_DECISIONS.md#adr-015) and
[ADR-016](./DESIGN_DECISIONS.md#adr-016).

**The substantive defect the judge exposed is `quantification`: 0.50 / 0.00 / 0.00 across the three
questions.** The agent largely fails to report figures comparable to the expected baselines — nothing
could see this before (`insight_accuracy` measures content-word recall, so saying "growth" counts as
a hit whether the reported figure is +25% or +3%).

**Read 0/3 as "all three fall below 0.7", but not as a precise scale**: q001 scored 0.700 and 0.679
on two runs, straddling the pass line, so `passed` oscillates between 1/3 and 0/3. The table reports
the `commit_dirty=false` run.

**Still unresolved**:
- `multi_metric_coverage` is at 1.000 on **all three** questions; the candidate terms are too generic.
- `causal_reasoning` / `business_actionability` still hit 1.000 on some questions. The prompt
  explicitly forbids awarding credit merely for business vocabulary, but the criteria remain lenient
  for competently written analysis — **only partly solved**.
- q004–q008 have never been scored.

**Do not read P1's 0.965 as a precise value**: repeated runs of the same configuration land between
**0.965 and 0.977**, and the spread comes **entirely** from one question, q008 (observed 0.90 / 0.93
/ 1.00); the other seven are identical run to run. q008 is also the hardest of the eight (two
windows + conditional aggregation + percent change + Top-N). The table reports the most recent
reproducible run (`commit_dirty=false`) — by that rule, not by picking the best-looking number.
Run-to-run noise of this magnitude is normal in this project; judge changes per-question rather
than by fractions of a point on the average. See [ADR-013](./DESIGN_DECISIONS.md#adr-013).

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
average is 0.965 with 8/8 passing. The gap comes from q005, where the agent genuinely dropped the
"term deposits" constraint.

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

# 1. Configure API key
cp .env.example .env
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

# Seed data
python -m chat_bi_agent.data.seed --truncate --with-events

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

Video / GIF coming later. In the meantime, follow Quick Start A, launch Streamlit, and try one question per tab:

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
│   └── calibrate_magnitudes.py
│
├── config/local.yaml          # Runtime config (model names, retrieval top_k, PG timeout, ...)
├── tests/                     # 316+ tests, organized by p1/p2/p3/shared/data/viz/eval/schema
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
| Observability | Langfuse v3 (self-hosted) | Full trace tree + LLM judge score writeback → ADR-003 |
| Agent orchestration | In-house function chain + `@observe` | Fixed flow, no LangGraph → ADR-002 |
| SQL parse/validate | sqlglot | AST rewriting + multi-dialect |
| Chinese tokenization | jieba | Preprocessing for schema retrieval |
| Database | PostgreSQL 16 | Isolated read-only user (chatbi_readonly) |
| Web UI | Streamlit | Demo-oriented, ~3× dev speed → ADR-009 |
| Visualization | Plotly | 6 chart types auto-inferred (rule-based) |
| Testing | pytest (316+ tests) + ruff | CI on GitHub Actions |

Full rationale and alternatives in [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md).

---

## 📖 Documentation

- [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md) — Tech-choice comparison, architecture evolution, 10 ADRs (Chinese)
- [EVALUATION_FRAMEWORK.md](./EVALUATION_FRAMEWORK.md) — Three-track methodology, question sets, rubrics, ground truth (Chinese)
- [金融 data agent 架构设计](./docs/金融data%20agent架构设计.md) — Original business-domain design (Chinese)
- [CONTRIBUTING.md](./CONTRIBUTING.md) — Dev environment and contribution flow

---

## 🧪 Tests & Code Quality

```bash
pytest -v                                  # run all tests
pytest tests/p3 -v                         # P3 only
pytest --cov=src --cov-report=html         # coverage → htmlcov/

ruff check src/ tests/ streamlit_app/ scripts/
ruff format src/ tests/ streamlit_app/ scripts/
```

---

## 📄 License / Author

MIT License · Shangyi Zhu · zhusayi1994@gmail.com

Questions or feedback welcome via email or Issue.

---

**Last updated**: 2026-06-30

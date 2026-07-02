# chat-bi-agent

![CI](https://github.com/Zsyyxrs/chat-bi-agent/actions/workflows/ci.yml/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![LLM: Qwen3.6](https://img.shields.io/badge/LLM-Qwen3.6--max--preview-7c3aed.svg)](https://dashscope.aliyun.com/)

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

### In-house three-track evaluation (baseline 2026-06-30)

| Track | Total | Passed | Avg Score | Notes |
|---|---:|---:|---:|---|
| **P1 NL2SQL** | 6 | 6 | **1.000** | Multi-table JOIN, time windows, aggregation, branch filters — all pass |
| **P2 Multi-Step Analysis** | 3 | 3 | **0.740** | 5-dim rubric (step completeness, metric coverage, insight, reasoning, business relevance) |
| **P3 RCA Attribution** | 7 | 7 | **0.900** · event_hit **7/7** | 4-dim rubric, all events matched, zero hallucination |

Evaluation methodology in [EVALUATION_FRAMEWORK.md](./EVALUATION_FRAMEWORK.md); raw baseline JSONs under [`results/`](./results/); latest markdown report at [`results/eval_report_2026-06-30.md`](./results/eval_report_2026-06-30.md).

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
│ Qwen3.6     │  │ PostgreSQL 16  │  │ Langfuse v3      │
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
| LLM (generation + judge) | Qwen3.6-max-preview (DashScope) | Single source, Chinese banking domain → ADR-001 |
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
- [金融 data agent 架构设计](./金融data%20agent架构设计.md) — Original business-domain design (Chinese)
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

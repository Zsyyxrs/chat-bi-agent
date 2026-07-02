# chat-bi-agent

![CI](https://github.com/Zsyyxrs/chat-bi-agent/actions/workflows/ci.yml/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![LLM: Qwen3.6](https://img.shields.io/badge/LLM-Qwen3.6--max--preview-7c3aed.svg)](https://dashscope.aliyun.com/)

**中文** | [English](./README.en.md)

> **面向银行业务场景的对话式 BI 智能体** —— 把"提需求 → 排期 → 开发报表 → 看报表 → 找数 → 人工归因"的传统链路，压缩成"**一句话提问 → 直接出数 → 自动归因 → 可追问**"。

---

## ✨ 三路径能力

| 路径 | 能力 | 典型问题 |
|---|---|---|
| **P1 精准取数** | 自然语言 → SQL → 取数 → 自动图表 | "上海分行 5 月高净值客户存款余额？" |
| **P2 多步分析** | 拆解 → 多步取数 → 事实抽取 → 综合洞察 | "春节前后现金支取行为有什么变化？" |
| **P3 RCA 归因** | 锚定事实 → 维度下钻 → 事件命中 → 根因合成 | "上海分行存款 5/14 下降 8%，原因是什么？" |

---

## 📊 评估成绩

### 自家三路径评测（2026-06-30 baseline）

| 路径 | 题量 | 通过 | 平均分 | 备注 |
|---|---:|---:|---:|---|
| **P1 NL2SQL** | 6 | 6 | **1.000** | 多表 JOIN、时间窗、聚合、分行筛选全过 |
| **P2 多步分析** | 3 | 3 | **0.740** | 5 维 rubric（步骤完整 + 多指标 + 洞察 + 推理 + 业务相关） |
| **P3 RCA 归因** | 7 | 7 | **0.900** · event_hit **7/7** | 4 维 rubric，全部命中埋雷事件、零幻觉 |

详细评估方法见 [EVALUATION_FRAMEWORK.md](./EVALUATION_FRAMEWORK.md)；原始 baseline JSON 在 [`results/`](./results/) 目录；最新 markdown 报告 [`results/eval_report_2026-06-30.md`](./results/eval_report_2026-06-30.md)。

一键复跑：

```bash
python scripts/run_all_evals.py              # 三路径全跑 + 生成 markdown 报告
python scripts/run_all_evals.py --only p3    # 只跑 P3
python scripts/eval_diff.py --phase p3       # 对比最近两个 P3 baseline
```

### 公开 benchmark

- **BIRD-financial dev subset** (n=106，模型 `qwen3.7-max-2026-05-20`)：

  跑了两个变体做对照——一个是外部 benchmark 的**能力天花板**参考，另一个是**现网 P1 pipeline 原样上跨域数据**的真实表现：

  | 难度 | n | Lean baseline<br/>(BIRD 专属 prompt) | P1 pipeline<br/>(现网中文银行域 prompt 原样) | Δ |
  | --- | ---: | ---: | ---: | ---: |
  | simple | 62 | 64.52% (40/62) | 50.00% (31/62) | −14.52 |
  | moderate | 37 | 48.65% (18/37) | 37.84% (14/37) | −10.81 |
  | challenging | 7 | 28.57% (2/7) | 28.57% (2/7) | 0 |
  | **overall** | **106** | **56.60%** (60/106) | **44.34%** (47/106) | **−12.26** |

  **两个数字怎么读**：
  - **Lean 56.60%** — 用 BIRD 专属英文 SQLite-aware prompt + 全表 schema + evidence，是 LLM + prompt substrate 的能力上限。
  - **P1 pipeline 44.34%** — 现网 P1（中文银行域 prompt / sqlglot PG 校验 / Reflector 重试）原样上 BIRD，把中文规则、PG 方言假设都带过去。
  - **Δ = 12.26 分**是**"我们为本域深度特化付出的跨域代价"**。
  - **主要失分模式**：27 条 syntax error（26%），源自 P1 prompt 里的 PG 方言假设——`EXTRACT(YEAR FROM ...)`、`ILIKE`、`DATE 'YYYY-MM-DD'` 字面量等在 SQLite 上跑不通。Reflector 3 轮重试也难救（35 题触发 reflect，只 4/35 = 11% 挽回）。

  子集选 `financial`（捷克银行真实数据，8 表）是因为和本项目领域同源、难度对等。评测入口 [`scripts/run_bird_financial.py`](scripts/run_bird_financial.py)（lean）与 [`scripts/run_bird_financial_p1.py`](scripts/run_bird_financial_p1.py)（P1），结果分别落盘 [`results/bird_financial_2026-07-01.json`](results/bird_financial_2026-07-01.json) 与 [`results/bird_financial_p1_2026-07-01.json`](results/bird_financial_p1_2026-07-01.json)。指标口径与 BIRD 官方 `evaluation.py` 一致（EX = 行集合等价 + `dev_tied_append.json` 42 条补丁）。数据集下载见 [`benchmarks/README.md`](benchmarks/README.md)。

---

## 🏗 系统架构

```
                       ┌─────────────────────────────┐
                       │  Streamlit Web UI (3 Tabs)  │
                       │   P1 取数 / P2 分析 / P3 RCA  │
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
│ SQLGen      │              │  ↓              │            │    (调 P1 取锚)    │
│ SQLValidate │              │  P1 Agent (×N)  │◄──reuse────┤ 2. drill_select   │
│ SQLExecute  │              │  ↓              │            │ 3. drill_run      │
│ Reflector   │              │  FactExtractor  │            │    (Pareto Top-K) │
│ (×1 retry)  │              │  ↓              │            │ 4. event_match    │
│             │              │  InsightSynth   │            │    (YAML 时间窗)  │
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
│ + Embedding │  │  user enforced)│  │ 全链路 trace      │
└─────────────┘  └────────────────┘  └──────────────────┘
```

**架构要点**：
- **三个独立 Agent，各管一条路径**（不强行复用一个 super-agent）
- **P2/P3 复用 P1 作原子取数层**（FactAnchor / 多步 plan 的每一步都是 P1 调用）
- **编排是函数链 + Langfuse `@observe` 装饰器**，**没用 LangGraph**（流程固定不需要图）
- **LLM 单源**（Qwen 既做生成也做评分），**没有独立 judge 模型**
- **P3 ground truth 用 YAML 事件库 + 传播引擎埋雷**（可控、可重放、可量化）

完整设计取舍见 [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md)。

---

## 🚀 Quick Start

### A. Docker Compose 一键（推荐）

```bash
git clone https://github.com/Zsyyxrs/chat-bi-agent.git
cd chat-bi-agent

# 1. 配置 API key
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY（必填）

# 2. 起全栈（Postgres + Langfuse 全套 + Streamlit App）
docker compose up -d

# 3. 灌种子数据 + 埋雷事件（一次性 job）
docker compose --profile seed run --rm seed

# 4. 首次启动需要在 Langfuse 创建 API Key
#    访问 http://localhost:3001 → admin@chatbi.local / admin12345
#    Settings → API Keys → 新建一对 → 回填到 .env 的 LANGFUSE_PUBLIC_KEY / SECRET_KEY
#    然后 docker compose restart app

# 5. 打开 Streamlit
open http://localhost:8501
```

服务端口：
- Streamlit App：`http://localhost:8501`
- Langfuse UI：`http://localhost:3001`
- pgAdmin：`http://localhost:5050`
- Postgres：`localhost:5433`（容器内仍 5432）

### B. 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 起 Postgres + Langfuse（不起 App）
docker compose up -d postgres langfuse pgadmin

# 灌数据
python -m chat_bi_agent.data.seed --truncate --with-events

# 本地跑 Streamlit
streamlit run streamlit_app/app.py
```

### 跑评估

```bash
python scripts/run_all_evals.py                    # 三路径全跑
python scripts/run_all_evals.py --only p1          # 只跑 P1
python scripts/run_all_evals.py --skip p2,p3       # 跳过 P2/P3
python scripts/run_all_evals.py --p3-limit 2       # P3 只跑前 2 题（省 token）
python scripts/run_all_evals.py --report-only      # 不跑，仅基于最新 baseline 生成报告

python scripts/eval_diff.py --phase p3             # 对比最近两个 P3 baseline
python scripts/eval_diff.py --phase p3 \
    --base results/baseline_p3_rca_2026-06-28.json \
    --head results/baseline_p3_rca_2026-06-29.json
```

---

## 🎬 Demo

视频/GIF 待补。建议先按 Quick Start A 起 Streamlit，三个 tab 各试一题：

- **P1 tab**：输入"上海分行 2026 年 5 月高净值客户的存款余额总额是多少？"
- **P2 tab**：输入"春节前后现金支取行为有什么变化？"
- **P3 tab**：输入"上海分行高净值客户的存款在 2026-05-14 突然下降了 8%，可能是什么原因？"

每条提问都会在 Langfuse 留下完整 trace（http://localhost:3001 实时可看）。

---

## 🧱 项目结构

```
chat-bi-agent/
├── src/chat_bi_agent/
│   ├── agents/                # 三个 Agent + 共享组件
│   │   ├── p1/                #   nl2sql_agent · sql_generator · sql_validator · reflector
│   │   ├── p2/                #   p2_analysis_agent · planner · fact_extractor · insight_synthesizer · report_writer
│   │   ├── p3/                #   p3_rca_agent · fact_anchor · drilldown_selector · drill_executor · event_matcher · synthesizer
│   │   └── shared/            #   schema_linker · sql_executor
│   ├── runners/               # P1/P2/P3 evaluation runners
│   ├── llm/                   # qwen_client.py + langfuse_setup.py
│   ├── viz/                   # chart_inference (rule-based) + plotly_renderer
│   ├── eval/                  # precision / multi-step / rca evaluators
│   ├── data/
│   │   ├── seed.py            #   种子数据生成 CLI
│   │   └── events/            #   YAML 埋雷事件库（4 个真实场景）
│   ├── schema/                # 表/列元数据 loader
│   └── config.py              # YAML + 默认值合并
│
├── streamlit_app/
│   ├── app.py                 # 三 tab 入口
│   ├── tabs/{p1_nl2sql,p2_analysis,p3_rca}.py
│   └── components/{chart,dataframe,sql,insight}_block.py
│
├── scripts/
│   ├── run_all_evals.py       # 一键跑齐 P1+P2+P3 + 生成 markdown 报告
│   ├── eval_diff.py           # baseline 回归检测
│   ├── verify_events.py       # 埋雷事件传播验证
│   ├── rejudge_baseline.py    # 重新跑 LLM judge
│   └── calibrate_magnitudes.py
│
├── config/local.yaml          # 运行时配置（模型名、检索 top_k、PG 超时等）
├── tests/                     # 316+ 测试，按 p1/p2/p3/shared/data/viz/eval/schema 分目录
├── results/                   # 评估 baseline JSON + markdown 报告
├── docker-compose.yml         # Postgres + Langfuse 全套 + App + Seed
├── Dockerfile                 # Streamlit 镜像
├── EVALUATION_FRAMEWORK.md    # 三路径评估方法详解
├── DESIGN_DECISIONS.md        # 技术选型 + 演进史 + ADR
└── CONTRIBUTING.md
```

---

## 🛠 技术栈

| 类别 | 选型 | 备注 |
|---|---|---|
| LLM（生成 + 评分） | Qwen3.6-max-preview（DashScope） | 单源，中文银行场景 → ADR-001 |
| 嵌入 | text-embedding-v4（DashScope，dim=1024） | schema 检索用 |
| 可观测性 | Langfuse v3（self-hosted） | 全链路 trace + LLM judge 评分回流 → ADR-003 |
| Agent 编排 | 自研函数链 + `@observe` 装饰器 | 流程固定，未用 LangGraph → ADR-002 |
| SQL 解析/校验 | sqlglot | AST 改写 + 多方言 |
| 中文分词 | jieba | schema 检索预处理 |
| 数据库 | PostgreSQL 16 | 只读用户隔离（chatbi_readonly） |
| Web UI | Streamlit | Demo 取向，3 倍开发速度 → ADR-009 |
| 可视化 | Plotly | 6 种图表自动推断（rule-based） |
| 测试 | pytest（316+ 项） + ruff | CI on GitHub Actions |

完整决策理由与替代方案对比见 [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md)。

---

## 📖 文档导航

- [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md) —— 技术选型对比 + 架构演进史 + 10 条 ADR
- [EVALUATION_FRAMEWORK.md](./EVALUATION_FRAMEWORK.md) —— 三路径评估方法、问题集、rubric、ground truth
- [金融 data agent 架构设计](./金融data%20agent架构设计.md) —— 业务背景与原始设计稿
- [CONTRIBUTING.md](./CONTRIBUTING.md) —— 开发环境与贡献流程

---

## 🧪 测试与代码质量

```bash
pytest -v                                  # 跑全部测试
pytest tests/p3 -v                         # 只跑 P3
pytest --cov=src --cov-report=html         # 覆盖率报告 → htmlcov/

ruff check src/ tests/ streamlit_app/ scripts/
ruff format src/ tests/ streamlit_app/ scripts/
```

---

## 📄 License / Author

MIT License · Shangyi Zhu · zhusayi1994@gmail.com

如有问题或反馈欢迎邮件或 Issue。

---

**最后更新**：2026-06-30

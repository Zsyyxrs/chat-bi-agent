# chat-bi-agent 设计决策

本文档记录 chat-bi-agent 的关键技术决策与取舍。分为三部分：

- **§1 技术选型对比表** —— 横向速览：用了什么、替代是什么、一句话理由
- **§2 架构演进史** —— 纵向时间线：从 v0 到 v1.0 每个节点做了什么
- **§3 ADR-001 ~ ADR-010** —— 每条决策的完整 Context / Decision / Alternatives / Consequences

三部分互为索引：选型表和演进史都指向对应 ADR，避免重复展开。

---

## §1 技术选型对比表

| 决策点 | 选型 | 主要替代 | 一句话理由 | 深入 |
|---|---|---|---|---|
| **LLM（生成 + 评分）** | **当前 `qwen3.7-max`**（DashScope；ADR-001 立项时为 Qwen3.6-max-preview） | GPT-4 / Claude 3.5 / DeepSeek-V2 | 中文银行场景 + 国内合规接入 + 单源省心 | [ADR-001](#adr-001-llm-选-qwen36-max-preview) |
| **Agent 编排** | 自研函数链 + `@observe` 装饰器 | LangGraph / CrewAI / AutoGen | 三路径流程都是**固定 DAG**，框架抽象换不来收益，多一层维护负担 | [ADR-002](#adr-002-自研函数链编排不引入-agent-框架) |
| **可观测性** | Langfuse v3（self-hosted） | LangSmith / Phoenix / OpenLLMetry | 自托管无数据出境风险 + trace tree 完整 + LLM judge 分数可回流 | [ADR-003](#adr-003-langfuse-v3-self-hosted-做全链路可观测) |
| **评分方式** | LLM-as-judge（Qwen 自评，4 维 rubric） | 人工标注 / Ragas / DeepEval | 启动期无标注预算，Qwen 自评在银行场景稳定性可接受，后续可切人工 | [ADR-004](#adr-004-llm-as-judge-qwen-自评-做评分) |
| **SQL 校验** | sqlglot（AST 解析 + dry-run） | 直接执行验错 / 自写 Antlr | Python 原生、多方言、AST 可改写；执行验错代价大且噪声高 | [ADR-005](#adr-005-sqlglot-做-sql-ast-校验) |
| **反思机制** | Reflector 单次重试 | 无反思 / 多次重试 / 树搜索（ToT） | 银行 SQL 错误模式有限（3-4 类），单次重试 ROI 最高 | [ADR-006](#adr-006-reflector-单次重试不做多次或树搜索) |
| **P3 ground truth** | YAML 事件库 + 传播引擎埋雷 | 用真实生产脱敏数据 / 手工 SQL 注入异常 | 可控、可重放、可量化、可解释；对齐 rubric 的 event_hit 维度 | [ADR-007](#adr-007-yaml-事件库--传播引擎-构造-p3-ground-truth) |
| **Schema 检索** | Embedding（text-embedding-v4）+ jieba 分词 | BM25 / 静态映射表 / GraphRAG | 中文同义词多（"存款/储蓄/余额"），embedding 召回 + jieba 分词组合覆盖率最好 | [ADR-008](#adr-008-embedding--jieba-做-schema-检索) |
| **Web UI** | Streamlit | React/Next.js + FastAPI / Gradio | Demo 场景，3 倍开发速度，直接给 Python 对象绑图表 | [ADR-009](#adr-009-streamlit-做-web-ui) |
| **数据库权限** | 双用户隔离（chatbi 写 / chatbi_readonly 读） | 单账号 + 应用层白名单 | Agent 生成的 SQL 由 readonly 用户执行，DB 层兜底防 DROP/DELETE | [ADR-010](#adr-010-postgresql-双用户隔离-写与读) |

---

## §2 架构演进史

时间线，从项目起步到当前 v1.0。每个节点只列**做了什么变更**和**为什么**，具体决策展开看 §3 ADR。

### v0 —— 骨架期（2026-05）

- 建仓：写 README、评估框架 spec（EVALUATION_FRAMEWORK.md）、金融 data agent 架构设计稿
- 数据层：`data/seed.py` 生成 100K 行合成银行数据；建 6 维度 + 5 事实表
- 埋雷：`data/events/*.yaml` 定义 4 个真实业务事件（安心 90 到期、春节支取、LPR 下调、七夕活动）+ propagation_engine 传播规则
- **确定评估先行**：三路径各 8 题，rubric 定义完，数字先零，代码后写

### v0.5 —— P1 跑通（2026-06 上旬）

- P1 NL2SQL 完整链路：SchemaLinker → SQLGenerator → SQLValidator → SQLExecutor
- 引入 **Reflector**：SQL 执行失败时，把错误信息回喂 LLM 单次重试（[ADR-006](#adr-006-reflector-单次重试不做多次或树搜索)）
- 接 **Langfuse v3**（self-hosted，docker-compose 全套）：所有 LLM 调用和 agent 节点带 `@observe`
- P1 评估：6 题 pass_rate 100%，avg 1.000

### v0.7 —— P3 五步 pipeline 落地（2026-06 中旬）

- P3 RCA 固定 5 步：**fact_anchor → drill_select → drill_run → event_match → synthesize**
  - fact_anchor 复用 P1 取"当前值 vs 前值"锚
  - drill_select 让 LLM 决定按哪几个维度下钻
  - drill_run 用 Pareto Top-K 提取显著贡献维值
  - event_match 用时间窗口 overlap 匹配 YAML 事件库
  - synthesize 用 LLM 输出 narrative + conclusion
- **不做动态 planner**：5 步顺序对所有 attribution 问题都够用，多了 LLM 容易失控（这是后来 v0.8 一堆 bug 印证的）

### v0.8 —— P3 单题排错（2026-06 下旬）

从 attribution_q001~q008 单题跑，每题都有独立 baseline JSON（见 `results/baseline_p3_rca_2026-06-24.attribution_q00X.json`）。真实 bug 与修复：

- **q007 跨指标 CROSS JOIN**（commit `a27f7d9`）：fact_anchor 把"AUM 下降"和"存款下降"两个指标的取数错拼成 CROSS JOIN。修：加"单指标对"约束，同一次 anchor 只允许一个指标对（current + prior）。
- **q007 存款口径错配**（commit `29b5bfa`）：agent 用 balance_daily.avg，YAML 期望用 holding.snapshot。修：改 YAML 对齐 agent 实测口径（**数据在哪就以哪为准**，不强求 agent 迁就 spec）。
- **q003/q006 fact_anchor 指标错配**（commit `ef57220`）：`_extract_current_prior` 按前缀配对，遇到多指标返回混淆。修：改按**后缀配对**（`_curr` / `_prior`）。
- **drill 方向错误**（commit `1bdec94`）：q004 下钻找"贡献 Top-K"但没考虑符号，找到的是"下降最少的分行"而不是"下降最多的"。修：sign-aware Pareto，按事件方向取 Top-K。
- **synth 幻觉编码**（commit `2ab8f28`）：narrator 会把 `BR_CITY_0006` 改写成"某上海分行"，丢失可追溯性。修：synthesizer prompt 注入"题面已固定实体"段，强制复述编码。
- **fact_anchor window-parity**（commit `ecdc346`）：BETWEEN prefilter 后再算 window parity 会失真。修：改为**众数判定**，容忍 prefilter。

结果：q001~q007 从平均 ~0.6 提到 ~0.9。

### v0.9 —— LLM judge 4 维 rubric（2026-06-26）

- P3 评分从"单一 conclusion 相似度"升级为 **4 维 weighted rubric**：
  - `event_hit`（40%）：是否命中埋雷事件 ID
  - `dim_recall`（30%）：是否找出关键维度
  - `conclusion_similarity`（20%）：语义匹配
  - `hallucination_penalty`（10%）：事实错误扣分
- 见 [ADR-004](#adr-004-llm-as-judge-qwen-自评-做评分)。

### v0.95 —— P3 全通（2026-06-29）

- attribution_q001~q007 全部达到 0.900，event_hit 7/7，平均 avg 0.900
- q008 因数据分布问题暂搁（题面预设条件在种子数据中未触发足量样本）
- P3 收工：baseline `results/baseline_p3_rca_2026-06-29.json`

### v1.0 —— UI + 一键化收尾（2026-06-30）

- **Streamlit 三 tab UI 上线**：`streamlit_app/tabs/{p1_nl2sql, p2_analysis, p3_rca}.py`，components 里有 chart/dataframe/sql/insight 4 类可复用块
- **Chart 自动推断**：`viz/chart_inference.py` 用 5 条规则（1-row → KPI / datetime+numeric → line / 1-cat+1-num → bar / 2-num → scatter / else → table）覆盖 6 种图表类型（pie 已定义但无推断规则，见 ADR-002 遗留）
- **Docker Compose 补齐 app + seed 服务**：`docker compose up -d` 起全栈，`docker compose --profile seed run --rm seed` 灌数据（见 commit `5d28cee`）
- **一键评测**：`scripts/run_all_evals.py` 跑齐三路径 + 生成 markdown 报告；`scripts/eval_diff.py` 做 baseline 回归检测（见 commit `40515e9`）

### 未纳入范围（明确 defer）

- **Code Agent + Python sandbox**：ROI 不够（P3 已能覆盖大部分 attribution 场景），复杂度高（需要沙箱隔离）。放 backlog。
- ~~**BIRD-financial dev 子集评测**~~：已完成，见 README §公开 benchmark（`qwen3.7-max-2026-05-20` 上 EX 56.60%）与 §3 新增 ADR-011。

---

## §3 ADR

每条 ADR 结构：**Status / Context / Decision / Alternatives / Consequences**。

---

### ADR-001: LLM 选 Qwen3.6-max-preview

**Status**: Accepted · 2026-05

**Context**:
- 项目面向**中文银行业务**，问题里大量出现"高净值客户、分行、AUM、存款余额、赎回、续作"等中文金融术语
- 部署环境需考虑**国内合规**：金融数据不可出境，海外 API 不能选
- 单人项目，**没预算维护多模型 fallback**

**Decision**:
选 Qwen3.6-max-preview 作为唯一生成 LLM，同时兼任 judge 模型。通过 DashScope（阿里官方）调用。默认 `temperature=0.1`。嵌入用 `text-embedding-v4`（dim=1024）。

**Alternatives considered**:

| 候选 | 优 | 劣 |
|---|---|---|
| GPT-4 / GPT-4o | 综合能力强 | 数据出境合规风险、成本高、中文金融术语不如国产模型精 |
| Claude 3.5 Sonnet | 长上下文、推理强 | 同上出境问题 + 国内无稳定接入 |
| DeepSeek-V2 | 中文强、便宜 | 项目起步时 tool calling / JSON mode 稳定性不如 Qwen |
| Qwen + GPT-4 dual | 更鲁棒 | 双 API key 双计费 + 结果分歧仲裁复杂度 |

**Consequences**:
- ✅ 国内调用低延迟（p50 ~2s），成本可控（P3 全跑 7 题 ~$3 RMB）
- ✅ 中文 schema 检索准确率高
- ⚠️ 单点风险：Qwen 出问题就全瘫。缓解：`llm/qwen_client.py` 抽象层已隔离，切换 LLM 只需改一个文件
- ⚠️ LLM-as-judge 用同一个模型自评，可能有一致性偏差（见 ADR-004 的应对）

**逃生口**：`llm/qwen_client.py:chat()` 返回统一 `ChatResult` 类型，切换到 OpenAI-兼容 API 只改这个文件。

---

### ADR-002: 自研函数链编排，不引入 Agent 框架

**Status**: Accepted · 2026-06

**Context**:
- 三路径流程都是**固定 DAG**：
  - P1: SchemaLinker → SQLGen → Validate → Execute → (Reflect × 1 retry)
  - P2: Planner → (P1 × N) → FactExtractor → InsightSynth → ReportWriter
  - P3: fact_anchor → drill_select → drill_run → event_match → synthesize
- 没有真正的**动态路由**（谁调谁在编写时就确定）
- 需要**细粒度 tracing**（每个节点独立 span，便于 debug P3 单题失败）

**Decision**:
不用 LangGraph / CrewAI / AutoGen。用普通 Python 函数 + Langfuse `@observe` 装饰器。每个"节点"就是一个方法，agent class 里手写调用顺序。

**Alternatives considered**:

| 框架 | 为什么没选 |
|---|---|
| **LangGraph** | 抽象层太重（StateGraph / conditional edges / checkpointing 全用不上），trace 语义反而不如原生 @observe 清晰 |
| **CrewAI** | 面向"多 agent 协作对话"场景，我们是**单 agent 内部多步**，不匹配 |
| **AutoGen** | 同 CrewAI，且中文文档少 |
| **纯 chain（LangChain LCEL）** | 已被 LangGraph 部分取代；且它的 tracing 强绑 LangSmith |

**Consequences**:
- ✅ 代码结构清晰：`p3_rca_agent.py:run()` 就是 5 行顺序调用，读代码即读架构
- ✅ Langfuse trace tree 精确到方法级，debug 时能定位到具体哪一步失败
- ✅ 少一层抽象，pytest 直接 mock 单个方法就能测
- ⚠️ 未来如果引入"动态路由"（比如根据问题类型走不同路径）需要自己写 dispatcher，但目前**没有这个需求**

**触发重考点**：如果出现真正的动态 workflow（比如根据 P1 结果动态决定是否走 P3），再评估 LangGraph。

---

### ADR-003: Langfuse v3 self-hosted 做全链路可观测

**Status**: Accepted · 2026-06

**Context**:
- 三路径 debug 严重依赖"看到 LLM 输入输出全文"
- 需要**评分回流**：LLM judge 打的分要能挂到对应 trace 上，形成 baseline
- **不能用云端 SaaS**（同 ADR-001 合规约束）

**Decision**:
Langfuse v3，全套 self-hosted，随 `docker-compose.yml` 一起起。栈：`langfuse-web` + `langfuse-worker` + Postgres + ClickHouse + Redis + MinIO。端口 3001（避开可能占用的 3000）。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **LangSmith** | SaaS，数据出境 |
| **Phoenix (Arize)** | 主打评估 dashboard，trace 深度不如 Langfuse |
| **OpenLLMetry** | OTel 兼容层，需自己搭后端（Jaeger/Tempo），trace 视觉差 |
| **纯日志（loguru + 结构化 JSON）** | 起步够用，但**无 UI 支撑不了 P3 单题深度 debug**（一次 5 步 pipeline 打几十条日志） |

**Consequences**:
- ✅ Trace tree 精确到 span：P3 一次 run = 1 个 trace，包含 5 个 span，每个 span 里嵌 LLM call
- ✅ LLM judge 分数用 `score()` API 打到 trace 上，UI 里能按分数排序找失败 case
- ✅ Prompt 版本管理：judge prompt 改动后可以在 Langfuse UI 里 diff
- ⚠️ 6 个服务的栈很重，本地跑内存占 ~2GB。缓解：本地开发时可以 `docker compose up -d postgres` 单起 Postgres，agent 会自动 fallback 到无 tracing 模式
- ⚠️ 首次启动需要在 UI 里手动创建 API Key 回填 .env（README 已注明）

---

### ADR-004: LLM-as-judge（Qwen 自评）做评分

**Status**: Accepted · 2026-06

**Context**:
- 三路径评估都需要打分：
  - P1: 6 维（表选择 / 过滤 / 列 / 聚合 / 结果行数 / 语法）
  - P2: 5 维（步骤完整 / 多指标 / 洞察 / 推理 / 业务）
  - P3: 4 维（event_hit / dim_recall / conclusion_similarity / hallucination_penalty）
- **没有标注预算**（人工标一次全套 ~$500）
- 需要能**反复迭代**（每改一个 prompt 就要重跑评估）

**Decision**:
用 Qwen 自评。每维给出 0/0.5/1 三档分数 + 简短解释。加权求和得 combined_score。P3 rubric 权重定为 event_hit 40% / dim_recall 30% / conclusion 20% / hallucination 10%（event_hit 最重是因为 attribution 场景"找对根因事件"是硬要求）。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **人工标注** | 成本 + 迭代速度双杀 |
| **Ragas** | 主打 RAG 场景（context recall / faithfulness），不匹配 attribution rubric |
| **DeepEval** | 需要 OpenAI key（同 ADR-001 合规） |
| **双 LLM 交叉评（Qwen + DeepSeek）** | 复杂度翻倍，起步阶段不值 |

**Consequences**:
- ✅ 迭代快：改完 agent 十分钟内出新分
- ✅ 分数分布合理：P3 avg 从 0.6 → 0.9 期间，每次修 bug 分数变化都能被 rubric 捕捉
- ⚠️ **自评偏差**：Qwen 评 Qwen 可能对自己"手下留情"。缓解方式（已实施）：
  1. rubric 里的 event_hit 是**硬对齐**（字符串匹配事件 ID，不由 LLM 主观打分）
  2. dim_recall 是**集合召回**（YAML 期望维度 vs agent 输出维度的 recall），也是硬指标
  3. 只有 conclusion_similarity 和 hallucination_penalty 依赖 LLM 主观判断，且加起来只占 30%
- ⚠️ Judge prompt 不稳定：commit 历史里 `rejudge_baseline.py` 就是为了 prompt 改动后重打分

**未来 upgrade**：如果 P3 数字停在 0.9 不动，可以引入**人工抽检 20% 样本**做校准。

---

### ADR-005: sqlglot 做 SQL AST 校验

**Status**: Accepted · 2026-06

**Context**:
- P1 SQL 生成后需要校验：语法合法性、表/列是否存在、是否 SELECT-only（不能有 DROP/UPDATE）
- 直接扔到 Postgres 执行验错：慢（100ms+）、错误信息对 LLM 不友好、留下 abort txn 需要 rollback

**Decision**:
用 sqlglot 做 AST-level 校验：
1. `parse_one(sql, dialect="postgres")` 拿 AST
2. 遍历 AST 检查是否只有 SELECT
3. 检查表名/列名是否在 schema 元数据里
4. 通过后才真正执行

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **直接执行验错** | 慢 + 错误信息噪声大（"syntax error at or near"对 LLM 反思用处不大） |
| **Antlr 自写 grammar** | 造轮子，Postgres 方言 grammar 复杂 |
| **regex + 黑名单** | 脆弱（各种 SQL 注释 / 编码技巧绕过） |

**Consequences**:
- ✅ 快（~5ms per SQL）+ 错误信息精准（能告诉 LLM"列 xxx 不存在于表 yyy"）
- ✅ AST 还能做**改写**：比如强制加 LIMIT、加 statement_timeout
- ✅ 多方言支持：未来加 MySQL/SQLite 只改 dialect 参数
- ⚠️ 覆盖不到所有语义错（如死锁、超时），这些仍需依赖执行时 Reflector 处理（见 ADR-006）

---

### ADR-006: Reflector 单次重试，不做多次或树搜索

**Status**: Accepted · 2026-06

**Context**:
- P1 SQL 有可能生成错，需要重试机制
- 每次 Qwen 调用 ~2s + 成本，重试次数直接乘上去
- 银行 SQL 常见错误类型有限（列名错、聚合层级错、时间窗错、JOIN 条件缺）

**Decision**:
`Reflector` 只做 **1 次重试**：SQL 执行失败或 validator 失败时，把错误信息 + 原 SQL 回喂 LLM，让它输出修正版。第二次仍失败则 abort。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **无 reflector** | P1 pass_rate 直接掉 20% |
| **多次重试（e.g. 3 次）** | 边际收益递减 —— 单次能修好的问题占 ~90%，第 2 次能修的 ~5%，第 3 次几乎无 |
| **Tree-of-Thoughts / MCTS** | 银行 SQL 场景**没有 branching decision**（不是数学证明或博弈），树搜索是杀鸡用牛刀 |
| **Human-in-the-loop** | 破坏 agent 全自动定位 |

**Consequences**:
- ✅ P1 pass_rate 从 ~80% → 100%（6/6）
- ✅ 成本可控：worst case 每题多 1 次 LLM 调用
- ⚠️ 特别难的 SQL（如 P1 里没有的 CTE / 递归查询）单次重试可能不够。目前评估集不覆盖这类，未来加时再评估

**Update 2026-08-15：实现曾长期与本决策相反，已修正**

代码里 `MAX_ATTEMPTS = 3` 配合 `range(1, MAX_ATTEMPTS + 1)`，实际跑的是
**1 初次 + 2 次重试**——正是上面 Alternatives 表里被否决的「多次重试（e.g. 3 次）」。
本 ADR 的 Decision 从写下起就没被实现过，而且没有任何信号提示：多跑一次不报错，
只是慢一点、贵一点，分数还一样。

翻全部 `results/` 产物统计，证据支持本 ADR 原本的判断：

| | 出现次数 | 最终成功 |
|---|---:|---:|
| attempts=2（第 1 次重试） | 23 | **13（57%）** |
| attempts=3（第 2 次重试） | 27 | **0（0%）** |

抽样核实那 27 次都是 `sql=无`、`ex=0` 的真失败。上面写的「第 3 次几乎无」还保守了，
实测是一次都没有。因此改代码而非改文档——不是决策错了，是实现没跟上决策。
`Reflector.__init__` 的默认值一并从 3 改到 2，否则不显式传参的调用方仍拿到旧预算。

**未验证到的部分（诚实记账）**：改完重跑 P1 全量，8 题 `attempts` 全是 1，
reflect 路径一次都没触发——这轮只能证明没引入回归，**不构成对改动本身的验证**。
依据仍是上表的历史统计。要真正验证需构造必然触发 reflect 的用例，另计。

守门 `tests/p1/test_reflect_budget_matches_adr.py` 钉住预算数值，改之前先更新本 ADR。

---

### ADR-007: YAML 事件库 + 传播引擎 构造 P3 ground truth

**Status**: Accepted · 2026-05

**Context**:
- P3 RCA 评估需要**已知答案**：知道数据变化是由哪个业务事件引起的，才能验证 agent 是否找对
- 真实生产数据：脱敏难，且事件因果不明（银行内部也未必标记）
- 手工在 SQL 里 inject 异常：不可复现、不可解释、不可量化

**Decision**:
定义**事件库**（`data/events/*.yaml`），每个事件描述：
- 事件 ID、发生日期、受影响维度（分行/客户层级/产品类型）
- 传播规则（`target_table`、`target_column`、`delta%`、`delay_days`、`ramp_days`）

由 `propagation_engine.py` 在 seed 时把事件效应"传播"到事实表数据里。当前 4 个事件覆盖：
- `anxin_90_expire`（2026-05-14 上海分行高净值理财到期）
- `spring_festival_withdrawal`（2026-02-15~23 全行现金支取高峰）
- `lpr_cut_q2`（2026-06-20 LPR 下调驱动贷款申请）
- `qixi_deposit_campaign`（2026-08-10 七夕定存活动）

评估时 rubric 的 `event_hit` 维度做**硬字符串匹配**：agent 输出的事件 ID 是否在期望列表里。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **真实脱敏数据** | 拿不到 + 答案不明 |
| **手工 SQL inject** | 不可复现、YAML 表达力更强 |
| **LLM 生成 attribution 题** | Ground truth 由 LLM 造 → 评估变自欺欺人 |

**Consequences**:
- ✅ 完全可控：改 YAML 就能造新场景，seed 一遍就能测
- ✅ 事件语义完整：delay/ramp/sampling 都能表达（春节支取是渐进 8 天、LPR 下调是延迟 7-14 天）
- ✅ event_hit 是硬指标，无 LLM 主观打分风险
- ⚠️ 合成数据的**统计特征**未必贴合真实（真实银行 tail 更长、分布更偏）。可以未来接生产数据校准
- ⚠️ 只有 4 个事件，覆盖不了所有 attribution pattern（如**多事件叠加**）。当前 q008 就是多事件叠加题，也是唯一未过题

---

### ADR-008: Embedding + jieba 做 schema 检索

**Status**: Accepted · 2026-06

**Context**:
- Schema linker 要在 6 维度 + 5 事实表里找出问题相关的表/列
- 中文金融术语**同义词极多**："存款/储蓄/余额"、"分行/网点/支行"、"高净值/私行/HNW"
- 表名列名混合中英（`dim_customer.customer_tier`、注释里"客户等级"）

**Decision**:
Embedding 检索：
1. 对每个表/列生成 embedding（描述 = "表名 + 中文注释 + 列名列表"）用 `text-embedding-v4`（dim=1024）
2. 问题先用 jieba 分词做中文归一化（"高净值客户" → tokens）
3. 用问题 embedding 检索 top-k 表（`retrieval.top_k_planner=8`、`top_k_nl2sql=4`）

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **BM25** | 词面匹配，同义词命中率低 |
| **静态映射表**（"高净值" → dim_customer） | 维护成本 O(n²)，schema 一变就得重写 |
| **GraphRAG** | 6 表 schema 不需要 graph 复杂度 |
| **纯 LLM in-context**（把整 schema 塞 prompt） | 6 表还行，未来 60 表就爆了 |

**Consequences**:
- ✅ 中文同义词召回好，P1 6/6 全过
- ✅ jieba 预处理让 "上海分行" 不会被切成 "上/海/分/行"
- ✅ top_k 可调（planner 场景放宽到 8，nl2sql 紧到 4 减 prompt 长度）
- ⚠️ Embedding 需要预计算（seed 时一次性生成）。schema 变更时要重跑
- ⚠️ jieba 词典对新金融术语不认识时会切错，需要自定义词典（目前无问题，未来可加）

---

### ADR-009: Streamlit 做 Web UI

**Status**: Accepted · 2026-06

**Context**:
- 项目主线是**评估驱动的 agent 系统**，UI 是"能看/能试"层，不是产品
- 需要展示 dataframe、chart、markdown、SQL 高亮四种块
- 单人项目，前后端全栈自己写

**Decision**:
Streamlit。三 tab 对应三路径。组件层抽出 `chart_block / dataframe_block / sql_block / insight_block`。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **React/Next.js + FastAPI** | 3-5× 开发时间，且需要维护 API layer + 状态管理 |
| **Gradio** | 组件抽象更死板（block 是 fn 输入输出），我们要控件级布局 |
| **纯 CLI + Jupyter** | Demo 场景不够直观 |
| **Django admin** | 是 CRUD 工具不是数据 app |

**Consequences**:
- ✅ 三 tab UI 一天写完
- ✅ Python 对象（DataFrame / Plotly Figure）直接绑 UI，无中间序列化
- ✅ `st.session_state` 管调用计数够用
- ⚠️ 无法做复杂前端交互（如拖拽、多光标）。目前无需求
- ⚠️ 并发能力弱（Streamlit 单会话 rerun）。Demo 场景无所谓，未来上量要换

**触发重考点**：DAU > 100 或需要多用户并发时换 Next.js。

---

### ADR-010: PostgreSQL 双用户隔离（写 与 读）

**Status**: Accepted · 2026-06

**Context**:
- Agent 生成的 SQL 由 LLM 产生，理论上可能生成 `DROP TABLE` 或 `UPDATE ... WHERE true` 等破坏语句
- 光靠 sqlglot AST 校验（ADR-005）不够 —— 万一 parser 漏判或 prompt injection 绕过
- 需要 DB 层兜底

**Decision**:
双 Postgres 用户：
- `chatbi`（写权限）：只给 seed 脚本 / 迁移脚本用
- `chatbi_readonly`（只读）：Agent 的 SQLExecutor 全用这个连接

`.env.example` 里两套用户都预置，`chat_bi_agent/agents/shared/sql_executor.py` 强制用 readonly 用户连接。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **单账号 + 应用层白名单** | 应用层白名单可被绕过（LLM 生成的 SQL 太多变种）；DB 层是**最后一道墙** |
| **Row-level security (RLS)** | 更细粒度，但配置复杂，且 attribution 场景需要跨行聚合 |
| **DB proxy（如 ProxySQL）** | 引入额外组件，运维成本 |

**Consequences**:
- ✅ Defense in depth：即使 Agent 被 prompt injection，最坏只能读
- ✅ 也能防止 agent 意外 `TRUNCATE`（Agent 写 SQL 时确实撞过 `DELETE FROM fct_transaction`，被 readonly 直接拒绝）
- ⚠️ 需要 seed 时切写用户，agent 运行时切读用户，两套 env 变量要小心不要弄反
- ⚠️ Statement timeout 也需要在 readonly 用户上配（当前 config `db.statement_timeout_ms=10000`）

---

### ADR-011: BIRD-financial 只跑 P1，SQLite 直连 + 独立 NL2SQL prompt

**Status**: Accepted · 2026-07-01

**Context**:
- README 承诺补齐外部公开 benchmark，选 BIRD dev 的 `financial` 子集（106 题、8 表捷克银行数据）跟本项目域同源
- BIRD 数据以 SQLite 分发；gold SQL 是 SQLite 方言
- P1 现网的 SQLGenerator system prompt 深度绑定本项目的银行域枚举（`branch_id` 编码、`customer_tier` 等），原样复用会污染 BIRD 评测

**Decision**:
- **DB 层**：`sqlite3` stdlib 直连 `financial.sqlite`，`mode=ro` 只读；`benchmarks/bird/` 整体 gitignore
- **NL2SQL 层**：为 BIRD 单写一份英文 SQLite-aware system prompt（`src/chat_bi_agent/eval/bird_financial/nl2sql.py`），只复用 `qwen_client.chat` 与 JSON 解析模式，**不复用** P1 的 SQLGenerator
- **Schema 层**：从 BIRD 自带 `dev_tables.json` + `database_description/*.csv` 动态拼英文 schema 段（含 PK/FK/枚举），不复用我们自己的 `schema_docs.yaml`
- **评测层**：EX（行集合等价，浮点整数折叠）+ `dev_tied_append.json` 42 条补丁；SQL Validator 跳过（`sqlglot` 用 `dialect="postgres"` 会拒 SQLite 反引号，executor 天然会兜错）
- **Executor**：`BirdSQLiteExecutor` 用 `mode=ro` URI + 后台线程 `conn.interrupt()` watchdog 兜 30s 超时
- **结果落盘**：`results/bird_financial_<date>.json`，schema 兼容 `scripts/eval_diff.py`；支持 `--resume-from` 断点续跑
- **首轮结果**：`qwen3.7-max-2026-05-20` 上 lean baseline EX=56.60% (60/106)，无一 timeout / syntax / parse 错
- **对照变体（pre-fix）**：加跑一路"**现网 P1 pipeline 原样上 BIRD**"（`scripts/run_bird_financial_p1.py`），执行器换 `BirdSQLiteExecutor` + schema 换 BIRD 8 表，其余 SQLGenerator / SQLValidator / Reflector 一字不改。结果 EX=44.34% (47/106)，**Δ=−12.26 分**。27 条 syntax 错源自 P1 prompt 里的 PG 方言假设（`EXTRACT(YEAR FROM ...)` / `ILIKE` / `DATE 'YYYY-MM-DD'`）在 SQLite 上不成立
- **dialect 参数化（fix）**：SQLGenerator / SQLValidator / Reflector / P1NL2SQLAgent 全部加 `dialect: str = "postgres"` 参数（默认与旧行为一致，向后兼容）；SYSTEM_PROMPT 走双变体（postgres 保留原样，sqlite 换成 STRFTIME / 无 DATE 前缀 / LOWER LIKE 规则）；`SQLErrorClass` 新增 `DIALECT_MISMATCH`，Reflector 在 SYNTAX_ERROR 时正则扫 prev_sql 里 5 个 PG-only / 2 个 SQLite-only 模式，命中就升级分类并给方言特定 hint。运行结果：EX 44.34%→**49.06%** (+4.72)、syntax 错 27→**0**、avg_attempts 1.58→**1.04**、avg_latency 45.4s→**30.1s**、gap 关闭 38%
- **附加发现**：**Reflector 的 DIALECT_MISMATCH 分类实际触发 0 次**——4 次 att=2 都是普通 SYNTAX_ERROR。SYSTEM_PROMPT 里加的方言规则本身足够让 LLM 一次写对，Reflector 兜底是 defence in depth 但在这次评测里没启用。这说明"upstream 修 prompt" 比 "downstream 加 reflect 兜底"效益更高

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **把 BIRD schema 导入 Postgres** | BIRD gold SQL 用 SQLite 方言（反引号、`IIF()`），跨方言重写 gold SQL 会破坏可比性；且 `trans` 表 106 万行导入耗时无收益 |
| **复用 P1 SQLGenerator 全量原样** | 该 prompt 强绑本项目银行域枚举，直接当唯一评测口径会污染 LLM 能力信号。**但作为对照变体单独跑一路**（`scripts/run_bird_financial_p1.py`），拿 lean 与 P1 两个数字的 Δ 反而是有价值的信息（见下面 Consequences 里的 12.26 分口径）|
| **跑 P2 / P3** | BIRD 只有单条 SQL gold，multi-step / attribution 无参考答案，路径不匹配 |
| **跑全量 dev（1534 题）** | 11 个 domain 全跑一次 API 成本 15x，且其余 10 个 domain 与本项目无关；README 只承诺 `financial` |

**Consequences**:
- ✅ 零新依赖（`sqlite3` stdlib）；executor / scorer / nl2sql 全部单测覆盖（31 测试全绿）
- ✅ 有断点续跑：跑到一半 quota 耗尽可 `--resume-from prev.json` 换模型接续
- ✅ 结果 JSON 记录 `dev_json_md5` + `sqlite_md5`，未来 BIRD 版本变化可检测
- ⚠️ EX 是严口径：语义等价但列位序不同 / 多余 NULL 列会算错；BIRD 官方评测同此，暂不做行匹配放宽
- ⚠️ 单模型评测（这次是 qwen3.7-max-2026-05-20），换模型后数字不可直接对比；换模型时把结果 JSON 归档并同时更新 README 表格
- ✅ **加了对照变体，让 benchmark 从"测模型"升级为"测系统"**：lean baseline 56.60% 是能力天花板，P1 pipeline 原样 44.34% 是本项目 stack 的真实跨域表现，Δ=−12.26 分给到"深度域特化 vs 跨域泛化"的定量口径。附加发现：P1 的失分几乎全在 PG 方言假设（`EXTRACT/ILIKE/DATE 'YYYY-MM-DD'`）—— 27 条 syntax 错、Reflector 仅救回 11%——把这个作为将来做"dialect-agnostic prompt 层"的证据线索

---

## 附：ADR 快速索引

| # | 决策 | 状态 |
|---|---|---|
| [ADR-001](#adr-001-llm-选-qwen36-max-preview) | LLM 选 Qwen3.6-max-preview | Accepted |
| [ADR-002](#adr-002-自研函数链编排不引入-agent-框架) | 自研函数链编排 | Accepted |
| [ADR-003](#adr-003-langfuse-v3-self-hosted-做全链路可观测) | Langfuse v3 self-hosted | Accepted |
| [ADR-004](#adr-004-llm-as-judge-qwen-自评-做评分) | LLM-as-judge 评分 | Accepted |
| [ADR-005](#adr-005-sqlglot-做-sql-ast-校验) | sqlglot AST 校验 | Accepted |
| [ADR-006](#adr-006-reflector-单次重试不做多次或树搜索) | Reflector 单次重试 | Accepted |
| [ADR-007](#adr-007-yaml-事件库--传播引擎-构造-p3-ground-truth) | YAML 事件库埋雷 | Accepted |
| [ADR-008](#adr-008-embedding--jieba-做-schema-检索) | Embedding + jieba schema 检索 | Accepted |
| [ADR-009](#adr-009-streamlit-做-web-ui) | Streamlit UI | Accepted |
| [ADR-010](#adr-010-postgresql-双用户隔离-写与读) | PostgreSQL 双用户隔离 | Accepted |
| [ADR-011](#adr-011-bird-financial-只跑-p1sqlite-直连--独立-nl2sql-prompt) | BIRD-financial 只跑 P1 + SQLite 直连 | Accepted |
| [ADR-012](#adr-012-q-sql-few-shot-检索注入-bird-验证净效应-0-同域生产未测) | Q-SQL few-shot 检索注入 | Accepted（默认阈值保守） |
| [ADR-013](#adr-013-语义层-metric-resolver-原型-6-指标模板-llm-抽-spec-fallback-回原-nl2sql) | 语义层 Metric Resolver 原型 | Accepted（2026-08-12 已接线到 P1 主路径，见 Update） |
| [ADR-014](#adr-014-评测集-gold-的可信度修哪些不修哪些以及行数守门) | 评测集 gold 的可信度守门 | Accepted |
| [ADR-015](#adr-015-p2-评分器中文分词修复饱和维度暂留) | P2 评分器中文分词修复 | Accepted（三个饱和维度待决） |
| [ADR-016](#adr-016-p2-rubric-llm-judge补回被删两维的度量能力) | P2 rubric LLM judge | Accepted |

新增 ADR 命名 `ADR-013`、`ADR-014` 继续追加。修改现有决策请把 Status 改为 `Superseded by ADR-XXX` 并保留原文。

---

### ADR-012: Q-SQL few-shot 检索注入，BIRD 验证净效应 ≈ 0，同域生产未测

**Status**: Accepted（功能上线，默认阈值保守）
**Date**: 2026-07-06

**背景**：外部对比（Vanna、WrenAI、DB-GPT）里 RAG-over-Q-SQL 是核心加分项——把历史成功的 (question, SQL) 对灌进向量库、检索最相似的作为 few-shot 注入 SQLGenerator prompt。理论上 BIRD 类 dataset 上带来 5-10 分。

**方案**：
- 新增 `ExamplePool`（JSONL 存储、按 sha1(question||sql)[:12] 去重）+ `ExampleRetriever`（cosine top-k，dialect / tag / exclude_ids / exclude_question_texts 过滤，阈值兜底）。
- `SQLGenerator.generate()` 加 optional `few_shot_examples: list[tuple[str, str]]`，注入到 schema 与 question 之间。
- `P1NL2SQLAgent` 加 `example_retriever` 参数（默认 `None` 完全向后兼容）；run() 里一次检索复用所有 attempt；`retrieved_example_ids` 落 Langfuse metadata + P1AgentResult。
- `bootstrap_example_pool.py` 从 BIRD 1427 条非 financial dev 题灌 SQLite 池——**financial 严格排除，防 dev 集自泄题**。

**BIRD 验证结果**（`qwen3.7-max` 家族）：

| 变体 | EX | 备注 |
|---|---|---|
| few-shot **off**（Jul-2, `qwen3.7-max`） | 49.06% (52/106) | 等价当前 pinned `qwen3.7-max-2026-05-17`（用户判断能力等价） |
| few-shot **@ min_sim=0.55**（Jul-6 morning, 同模型） | 52.83% (56/106) | +3.77 EX vs Jul-2；**但逐题分析 8/8 翻转题 `retrieved_example_ids = []`**——few-shot 未激活，翻转全是模型日间噪声 |
| few-shot @ min_sim=0.4（Jul-6 afternoon, `qwen3.7-max-preview`） | 53.77% (57/106) | **数据被污染**：这次跑跨了模型（preview 分支是独立能力线，同 20 题上 preview 12/20 vs pinned 8/20，边缘显著），不能归因给 threshold |
| 20 题探针 @ 0.4（pinned max，0.55 零命中子集） | 8/20 vs Jul-2 baseline 10/20 | **-2 EX 反向初步信号**——低阈值放行弱相关 example 反向误导的猜想有小样本支持 |

**净效应结论**：
- **BIRD 跨库场景下 few-shot 对准确率净效应 ≈ 0**（0.55 阈值的 +3.77 归因模型噪声；0.4 阈值有 -2 反向初步信号）
- 之前一度报告的"latency -40%（32.3s → 19.2s）"**是伪信号**——preview 那次跑里 3 道题命中 300s agent_exception 超时把平均拉高，去掉离群后 preview 典型延迟 ≈ 17s ≈ pinned 18.2s，与 few-shot / 阈值无关
- 唯一稳定的结论：**BIRD 是 few-shot 最差场景**（跨库 pool，financial 严格排除后语义距离天然大）

**方法学错误 postmortem**（留证据学习）：
1. **跨模型 A/B 未察觉**：Jul-6 早上跑 0.55 时是 `qwen3.7-max`，下午换成 `qwen3.7-max-preview` 再跑 0.4，直接把 Δ 归因给 threshold。**教训**：任何 A/B 之前必须 grep `model:` 字段确认同 model；未来在 result JSON 里额外落 `commit_hash` + `config_hash`。
2. **单次 latency 数字过度解读**：把 32.3s → 19.2s 直接解释成"few-shot 让模型思考更快"，没检查是否有 timeout 离群。**教训**：avg_latency 与 p50/p95 一起看；单次跑 latency 只做趋势不做归因。
3. **过早庆祝 +3.77**：commit message 用了 "P1-on-BIRD 收 +3.77 分"，实际逐题分析否掉。**教训**：commit summary 用"检索注入"这类事实描述，不用"收 X 分"这种未经归因的绩效数字。

**替代方案对比**：

| 方案 | 采纳？ | 理由 |
|---|---|---|
| **不做 few-shot，只等语义层（WrenAI MDL 路线）** | 否 | 语义层是长期方向但工作量大；few-shot 是最便宜先验证的"是否能加"实验 |
| **做 few-shot 但只在同域生产用，BIRD 不做** | 否 | 需要 BIRD 校验实现正确性 + 建立方法学 |
| **做 few-shot 且以 BIRD 提分为目标** | **否**（本次结论） | 跨库场景先验就低，不适合当收益证明；工程做完但不改 default 阈值 |

**默认配置**：`--few-shot-min-sim = 0.7`（同域场景，比 BIRD 跨库那轮用的 0.55 严格，宁缺毋滥；0.55 是本 ADR 实验期的值，代码默认后来改为 0.7 而此处一直没回填，2026-08-15 排查时更正）；`--example-pool` 默认 None（off）。**生产 P1 已 hot-load `data/example_pool_prod.jsonl`**（`min_sim=0.7` 更严，池空时零成本 fallback）——2026-08-13 核对：池内 31 条，retriever 实际生效。

**跟进项**：
- ~~**待做**：给中文银行域构建生产 pool~~ ✅ pool 已建成并接入生产（夜间 cron 从 👍 反馈追加）
- ~~**待做**：结果 JSON schema 加 `commit_hash` + `config_hash` 字段~~ ✅ 已完成，
  见 `run_metadata`。2026-08-13 又补了 `model` / `embed_model`——此前 verify_ab 把
  `model` 列为 CRITICAL 但 payload 根本没这个键，模型漂移守门形同虚设（换模型时一声没吭）
### Update 2026-08-14：同域 few-shot A/B 跑完——**无收益，表面收益全来自泄题**

ADR-012 悬了一个多月的核心问题终于有答案了。34 题同域标尺，三臂同 commit
`43935f0`、同模型 `qwen3.7-max`，verify_ab 两组均退 0：

| 臂 | avg_score | 相对基线 |
|---|---:|---:|
| 无 few-shot | 0.9395 | — |
| few-shot **+ 近似泄题守门** | 0.9348 | **−0.005** |
| few-shot 无守门 | 0.9446 | +0.005 |

**34 题里 29 题三臂完全相同**，只有 5 题有差异且方向不一。

**关键在于「无守门」那 +0.010 的来源**：逐题看只有 mr_n06（+0.100）与
mr_n14（+0.300）在无守门时变好，合计 +0.400/34 ≈ +0.012，几乎等于观测到的差值。
而这两题正是池内有近似副本的题。**泄题贡献了全部"收益"；守门一开，
few-shot 净效应归零（−0.005，在噪声内）。**

**先修守门再跑是必要的**：不修的话会拿着 +0.005 得出"同域 few-shot 有效"的
错误结论，而这正是 ADR-012 要回答的问题。

**泄题为什么必然发生**：pool 从早期 P1 gold 样例 bootstrap，而评测集覆盖同样的
业务面——两边同源，重合是结构性的，不是谁写错了。原 leave-one-out 只挡精确文本，
34 题里 8 道有 cosine>0.85 的近似副本，只有 1 道逐字相同能被挡住。
守门做成**可选、默认关**：生产环境里"历史相似问题的 Q-SQL"正是 few-shot 的
价值所在，只有评测时它才是泄题。

**结论与决策**：
- 池 31 条规模下，同域 few-shot **没有可测量的收益**
- 生产 P1 仍挂着 retriever（`min_sim=0.7`）。**不建议据此关闭**——负结果是
  "在 31 条池 + 这 34 题上测不出收益"，不是"有害"；成本也极低（池空即零成本 fallback）
- 真正该做的是**让池长大**：31 条里能匹配上评测题的本就不多，
  再攒一批真实使用样本后重测

**顺带修掉的基础设施缺口**：per_question 此前根本没记 `retrieved_example_ids`，
导致我一度误判"few-shot 完全没生效"。产物里看不到一个特性有没有真的启用，
结论就无从复核——现在落 `n_few_shot_used` 与 `n_questions_with_examples`。
- **不做**：不在 BIRD 上继续调阈值——BIRD pool 是跨库先天劣势，再调也是在 noise floor 里打转

**Trace**：
- 代码：commit `780294c`（feat: Q-SQL few-shot 检索注入）
- 数据：`results/bird_financial_p1_fewshot_2026-07-06.json`（0.55, max）+ `bird_financial_p1_fewshot_sim04_2026-07-06.json`（0.4, preview 污染）+ 两份 20 题探针
- 讨论：本 ADR 完整覆盖

---

### ADR-013: 语义层 Metric Resolver 原型（6 指标模板，LLM 抽 spec，fallback 回原 NL2SQL）

**Status**: Accepted（原型阶段，未接线到 P1 主路径）
**Date**: 2026-07-08

**背景**：dbt 2026 benchmark 显示语义层比裸 text-to-SQL 高 10-14 分（GPT-5.3 Codex 从 84.1% → 100%）；WrenAI 全栈押注这条路线。ADR-012 postmortem 里 few-shot 净效应 ≈ 0 也验证了另一个方向：**改 prompt/RAG 是隔靴搔痒，真正的杠杆在"先收窄问题空间"**。原型目标：定义几个核心银行指标模板，把 NL 问题降维成 `{metric, dims, filters, time_window}`，走 governed SQL 路径。

**方案**：
- **[`config/metrics.yaml`](../config/metrics.yaml)** — 6 个种子指标：`deposit_balance` / `loan_balance` / `customer_aum` / `customer_count` / `product_count` / `transaction_amount`。每个指标声明 fact_table + fact_alias、metric_expr（`AVG(fbd.balance)` 之类）、hard_filters（永远 AND，护业务口径）、date_column、joins（join_id → SQL）、dim_catalog、filter_catalog（含 enum_values 严格校验）。
- **[`src/chat_bi_agent/agents/p1/metric_resolver.py`](../src/chat_bi_agent/agents/p1/metric_resolver.py)**：
  - `MetricCatalog.from_yaml()` 加载
  - `_build_extractor_prompt(catalog)` 生成 system prompt，把可用指标 / dims / filters / enum 全枚举给 LLM
  - `resolve(question, catalog)` 端到端：LLM → `MetricSpec` → `render_sql_from_spec` 拼 SQL
  - `render_sql_from_spec` 严格校验：enum 值必须命中、dim/filter 名必须存在、join 自动收集去重、time_window 仅当 date_column 存在时生效

**Smoke 结果**（4 个问题，qwen3.7-max）：

| 问题 | 结果 |
|---|---|
| "查询上海分行的高净值客户 ID/姓名/等级"（list 查询） | ✅ LLM 返 `metric_id=null` → 抛 MetricResolverError → **上游 fallback 到原 NL2SQL** |
| "统计有多少个产品分类为理财且风险等级为高" | ✅ metric=product_count、`WHERE product_category='WEALTH' AND risk_level='R5'`——**中文口语精准归一化**到英文 enum |
| "杭州（BR_CITY_0000）和南京（BR_CITY_0002）大众客户数量" | ⚠️ metric=customer_count 对，customer_tier='MASS' 对，但**丢了 branch_id IN 过滤**——目前 `op` 只支持 `=`，多值场景不支持 |
| "上海分行 2026 年 5 月高净值客户存款余额" | ✅ metric=deposit_balance、joins 自动带 branch+customer、time_window 从中文月份精确到 5-01/5-31、customer_tier=HIGH_NET_WORTH |

**为什么原型不改 SQLGenerator 主路径**：
- Smoke 上 1/4 case 有已知限制（多值 IN），改主路径前应先修
- ADR-012 明写 "不动 P1 生产 agent 默认"——同样克制原则，原型先跑出来看效果
- 接线到 P1 SQLGenerator 的 fallback 逻辑（先 resolve，失败走原 NL2SQL）是**下一个 commit**

**替代方案对比**：

| 方案 | 采纳？ | 理由 |
|---|---|---|
| **不做 metric 层，只调 few-shot 阈值/pool** | 否 | ADR-012 已证明 few-shot 在跨域 ≈ 0 净效应；同域效果预估也有限（+2~5），语义层估算 +5~10 |
| **仿 dbt semantic layer（YAML 定义 measure + entity + dimension）** | 否 | 学习曲线陡；先用最小可用形态（metric_expr + dim_catalog + filter_catalog）验证方向 |
| **仿 WrenAI MDL（modeling definition language 完整数据契约）** | 未来 | MDL 是完整解决方案，工程量大；当前原型只做"metric 优先"这条最有价值切片 |
| **完全走 SQL template，不用 LLM 抽 spec** | 否 | 无法处理 NL 变体；LLM 抽 spec 是"降维"环节，不可省 |

**跟进项**：
- **P1**：`op='IN'` 多值过滤支持，覆盖 smoke Q3 的 case
- **P1**：接线到 SQLGenerator——先 `try resolve()`，`MetricResolverError` 就走原 `generate()`；`P1AgentResult.metric_id` 字段记录本次是否命中，供 Langfuse 分析命中率
- **P2**：Metric 命中 vs 未命中的 A/B（跑 P1 6 gold + BIRD 或未来同域 30+ 题集）
- **P2**：扩指标目录（loan 相关、campaign_response 相关、fct_risk_event 相关；总量 15-20 个即覆盖 80% 高频问题）
- **P3**：加"metric 追问"的 UX（Streamlit 里显示"识别到指标 X，维度 Y"让用户可以点确认/修正）
- **P3**：Langfuse 里加 metric_hit_rate 看板——生产化上线的 GA 门槛

**默认配置**：**默认不启用**——原型独立可跑（`from chat_bi_agent.agents.p1.metric_resolver import resolve`），但 P1 主路径未挂。等 IN 支持 + 3-5 个指标扩容后再启用。

**Trace**：
- 代码：本 commit
- 数据：`config/metrics.yaml`（6 metrics）+ 17 单元测试全绿 + 4-题真 Qwen smoke（3 命中 + 1 fallback）
- 讨论：本 ADR 完整覆盖

### Update 2026-08-12：接线完成 + A/B 数字

**变更概览**：
- 新增 `MetricRouter` 类（`RouteResult` dataclass + `try_route()` never raises）
- `P1NL2SQLAgent.__init__` 加 `metric_router: MetricRouter | None = None` 参数
- `run()` 在 SchemaLinker 之前 try_route；命中且模板 SQL 跑通直接返（不进 Reflect Loop）
- `P1AgentResult` 加 5 字段：`route / metric_id / prefilter_cosine / metric_spec / metric_fail_reason`
- `render_sql_from_spec` 加 `op='IN'` 支持（enum/string/numeric 三类校验；空 list 与非 list val 抛 `unsupported_op` / 类型错）
- `run_p1_eval.py` 加 `--metric-catalog` / `--metric-prefilter-threshold`；`results/*.json` 顶层 `metric_router` 汇总段
- Langfuse trace metadata 加 `route / metric_id / prefilter_cosine / metric_fail_reason`；batch trace 打 `arm:baseline` / `arm:metric_router` tag 区分实验臂

**接线时发现并修掉的 catalog 定义错**（集成 smoke 打真 Postgres 暴露）：

原型阶段的 4-题 smoke 只覆盖了 3 个命中 case，`config/metrics.yaml` 里有 3 处定义
从未被真实执行过，全是必挂的错：

| 问题 | 影响 | 修法 |
|---|---|---|
| `fbd.account_type` 列不存在（在 `dim_account` 上） | `deposit_balance` + `loan_balance` 100% executor_fail | 加 `account` join；因 `hard_filters` 无法声明 `requires_join`，给 `Metric` 新增 `hard_filter_joins` 字段，无条件拼进 FROM |
| `ft.channel` 列不存在（实际 `transaction_channel`） | `transaction_amount` 的 channel 维度/过滤全废 | 改列名 |
| `dc.is_active` 是 boolean 却声明成 `string` | 拼出 `= 'True'`，PG 类型错 | 新增 `boolean` filter 类型，渲染 TRUE/FALSE |

`dim_account` 与 `fct_balance_daily` 是多对一，join 不放大行数（实测 449 行 join 后仍 449 行），
AVG 口径不变。回归验证：6 metric × 全部 dim/filter 共 48 个组合真打 PG，改前 18 broken → 改后 0 broken。

**教训**：原型阶段"单测全绿 + 少量 smoke"不足以证明 catalog 正确——模板里的列名只有真正
execute 才会被校验。catalog 类改动必须配一个「全组合真打 DB」的回归扫描。

**首轮 A/B 数字**（2026-08-13，**旧的** 6 题 happy path，同 commit `2f60c1d`）：

> 这套题后来被判定为不合格的标尺并已替换，数字保留作为过程记录。
> 现行结论见下方「第二轮 A/B（34 题新标尺）」。


| 指标 | Baseline | Router t=0.70 | Router t=0.57 |
|---|---:|---:|---:|
| avg_score | 0.908 | 0.908 | 0.908 |
| passed | 6/6 | 6/6 | 6/6 |
| metric_hit_rate | — | 0.000 | 0.333 |
| prefilter_hit_rate | — | 0.000 | 0.667 |
| precision_when_hit | — | — | **1.000** |
| precision_when_fallback | — | — | 0.850 |
| precision_when_bypass | — | 0.908 | 0.875 |
| fallback_rate | — | — | 0.500 |

**fail_reason_breakdown**（t=0.57）：2 no_metric，其余全 0

**逐题得分三臂完全相同**（0.850 / 1.000 / 0.850 / 0.850 / 1.000 / 0.900）——
t=0.57 把 q002、q006 走了 governed 模板路径，答案与 NL2SQL 一字不差。

**判定结果：条件绿灯**

判定表两条硬指标都过了：`precision_when_hit` 1.000 ≥ baseline 同题 1.000，
`metric_hit_rate` 0.333 ≥ 0.30。但**这把尺子本身不可信**，不足以据此放开默认阈值：

- 0.333 正好是这套题的天花板。用 resolver 逐题验证过，6 题里只有 q002
  （product_count）和 q006（customer_count）是真指标型问题，另外 4 题 LLM 都
  正确返回 `metric_id=null`。**即使 prefilter 完美，命中率上限也只有 2/6**——
  绿灯线 0.30 在这套题上几乎没有区分度
- n=6，且 baseline 自身有噪声（同一 commit 连跑三次 avg 在 0.908–0.925 之间飘，
  q007 在 0.900/1.000 之间跳）
- 命中样本只有 2 个，撑不起"语义层不会答错"的结论

**延迟：不下结论**。三臂 avg 差异（19.1s / 7.6s / 9.4s）几乎全由 baseline 的
单题离群值撑起（q003 一次 84s）。看命中题本身，governed 路径反而更慢
（q002 13.6s vs 4.0s，q006 6.9s vs 6.0s）——它省掉了 SchemaLinker + SQLGenerator，
但换来 prefilter embedding + spec 抽取两次调用，净额并不省。n=6 且方差极大，
任何延迟结论都不成立。

- **t=0.70（当时默认，后已改为 0.63）：0 命中**。6 题 cosine 全落在 0.48–0.65，最高 0.6475 < 0.70。
  路由层等于没启用——`precision_when_bypass` 与 baseline 逐题完全相同，
  向后兼容得到实证，但也没产生任何收益。
- **t=0.57：命中率 0.333，正好是这套题的天花板**。用 resolver 逐题验证过，
  6 题里只有 q002（product_count）和 q006（customer_count）是真正的指标型问题，
  另外 4 题 LLM 都正确返回 `metric_id=null`（明细/事件流查询，或指标不在目录里）。
  **即使 prefilter 完美，命中率上限也只有 2/6 = 0.333**——绿灯线定的 0.30 在这套
  题上几乎没有区分度，说明**这 6 题不是衡量语义层的合适标尺**。

**已修掉的评分器伪影**（首轮 A/B 曾让 `precision_when_hit` 假跌到 0.925）：

首轮 q006 的 metric 路径得分 0.850 vs baseline 1.000，但两条 SQL 返回的结果集
完全相同（`BR_CITY_0000: 58` / `BR_CITY_0002: 59`，仅行序不同）。拆开评分维度，
六项里五项一模一样，只有 `column_score` 是 0.0 vs 1.0。

成因：`render_sql_from_spec` 对每个 dim 都无条件输出 `dc.branch_id AS branch_id`，
而 `PrecisionRetrievalEvaluator` 会剥掉带 `AS` 的列以还原"真实 schema 列"——
模板把所有列都别名化，剥完是空集，Jaccard 归零。
**gold SQL 里 `branch_id` 恰好不带别名**，NL2SQL 因此天然占优。

修法选了改模板而非改评分器：冗余别名本身就是噪音，而动尺子有"改评分标准让自家
指标好看"的嫌疑。现在只在「是裸列引用」且「别名 == 裸列名」时省略 `AS`，
聚合表达式与真正重命名的 dim 一律保留。修完 q006 回到 1.000，返回行不变。

**教训**：跨路径比较前先确认尺子对两条路径中立。这个伪影差点把一个无回归的
特性判成回归——真去"优化"语义层反而会走错方向。

**接线过程中修掉的两个必挂 bug**（都是单测全绿、真跑才暴露的）：

1. `qwen_client.embed` 未分批 → DashScope 单次上限 10 条，MetricRouter 要 embed
   32 条 alias，treatment 轮**起步即崩**。修在客户端而非调用方：
   `SchemaLoader.build_index()` 正好卡在 10 条表文档上，再加一张表主路径同样会炸。
2. 抽取 prompt 与渲染能力脱节 → Task 1 加了 `op='IN'` 的**渲染**，但 prompt 仍写着
   "op 目前只支持 '='"。LLM 被明确告知不能用 IN，遇到"杭州和南京两个分行"
   直接把约束丢掉，改成按全部城市分组。q006 因此掉到 0.433。
   **这类失败最危险：SQL 合法、validator 过、executor 过、返回一堆行，只是答的是
   另一个问题**——现有 guardrail 一个都拦不住。修完 q006 回到 0.850（差额即上述伪影）。

**教训**：语义层的 guardrail 只覆盖"结构合法性"（enum 值域、列存在、join 完整），
覆盖不了"语义忠实度"（约束有没有被悄悄丢掉、值有没有塞错列）。string 类型 filter
尤其危险——没有 `enum_values` 可校验，值传错不报错，只静默返回 0 行。

### Update 2026-08-13：跟进项四项落地 + 第二轮 A/B

首轮 A/B 的四条「下一步」全部执行完毕。

**1. 指标目录 6 → 18**（原 P2 项）

补齐四个此前完全未覆盖的域：持仓（`fct_holding`）、风险（`fct_risk_event`）、
营销（`fct_campaign_response`）、交易笔数/笔均。enum 值域全部从真库
`SELECT DISTINCT` 取，不是手写猜的。全组合回归从 48 涨到 145 个组合，真打 PG 全绿。

**2. string filter 值域探针**——补上语义层最后一个无防护失败面

`enum` 有 `enum_values` 兜底，`numeric`/`boolean` 值域无穷，`time_window` 为空是
业务事实——只有 `string` filter 完全没防护。做法：`MetricRouter` 加可选 `probe_fn`
（与 `embed_fn` 同样的注入风格），resolve 成功后对 string filter 跑一条
`SELECT 1 FROM ... WHERE <谓词> LIMIT 1`，查不到行就判 `value_out_of_domain` 退回 NL2SQL。

探针刻意不带 `time_window` 与 `hard_filters`：那两者为空是业务事实不是抽取错误，
只回答"这个值在这一列里存在吗"。

**这个守门在第二轮 A/B 里真的救了一次**：题目问"理财产品推荐**活动**"，库里真实
`campaign_name` 是"理财产品推荐"（无"活动"二字），LLM 抽出了不存在的值。没有探针
它会静默返回 `SUM(...)` 空集 = NULL。代价是损失一次召回——这正是设计意图。

**3. 排序/取顶类问题一律拒绝**

第二轮 A/B 暴露的新漏洞：问"存款余额最高的前 5 个分行"，LLM 映射到
`deposit_balance` 却丢掉 Top-5，返回全部分行。SQL 合法、值域也对、
validator/executor 全过，只是答的是另一个问题——与当初丢掉 IN 约束同一类失败。

`MetricSpec` 结构里根本没有 ORDER BY / LIMIT 字段，这类问题必须拒绝而非硬凑。
prompt 加规则：涉及「最高/最低」「前 N 个」「排名」「Top」一律返回 `metric_id=null`。

**注意评分器同样看不全这类错误**：mr_n12 被误路由后只扣了 0.075（表/过滤/聚合都对），
分数根本不足以把它暴露出来——是逐题看 SQL 才发现的。

**4. 换标尺**：新建 `src/chat_bi_agent/data/metric_routing_evaluation.yaml`

34 题，指标型 20 题（58.8%），非指标对照 14 题。相比旧的 6 题（指标型仅 2 题、
命中率天花板 0.333），这套题才有区分度。

关键设计——**`expected_route` 作为 ground truth**。只统计命中率会掩盖一半的问题：
"触发了多少次"不等于"触发得对不对"。有了标注才能算 precision/recall，
`payload.metric_router.routing_accuracy` 给出 TP/FP/FN/TN。

防"照着目录写题"的自证陷阱：题面按业务提问方式写，尽量不逐字复用 catalog alias；
非指标题分三类且都不是凑数——明细查询 5 题、目录表达不了的聚合 5 题（占比/argmax/
Top-N/未建模的列）、多步分析 4 题（跨时间窗、跨事实表、开放式）。
`expected_result_count` 由 gold SQL 真打 PG 回填。

**阈值不能取 argmax——那是过拟合**

在 34 题上扫阈值，最优 F1 出现在 0.5919（F1 0.783）。但做 200 次随机半分交叉验证：

| | 值 |
|---|---:|
| 选出的阈值中位数 | 0.5919 |
| 阈值选择范围 | 0.5512 – 0.7140（σ=0.034） |
| 样本内 F1 | 0.804 |
| **样本外 F1** | **0.708** |

样本外掉 0.096，且阈值本身在半个区间内飘——**取 argmax 就是在拟合这 34 题**。
F1 在 0.59–0.63 之间是平的（0.766–0.783），故取 **0.63**：在平台区内、比中位数保守、
且不是精确 argmax。

（离线算的 cosine 与实跑记录最大偏差 0.000218，验证了"只 embed 问题选阈值"这条
省钱路径可信——不必为调阈值跑整轮 eval。）

**第二轮 A/B 结果**（34 题新标尺，同 commit `5da64da`，模型 `qwen3.7-max`，
verify_ab 两组均退 0）：

| 指标 | Baseline | Router t=0.70 | Router t=0.63 |
|---|---:|---:|---:|
| avg_score | 0.9346 | 0.9370 | **0.9539** |
| passed | 31/34 | 31/34 | **32/34** |
| metric_hit_rate | — | 0.2647 | **0.4412** |
| precision_when_hit | — | 1.000 | **1.000** |
| 路由 precision | — | 1.000 | **1.000** |
| 路由 recall | — | 0.45 | **0.75** |
| 路由 F1 | — | 0.6207 | **0.8571** |
| 路由 false_positive | — | 0 | **0** |
| fallback_rate | — | 0.10 | 0.2857 |

**判定：绿灯，默认阈值改为 0.63**（`5da64da` → 本次提交）

t=0.63 相对 t=0.70 严格占优：precision 同为满分（零假阳性路由），recall 从 0.45
提到 0.75。**走语义层的 15 题里 2 题更好、13 题持平、0 题更差**——
更好的两题是 mr_m03（+0.217）与 mr_m15（+0.250），governed 模板赢过 LLM 手写 SQL。

这也实测确认了 Top-N 拒绝规则（`a9c3b83`）的价值：修复前 t=0.63 有 1 个假阳性
（mr_n12 那道 Top-5 题），precision 0.9375 / F1 0.8333；修复后假阳性归零，
precision 1.000 / F1 0.8571——与修复时的推算数字分毫不差。

`value_out_of_domain` 探针在本轮再次触发（仍是"理财产品推荐活动"那个不存在的
`campaign_name`）。两轮都命中，说明这类失败稳定存在，不是偶发。

**订正一个此前写错的判定标准**

早前 README 写过「`precision_when_bypass` ≠ baseline 是不回归的硬底线」。
**这个等式在非确定性 LLM 下不可能成立**：本轮 t=0.63 的 13 道 bypass 题里有 3 道
得分与 baseline 不同（-0.250 / +0.067 / +0.400），而这些题两臂走的是**完全相同的
nl2sql 代码路径**——纯粹是跑间噪声。

真正该看的是：
1. **单题 LLM 噪声可达 ±0.4**，所以 avg_score 上零点几个百分点的差异（如 0.9346
   vs 0.9370）没有意义，不能据此下结论
2. bypass 题只应有**无系统性偏向的随机漂移**；若出现单向系统性变化才是红旗
3. 结论要建立在**逐题、且走 governed 路径**的比较上——模板 SQL 是确定性的
   （同 spec → 同 SQL → 同分），本身就消除了这部分方差。这也是语义层的一项
   附带收益：把一部分查询从"每次跑分都在抖"变成可复现

**模型口径提醒**：本轮用 `qwen3.7-max`，与 2026-08-13 更早那轮（另一模型）
**不可跨表比较**。此前 P1 payload 根本没落 `model` 字段，verify_ab 的模型漂移
守门形同虚设，换模型时一声没吭——已在 `5da64da` 修好。

**下一步**（按优先级）：
1. **换标尺**：6 题里只有 2 题是指标型，无法衡量语义层。需要一套指标型问题占比
   ≥ 50% 的评测集，否则命中率这个指标没有意义
2. ~~默认阈值~~ ✅ 已改为 0.63（见上方第二轮 A/B）
3. ~~扩指标目录到 15-20 个~~ ✅ 已扩到 18
4. ~~补语义忠实度守门~~ ✅ string filter 值域探针已上线，两轮 A/B 各触发一次

**新的下一步**：
1. ~~评分器看不见语义不忠实~~ ✅ 已加 `result_match` 结果集比对维度。
   刻意**不计入 combined_score**——现有权重和为 1.0，加进去会改变所有历史分数、
   废掉 baseline 可比性，而重建基线要花钱重跑。它作为诊断字段存在，
   `payload.result_match.mismatched_ids` 直接点名哪几题"答的是另一个问题"。
   实测回放 mr_n12：combined_score 0.708（看不出问题），result_match=False（判假）。
   **是否并入总分是一个需要重建基线的独立决定**，留待将来。
2. ~~阈值随模型漂移~~ ✅ 已固化为 `scripts/sweep_prefilter_threshold.py`。
   换 embedding 模型后必须重跑；只花 embedding 的钱（几十秒），不必跑整轮 eval
   （离线 cosine 与实跑记录偏差 0.000218）。脚本同时输出交叉验证的过拟合幅度，
   并提示"表里的 FP 是 prefilter 误触，不等于错误路由"——实测 t=0.63 下
   prefilter FP=5 但真正的路由 FP=0，误触会被 resolve 与值域探针拦下。
3. **扩题量（暂缓，成本考虑）**：34 题里指标型 20 题，`routing_accuracy` 的分母偏小
   （recall 0.75 = 15/20，少一题就跳 5 个点）。目标 60+ 题，待预算允许再做

**跟进项闭环**：
- P1 ✅ `op='IN'` 支持完成
- P1 ✅ 接线到 P1 主路径完成（前置路由而非 SQLGenerator 内部 try/except）
- P2 ✅ 指标目录扩容完成——已到 18 个，覆盖 holding/risk/campaign/transaction 四个新域
- P3 ✅ Streamlit "识别到 metric=X" UX 完成——P1 tab 命中时显示业务名 +
  expander 摊开语义层的理解（指标/维度/过滤/时间窗），让"识别错了"能当场被发现
- P3 保留：Langfuse 看板 `metric_hit_rate` 图。**注意："数据已就绪、只差建图"是错的**
  （2026-08-14 实测纠正）：Langfuse 的 metrics 聚合层**不支持按 metadata 分组**，
  按 `metadata.route` 查直接 400——合法维度只有
  `id / name / tags / userId / sessionId / release / version / environment / timestampMonth`。
  `route` 存在 metadata 里，所以以原埋点方式**根本画不出来**。
  已修：生产 P1 tab 额外把 route 打成 tag（`route:metric` 等），tags 是可聚合维度。
  埋点必须现在补——**看板和告警什么时候建成本都一样，但今天没打 tag 的 trace
  永远补不回可聚合性**，这是唯一有时间不对称的部分。
  建图本身仍保留：`metric_hit_rate` 早已在 eval payload 的 `payload.metric_router` 里，
  看板的增量价值是"看生产流量上的表现"，而目前生产流量为零
  （428 条 P1 trace 全部来自开发/评测），故不急。
- **不做（2026-08-14 判定）**：Langfuse 侧的"P1 通过率跌破 90%"告警。两个原因：
  ①**生产环境没有通过率**——通过率的定义是 `combined_score ≥ 0.7` 对着 gold SQL 打分，
  生产没有 gold；Langfuse 里现存 score 仅 26 条且全是 `user_feedback`（👍/👎，
  n 小且有自选择偏差）。剩下能算的只有执行失败率 / `give_up` 率 / 回退率，
  它们测的是"跑通了没有"而不是"答得对不对"——**语法正确、能执行、答非所问，
  在这类指标里跟成功长得一模一样**，装了比不装更危险。
  ②**本项目真实发生过的失效全是静默回归**（`op='IN'` prompt 与 renderer 不一致、
  `request_timeout` 参数名写错、payload 缺 `model` 导致 verify_ab 的 CRITICAL 检查空转），
  没有一条会被观测告警抓到——它们归属 CI/runner 门禁，而那个门禁已经有了（`verify_ab`）。
  等有真实流量再重新评估。
- **新增保留**：P2/P3 tab 尚未接语义层。它们内部调 P1，接了各自的 eval 基线要重建，
  按成本考虑暂缓——目前只有 P1 tab 享受到语义层

**Trace**：代码见 PR #6（已合入 main）+ 后续 Streamlit 接线；spec 与 plan 在
`docs/superpowers/`（gitignored，本地保留）

### Update 2026-08-14：两项扩展性改造（全局 join 注册表 + 候选裁剪）

前两轮 A/B 都在问"语义层准不准"。这次问的是另一个问题：**这套 YAML 扩到生产规模
还成立吗**。18 个指标是原型规模，真实银行的指标目录是几百条量级。审视下来有两处
会先撞墙，都不是准确率问题，是结构问题。

**1. join 定义重复 → 全局 join 注册表**

改前 18 个 metric 里内联了 26 条 join 子句，而它们**反解后只有 4 个模板**——
`dim_account` / `dim_branch` / `dim_customer` / `dim_product` 各一条，零不规则。
`deposit_balance` 与 `loan_balance` 的 joins 块几乎逐字相同。改一次 `dim_branch`
的 join 条件要扫全库，这在 300 条指标时是维护灾难。

做法是把 join 提到 metric 之上，`{fact}` 占位该 metric 的 fact_alias：

```yaml
joins:
  branch: "JOIN dim_branch dbr ON {fact}.branch_id = dbr.branch_id"
```

`MetricCatalog.from_yaml` 加载时替换占位符，写进各 metric 的有效 join 表；
`render_sql_from_spec` 一行没动。YAML 净减 34 行，26 条 join 收敛成 4 条。

两个设计要点：

- **本地覆盖全局**：metric 里同名 `joins` 优先级最高。当前生产 YAML 用不上，
  但真实银行一定有不规则 join（历史遗留外键、桥接表），没有逃生舱这个抽象会在
  第一个反例上崩掉，逼人退回全内联。
- **自连保护**：被 join 表的别名撞上 fact_alias 时该条自动失效。这不是假想问题——
  `customer_aum` 的 fact 表就是 `dim_customer`（别名 `dc`），不挡的话全局 customer
  join 会拼出 `ON dc.customer_id = dc.customer_id`。`account_count`、`branch_count`
  同理。这三个 metric 实测都正确跳过。

**迁移是无损的，且验证过**：迁移前先反解确认 26 条 join 收敛到 4 个模板（有任何
一条不规则就不该做全局化）；迁移后逐条比对，**26 条先前声明的 join 全部逐字符渲染
一致**。全局化后每个 metric "可用" 的 join 变多了，但 join 只有被 dim/filter 的
`requires_join` 点名才会进 SQL，那些声明一行没改——所以输出 SQL 完全不变。

**2. prompt 随目录线性膨胀 → top-k 候选裁剪**

更硬的瓶颈。`_build_extractor_prompt` 原本把**整个 catalog** 枚举进 system prompt，
每个指标约 272 字符。18 个指标 6,380 字符还能接受，300 个就是 ~83,000 字符，
每次查询都付一遍。而且候选越多、语义相近的指标互相干扰越强。

讽刺的是**解药早就算出来了却扔掉了**：`MetricRouter.try_route` 用 embedding 算出
最相似的 metric，但只拿它当阈值 gate，随后仍把完整 catalog 传给 resolver。
现在改成按 metric 聚合 cosine（一个指标多条 alias 只占一个候选位）、取 top-k
写进 prompt：

| | 18 个指标 | 扩到 300 个 |
|---|---:|---:|
| 全量 prompt | 6,380 chars | ~83,000 chars |
| top-8 prompt | 3,713 chars | **3,713 chars** |

**prompt 大小从此与目录规模解耦**——这才是重点，58% 那个眼前收益是次要的。
阈值 gate 语义不变，仍是 top-1 cosine vs 0.63；裁剪只影响 prompt 里描述哪些指标，
SQL 仍按完整 catalog 渲染。`k=8` 为默认值，`--metric-top-k` 可覆盖。

**为什么这需要一轮 A/B 而不是直接合**：裁剪改变了喂给 LLM 的上下文，18 个指标下
有 10 个被裁掉，不是空操作。两个方向都可能：候选变少减少干扰（提分），或召回不足
把正确指标裁出去（掉分）。这是实证问题。

**A/B 结果**（34 题标尺，few-shot off，t=0.63，唯一变量 `--metric-top-k`）：

| 指标 | top_k=99（旧全量） | top_k=8（新默认） |
|---|---:|---:|
| avg_score | 0.9564 | 0.9436 |
| passed | 32/34 | 32/34 |
| metric_hit_rate | 0.4412 | **0.4412** |
| n_route_metric | 15 | **15** |
| 路由 TP/FP/FN/TN | 15/0/5/14 | **15/0/5/14** |
| 路由 precision / recall / F1 | 1.000 / 0.75 / 0.8571 | **1.000 / 0.75 / 0.8571** |
| precision_when_hit | 1.000 | 0.9833 |
| precision_when_bypass | 0.9628 | 0.9500 |
| latency avg | 20,917 ms | 23,554 ms |

`verify_ab.py --expected-differ metric_router` 退 0（同 commit、同 config_hash、
同 model，可归因）。

**结论：裁剪对路由行为零影响，合入。**

**路由层完全没动**：同样 15 题走 governed 路径、`metric_id` 抽取逐题相同、
TP/FP/FN/TN 与 `fail_reason_breakdown`（5 个 no_metric + 1 个 value_out_of_domain）
分毫不差。被裁掉的 10 个指标里没有一个是本该被选中的。

avg_score 差的 −0.0128 是噪声，不是回归。全部 34 题里只有 4 题分数变了，其中
3 题（mr_n11 +0.133、mr_n13 −0.017、mr_n14 −0.300）根本不走 governed 路径——
两臂跑的是完全相同的 nl2sql 代码，纯跑间抖动。这与上一轮记录的现象一致
（`precision_when_bypass` 两臂也不同，同理）。

**唯一走 governed 路径却变了的 mr_m06，经重复采样证伪**。它的 `metric_id` 两臂
相同（`customer_count`），差异只在 dims：`branch_id`（对，−0）vs `branch_city`
（行数同为 2 但列不符，−0.25）。governed 路径 spec→SQL 是确定性的，但
question→spec 仍是 LLM 调用，所以这里有噪声空间。同题各跑 6 次：

| | dims 分布 |
|---|---|
| full(k=99) | `branch_id` × 6 |
| k=8 | `branch_id` × 6 |

**12/12 全部产出正确的 `branch_id`，那次 `branch_city` 一次都没复现**——是单次
LLM 抖动，与裁剪无关。

**k=8 的安全余量比预想大得多**。对 15 道成功路由的题算正确指标在 embedding
召回里的排名：

| 排名 | 题数 |
|---:|---:|
| 1 | 13 |
| 2 | 2 |

**最深只到第 2 名**，连 k=3 都能全覆盖 15/15。这也解释了为什么裁剪毫无影响：
k=8 相对实际需要有 4 倍余量。真实语义相近的指标（`deposit_balance` vs
`total_balance`、`transaction_count` vs `transaction_amount`）靠 alias 就已经把
正确项顶到前两名，不需要靠"多塞候选"来兜底。

**延迟不下结论**：+2.6s 的差异落在 p95 60s→70s 这种量级的方差里，n=34 撑不起
结论。理论上裁剪应当略微更快（prompt 短 42%），但抽取调用只是端到端的一小段。

**这一轮暴露并已修的产物缺陷**：`results/*.json` 的 `metric_router` 段原本不记录
`top_k`，只看产物无法判断某份结果属于哪一臂——与 `e7784a5` 修掉的 few-shot 用量
记录是同一类问题（配置不落盘 = 结果不可归因）。已补 `top_k` 字段；
**上面这两份结果文件早于该字段，靠文件名区分**。

**Trace**：代码见本次提交；测试 `tests/p1/test_metric_resolver.py`（全局 join 6 例）
+ `tests/p1/test_metric_router.py`（候选裁剪 6 例）+ `tests/eval/`（CLI 透传 2 例）

---

### ADR-014: 评测集 gold 的可信度——修哪些、不修哪些，以及行数守门

**Status**：Accepted（2026-08-14）

#### Context

起因是一个简单的问题：README 头条的「P1 6 题 / 1.000」还成立吗。查下来不成立，
而且失效方式全都是**静默的**——不报错、不告警，只是分数悄悄变低或题目悄悄不跑。

**失效一：gold 行数与种子数据脱节。** q001/q003/q004 的 `expected_result_count`
还是初版种子数据的值（2/49/1），reseed 后真实行数是 29/674/88。`result_count` 在
`combined_score` 里占 0.15 权重，于是**生成的 SQL 逐字符正确也被扣 0.15**。对比
2026-06-06 baseline 与 08-13 重跑可以确认：这三题的 SQL 完全相同，分数 1.0 → 0.85。
漂移本身是 06-06 至 06-18 之间 `dimension_generator` 改了 7 次（`35a1007` 等）
改变 RNG 消费顺序、随后 reseed 造成的，一次性历史事件，但没有任何东西会报出来。

**失效二：两道题的 gold 比 agent 更错。** `run_p1_eval` 里有一份硬编码白名单
`HAPPY_PATH_IDS` 只跑 8 题里的 6 题，排除 q005/q008。跑开来才发现：

- **q005**：余额是 stock 指标，原 gold 用 `EXTRACT(MONTH)=2` 不钉时点，把 2 月
  28 天的日快照相加得 137,120,000——是月末真值 5,000,000 的 **27.4 倍**，算出来的
  是「账户·天」而不是余额。
- **q008**：题面明写「定期存款」，原 gold 却没有 `account_type` 过滤（446 个账户
  全进，加 SAVING 后 111 个）。agent 过滤对了反被判 `result_match=False`。

**失效三是前两条的成因。** 白名单把这两道题藏起来，于是默认口径与公布口径长期
不一致，没人有机会去跑它们。这与本项目此前修过的几处是同一模式（`op='IN'` prompt
与 renderer 不一致、`request_timeout` 参数名写错、payload 缺 `model` 导致 verify_ab
的 CRITICAL 检查空转）——**失败信号被吃掉**。本轮跑批中途又演了一遍：
`python ... | tail -8` 的退出码取自 `tail`（恒为 0），一个崩掉的 A/B 臂报了 exit 0。

#### Decision

**1. 划一条修 gold 的边界线。**

改 gold 是危险操作：在已知 agent 答案的前提下调整 gold，任何系统都能刷到 1.000。
因此只修两类，其余一律不修：

| | 修 | 理由 |
|---|---|---|
| 违反业务语义 | ✅ | stock 指标跨日求和。不修等于让评测持续说谎 |
| 题面写了但 gold 没实现 | ✅ | 题面有「定期存款」而 gold 无 `account_type` 过滤。gold 与自己的题面矛盾 |
| 解释分歧 | ❌ | 两种读法都站得住时不动。这里是拟合 agent 的入口 |

**q008 是边界案例，值得单独记。** 它的第三处问题——gold 取「前段 MIN、后段 MAX」，
而题面说「初始/末日」——两边都不干净：MIN/MAX 是最低值/最高值且会系统性放大变化
幅度；agent 的端点取法虽贴字面，但账户在窗口内开户/销户时会静默丢行。

判据是**选一个既不等于旧 gold、也不等于 agent 答案的第三方案**：改为前后两段
日均对比（`AVG(CASE WHEN ...)`）。这样改动无法被指为拟合 agent。同时它与
`config/metrics.yaml` 里 `deposit_balance` 的 `AVG(balance)`「日均」口径一致，
并保住了题目 `evaluation_criteria` 明写的 `complex_aggregation: CASE WHEN` 考点
——若改成端点取法，这题唯一的复杂度就没了，退化成两个定日子查询 JOIN。

**2. 加行数守门测试。** `tests/eval/test_gold_sql_row_counts.py`，逐题真打 PG，
断言 gold SQL 的行数与 yaml 声明一致，覆盖 precision(8) + metric_routing(34) 共 42 例。
改 `dimension_generator` 或重新 seed 之后漂移一定会再来，届时应当是 CI 红灯而不是
分数悄悄掉。

两处刻意验证，不是写完就算：

- **确认它真能报红**：先把 q001 的 29 改回失真值 2，跑出红灯并确认报错点名了题号、
  期望、实际与后果，才恢复。没见过红的守门等于没有守门。
- **确认它不会误伤**：模块加载 `.env` 后 `PG_HOST` 恒有值，用它当 skip 开关会让
  没起 docker 的人撞连接错误而不是跳过（`make test` 不过滤 integration）。改成探
  真实连通性，验证了库可达时 43 passed、指向错误端口时 42 skipped。**守门自身的
  失效方式同样是静默的**——不验证跳过路径，它会以「gold SQL 跑不通」的伪装报错，
  然后被当成环境问题忽略。

**3. 删掉 `HAPPY_PATH_IDS` 白名单。** gold 修好后两题都及格，白名单再无理由。
删除后 `make eval-p1` 与 `run_all_evals.py` 自动跑全量，默认口径与公布口径从此一致。

#### Alternatives

| 方案 | 采纳 | 理由 |
|---|---|---|
| 只回填行数，不碰 q005/q008 | 否 | 白名单继续藏着两道坏题，等于承认评测集有不敢跑的部分 |
| 把 `expected_result_count` 改成运行时跑 gold SQL 动态取 | 否 | 自愈，但会改变一个计分维度的语义（退化成与 `result_match` 重复），且需重建历史基线。守门测试能达到同样效果而不动评分 |
| q008 gold 改成端点取法（与 agent 一致） | 否 | 唯一坐实「对着 agent 拟合」的选项；且会拿掉 CASE WHEN 考点 |
| q008 不动，留 `result_match=False` 当诊断 | 否 | 在所有人都认为 gold 站不住的题上留一个恒假信号，会训练人忽略 `result_match`，消耗这个诊断位的可信度 |
| 把 `result_match` 并入 `combined_score` | 未来 | 需重建全部历史基线，是独立决定（见 ADR-013 Update 2026-08-13） |

#### Consequences

**P1 成绩单重述**（8 题全量，`qwen3.7-max`，commit `fbf516f`，verify_ab 退 0）：

| | 基线（路由关） | 语义层 t=0.63 |
|---|---:|---:|
| avg_score（8 题） | 0.9646 | 0.9688 |
| passed | 8/8 | 8/8 |
| avg_score（6 题 happy 子集） | 1.0000 | 1.0000 |
| metric_hit_rate | — | 0.25 |
| precision_when_hit | — | 1.000 |

**6 题口径回到 1.000 且可复现**；8 题全量 0.965，首次全部及格。语义层再一次
**对分数零影响**（逐题完全相同），与前三轮 A/B 结论一致。

**两处诚实记账：**

1. **q005 掉到 0.82 是真扣该扣的。** 改题面钉时点后，agent 反而把
   `account_type='SAVING'` 过滤丢了——题面写着「定期存款」，它漏了。改 gold 前它
   是带这个过滤的。属于 prompt 敏感性还是跑间抖动，n=1 说不了。
2. **q008 改完预测错了。** 事前判断是「分数会先掉，因为 agent 做端点、gold 做日均」，
   实际 0.75 → 0.90：agent 跟着改后的题面换成了两段 AVG。预测错在假设 agent 不会
   跟着题面走。

**残留差异（不再修）**：q008 的 `result_match` 仍为 False，因为边界日归属不同——
gold 把 4/14 算进后段（前 7 天 + 后 8 天），agent 排除 4/14（前 7 天 + 后 7 天，
与题面「前 7 天、后 7 天」字面更符）。这恰好演示了为什么需要那条边界线：
**每修一轮 gold 都会露出下一道解释缝，不设线就会一直修到 gold == agent。**

**副产品观察**：q008 改题面后，语义层 arm 的路由从 `nl2sql` 变成
`metric_then_nl2sql`（`metric_id=deposit_balance`，`prefilter_cosine=0.6389`）——
「日均余额」的措辞把它顶过了 0.63 阈值，**余量只有 0.009**。随后 resolve 以
`no_metric` 正确退回（取顶类问题语义层一律拒绝，见 `a9c3b83`），无损。但这说明
prefilter 对措辞敏感，且当前阈值在这类问题上余量很薄。记录备查，暂不调整。

#### 补记 2026-08-14：一键报告拿不到新 P1 结果 + q008 的跑间抖动

**又一处同族失配。** `run_all_evals.py` 里 P1 的 glob pattern 是
`baseline_p2_validator_reflector_*.json`（更早的历史文件名），而 `run_p1_eval` 实际写
`baseline_p1_eval_<date>.json`。两者从来对不上，`results/` 里匹配旧 pattern 的只有
2026-06-03 那一份。后果是**一键报告在结构上就不可能显示新的 P1 数字**——跑完 P1、
写完新 JSON，然后 glob 到六月的旧文件出报告。这是「P1 6 题 1.000」能在报告里活两个月
的机制性原因，比白名单更隐蔽：白名单至少还写在 runner 里，这个失配藏在两个文件之间，
且 docstring 里那句「pattern 兼容」与代码相反。已修（`33bcf27`），并加守门
`tests/eval/test_run_all_evals_patterns.py`：读 runner 源码抽默认输出名，断言 pattern
能匹配上；不连库不跑 LLM，0.05s。P2/P3 的 pattern 本来就是对的。

**q008 的跑间抖动比预想大。** 同一基线配置（路由关）连跑三次：

| 跑次 | 8 题 avg | q008 | 其余七题 |
|---|---:|---:|---|
| `fbf516f` v3-A | 0.9646 | 0.90 | 逐次完全相同 |
| `9172baf` 一键（脏树） | 0.9688 | 0.933 | 同上 |
| `33bcf27` 一键（干净树，权威 baseline） | **0.9771** | 1.00 | 同上 |

**波动 100% 集中在 q008**。这修正了上文「残留差异」那段的结论：q008 的
`result_match=False` 不是稳定现象，边界日归属分歧只在部分跑次出现——**上文据单次
观察下的判断，样本不足**。结论方向不变（不再修 gold），但理由要改成「抖动中的一种
表现」而非「稳定的口径分歧」。

推论上更重要的一点：**q008 是这 8 题里唯一不稳定的题**，它同时也是最复杂的一题
（双窗口 + 条件聚合 + 变化率 + Top-N）。README 因此标注 0.977 不可当精确值读，并给出
0.965–0.977 的实测区间。取权威 baseline 用的是「最新一次 `commit_dirty=false` 的跑」
这个可复述规则，而不是挑最好看的数——但读者有权知道它恰好是三次里最高的那次。

#### 补记 2026-08-15：对该失效族做定向排查

前面几处都是顺着一根线撞出来的，说明这一族有基率，值得主动扫一遍。排查范围限定为
「文档/配置声称 X、代码实际 Y，且失配不报错」，查了五类：argparse 默认值、模块级常量、
脚本间的文件名/pattern 耦合、Makefile 与 README 引用的脚本、`.env.example` 与代码
读取的变量。**清白的**：脚本引用全部存在，环境变量完全一致。**查出四处**：

| # | 失配 | 处置 |
|---|---|---|
| F1 | `MAX_ATTEMPTS=3` vs ADR-006「1 次重试」 | 改代码，见 ADR-006 Update |
| F2 | `local.example.yaml` 的 `chat_model` 是 `qwen3.6-plus-2026-04-02`，所有公布数字用的是 `qwen3.7-max`；§1 表与 README badge 又各写了第三、第四个值 | 全部对齐到当前模型 |
| F3 | ADR-012 写 `--few-shot-min-sim` 默认 0.55，代码是 0.7 | 回填文档 |
| F4 | CI 跑 `pytest -m "not integration"`，行数守门不执行 | **未修**，需给 CI 加 Postgres service + 种子数据 |

F2 的危害最直观：照模板配好环境的人，跑出来的是和每一个已公布数字都不同的模型，
README 上的成绩他一个都复现不了，而且**不会有任何报错告诉他为什么**。

**排查中被自己的埋点救了一次**：比对改动前后分数时，因日期跨天读成了前一天的产物，
等于拿旧结果跟它自己比。是 `run_metadata.commit_hash` 显示的提交早于改动才发现——
如果产物里没有这个字段（`5da64da` 之前正是如此），这个错会直接进汇报。

**Trace**：`9183f29`（行数回填）、`46cc2ab`（q005/q008 gold）、`7e11f4b`（README）、
`29b19a1`（删白名单）、`fbf516f`（q008 日均 + 行数守门）、`33bcf27` 与 `200dadb`
（report/diff 两处 pattern 失配 + 守门）、`78094f1`（reflect 预算 + 模型/阈值回填）；
权威产物 `results/baseline_p1_eval_2026-08-15.json` 与
`results/eval_report_2026-08-15.md`；A/B 产物
`results/p1_full8_{baseline,metric_t063}_v3_2026-08-14.json`

---

### ADR-015: P2 评分器中文分词修复，饱和维度暂留

**Status**：Accepted（2026-08-15）

#### Context

排查覆盖率时发现 `eval/multi_step_analysis_evaluator.py` **0% 测试覆盖**——产出 P2
那个 0.740 的代码一行测试都没有，而同期 P1 评分器 84%、P3 评分器 90%。读下去发现两个
问题，一个是 bug，一个是设计缺陷。

**Bug：中文洞察匹配等于没分词。** `insight_accuracy`（**25% 权重，五维里最高**）原实现是
`exp_insight_val.split()[:5]` + 任一 token 命中即算数。对中文按空格切，两个方向同时失效：

| 洞察形态 | split 结果 | 后果 | 评测集中数量 |
|---|---|---|---:|
| 纯中文「识别出春节是季节性高峰而非异常」 | 整句 1 个 token | 要求逐字出现，改述一律 0 分 | 10 / 31 |
| 中英混合「2 月 15-23 日现金支取量增加约 25%」 | 首 token `'2'` | 含数字 2 即命中，白送分 | 21 / 31 |

**31 条 expected_insight 里没有一条被正确评估**，而失效是静默的——分数照常产出，
看不出它没在测洞察。

**设计缺陷：三个维度恒定饱和。** 逐维分解 2026-06-07 那份 baseline：

| 维度 | 权重 | q001 | q002 | q003 |
|---|---:|---:|---:|---:|
| multi_metric_coverage | 20% | 1.000 | 1.000 | 1.000 |
| reasoning_quality | 20% | 1.000 | 1.000 | 1.000 |
| business_relevance | 15% | 1.000 | 1.000 | 1.000 |
| insight_accuracy | 25% | 0.500 | 0.250 | 0.250 |
| step_completeness | 20% | 0.400 | 0.600 | 0.600 |

判据太松：`reasoning_quality` 数「因此/所以/由于/导致…」出现 4 个即满分，
`business_relevance` 数「客户/分行/产品/风险…」出现 5 个即满分。任何通顺的中文分析
都会自动拿满。**55% 的权重是常数，不是测量。**

#### Decision

**只修 bug，不动饱和维度。** 分词是确凿错误（有唯一正确答案），阈值松紧是口味问题
（收紧只会让数字变难看，不必然更有意义）——把两者混在一轮改，事后无法归因。

修法不造轮子：P3 的 `rca_evaluator` 早就解决过同一个坑，其注释原话是「比 `.split()`
的关键优势：中文按词切分」。把 jieba 分词与 77 词停用词表提到 `eval/zh_tokenize.py`
共用，比对改为**内容词召回率**。用召回而非 Jaccard 是刻意的：agent 回答是长篇叙述，
Jaccard 会被长度稀释到接近 0，分不出「说到了」和「没说到」。

#### Consequences

3 题复跑（`--limit 3`，与历史 baseline 同题）：

| | 旧 | 新 | Δ |
|---|---:|---:|---:|
| insight_accuracy q001 / q002 / q003 | 0.500 / 0.250 / 0.250 | 0.627 / 0.577 / 0.491 | **+0.127 / +0.327 / +0.241** |
| 其余四维 | — | — | **逐位相同** |
| avg | 0.7403 | **0.7980** | +0.0577 |

**归因干净但要说准**：`insight_accuracy` 是唯一变动的维度。原本担心 06-07 那份出自两个月前
的 agent、差异不能全归给评分器，但 `step_completeness`（唯一非饱和、且完全由 agent 行为
决定的维度）0.400/0.600/0.600 一位没动，说明 agent 可测行为稳定。**不过另外三维「没变」
有一半是天花板效应**——它们恒等于 1.000，本就没有变化空间，真正提供信息的只有
`step_completeness` 一个。证据方向一致，但比表面看起来弱。

**三个饱和维度在新一轮里仍全是 1.000**，55% 权重依旧是常数。因此 **0.798 只是「不再明显
错」，尚不足以当能力指标**，README 已如实标注。是否收紧、或整体改成 P3 那样的 LLM judge
（约 200–300 行 + 自建 rubric + 每题一次 LLM 调用），留作独立决定。

**顺带修掉的破坏性 bug**：`run_p2_eval` 里 `OUTPUT_DATE = "2026-06-07"` 是硬编码字符串
（P1/P3 都用 `datetime.now(UTC)`），**每一次 P2 跑批都会覆盖那份历史 baseline**，且终端
仍打印旧日期，看不出异常。本次复跑真的覆盖了，靠该文件已被 git 跟踪才恢复——若当时顺手
commit，那份两个月前 agent 产出的对照组就永久丢失且无法重建。守门见
`tests/eval/test_runner_output_dates.py`。同时给 `run_p2_eval` 补了 `--limit/--qid`
（P1 有 `--question-set`、P3 有 `--limit`，唯独 P2 没有子集开关，连"只复跑可比的 3 题"
都做不到）。

#### Update 2026-08-17：B 档——先删后加，以及一次险些成立的错误结论

**又一个同类 bug（不是阈值问题）**：`multi_metric_coverage` 原实现
`any(m in agent_response for m in metric)`——`metric` 是字符串，`for m in metric`
迭代的是**单个字**，判据退化成「指标名里任意一个字出现过吗」。`'长'`（长期/董事长）、
`'户'`（账户）在银行叙述里几乎必然出现。与洞察维是同一族，都是静默失效。已改整词匹配。

**「先删后加」的依据来自横向对照 P1/P3**。三条路径的差别不在用不用 LLM，在**有没有
ground truth**：

| | 真值来源 | 高权重维度怎么判 |
|---|---|---|
| P1 | gold SQL | 全确定性 + 真打 PG 比对结果集 |
| P3 | YAML 事件库（埋雷时即知真因） | `event_hit` 40% + `dimension_recall` 30% 确定性，仅 20% 用 LLM judge |
| P2 | **只有 `expected_insights`** | 其余靠数关键词 |

`reasoning_quality` 与 `business_relevance` **没有可比对的对象**——题目 YAML 里没有
任何东西说这题的推理该长什么样。调阈值不可能产出有意义的度量（只是把常数从 1.0 变成
0.6），故移出总分降为诊断字段（与 P1 的 `result_match` 同样处理）。剩余三维按原比例
归一为 0.30/0.30/0.40。

**结果**（3 题，`partial=false`）：

| | A 档后 | B 档后 |
|---|---:|---:|
| avg | 0.7980 | **0.6551** |
| passed | 3/3 | **2/3** |
| multi_metric_coverage | 1.000 ×3 | 0.500 / 1.000 / 1.000 |

**这是本项目唯一往下修的分数**，因为此前的高分有相当部分是白送的。

**仍未解决**：`multi_metric_coverage` 在 3 题里仍有 2 题满分——整词匹配修掉了最离谱的
误判，但候选词（率/增长/金额/客户/流）本身太通用。「后加」（照 P3 的
`_llm_judge_conclusion` 补 rubric LLM judge）尚未做。

#### 过程中险些成立的错误结论

B 档第一轮跑完，终端给出「Total 3 / Passed 0 / Avg 0.631」。而我的预期正是「分数明显
下降、可能跌破及格线」——**完美吻合**，几乎直接当成结论。实际上 3 题里 2 题撞
embedding 端点 `ConnectionError` **从未执行**，0.631 只是唯一跑成那题的分数。

成因：`total_questions` 开跑前就设成 3，异常时 `continue` 跳过，而 `avg_score` 只对
成功评分的题求平均——**两个数字来自不同分母**；`0/3` 又把「没通过」与「压根没跑」
混为一谈。产物里本有 `partial` 字段，但从无代码设置（2026-06-07 那份的 `partial=true`
是人手写的）。

**这个失效比今天其他几个更危险，因为它伪装成了预期结果**：改评分器时分数变低是预料
之中的，所以一次三分之二没跑成的残缺运行，长得跟「改动生效了」一模一样。发现它靠的是
逐维展开时 `KeyError: 'sub_scores'`，纯属运气。

已修：payload 加 `scored_questions` / `errored_questions` / `partial`，终端打印警告，
`run_all_evals` 报告行同步标注。**修完立刻自证**——下一轮又遇同样故障，这次直接报出
「⚠️ 2/3 题未执行」。刻意不改 `avg_score` / `pass_rate` 字段名：`run_all_evals.py:97`
与 `eval_diff.py` 直接按 `d['avg_score']` 取值且无 `.get` 兜底，改名会让报告生成
KeyError——正是本轮一直在修的那类失配。

**顺带修掉根因**：瞬时重试原为 2 次 + 线性退避 2s/4s，总计只扛得住约 6 秒抖动。当天有
三轮跑批死于 dashscope 的 DNS/连接瞬断（合计 45+ 分钟与对应 LLM 花费）。对「一轮 20
分钟起」的批量评测，6 秒就放弃是明显失配。改为 4 次 + 指数退避 2/4/8/16（约 30s）。
`_call_with_retry` 的原注释判断完全正确，只是预算给小了一个数量级。

**B0（enabler）**：产物落盘 `eval_input`（评分器完整入参），配套
`scripts/replay_p2_scoring.py` 可离线重放，实测逐维零偏差复现产物分数。此前只存 200 字
预览而评分用的是完整回答，改一次评分器就得重跑 22 分钟——没有这个闭环，B 档的阈值
迭代根本做不了。重放脚本对缺 `eval_input` 的旧产物直接拒绝而非用预览凑数。

**Trace**：`65d6d73`（分词修复 + 11 个测试，该模块此前 0% 覆盖）、`2fd4a4f`（整词匹配
+ eval_input + 重放脚本）、`bb8fde0`（残缺记账）、`67a9575`（重试预算）；
产物 `results/baseline_p2_analysis_2026-08-15.json`（A 档后）与
`results/baseline_p2_analysis_2026-08-17.json`（B 档后，`partial=false`）

---

### ADR-016: P2 rubric LLM judge——补回被删两维的度量能力

**Status**：Accepted（2026-08-17）

#### Context

ADR-015 的 B 档把 `reasoning_quality` 与 `business_relevance` 移出总分，理由是它们**没有
可比对的对象**（判据只能是数连接词、数业务名词，任何通顺中文都拿满）。删除是对的，但
留下一个真实缺口：**推理链条与业务可落地性此后完全没有被度量**——P2 的总分只剩三个
维度，全是词面匹配，没有任何东西在看这段分析讲没讲通。

问题是：换成 LLM judge 就能解决吗？如果只是把「数关键词」换成「问 LLM 这段写得好不好」，
那还是没有可比对的对象，只是把常数 1.0 换成了 LLM 的主观印象分，缺陷更难发现。

**决定性的观察是 P2 题目 YAML 里本来就有 per-question 的人工锚点**，只是从没被用过：

```yaml
analysis_steps:      # 解题所需的关键步骤，逐条写死
expected_insights:   # 期望洞察，含量化基准（+25% / +12% / 58% / 42%）
evaluation_criteria: # 本题人工 rubric，如「Agent 是否识别出 2-5 天的响应延迟窗口」
```

这正是 P3 `_llm_judge_conclusion` 的做法：通用 4 维 backbone + 把每题的
`evaluation_criteria` / `expected_key_metrics` 注入 prompt 作为该题的重点检查项。

#### Decision

**照 P3 补 4 维 G-Eval rubric judge，每一维都锚在本题 YAML 字段上。**

| judge 维度 | 锚 |
|---|---|
| `step_fidelity` | `analysis_steps` |
| `quantification` | `expected_insights` 里的量化基准 |
| `causal_reasoning` | 本题 `evaluation_criteria` |
| `business_actionability` | 本题 `evaluation_criteria` |

**与被删两维的本质差别就是这个锚**：被删两维锚在通用词表上（对任何题目都一样，所以必然
饱和），judge 四维锚在每题人工写死、且写在 agent 跑之前的字段上。

三个刻意的设计选择：

**1. 权重只给 25%，确定性三维保持 75%（对齐 P3 的 80/20）。** judge 的锚是「人写 rubric
文本 + LLM 判读」，比 P1 的 gold SQL、P3 的事件库弱一个量级，不该让它主导总分。
新权重：insight 0.35 / step 0.25 / metric 0.15 / rubric 0.25。

**2. 失败不回退到启发式。** P3 的 judge 失败回退 Jaccard，因为 Jaccard 对「结论是否相似」
至少是个弱信号。P2 这四维没有这种替代品——唯一想得到的廉价近似，**正是 ADR-015 刚删掉的
关键词计数**。把它放进 fallback 分支只会让缺陷更隐蔽：平时看不见，judge 一挂就悄悄接管。
所以 judge 失败时该维**退出计分**，其余维度归一。

归一而非记 0，是因为记 0 等于拿基础设施故障扣 agent 的分。但归一会让两种运行的口径不同，
所以必须配套记账：`AnalysisScore.rubric_available`、产物 `rubric_unavailable_questions`、
终端警告、一键报告标注——与 ADR-015 处理残缺运行的做法一致。

**3. 逐维中位数 ×3（self-consistency），这一条与 P3 不同。** 依据是实测：同一份 agent
回答连判 3 次，**`temperature=0` 并不给出确定性输出**。

| | 单次判分（3 次重复） | 摆幅 |
|---|---|---:|
| q001 | 0.750 / 0.812 / 0.750 | 0.062 |
| q002 | 0.750 / 0.875 / **0.562** | **0.313** |
| q003 | 0.375 / 0.312 / 0.312 | 0.063 |

q002 那 0.313 的摆幅乘 25% 权重 ≈ 总分 ±0.078，**与真实退化同量级**——单次判分根本无法
区分「agent 变差了」和「judge 这次心情不同」。改逐维中位数 ×3 后实测：

| | 中位数 ×3（3 次重复） | 摆幅 |
|---|---|---:|
| q001 | 0.812 / 0.812 / 0.812 | 0.000 |
| q002 | 0.625 / 0.625 / 0.688 | 0.062 |
| q003 | 0.375 / 0.312 / 0.312 | 0.062 |

**最坏摆幅 0.313 → 0.062，降到约五分之一。** 成本可忽略：judge 每题几秒，agent 每题
300~500s。3 次里挂 1 次仍出分（`samples` 字段记实际次数），全挂才算 judge 未判。

#### Consequences

3 题复跑（`--limit 3`，`partial=false`，`commit_dirty=false`，产物 `ran_at 08:25Z`）：

| | ① 分词修复后 | ② 删两维后 | ③ 加 judge 后 |
|---|---:|---:|---:|
| avg | 0.7980 | 0.6551 | **0.6013** |
| passed | 3/3 | 2/3 | **0/3** |

逐题逐维（③）：

| qid | step 25% | metric 15% | insight 35% | **rubric 25%** | 总分 |
|---|---:|---:|---:|---:|---:|
| q001 | 0.40 | 1.00 | 0.69 | **0.75** | 0.679 |
| q002 | 0.60 | 1.00 | 0.42 | **0.56** | 0.587 |
| q003 | 0.60 | 1.00 | 0.37 | **0.44** | 0.537 |

rubric 子分：

| qid | step_fidelity | quantification | causal_reasoning | business_actionability |
|---|---:|---:|---:|---:|
| q001 | 1.00 | **0.50** | 1.00 | 0.50 |
| q002 | 0.75 | **0.00** | 0.50 | 1.00 |
| q003 | 0.75 | **0.00** | 0.50 | 0.50 |

**judge 确实在区分，不是又一个饱和维度。** rubric 均值 0.75 / 0.56 / 0.44——被删两维在
同样三题上恒等 1.000（诊断字段仍在产物里，本轮依旧全 1.000，可直接对照）。

**最有信息量的发现是 `quantification` 维**：0.50 / 0.00 / 0.00。agent 的分析**基本不报
与期望基准可比的数字**。这是一直存在、但此前没有任何维度看得见的缺陷——
`insight_accuracy` 算的是内容词召回，说到「增长」就算命中，不管报的是 +25% 还是 +3%。

**`causal_reasoning` / `business_actionability` 的问题是量程被压，不是饱和。** 汇总全部
观测（两轮基线 + 单次判分探针 3 次 × 3 题）：

| judge 维度 | 观测取值范围 | 最低值 |
|---|---|---:|
| `quantification` | 0.00 – 0.50 | **0.00** |
| `step_fidelity` | 0.25 – 1.00 | **0.25** |
| `causal_reasoning` | 0.50 – 1.00 | 0.50 |
| `business_actionability` | 0.50 – 1.00 | 0.50 |

后两维在题间**确实会动**（不像被删的两维恒等 1.000），但**下界卡在 0.50**，有效量程被压到
[0.5, 1.0]。这个区别很重要：说「饱和」会指向「改 prompt 收紧判据」，而事实是**分不出
「判据偏松」还是「这 3 份回答本身就是中等水平」**——n=3、每维 3 个观测值，撑不起任何一边。

初稿把这两维写成「部分饱和」，是从「q001 causal、q002 business 取到 1.00」这个单点推出的
过度概括，与本会话早前对 q008「口径分歧」的过度概括同类。已改。

**`passed 0/3` 不是稳定刻度。** 同一份代码跑两轮，q001 分别落 0.700 与 0.679——跨在
0.7 两侧，`passed` 因此在 1/3 与 0/3 之间摆动。中位数 ×3 压掉的是 **judge 在固定输入上
的**噪声（0.313 → 0.062，见 Decision），**agent 自身的跑间波动照旧**：两轮之间
q001 `insight` 0.71→0.69、`business_actionability` 0.75→0.50，q002 `causal_reasoning`
1.00→0.50。判断优劣仍须逐题比较，不能看 avg 上的零点几个百分点——与 ADR-013 同一结论。

**顺带撞上并遵守了自己定的规则。** 第一轮跑批产物记的是
`commit_hash 72a7fb6 / commit_dirty true`——而 72a7fb6 **根本不含 rubric judge**，元数据
指向一个不存在这段代码的提交。这与本会话早前差点报错产物是同一个陷阱（当时也是靠
`run_metadata.commit_hash` 发现的）。README 自己写着「表里取的是最新一次可复现的跑
（`commit_dirty=false`）」，所以先 commit（`9bc37c8`）再重跑，弃用第一轮的 0.6066。

**离线重放闭环保住了。** rubric 子分随产物落盘（照 P3 的 `conclusion_rubric`），
`scripts/replay_p2_scoring.py` 默认复用它——改确定性维度时精确重放、零 LLM 花费；
`--rejudge` 才重跑 judge（照 P3 的 `scripts/rejudge_baseline.py`）。旧产物没有 rubric，
重放时按三维归一并显式告警，不与四维题混为一谈。

**副作用：默认开 judge 让单元测试挂住。** `use_llm_judge` 默认 True（与 P3 一致，避免谁
忘了开就静默少一维），代价是两处不带该参数的构造让 `pytest -m "not integration"` 跑过
120s 仍未结束，且不报错、看不出在等什么。守门见
`tests/eval/test_p2_rubric_judge.py::test_no_unit_test_constructs_the_evaluator_bare`
（扫 `tests/` 全目录）。另外 `replay_p2_scoring.py` 原本没有 `load_dotenv`，`--rejudge`
第一次跑就三题全降级——好在降级是响的（三条 warning + rubric 列显示 `—`），当场看见。

#### Update 同日：judge 立刻证伪了 `step_completeness`

judge 上线后第一个兑现的收益不是新分数，是**证伪了一个已存在的维度**。

`step_completeness`（当时 25% 权重）的实现是 `len(mentioned_steps) / len(analysis_steps)`。
q001 实测 0.40（agent 只规划 2 步，YAML 有 5 步），而 judge 的 `step_fidelity` 拿**同一份
`analysis_steps`** 判内容给了 1.00。人工核对回答全文，5 步的实质内容全部覆盖：

| YAML step | 回答中的对应 |
|---|---|
| 查 2/1-14 WITHDRAW 总额/笔数/客户数 | 「节前 MOBILE 渠道支取总额 1274728.88 元，日均 91052.06 元，涉及 123 位客户」 |
| 查 2/15-23 同上 | 「进入假期后…MOBILE 总额降至 957985.79 元」 |
| 计算日均值和增长率 | 「ATM 日均…大幅增长约 319%」「柜台骤降 70.7%」 |
| 按渠道分别统计 | ATM / COUNTER / MOBILE / INTERNET 四渠道逐一给出 |
| 对比并总结变化特征 | 「假期资金流出结构发生根本性转移」+ 四条业务洞察 |

**judge 是对的**：旧维度罚的是「把 5 步并成 2 步做完」，即计划粒度，与维度名声称的「步骤
完整性」无关。这是本轮第三次遇到同一族问题——维度名说一件事，实现算另一件。

**两个维度锚在同一份 ground truth 上而结论相反，等于互为对照。** 只有一种测法时做不到这种
证伪；这是补 judge 的一个未预期收益。

**换成内容词召回也不行**，实测比数节点还低：

| qid | 数计划节点（原） | 内容词召回 | judge 判内容 |
|---|---:|---:|---:|
| q001 | 0.40 | 0.553 | **1.00** |
| q002 | 0.60 | 0.337 | 0.75 |
| q003 | 0.60 | 0.350 | 0.75 |

原因是期望步骤写成**指令式文本且带表名**（「从 `fct_holding` 查询 2026-05-07 的
PROD_WEA_0000 持有人数」），agent 用业务语言报结果，不会复述表名。召回率在惩罚「没有复述
schema」，是另一个错的测法。**没有可行的确定性替代品**——这个锚天生是指令形状的。

**Decision**：`step_completeness` 降为诊断（保留计算，它仍是「计划粒度」的有用信号），步骤
判定整体交给 judge 的 `step_fidelity`。权重 insight 0.35 / step 0.25 / metric 0.15 /
rubric 0.25 → **insight 0.45 / metric 0.20 / rubric 0.35**。judge 份额上升是这次搬迁的直接
结果，不是更信任 LLM 判读，守门上限随之由 0.30 放宽到 0.35（仍要求确定性侧过半）。

**结果**（重评，非重跑）：

| | ③ 加 judge | ④ 步骤降诊断 |
|---|---:|---:|
| avg | 0.6013 | **0.6255** |
| passed | 0/3 | **1/3** |
| q001 | 0.679 | **0.774** |
| q002 | 0.587 | 0.585 |
| q003 | 0.537 | 0.518 |

**为什么重评而不重跑 agent**：agent 跑间本身有波动（q001 两轮 0.700 / 0.679），重跑会把
「评分器变了」与「agent 这次跑得不同」搅在一起。重评保持 agent 输出不变、只换评分器，
归因干净——这正是 B0 那个 `eval_input` 闭环的兑现（`scripts/replay_p2_scoring.py --write`）。
重评产物保留原 agent 跑的 `ran_at` / `run_metadata`，评分器出处另记 `rescorer_metadata`；
**两条出处必须分开**，否则日后无法回答「分数变化来自哪一侧」。守门见
`TestRescoredArtifactProvenance`。

#### 一处需要修正的表述

本 ADR 初稿写 agent「基本不报与期望基准可比的数字」。核对三题回答后，准确说法是**不算 gold
要求的派生比率**：

| qid | 回答里的百分比 | 大数字 | judge `quantification` |
|---|---:|---:|---:|
| q001 | 4 个（319% / −70.7% / +16.9% / +1.9%） | 13 | 0.50 |
| q002 | 1 个 | 6 | 0.00 |
| q003 | **0 个** | 7（9141 / 5265 / 720） | 0.00 |

q003 最清楚：分子分母都查出来了却从没相除算出赎回率、续作率；q001 证明 agent **会**算。
所以这不是「不量化」，是「不做最后一步除法」——两者对应的改进方向完全不同。

**剩下唯一未被检验的计分维度是 `multi_metric_coverage`（20%）**，3 题全满分、零区分度。
它在更早一轮 q001 上取过 0.500，不是纯常数，n=3 判不了该删还是该改。

**Trace**：`tests/eval/test_p2_rubric_judge.py`（44 个测试）；
产物 `results/baseline_p2_analysis_2026-08-17.json`（③，原始跑）与
`results/baseline_p2_analysis_2026-08-17_rescored.json`（④，重评）

---

**最后更新**：2026-08-17

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
| **LLM（生成 + 评分）** | Qwen3.6-max-preview（DashScope） | GPT-4 / Claude 3.5 / DeepSeek-V2 | 中文银行场景 + 国内合规接入 + 单源省心 | [ADR-001](#adr-001-llm-选-qwen36-max-preview) |
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

新增 ADR 命名 `ADR-011`、`ADR-012` 继续追加。修改现有决策请把 Status 改为 `Superseded by ADR-XXX` 并保留原文。

---

**最后更新**：2026-07-01

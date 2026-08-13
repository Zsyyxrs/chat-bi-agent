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
| [ADR-012](#adr-012-q-sql-few-shot-检索注入-bird-验证净效应-0-同域生产未测) | Q-SQL few-shot 检索注入 | Accepted（默认阈值保守） |
| [ADR-013](#adr-013-语义层-metric-resolver-原型-6-指标模板-llm-抽-spec-fallback-回原-nl2sql) | 语义层 Metric Resolver 原型 | Accepted（2026-08-12 已接线到 P1 主路径，见 Update） |

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

**默认配置**：`--few-shot-min-sim = 0.55`（保守，覆盖率仅 33% 但不制造负例）；`--example-pool` 默认 None（off）。生产 P1 目前不挂 retriever。

**跟进项**：
- **待做**：给中文银行域构建生产 pool（历史 judge=1 的 Q-SQL 对），量测同域场景下 few-shot 是否真加分——这才是 few-shot 应该证明价值的地方
- **待做**：结果 JSON schema 加 `commit_hash` + `config_hash` 字段，防止未来跨代码/跨配置比较
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

**A/B 数字**（2026-08-13，6 题 happy path，同 commit `2f60c1d`，verify_ab 两组均退 0）：

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

- **t=0.70（当前默认）：0 命中**。6 题 cosine 全落在 0.48–0.65，最高 0.6475 < 0.70。
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

**下一步**（按优先级）：
1. **换标尺**：6 题里只有 2 题是指标型，无法衡量语义层。需要一套指标型问题占比
   ≥ 50% 的评测集，否则命中率这个指标没有意义
2. **默认阈值暂维持 0.70**：t=0.57 在这套题上已验证无回归，但只有 2 个命中样本，
   撑不起放开默认值的决定。等标尺换掉再定，届时按 cosine 分布重新选点
3. 扩指标目录到 15-20 个（原 P2 项）——目录只有 6 个指标是命中率低的根因之一
4. 补"语义忠实度"守门：string filter 的值域校验（比对真实列的 distinct 值），
   这是目前唯一没有任何自动化防护的失败面

**跟进项闭环**：
- P1 ✅ `op='IN'` 支持完成
- P1 ✅ 接线到 P1 主路径完成（前置路由而非 SQLGenerator 内部 try/except）
- P2 保留：指标目录扩容至 15-20 个（loan/campaign/risk 相关，独立 PR）
- P3 保留：Streamlit "识别到 metric=X 确认" UX
- P3 保留：Langfuse 看板 `metric_hit_rate` 图

**Trace**：本次代码见 branch `feat/metric-router-p1-integration`；spec 与 plan 在
`docs/superpowers/`（gitignored，本地保留）

---

**最后更新**：2026-08-12

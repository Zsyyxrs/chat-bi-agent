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
| **LLM（生成 + 评分）** | **当前 `qwen3.7-max`**（DashScope；ADR-001 立项时为 Qwen3.6-max-preview） | GPT-4 / Claude 3.5 / DeepSeek-V2 | 中文银行场景 + 国内合规接入 + 单源省心 | [ADR-001](#adr-001) |
| **Agent 编排** | 自研函数链 + `@observe` 装饰器 | LangGraph / CrewAI / AutoGen | 三路径流程都是**固定 DAG**，框架抽象换不来收益，多一层维护负担 | [ADR-002](#adr-002) |
| **可观测性** | Langfuse v3（self-hosted） | LangSmith / Phoenix / OpenLLMetry | 自托管无数据出境风险 + trace tree 完整 + LLM judge 分数可回流 | [ADR-003](#adr-003) |
| **评分方式** | LLM-as-judge（Qwen 自评，4 维 rubric） | 人工标注 / Ragas / DeepEval | 启动期无标注预算，Qwen 自评在银行场景稳定性可接受，后续可切人工 | [ADR-004](#adr-004) |
| **SQL 校验** | sqlglot（AST 解析 + dry-run） | 直接执行验错 / 自写 Antlr | Python 原生、多方言、AST 可改写；执行验错代价大且噪声高 | [ADR-005](#adr-005) |
| **反思机制** | Reflector 单次重试 | 无反思 / 多次重试 / 树搜索（ToT） | 银行 SQL 错误模式有限（3-4 类），单次重试 ROI 最高 | [ADR-006](#adr-006) |
| **P3 ground truth** | YAML 事件库 + 传播引擎埋雷 | 用真实生产脱敏数据 / 手工 SQL 注入异常 | 可控、可重放、可量化、可解释；对齐 rubric 的 event_hit 维度 | [ADR-007](#adr-007) |
| **Schema 检索** | Embedding（text-embedding-v4）+ jieba 分词 | BM25 / 静态映射表 / GraphRAG | 中文同义词多（"存款/储蓄/余额"），embedding 召回 + jieba 分词组合覆盖率最好 | [ADR-008](#adr-008) |
| **Web UI** | Streamlit | React/Next.js + FastAPI / Gradio | Demo 场景，3 倍开发速度，直接给 Python 对象绑图表 | [ADR-009](#adr-009) |
| **数据库权限** | 双用户隔离（chatbi 写 / chatbi_readonly 读） | 单账号 + 应用层白名单 | Agent 生成的 SQL 由 readonly 用户执行，DB 层兜底防 DROP/DELETE | [ADR-010](#adr-010) |

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
- 引入 **Reflector**：SQL 执行失败时，把错误信息回喂 LLM 单次重试（[ADR-006](#adr-006)）
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
- 见 [ADR-004](#adr-004)。

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

<a id="adr-001"></a>

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

<a id="adr-002"></a>

### ADR-002: 自研函数链编排，不引入 Agent 框架

**Status**: Accepted · 2026-06

**Context**:
- 三路径流程都是**固定 DAG**：
  - P1: SchemaLinker → SQLGen → Validate → Execute → (Reflect × 1 retry)
  - P2: Planner → (P1 × N) → FactExtractor → InsightSynth → ReportWriter
  - P3: fact_anchor → drill_select → drill_run → event_match → synthesize
- 没有真正的**动态路由**（谁调谁在编写时就确定）—— ⚠️ 到 2026-09 已不完全成立，见下方 Update
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
（⚠️ 这句**已被 2026-09-04 Update 改写成三条更准的触发条件**，以下方为准。）

**外部对照（2026-09-01，DB-GPT v0.8.2 代码核对）**：

DB-GPT 造了一整套编排 DSL（AWEL，把工作流建模成算子 DAG），官方给出的动机与本 ADR 判断一致：

> 多智能体的自动编排能力被模型能力严重限制，同时对于需要确定性的场景（如 pipeline 类任务）
> 根本不需要用大模型的自动编排。

更有说服力的是**它自己没用上**：AWEL 是其核心卖点，但经典 NL2SQL scene
（`ChatWithDbAutoExecute`）的流程 `generate_input_values → prompt → LLM → out_parser
→ do_action` 是 `base_chat.py` 里硬编码的方法调用顺序，**不是声明式 DAG**；AWEL 在那里
只被用作「LLM 调用 + 缓存」的封装。此外文档宣称的三层架构（Operator / AgentFream / DSL）
里，`AgentFream`、DSL、Ray runner 在代码中**全部不存在**（全仓 grep 零命中），
真实可用的只有 Operator 层 + 单机 runner。

即：一个 19.8k star、以编排框架为卖点的项目，主链路仍然是手写调用顺序，且框架三层里两层是路线图。
这支持本 ADR 的取舍——固定 DAG 场景下，编排框架的收益主要是**声明式表达**，而非能力，
其代价（抽象层、trace 语义变差）我们不需要承担。

**评估过、明确不做**：AWEL 的 `BranchOperator` / `BranchJoinOperator` 曾被考虑用来显式建模
Reflector 的重试分支。不做——理由与上表 LangGraph 那行相同（`conditional edges` 即此物），
且 ADR-006 已定「单次重试」，分支逻辑用 `if/else` 表达完全够；改造对象
`nl2sql_agent.py:run()` 承载四个终态 route（`metric` 模板 / `nl2sql` LLM 生成 /
`metric_then_nl2sql` 降级 / `metric_denied` RLAC 拒绝，末一个由 ADR-017 于 2026-09-04 加入），
是测试覆盖最厚的代码，为零功能收益动它是纯亏。记此一笔以免重复评估。

**Update（2026-09-04）：LangGraph 进阶四件套 / CrewAI / AutoGen 逐项对照（读不做，无代码改动）**

这一段不是重新评估选型，结论不变。补它的原因是：上表那四行是**判断**，不是**机制对照**——
被追问「LangGraph 的 checkpoint / interrupt / subgraph 具体是什么，你为什么说用不上」时，
「抽象层太重」不是一个能站住的回答。以下把每个机制写清楚，并逐条对上本项目的现状。

| LangGraph 机制 | 它实际做什么 | 本项目为什么用不上 |
|---|---|---|
| **checkpoint** | 以 `thread_id` 为键把每步后的 `StateSnapshot` 落到 checkpointer（`InMemorySaver` / `SqliteSaver` / `PostgresSaver`）。由此派生断点续跑、`get_state_history()` 回看、`update_state()` 改写历史后分叉重放（time travel）。1.0 起可用 `durability` 三档控制写入时机：`"exit"`（只在结束时写，最快，进程崩了中间态全丢）/ `"async"`（异步写，崩溃时有小概率丢）/ `"sync"`（下一步开始前同步写，最 durable） | 一次 `run()` 是**单进程内几十秒跑完的一次性调用**，没有跨请求会话状态，也没有长时人工等待。失败的代价是重跑一题（一次 LLM 调用），不是丢掉一小时的工作。⚠️ 需要显式区分的是：我们**有**持久化的执行记录（Langfuse trace），但那是**只读的事后复盘**，不是可续跑的状态机快照——需求是「查为什么错」，不是「从第 3 步接着跑」。把 trace 当 checkpoint 说是混淆 |
| **interrupt / HITL** | 节点里调 `interrupt(value)` 抛 `GraphInterrupt`，把 value 交给客户端，客户端用 `Command(resume=...)` 续跑。硬依赖 checkpointer。**坑**：官方明确写「The graph resumes from the start of the node, re-executing all logic」——恢复时整个节点从头重跑，`interrupt` 之前的副作用会执行第二次，所以 interrupt 必须放节点开头或单独节点 | 本项目唯一像 HITL 的位置是「执行 SQL 前让人确认」。这一位置我们**故意用确定性代码而不是人工确认**：sqlglot AST 校验 + 函数级黑名单（ADR-005）、只读用户（ADR-010）、RLAC 渲染期注入且拒绝不回退（ADR-017）。理由与 ADR-017 记的 SQLBot fail-open 教训同源——**权限与安全边界不能靠人点一下确认**。至于「用户看到结果后 👍/👎」，那是 Streamlit 的 request/response，在 UI 层，不需要编排层参与 |
| **subgraph** | 把编译好的 graph 当节点用。同 state schema 直接 `add_node(subgraph)`；不同 schema 就在外层节点里 `.invoke()` 并手写 state 进出转换。主要收益是团队并行开发与跨项目复用 | 我们**已经在做等价的事**：P2 调 P1 就是「子图当函数调」——`self._run_step()` 一个方法，state 转换是 `inject_context()` + `_p1_result_to_step_result()` 两个纯函数。换成 subgraph 只是给同一件事加仪式，还要额外处理它的 checkpointer 三态（`None` 每次调用新建 / `True` 跟随线程且禁止并发调同一子图 / `False` 不持久化）和 `get_state(config, subgraphs=True)` 的可见性问题 |
| **Send / map-reduce** | 一个节点向下游 fan-out 出 N 个并行分支并汇总 | **这是唯一一处框架真有而我们没有的能力**，如实记下来。但 P2 的 N 个 step 顺序执行是**故意的**：step *i* 的结果要 `inject_context()` 进 step *i+1*，本来就没有并行度可拿。真要并行的是「多个互不依赖的 step」，那要先改 Planner 产出依赖图——瓶颈在规划，不在编排框架 |

**一处必须自我修正的事实**：本 ADR 的 Context 写「没有真正的动态路由（谁调谁在编写时就确定）」，
到 2026-09 这句已经**不完全成立**：

- `nl2sql_agent.py:run()` 有四个终态 route：`metric` / `nl2sql` / `metric_then_nl2sql` / `metric_denied`
- `p2_analysis_agent.py:run()` 是个 `while` 循环，replan 后会**退回到失败的 index 重跑**（`MAX_REPLAN = 1`）

这两处恰恰就是 LangGraph 的 `conditional edges` 和 `Command(goto=...)` 要建模的东西。
结论仍然不变，但理由要换成更准的那个：**分支目标集合在编写时是封闭的**（4 个取值 / 回到 index *i*），
**循环有硬上限**（1 次），用 `if/else` + `while` 表达出来是十几行，读代码即读架构；
换成声明式 edge，要同时付 state schema、checkpointer、trace 语义三份成本，换回来的只有声明式表达。

因此**触发重考点也随之改写**（取代原文那句「如果出现真正的动态 workflow」）：

> 触发条件不是「出现分支」，而是以下任一：
> ① 路由目标集合在编写时**不封闭**（例如由 LLM 决定下一个调用哪个 agent，且候选集来自注册表而非枚举）；
> ② 出现**跨进程 / 跨请求需要续跑**的长流程（例如人工审批后隔天继续）；
> ③ 出现真正需要 fan-out 并行且有依赖图的执行计划。
> 目前三条都不满足。

**CrewAI（2026-09 复读）**：它现在是两层。`Crew` 是「角色扮演 + 自治协作」，
我们结构上就不匹配（单 agent 内部多步，不是多角色对话）——这条与原表判断一致。
`Flow` 是后加的事件驱动层（`@start` / `@listen` / `@router`），官方现在把它定位成生产编排原语，
「Crew 是工人，Flow 是管事的」。对我们而言 Flow 层退化成「装饰器版的函数调用图」：
把 `run()` 里 5 行顺序调用改写成 5 个带装饰器的方法加一个 state 类，收益仍然只有声明式表达。

**AutoGen（2026-09 复读，结论比原表强）**：v0.4 换成了事件驱动 actor 模型，本身是像样的设计。
但 **2025-10 AutoGen 与 Semantic Kernel 双双进入 maintenance mode**（只收 bugfix 与安全补丁），
团队合并去做 Microsoft Agent Framework，后者 2026-04 发 1.0 GA。
这条把原表「中文文档少」那个薄弱理由换成一个真实的 ADR 级论据：
**编排框架层在 18 个月里换了一代**（LangChain LCEL → LangGraph 1.0；AutoGen v0.2 → v0.4 → MAF），
而「Python 函数按顺序调用」没有迁移成本。这不是「原生比框架好」的空泛主张——
框架换代对**吃了它抽象**的代码是真金白银的迁移，对只吃了 `@observe` 的代码不是。

**面试口径（三句）**：
1. 我读了但没做，因为四个机制里三个（checkpoint / interrupt / subgraph）在「单进程、几十秒、无人工等待」的形态下没有对应需求，第四个（Send 并行）我们的瓶颈在 Planner 不在编排层；
2. 我们确实有分支和重试循环，但目标集合封闭、循环有上限，`if/else` 表达够用，声明式换不来能力只换来表达；
3. 触发我改主意的条件我写死在 ADR 里了——路由不封闭、需要跨请求续跑、或需要有依赖图的并行。

*参考*：[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、
[Durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)、
[interrupt reference](https://reference.langchain.com/python/langgraph/types/interrupt)、
[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)、
[AutoGen Update discussion](https://github.com/microsoft/autogen/discussions/7066)、
[Microsoft Agent Framework](https://devblogs.microsoft.com/agent-framework/microsofts-agentic-ai-frameworks-autogen-and-semantic-kernel/)。

---

<a id="adr-003"></a>

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

**Update 2026-09-02：trace 从「有埋点」到「埋点可信」——四个各自独立的洞**

上面写的「trace tree 精确到 span」在两条路径上其实不成立，且都不报错。集中修一轮：

1. **P3 并行 drill 的 trace 是断的**（`82e0708`）。Langfuse v3 的上下文靠
   contextvars（OTel context）传，`ThreadPoolExecutor` 默认不传给 worker 线程
   ——每个 drill 各自成了 root trace，P3 的树从来没连上过。改为每个任务各拷一份
   `Context`（共用一份会在两线程同时进入时抛 `cannot enter context: already entered`，
   而真实 drill 是 30–60s 的 LLM 往返、必然重叠）。测试用 `Barrier` 强制并发重叠，
   断言底层 contextvar 继承而非 `trace_id`——测试环境未必配 key，两边都是 `None`
   会让断言空过。
2. **P2 的 step 元数据被逐轮覆写**（`30caa22`）。没有 per-step span 时，
   `update_current_span` 每轮都在改写 `p2_analysis_run` 自身的 metadata，只有最后
   一步留得下来——加了元数据但不生效。抽出 `_run_step` 挂 `@observe(name="p2_step")`，
   与 P3 早就有的 `_execute_single_drill` 对齐。
3. **`usage_details` 补 cache / reasoning，并显式给 `total`**（`51ce65b`）。
   实测本地 Langfuse v3：服务端把 `usage_details` 里所有非 `total` 键**求和**当作
   total。而 `reasoning_tokens` 是 completion 的子集、`cached_tokens` 是 prompt 的
   子集，直接加细分键会让 353 变成 692——**token 凭空翻倍且不报错**。改用嵌套
   `prompt_tokens_details` 则不双计，但细分数据被服务端静默丢弃。最终取显式写
   `total` 压住服务端求和。
4. **span 增加 `local_operation` 维度**（`5b97b60`，外部对照：SQLBot `chat_log`
   的同名布尔量）。`sql_generation` 与 `sql_execution` 在 trace 里并排躺着，看总耗时
   分不出该优化 prompt 还是该优化 SQL。判据取严：`local_operation=True` ⟺ 该 span
   耗时里不含任何模型推理（chat 与 embedding 都算），据此 `schema_linking` 归 LLM 侧
   （它内部调 `qwen_client.embed`），只有 `sql_validation` / `sql_execution` 是 local。
   20 处裸 `@observe` 全部迁到 `observe_local` / `observe_llm`（`obs/span_kind.py`），
   并加守门测试：`src/` 下再出现裸 `@observe(` 就 CI 失败——与 catalog 静态门禁同一
   思路，「新增路径忘了接闸」必须是 CI 失败而不是线上才发现。

第 4 项踩到一个会**静默毁掉第 3 项成果**的坑，值得单记：Langfuse 3.15 的
`update_current_span` 会构造 `LangfuseSpan(as_type="span")`，而
`LangfuseObservationWrapper.__init__` 无条件执行
`otel_span.set_attribute(OBSERVATION_TYPE, as_type)` → 在 generation/embedding
observation 里调它会把类型**降级成 span**，`qwen_chat` / `qwen_embed` 的 token 与
cost 归因当场失效（`update_current_generation` 同理会把 embedding 改写成 generation）。
所以打标直接写 OTel 属性，不碰 observation type。降级在测试里实测复现过
（`assert 'span' == 'embedding'`），两条防降级断言留在 `tests/shared/test_span_kind.py`。

**共同教训**：这四条没有一条会抛异常、也没有一条会让分数变化——trace 照样有、
数字照样出，只是错的。可观测性本身缺乏可观测性，唯一的办法是给埋点写断言
（并发重叠、per-step 计数、token 不双计、observation type 不降级），而不是靠肉眼看 UI。


---

<a id="adr-004"></a>

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

<a id="adr-005"></a>

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

**外部对照（2026-09-01，DB-GPT v0.8.2 代码核对）**：

上表「regex + 黑名单：脆弱」那一行，DB-GPT 提供了实物样本。其新 agentic 路径的
`sql_query` 工具是全项目唯一的 SQL 防线，实现为**对原始字符串的首关键字黑名单**：

```python
sql_upper = sql_stripped.upper().lstrip()
forbidden = ["INSERT","UPDATE","DELETE","DROP","ALTER","TRUNCATE","CREATE","GRANT","REVOKE"]
for kw in forbidden:
    if sql_upper.startswith(kw):   # 只看开头
        return "安全限制: 仅支持 SELECT 查询。"
```

`WITH x AS (...) DELETE ...`、`/* c */ DROP ...`、`(DELETE ...)`、`SELECT 1; DROP TABLE t`
全部放行；而 `SELECT pg_read_file(...)` 这类**首词是 SELECT 的数据读取函数**整类不在防护范围内
——正是本项目 2026-09-01「函数黑名单」提交（顶层白名单管不到 SELECT 里调了什么）要堵的洞。

且放行后调用的 `run()`（`datasource/rdbms/base.py:620`）对非 SELECT 语句会
`self._write(command)` 并显式 `session.commit()`，DDL 走 else 分支直接 `session.execute()`。
其经典 scene 路径连这层字符串检查都没有，prompt 里也未约束 SELECT-only。
即：**默认配置下 LLM 生成的 DELETE/DROP 会落到库上，唯一防线是数据库账号权限**
（这也从反面支持 ADR-010 的双用户隔离）。

**Update 2026-09-01 / 09-02：函数级黑名单，以及一条按包名前缀的规则**

原决策的第 2 步「只有 SELECT」是**顶层语句类型**检查，管不到 SELECT 里调了什么
（`13d9359`）。`SELECT pg_read_file('/etc/passwd')` 顶层是 SELECT，表/列检查也过
——上面 DB-GPT 那段拿来当反面样本的洞，本项目自己同样敞着。补 AST 级函数黑名单：
遍历 `exp.Anonymous` / `exp.Func` 节点比对函数名。

随后（`a512a52`，对照 SQLBot v1.10.1 的分库危险函数表）发现黑名单只覆盖 PG/DuckDB
——而 `dialect` 是构造参数，**将来指向别的库时 MySQL/MSSQL/Oracle 那几类就是裸奔**。
补三类：MySQL `load_file`；MSSQL `xp_cmdshell` / `sp_executesql` / `openrowset` /
`opendatasource` / `openquery`；PG 原先漏掉的文件面与进程面（`pg_file_read` /
`pg_ls_logdir` / `pg_terminate_backend` 等）。另收侦察类 `version` /
`current_user` / `current_database`——对指标口径驱动的 BI 查询零业务价值，只泄露
库版本、账号与网络面。

**新增一条按包名前缀的规则 `_FORBIDDEN_QUALIFIERS`**，因为按函数名匹配对 Oracle
结构上失效：`utl_http.request(...)` 在 sqlglot 里是 `Dot(Identifier, Anonymous)`，
`Anonymous` 只带方法名 `request`。而 `request` / `sleep` 这类通用词进黑名单又会大面积
误伤业务列名，只能拦包名。

**有意不收 `user` / `session_user`**：实测二者裸写会被 sqlglot 解析成 `Column` 而非
`Func`，放进黑名单只会误伤名为 `user` 的业务列。已加测试锁住这条「不过度拦截」——
黑名单的失败模式是双向的，只测「拦住了坏的」会让它慢慢变成一个误伤机器。

---

<a id="adr-006"></a>

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

**外部对照（2026-09-01，DB-GPT v0.8.2 代码核对）**：

DB-GPT 的 `DataScientistAgent` 走的是相反路线：`max_retry_count = 5`，且校验方式是
**执行验错**而非静态校验——`correctness_check` 把 SQL 真跑一遍，看有没有返回行。
两个后果值得记下，都印证本 ADR 与 ADR-005 的取舍：

1. **空结果集被判为失败**：`if not values or len(values) <= 0: return (False, ...)`。
   一个语义正确、业务上本就该返回 0 行的查询会被判错并触发重试，模型被反复施压
   「改写 SQL 直到出数」——把「无数据」和「SQL 错」混为一谈，是结构性的幻觉诱因。
2. **校验发生在副作用之后**：`ChartAction.run()` 先 `query_to_df(sql)` 出图，
   `correctness_check` 再跑一遍验证，同一条 SQL 每轮执行两遍；若该 SQL 有写副作用，
   等「校验」完已经晚了。

而它的经典 scene 路径 (`base_chat.py`) 的重试是 `@async_retry`，默认 `retries=1`
（即实际不重试），且**不把错误信息回喂模型**——纯粹重摇一次，与本项目
Reflector 基于错误分类拼 repair hint 的做法不是一回事。

结论：重试预算的大小不是关键，**校验在执行前还是执行后**才是。本项目
「sqlglot 静态校验（ADR-005）+ 单次带 hint 重试」的组合方向正确，维持不变。

---

<a id="adr-007"></a>

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

<a id="adr-008"></a>

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

<a id="adr-009"></a>

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

<a id="adr-010"></a>

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

<a id="adr-011"></a>

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
| [ADR-001](#adr-001) | LLM 选 Qwen3.6-max-preview | Accepted |
| [ADR-002](#adr-002) | 自研函数链编排 | Accepted |
| [ADR-003](#adr-003) | Langfuse v3 self-hosted | Accepted（2026-09-02 埋点可信度四修，见 Update） |
| [ADR-004](#adr-004) | LLM-as-judge 评分 | Accepted |
| [ADR-005](#adr-005) | sqlglot AST 校验 | Accepted（2026-09-01/02 补函数级黑名单，见 Update） |
| [ADR-006](#adr-006) | Reflector 单次重试 | Accepted |
| [ADR-007](#adr-007) | YAML 事件库埋雷 | Accepted |
| [ADR-008](#adr-008) | Embedding + jieba schema 检索 | Accepted |
| [ADR-009](#adr-009) | Streamlit UI | Accepted |
| [ADR-010](#adr-010) | PostgreSQL 双用户隔离 | Accepted |
| [ADR-011](#adr-011) | BIRD-financial 只跑 P1 + SQLite 直连 | Accepted |
| [ADR-012](#adr-012) | Q-SQL few-shot 检索注入 | Accepted（默认阈值保守） |
| [ADR-013](#adr-013) | 语义层 Metric Resolver 原型 | Accepted（2026-08-12 已接线到 P1 主路径，见 Update） |
| [ADR-014](#adr-014) | 评测集 gold 的可信度守门 | Accepted |
| [ADR-015](#adr-015) | P2 评分器中文分词修复 | Accepted（三个饱和维度待决） |
| [ADR-016](#adr-016) | P2 rubric LLM judge | Accepted |
| [ADR-017](#adr-017) | RLAC session 属性注册表与值域门禁 | Proposed（2026-09-04 执行面首次真实调用 + 拒绝不再回退 + session 值存在性探针，见两条 Update；注册表本体待「接入认证」触发） |
| [ADR-018](#adr-018) | MCP server 只暴露 P1，身份锁在服务端 | Accepted（2026-09-08 真跑：三种身份三种结果） |

新增 ADR 从 `ADR-019` 继续追加。修改现有决策请把 Status 改为 `Superseded by ADR-XXX` 并保留原文。

---

<a id="adr-012"></a>

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

<a id="adr-013"></a>

### ADR-013: 语义层 Metric Resolver 原型（6 指标模板，LLM 抽 spec，fallback 回原 NL2SQL）

**Status**: Accepted（原型阶段，未接线到 P1 主路径）
**Date**: 2026-07-08

**背景**：dbt 2026 benchmark 显示语义层比裸 text-to-SQL 高 10-14 分（GPT-5.3 Codex 从 84.1% → 100%）；WrenAI 全栈押注这条路线。ADR-012 postmortem 里 few-shot 净效应 ≈ 0 也验证了另一个方向：**改 prompt/RAG 是隔靴搔痒，真正的杠杆在"先收窄问题空间"**。原型目标：定义几个核心银行指标模板，把 NL 问题降维成 `{metric, dims, filters, time_window}`，走 governed SQL 路径。

**方案**：
- **[`config/metrics.yaml`](./config/metrics.yaml)** — 6 个种子指标：`deposit_balance` / `loan_balance` / `customer_aum` / `customer_count` / `product_count` / `transaction_amount`。每个指标声明 fact_table + fact_alias、metric_expr（`AVG(fbd.balance)` 之类）、hard_filters（永远 AND，护业务口径）、date_column、joins（join_id → SQL）、dim_catalog、filter_catalog（含 enum_values 严格校验）。
- **[`src/chat_bi_agent/agents/p1/metric_resolver.py`](./src/chat_bi_agent/agents/p1/metric_resolver.py)**：
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

### Update 2026-08-27：catalog 静态门禁 + 血缘 + 全局 join 剪枝

兑现 [Update 2026-08-12](#adr-013) 结尾留的那条教训——原话是「**catalog 类改动必须
配一个「全组合真打 DB」的回归扫描**」。当时 6 指标 × 全部 dim/filter 共 48 个组合里
18 个是坏的（`fbd.account_type` 列不在 fact 表上、`ft.channel` 实际叫
`transaction_channel`、`dc.is_active` 是 boolean 却声明成 string），全靠真打 PG 才
暴露。那条跟进项从那时起一直空着，而指标已从 6 个涨到 18 个、`requires_join` 有 40 处，
同类错误的暴露面比当时大三倍。

**做成静态的，不是真打 DB。** 对照 `schema/schema_docs.yaml` 而非连接 Postgres——
代价是漏掉「schema_docs 与真库不同步」这一层（另有 `tests/schema/` 守），收益是它能
当常规单测跑，每次改 catalog 都自动过一遍，而不是靠人记得跑扫描。这个取舍是本次的
核心决定：**能天天跑的弱检查 > 需要环境的强检查**。

**新增**：

- `src/chat_bi_agent/agents/p1/metric_lineage.py`
  - `validate_catalog(catalog, loader)` → `list[CatalogIssue]`，比对 catalog 里每一处
    **带别名前缀的列引用**与 schema。裸标识符不查——混着 SQL 函数名和关键字，判不准，
    宁可漏报也不误报。
  - `metric_lineage(metric)` → `Lineage(required_tables, conditional_tables)`
- `tests/p1/test_metric_lineage.py` — 11 例，含真实 catalog 门禁
- `scripts/check_metric_catalog.py` — 同一套检查的人读版 + 血缘表

**门禁验证过能咬人**：把当年那两个 bug 注回 `config/metrics.yaml`，2 处注入 →
6 个指标全部捕获（`deposit_balance` / `loan_balance` / `total_balance` 命中
`account_type`，`transaction_amount` / `transaction_count` / `avg_transaction_amount`
命中 `channel`）。**不验证门禁会不会红的门禁不算门禁**——与 ADR-013 那次「单测全绿
但 catalog 全坏」是同一个道理。

**血缘分两层，不拍平**：

```
deposit_balance
  必然  fct_balance_daily, dim_account
  条件  dim_branch    ← dim:branch_name, dim:branch_city, filter:branch_city, filter:branch_name
  条件  dim_customer  ← dim:customer_tier, filter:customer_tier
```

`dim_account` 在「必然」里是因为 hard_filter 挂在它上面，改它影响该指标的**每一次**
查询；改 `dim_branch` 只影响用了 branch 维度的那部分。拍平成一个表清单会让影响分析
失真，这是分层的唯一理由。

**顺带挖出并修掉的结构问题：全局 join 注册表过度挂载**

校验第一版报了 16 处错，查下来**不是真错**——[Update 2026-08-14](#adr-013) 引入的全局
join 注册表会把每条 join 挂到**每个** metric 上，而 `render_sql_from_spec` 只拼
`hard_filter_joins` 与被选中 dim/filter 的 `requires_join`。没人引用的 join 永远
拼不出来，所以不是当前的错。

但它们也不是无害的。根因是**全局模板假设每个 fact 都带 account_id / branch_id /
customer_id / product_id，而这只对真 fct 表成立**：

| fact 表 | account_id | branch_id | customer_id | product_id |
|---|---|---|---|---|
| fct_balance_daily / fct_holding / fct_transaction | 有 | 有 | 有 | 有 |
| fct_risk_event | 有 | 有 | 有 | — |
| fct_campaign_response | — | 有 | 有 | 有 |
| dim_customer | — | 有 | 有 | — |
| dim_product | — | — | — | 有 |
| dim_branch | — | 有 | — | — |

`customer_count`（fact 是 `dim_customer`）身上挂着 `JOIN dim_account da ON
dc.account_id = da.account_id`，而 `dim_customer` 没有 `account_id` 列。这些 join
不是「暂时没用」，是**结构上不可能**——谁给对应维度加一条 `requires_join` 就会炸。

**改法**：`_resolve_joins` 增加 `referenced` 参数（= `hard_filter_joins` ∪ 各
dim/filter 的 `requires_join`，与 `needed_joins` 同源），**没被引用的全局 join 不挂**。
两个边界：

- **本地 joins 不剪**——那是作者显式写的逃生舱，意图明确；没人引用时由校验器报成
  `severity="latent"`
- **不改写作方式**——新增一个 `requires_join: [branch]` 的维度时 branch 自动变可达，
  照常挂上。剪枝只影响从来没人提过的那些

改后 18 个指标的 join 总数 25 条，`product_count` 归零（它本来就不需要任何 join），
16 处潜伏项清零。

**这次没做、且建议不做的：指标版本管理**

排期里与血缘并列的一项。结论是**划掉**：`config/metrics.yaml` 在 git 里，
`git log -p config/metrics.yaml` 就是带作者和时间的口径变更台账；真正需要的
`effective_from` / `effective_to`（历史数据按旧口径解释）在合成 seed 数据上没有
可验证的场景；且它的难点在流程（谁批、怎么留痕）不在代码。等有真实口径变更时再说。
参照 Snowflake `AI_VERIFIED_QUERIES` 的 `VERIFIED_AT` / `VERIFIED_BY` 是那时的抄法。

**已知局限**：

- 只查 `alias.column` 形式的引用，裸列名不查
- `schema_docs.yaml` 故意省略了 `dim_*` 的 `create_time` / `update_time`，catalog 若
  引用这两列会误报（当前没有引用）
- 校验的是「列存在」，不校验类型——`dc.is_active` 那类 boolean/string 错配仍需
  `test_is_active_filter_typed_boolean_not_string` 这种针对性断言兜

**Trace**：代码见本次提交；测试 `tests/p1/test_metric_lineage.py`（11 例）+
`tests/p1/test_metric_resolver.py` 新增剪枝 3 例；全量 664 passed / 55 skipped

---

**Update 2026-08-28：口径分歧以 catalog 为准；34 题标尺重测；分流必须带值域探针**

三件事，都是这一轮扩 catalog（dim↔filter 对称、ORDER BY/LIMIT、prefilter 双路召回）
之后才暴露出来的。

**1. gold 与 catalog 的口径分歧，一律以 catalog 为准。**

`example_pool_prod.jsonl` 里「华东大区各省 4 月日均存款余额」那条，gold 用
`p.product_category = 'DEPOSIT'` 定义存款，catalog 的 governed 定义是
`da.account_type IN ('CURRENT','SAVING')`。**语义层存在的意义就是终结这种分歧**——
让两套定义并存，等于把「存款是什么」这个问题留给每条 SQL 各自回答。所以定死：
catalog 是唯一口径来源，gold 与它冲突时错的是 gold。

实测这次是零代价的：两条 SQL 在真库上返回完全相同的两行（上海 137077.25 /
江西 99730.64）。合成数据生成器把 `account_type` 与 `product_category` 做了类别级
双射（`dimension_generator.py:240-246`），DEPOSIT 只对应 CURRENT/SAVING。
**但别把「这次相等」当成「一直相等」**——真实行内数据没有这个双射，那时这个决策
才真正花钱，而那正是它该被提前定死的理由。

漂移检测器按字符串字面量比对，看不出两者等价，会一直报。所以 `--migrate` 加了
`--adjudicated`：人逐条裁决过的漂移才允许带着漂移移出池子，其余漂移一律留下。

**2. 34 题标尺重测：precision 仍是 1.000，但标尺本身改了一条，须知情。**

放宽过滤面之后重跑，**按旧标签算 precision 掉到 0.9444**（唯一假阳性 `mr_n12`
「存款余额最高的前 5 个分行」）。查它的标注理由——`route_note: "Top-N——spec 无排序/取顶"`
——**正是本轮 ORDER BY/LIMIT 特性干掉的那个前提**。它生成的 SQL 与 `expected_sql`
逐字等价，score 1.0、result_match True。按「前提作废的测试要改写，不要删」这条，
改标为 `metric` 并在 YAML 里留了前因。

| | 08-14 基线 | 本轮/旧标 | 本轮/新标 |
|---|---:|---:|---:|
| 路由 precision | 1.000 | 0.9444 | **1.000** |
| recall | 0.75 | 0.85 | **0.8571** |
| F1 | 0.8571 | 0.8947 | **0.9231** |
| TP/FP/FN/TN | 15/0/5/14 | 17/1/3/13 | **18/0/3/13** |
| avg_score | 0.9436 | 0.9662 | 0.9662 |
| result_match | 0.7059 | 0.7941 | 0.7941 |

**改标尺去修好看的数字是重罪，所以把判据写死在这里**：只有当标签的理由是一句
「模板表达不了 X」、而 X 已经实现且生成的 SQL 与 gold 实测等价时才准改；
凡是理由涉及业务语义的，一个字都不许动。同一轮里 `mr_n09`（argmax 按日）就
**没有**改标——它的旧理由（无排序）同样作废，但仍该走 nl2sql，因为
`transaction_amount` 的 `dim_catalog` 没有按日维度，`GROUP BY dt` 表达不了。
只订正了理由，标签保持 nl2sql。

**遗留风险**：`mr_n12` 的 cosine 是 0.6314，阈值 0.63，余量 0.0014。两轮重跑都
稳定命中（cosine 是确定性的），但换 embedding 模型或改 alias 都可能把它推下去，
届时表现为 recall 掉一格而非报错。

**3. 拿分流结果动 few-shot 池，必须带 `--probe`。**

`triage_example_pool.py` 原本不注入 `probe_fn`（string filter 的值域探针，要连 PG），
上一轮据此判定 idx24「上海分行 5 月反洗钱告警数」由 B 升 A，记作 dim↔filter
对称性修复的战果。**注入探针后它掉回 B**（`value_out_of_domain`），而且查下去
发现 gold 自己就是坏的：`dim_branch.city` 根本没有 `'上海'`（真正的「上海分行」
是 `BR_PROV_0003`，省级行，`city` 为 NULL），gold SQL 实测返回 `alert_count = 0`。

不带探针的分档偏宽松，拿它决定「哪些题移出池子」会**两头落空**——移出去了，
生产上却被探针拒绝退回 NL2SQL，而此时 few-shot 兜底也已经没了。所以
`build_router()` 把 probe 提成显式参数，脚本不带 `--probe` 时会打印警告。

**分流执行结果**（`--probe`，31 条生产池）：A 14（干净 13 + 裁决 1）/ B 12 / C 5。
14 条移出 `data/example_pool_prod.jsonl`（31 → 17），归档到
`data/example_pool_metric_governed.jsonl`（不删，可回滚、可审计）。移出前逐条拿
gold 与 governed SQL 在真库上比对结果集，14/14 完全一致。

**promotion 不加归档排除名单（已定，2026-08-28）**：`nightly_promote.sh` →
`bootstrap_prod_pool.py` 按 `example_id` 合并去重，**不认识归档文件**——同一条
(question, sql) 再被 👍 一次就会重新灌回池子。**这是有意保留的**：一条已经交给
语义层的问题重新以 few-shot 形式冒出来，说明它在生产上没走成 governed 路径而是
退回了 NL2SQL，那正是语义层退化的信号，堵住它等于把告警静音。代价是这次迁出可能
被 cron 撤销，可接受——迁出本来就该按 catalog 现状重新判定，而不是一次性生效。

**一条坏 gold 已剔除**：`d525ae0fc0e3`「上海分行 2026 年 5 月的反洗钱告警数量」，
gold 实测返回 `alert_count = 0`。返回空的样本当 few-shot 是有害的——它教模型去
filter 一个库里不存在的值（`dim_branch.city` 没有 `'上海'`）。剔到
`data/example_pool_quarantine.jsonl` 并在记录里写了复活条件。

顺带把迁出后剩下的 17 条 gold 全部打了一遍真库，确认这类缺陷**只有这一条**：
其余 16 条都返回非空且非全零。**gold 会返回空**这件事在池子里没有任何机制拦——
`bootstrap_prod_pool.py` 只认 👍，不验证 SQL 真的解出了东西。这是下一个该补的门禁。

**池子最终状态**：原 31 条 = 生产池 16 + governed 归档 14 + 隔离 1，三份互不重叠、
并集等于原始。

**Trace**：`scripts/triage_example_pool.py` 加 `build_router()` / `split_pool()` /
`--probe` / `--migrate` / `--adjudicated`；测试 `tests/scripts/test_triage_example_pool.py`
（10 例）；全量 743 passed / 5 skipped，`ruff check` 干净，
`check_metric_catalog.py --quiet` 退 0

---

**Update 2026-08-28（二）：阈值重扫结论「不改」；promotion 补 gold 执行门禁**

**1. prefilter 阈值 0.63 维持不变。**

重扫的前提是先修一个静默 bug：`sweep_prefilter_threshold.py` 自己重算 cosine
（单路，只 embed 整句），双路召回上线后与 `try_route` **悄悄分叉**——脚本注释里
「离线与跑批偏差 0.000218」那条保证早就失效了，而失效方式是静默的：扫出来的
阈值看着正常，只是对不上线上行为。已把打分收敛到
`MetricRouter.rank_metrics()`，线上与离线物理同源，重测偏差 0.000115。

扫描结果（34 题，双路 cosine。**注意 FP 是 prefilter 误触即成本，不是错误路由**）：

| 阈值 | TP | FP | FN | TN | prec | recall | F1 | |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0.6300 | 19 | 8 | 2 | 5 | 0.704 | 0.905 | 0.792 | ← 当前默认 |
| 0.6410 | 18 | 6 | 3 | 7 | 0.750 | 0.857 | 0.800 | |
| 0.6607 | 17 | 5 | 4 | 8 | 0.773 | 0.810 | 0.791 | CV 中位 |
| 0.6640 | 17 | 4 | 4 | 9 | 0.810 | 0.810 | **0.810** | 全集 argmax |

**为什么不动**：

- 抬到 argmax 0.6640 要丢掉 `mr_m15` 和 `mr_n12` 两条真阳性，换来少 4 次
  白花的 LLM 调用。这两条走 governed 路径时 score 都是满分——**拿正确答案换
  调用次数，方向错了**。
- 200 次随机半分交叉验证：样本内 F1 0.828、样本外 0.738，过拟合幅度 0.09；
  选出的阈值 σ=0.0376，在半个区间内飘。0.63 与 0.6410 的 F1 差 0.008，
  **落在这个噪声带里，不是真实差异**。
- 这张表的 FP 该读作成本：过了阈值还有 resolve 判空和值域探针两道闸，
  实测整轮 routing precision 是 1.000（零错误路由）。用 F1 选阈值等于把成本
  和正确性加权成一个数，而这两者在这里的量纲完全不同。

**代价要记账**：双路召回把 prefilter 命中率从 0.6176 抬到 0.7941，误触 5→8，
`fallback_rate` 0.2857→0.3333。多出来的是 LLM 调用，不是错答案。`mr_n12`
卡在 0.6314、离阈值只有 0.0014——换 embedding 模型或改 alias 都可能把它推下去,
届时表现为 recall 掉一格而非报错。**换模型后必须重跑这个扫描**。

**2. promotion 补 gold 执行门禁（默认开）。**

`bootstrap_prod_pool.py` 原本只认 👍，不验证 SQL 真的解出了东西——
`d525ae0fc0e3` 就是这么混进池子的。现在每条 gold 在 embedding **之前**打一次
真库，三种情况拒收：SQL 报错、结果集为空、**单行且每个值都是 0/NULL**。

第三条是重点：`d525ae0fc0e3` 返回的是**一行** `{'alert_count': 0}` 而不是空集，
只判空集会漏掉它，而它恰恰是这个门禁存在的全部理由。该规则只在单行时生效——
分组结果里某一组是 0 是正常的，多行一律放行。

探针连不上时**抛异常而不是静默放行**：宁可让 nightly cron 失败，也不要往池子里
灌未经验证的样本。`--no-verify-gold` 可以关，但会打印警告。

已拿真数据验证过这个门禁会红：把隔离的 1 条 + 在库的 16 条一起过，
恰好拒收 `d525ae0fc0e3`，其余 16 条全部放行。**不验证门禁会不会红的门禁不算门禁。**

**Trace**：`MetricRouter.rank_metrics()`；`sweep_prefilter_threshold.py` 改走它；
`bootstrap_prod_pool.py` 加 `gold_yielded_nothing()` / `reject_empty_gold()` /
`--no-verify-gold`；测试 `tests/scripts/test_bootstrap_prod_pool_gold_gate.py`（7 例）
+ `test_rank_metrics_matches_the_cosine_try_route_gates_on`；
全量 751 passed / 5 skipped

---

**Update 2026-08-29：存量/流量语义；补指标补维度；idx7 的召回**没有**解决**

**1. 存量指标跨日求和是错的，模板此前无法表达时点口径。**

生产池「华东大区 2026 年 4 月的存款余额总额」prefilter 未命中。原以为是召回
问题、加个 alias 就行，查完发现完全不是：

    deposit_balance.metric_expr = AVG(fbd.balance)              区间日均
    该题 gold                   = SUM(...) WHERE dt = '2026-04-30'   末日时点

问的是时点总额，指标建的是区间日均，**两者本来就不是一回事**。给
`deposit_balance` 加 alias 等于把语义不符的指标拽进阈值，制造一个真正的假阳性。

而直接建个 `SUM` 指标同样不行：模板只会拼 `date_column BETWEEN start AND end`，
**存量指标跨日 SUM 等于把同一笔钱数 30 遍**。所以先补机制：`time_semantics`
字段，默认 `range` 保持原语义；标 `point_in_time` 的指标时间窗渲染成
`date_column = 末日`（期末余额，财务通行约定），抽取 prompt 同步标注。

新建 `deposit_balance_total`，aliases 刻意避开「存款总额」「存款金额」这类与
`deposit_balance` 太近的说法，必须带「合计/总额/期末/月末」才算它。
**这个规避是有效的**：标尺上 `mr_m02`「高净值客户的存款余额平均」和 `mr_n12`
「存款余额最高的前 5 个分行」都留在 `deposit_balance` 上没被抢走。

**2. 按日维度三个 `fct_transaction` 指标都加，不是只给 `transaction_amount`。**

否则「哪天笔数最多」被拒而「哪天金额最高」能答，这种任意的能力缺口正是本项目
在治理的东西。`dim↔filter` 门禁对 `date_column` 有豁免，不误报。
据此把 `mr_n09` 改标 `metric`——上一条 Update 里写的「补维度前不要改标」，
前提已兑现，生成 SQL 与 gold 实测同值（2026-05-29 / 17642228.37）。

**3. 标尺重跑：零假阳性保住了。**

| | 08-28 | 08-29（新 catalog） |
|---|---:|---:|
| 路由 precision | 1.000 | **1.000** |
| recall | 0.8571 | 0.8636 |
| F1 | 0.9231 | **0.9268** |
| TP/FP/FN/TN | 18/0/3/13 | 19/**0**/3/12 |
| avg_score | 0.9662 | 0.952 |

`avg_score` 掉的 0.0142 集中在 `mr_n14`（−0.400）与 `mr_n11`（−0.100），
**两题都不走 governed 路径**（一个纯 nl2sql，一个回退后由 nl2sql 出 SQL），
属 ADR-013 记过的 nl2sql 臂跑间噪声。**但没有重复采样证实**，见第 5 条。

**4. idx7 的召回没解决——补指标没有接住它。**

    补指标前 cosine 0.6084 → 补指标后 0.6153（阈值 0.63，仍差 0.0147）

题面里「存款余额总额」与新 alias **逐字相同**，top-1 cosine 却只有 0.6153——
整句的时间/地域修饰（「华东大区 2026 年 4 月…是多少？」）把相似度稀释掉了，
双路剥离也没救回来。这与 `_strip_time_modifiers` 要治的是同一个病，只是这条
剥完仍然不够。

**结论：不为这一条调 alias 或动阈值。** 补指标的长期价值是补上了「时点 vs
区间」这个真实的口径缺口（`SUM` 存量指标本来就没法正确渲染），而单题召回是
prefilter 的老问题，靠给单题喂 alias 去解决等于拿标尺喂阈值。留作待办。

**5. 两项验证没做完——API 配额耗尽（403 Free quota exhausted）。**

- **few-shot 复测的基线臂（旧池 31 条）没跑完**，「16 条 vs 31 条」的对比不存在。
  另外这轮暴露出**标尺选错了**：34 题标尺上 few-shot 只命中 5/34 题且全是
  `mr_n*` 非指标题，这个分布本来就几乎吃不到生产池的 few-shot，就算基线臂跑完
  也说明不了池子缩水的影响。要测该用同分布的
  `precision_retrieval_evaluation.yaml`，或对生产池自己做留一验证。
- **分流快照 `docs/triage/triage_fix7_newmetric.json` 不可用**：16 条里 11 条
  报 403，那 11 条全被记成 C 档，是假象不是真分档。配额恢复后必须重跑覆盖。

**Trace**：`Metric.time_semantics`；`config/metrics.yaml` 加
`deposit_balance_total` + 三个交易指标的 `dt` 维度；测试
`test_point_in_time_metric_pins_to_one_day_not_a_range` 等 4 例；
全量 755 passed / 5 skipped，`ruff` 干净，`check_metric_catalog.py --quiet` 退 0

---

**Update 2026-08-29（二）：补完两项验证。few-shot 迁出无可测退化，但先前的读法是错的**

配额恢复后补跑上一条 Update 第 5 项欠下的验证。

**1. 分流重跑，结果存 `docs/triage/triage_fix8_probe.json`。**

上一条 Update 点名不可用的 `triage_fix7_newmetric.json`（16 条里 11 条是 403
假数据、全被记成 C 档）**已删除**。当时写的是「重跑覆盖」，但重跑实际写到了新
文件名，污染快照并没有被覆盖掉——一份看着正常、分档却是假的快照留在目录里，
比没有更危险，所以直接删而不是留着。

    A 0 条 / B 11 条 / C 5 条

**A 档为 0 正是预期**：能被语义层确定性接住的 14 条已经在上一轮迁出，池子里
剩下的按定义就该是接不住的。这条结果本身就是迁出完整性的验收——如果还有 A 档，
说明上一轮漏迁了。

`idx7` 确认仍在 C 档（cos 0.6153），与上一条 Update 的结论一致。

**新暴露的缺口**：「2026 年每个月末最后一天的活期存款余额加总，按月分组」现在被
`deposit_balance_total` 吸走（cos 0.7143，此前是 `total_balance`），并以
`unknown_dim` 被拒——它要的是**每月一个快照**，而 spec 只有一个 `time_window`，
`dt = 末日` 只能钉一个日子。属于正确拒绝（安全回退），但这是新指标带出来的
可见能力边界：时点语义目前只支持「一个时点」，不支持「按周期滚动取时点」。

**2. few-shot 迁出复测：无可测退化。但先做对了方法，才看到该看的东西。**

上一条 Update 里我按 `n_questions_with_examples = 5/34` 判断「34 题标尺测不出
池子缩水」——**这个读法是错的**。走 governed 路径的题根本不调 retriever，
所以那个 5 的分母里混进了 19 道压根不用 few-shot 的题。

正确做法是先做**离线检索比对**（few-shot 检索纯靠 embedding，不必打 LLM）：
拿旧池 31 与新池 16 分别对每道题跑一遍 `ExampleRetriever`，再用实际路由过滤掉
走语义层的题。结果：

| 类别 | 题数 |
|---|---:|
| 走语义层，不用 few-shot | 19 |
| 检索结果无变化 | 6 |
| 样本变少但仍有 | 3 |
| **两头落空**（丢光 few-shot 且没被语义层接住） | **6** |

有了这 9 道「可能受影响」的题，A/B 才有靶子。**同 commit、同 catalog、
唯一变量是池子**：

| | B 旧池 31 | A 新池 16 |
|---|---:|---:|
| avg_score | 0.9446 | 0.9544 |
| passed | 31/34 | 31/34 |
| 路由 precision / recall / F1 | 1.000 / 0.8636 / 0.9268 | 同左 |
| result_match | 0.7647 | 0.7647 |
| few-shot 命中题数 / 样本数 | 11 / 17 | 5 / 5 |

**别看 avg_score 那个 +0.0098**——它几乎全部来自 `mr_m06`（+0.417），而该题
**两臂都用 0 条 few-shot**、`metric_id` 相同、只有 dims 在 `branch_city`/
`branch_id` 之间跳，正是 ADR-013 上一轮已经重复采样证伪过的那条噪声题。
拿它当「迁出提升了效果」的证据是自欺。

真正该看的是那 6 道两头落空的题：**4 道分数完全不变，2 道小幅下滑**
（`mr_n06` −0.100、`mr_n13` −0.017），合计 −0.117。方向与预期一致，量级很小。

**结论：迁出没有造成可测的退化。** 但要写明这个结论的强度：受影响的样本只有
6 道，统计功效很低，−0.117 与噪声不可区分。它能支持「没看到退化」，
不能支持「证明无退化」。真要把强度提上去，得扩标尺或对生产池做留一验证。

**方法上的教训**（比结论更值钱）：**先用不花钱的离线量测圈出「可能受影响」的
子集，再决定要不要打 LLM。** 上一轮直接跑整轮 A/B，拿到的是一个被 19 道无关题
稀释、又被一道已知噪声题主导的平均分——既贵又什么都证明不了。

**Trace**：`docs/triage/triage_fix8_probe.json`（A 0 / B 11 / C 5）；
`results/mr34_newcatalog_fson_pool16_2026-08-29.json` 与
`..._pool31_2026-08-29.json`；离线检索比对脚本未入库（一次性诊断）

---

**Update 2026-08-29（三）：池子台账进 git——治理决策此前只存在于一台机器上**

`data/example_pool*.jsonl` 因为 embedding blob 被 gitignore（~4MB/1k 行）。于是
本轮分流的全部成果只活在一台机器的磁盘上：16 条生产池 / 14 条已交给语义层的
归档 / 1 条隔离，以及「idx14 的口径以 catalog 为准」这类**人工裁决**。换机器或
重建容器就全没了，重建要重跑分流（打 LLM）再让人重新裁决一遍。

**embedding 占了 98% 的体积**——去掉之后三个池子总共 13KB，进 git 毫无压力，
而 embedding 是模型的确定性产物，随时可重算。所以固化的是内容 + 决策，不是文件。

`data/pool_snapshot.jsonl`（**刻意不叫 `example_pool*`，否则被 gitignore 吃掉**）
一行一条，`bucket` 字段记的就是治理决定：`prod` / `metric_governed` /
`quarantine`。按 `(bucket, example_id)` 排序，否则每次重存都产生一坨与内容无关
的行移动，评审时看不出真正改了什么。

    python scripts/pool_snapshot.py --save      # 池子 → 台账（改完池子就跑，然后提交）
    python scripts/pool_snapshot.py --check     # 比对，有漂移退 1（不打 LLM，随时可跑）
    python scripts/pool_snapshot.py --restore   # 台账 → 重建池子（重算 embedding）

`--check` 报三类漂移，**「换桶」是最隐蔽的一种**：条数对得上，但某条题从生产池
挪到了「已交给语义层」，治理含义完全不同。nightly promote 灌进新样本时也会在
这里显形（按上一条 Update 的决定，promotion 不加排除名单，所以漂移是预期内的，
`--check` 的作用是让它可见而不是拦截）。

**往返验证**（真删掉三个池子再 restore）：非 embedding 字段逐字一致；embedding
重算为 1024 维；34 题检索命中的 example_id **全部相同**，仅 2 题的 cosine 在小数
点后第 3 位有差（embedding API ~1e-3 抖动）。另量了边界余量：检索的两条硬边界
（`min_similarity` 0.7、`leak_guard` 0.9）附近 0.005 内**没有任何条目**，
所以这点抖动不可能改变检索集合。

**Trace**：`scripts/pool_snapshot.py`；测试 `tests/scripts/test_pool_snapshot.py`
（6 例）；`data/pool_snapshot.jsonl` 31 条 / 15.7KB 入库

---

**Update 2026-08-30：吃掉 B 档两条；沿途挖出三个静默错答**

目标是 B 档里性价比最高的两条。做的过程中翻出三个此前一直在的问题，
**每一个都是「SQL 合法、结果非空、执行反馈抓不到」那一类**。

**1. `op` 可注入（已修）。** `op` 是自由文本直拼进 SQL，实测：

    {"col":"region","op":"= 'X' OR 1=1 --","val":"华东"}
    →  AND dbr.region = 'X' OR 1=1 -- '华东'

整条过滤器被中和。**对所有过滤器类型都成立**，不只 numeric。governed 模板的
立身之本就是 LLM 只能填值、不能改结构，所以 `op` 必须白名单。`numeric` 的 `val`
同样不加引号直拼，是第二个口子（bool 单独排除——与 `limit` 同一个坑）。
`numeric` 此前没有任何指标在用，属于潜伏；加 `pnl` 过滤器会把它变成实弹。

**2. `fct_holding` 的存量跨快照求和（已修）。** 它是月末快照表（22 个月末），
两个 holding 指标却标着 `range`。**被数据掩盖**：单月窗口恰好只命中一个快照，
所以一直没暴露。跨月立刻现形：

    「2026 年的持仓总市值」  6,276,367,114 → 763,284,057   （8.2x）

同时把时点渲染从「钉字面末日」改成「取窗口内最后一个有数据的快照」——问
「2026 年」时 12/31 根本没有快照，钉字面末日会返回空。

**3.「活期存款」被算成「活期+储蓄」，2 倍（已修）。**

    问：每个月末最后一天的「活期存款」余额加总
    gold  account_type = 'CURRENT'              10,999,902.51
    生成  account_type IN ('CURRENT','SAVING')  22,106,260.49

根因是 `deposit_balance` 的 aliases 里写着「活期存款」「储蓄存款」，而它的
hard_filter 是两者之和——**拿窄口径的名字去指宽口径的指标**，且 `account_type`
被写死无法收窄。这是既有缺陷，不是本轮引入，靠分流的漂移检测器逮住的。

修法是**显式具名的窄口径指标** `current_deposit_balance_total`，不是给
`deposit_balance` 开 `account_type` 过滤器（那等于让 LLM 覆盖 governed 口径），
也不是用无 hard_filter 的通用指标 + 过滤器（LLM 漏填就退化成「全部账户」，
错得更多且同样静默）。同时把误导性的 aliases 从 `deposit_balance` 摘掉。

**别把「活期存款」「储蓄存款」写回 `deposit_balance` 的 aliases。**

**4. 新能力：时点指标支持「每周期一个快照」。**

grain **不能做成指标的静态属性**——同一个指标，「5 月末余额」要单个快照，
「每月末余额，按月分组」要每月一个。所以由 spec 选中的维度决定：dim 标
`period_grain: month` 时，快照子查询按同一个 `DATE_TRUNC` 分组。按分行分组
不触发，分行不是周期。

**周期维度要一个个指标地给，不能顺手全加。** 给 `deposit_balance_total` 加上
month 之后，「对比 4 月和 5 月的存款余额变化」从**安全回退**变成了**自信地用错
口径回答**（问的是按月日均，它给月末合计，`result_match` False），标尺上直接
冒出一个假阳性。摘掉即恢复。**解锁一个维度就是解锁一批新的错答路径。**

**成果**（34 题标尺，few-shot off）。

⚠ **首次记录时这张表是错的**：起点那轮跑在 `qwen3.7-max`，后面几轮跑在
`qwen3.7-flash`——`config/local.yaml`（gitignored）的 `chat_model` 在会话中途
被改过，而我没有核对 `results/*.json` 里的 `model` 字段就做了对比，等于把换模型
的影响算进了 catalog 改动。**这正是 ADR-013 Update 2026-08-12 写死的那条
「两轮必须在同一个 commit、同一个模型上跑」，`verify_ab.py` 把 `model` 列为
CRITICAL 字段就是为了拦这个。** 补跑基线臂后的正确版本：

| | 旧 catalog / max | 旧 catalog / flash | 新 catalog / flash |
|---|---:|---:|---:|
| 路由 precision | 1.000 | 1.000 | **1.000** |
| recall | 0.8636 | 0.8636 | 0.8182 |
| F1 | 0.9268 | 0.9268 | 0.900 |
| TP/FP/FN/TN | 19/0/3/12 | 19/0/3/12 | 18/**0**/4/12 |
| avg_score | 0.952 | 0.9412 | **0.9632** |
| result_match | 0.7647 | 0.7059 | **0.7941** |

前两列之差 = 换模型；后两列之差 = 本轮改动。**换模型对路由零影响**
（TP/FP/FN/TN 逐格相同），只动了 avg_score 与 result_match。所以：

- 「precision 保持 1.000、零假阳性」在两个模型上都成立
- recall 掉那一格确实是本轮改动带来的（`mr_m06`），不是换模型
- 改进幅度比首版记的更大：同模型下 avg_score +0.022（首版记 +0.011）、
  result_match +0.088（首版记 +0.029），首版被模型差异稀释了

**教训：读 `results/*.json` 先看 `model` 和 `commit_hash`，再看分数。**
模型配置在 gitignored 的 `config/local.yaml` 里，会被别人改而不留 git 痕迹。

分流 **A 档 0 → 2**，正是那两条目标题，实测值与 gold 一致
（活期月末 9 行逐月相符；持仓笔数 11）。剩下那条漂移只是
`TO_CHAR('YYYY-MM')` vs `DATE_TRUNC('month')` 的写法差异，9 行数值完全一致。

recall 比起点低一格：4 条未命中里 3 条 **cosine 逐位相同**、起点就已未命中；
唯一增量是 `mr_m06`——ADR-013 记过的噪声题，cosine 同样逐位相同（0.6754），
只是 LLM 这轮选了 `branch_count`。**摘 alias 没有伤到召回**。

另核过一件事：新增指标会挤动每道题的 top-8 截断线。全量扫 34 题，
新增的 `holding_count` **没有把任何一道题本该被选中的指标挤出候选**。

**5. `op` 白名单必须与抽取 prompt 同步（已修）。**

白名单收 7 个运算符，prompt 却写着「op 支持 '=' 与 'IN'」。加 `pnl` 过滤器后
实测 LLM 靠推断填对了 `pnl > 0`——**但那是运气**：照 prompt 字面执行会填
`{col: pnl, op: '=', val: 0}`，渲染成 `fh.pnl = 0`（浮盈为零）而不是为正，
SQL 合法、结果非空，又一个静默错答。已在 prompt 里教全比较运算符并写明这个坑，
加了断言把「白名单收的运算符必须在 prompt 里教过」固化下来。

**这一条改完没能重测标尺——API 配额再次耗尽。** 分流侧已确认 `pnl` 那题仍填
`>`、漂移 0；标尺侧待补。

**Trace**：`_ALLOWED_OPS`；`MetricDim.period_grain`；`config/metrics.yaml` 加
`holding_count` / `current_deposit_balance_total` / `pnl` 过滤器 / 月度周期维度，
三个 holding 指标改 `point_in_time`；
`results/mr34_final_2026-08-30.json`；`docs/triage/triage_final.json`；
全量 775 passed / 5 skipped

---

**Update 2026-09-07：promotion 的判定改「取最新一条 user_feedback」；撤掉 UI 上一句不实承诺**

起因是一句提问：面板上给错 SQL 点了 👍 会不会污染池子。答案是「拦得住一类，拦不住
另一类」——顺着查出三处该修的，都跟判定谁说了算有关。

**1. `judge_pass` 摘掉。**

`load_langfuse()` 原来按 `max(user_feedback, judge_pass)` 定通过分。而 `judge_pass`
**全仓没有任何写入方**，只在这一个过滤条件里出现过一次。这行代码等于提前给一个还
不存在的 LLM judge 授权：接上那天 `max(user_feedback=0.0, judge_pass=1.0) = 1.0`，
机器判的分盖过人点的 👎。**没有输入源的 or 分支不叫「预留扩展」，叫预置的越权。**
等它真存在、且定义了跟人类反馈谁优先，再加回来。

**2. `max` 改成取最新一条 `user_feedback`。**

`max` 表达不了「改主意」。UI 靠 `st.session_state` 挡同会话二次投票，但刷新页面就能
再投——用户点完 👍 看仔细了改点 👎，`max` 把这次纠正整个吞掉。这是 `max` 在本项目
里唯一够得着的危害路径，不是多人对冲（每次提问生成独立 trace，两个人投同一条基本
不发生）。

**没有做「一票否决」，尽管它更严。** 因为 👎 的语义本来就是脏的：它自己的 help 文案
主动鼓励用户拿它表达「对但不够好」。给一个被故意放宽了语义的按钮发否决权，会误杀
正确样本。「取最新」只表达这个人最后的判断，不多不少。

时间戳做了防御：`timestamp` / `created_at` / `createdAt` 三个字段名都试，`datetime`
和 ISO 字符串都吃，naive 的补 UTC——不补的话跟 aware 的一比就 `TypeError`，而那是
在 nightly cron 里炸。全都取不到时间戳才退回按到达顺序，因为 **API 不保证返回顺序，
拿位置当时间是猜**，只配垫底。

**3.「👎 进回归测试集」是空头支票，已撤。**

UI 三处这么写（button help / 成功提示 / 已投票 caption），而全仓消费 `user_feedback`
的只有 `bootstrap_prod_pool` 一处，它只筛 ≥1.0——0.0 落进 Langfuse 就停在那，没有
任何下游。改成说实话：P1 的 👎 把这条挡在 few-shot 池外、同一条以最后一次点击为准，
P2/P3 只计满意度。保留了「不够好也可以点」的鼓励，但把代价写在旁边（这条正确样本
不会被复用）——**让点的人自己权衡，比替他们决定好**。`feedback_block.py` docstring
里钉了一条「👎 目前没有下游消费方，文案不许承诺它」并注明查证日期。

**范围之外，明确不做的一项**：把归档 / quarantine 名单接进 promotion 的排除逻辑。
归档那 14 条按 2026-08-28 的决策**故意**不堵——归档 `example_id` 原样回来是语义层
退化的信号，堵住等于把告警静音；quarantine 那条则**根本不需要名单**，gold 执行门禁
每次都会独立地把它再拒一遍，加名单纯属冗余。

**这次没有解决的**：口径错、但跑得出数的 SQL 仍然畅通无阻。执行门禁只看「解出东西
没有」，反馈聚合只看「谁最后说的」，两道闸都不看「解得对不对」。真要堵得拿 catalog
命中的题做 gold vs governed 交叉比对——`caliber_drift()` 是现成的，零 LLM 零 DB，
按 2026-08-28 那次 A/B/C 分布（A 14 / B 12 / C 5）覆盖率约 45%，B/C 档弃权。
记在这里，没做。

**Trace**：`bootstrap_prod_pool.py` 加 `FEEDBACK_SCORE_NAME` / `_score_timestamp()` /
`resolve_pass_score()`，`load_langfuse()` 改调它；`feedback_block.py` 三处文案 +
docstring；`test_bootstrap_prod_pool.py` +10 例（judge_pass 单独不算数 / 盖不过人类
👎 / 双向改主意 / 乱序只看时间戳 / datetime·ISO·naive 混用 / 无时间戳退回顺序 /
有时间戳的赢过没有的 / 空输入 / `value=None` 跳过）；`test_tab_guide.py` 那条
`assert "回归" in joined` 前提作废，按「改写不删」反向断言并补 `"最后一次"`；
全量 867 passed / 57 skipped（57 = 未起 Docker 的 PG 集成测试）

---

<a id="adr-014"></a>

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

> **Update（2026-09-08）：上面这张表里至少一半的「抖动」不是模型抖动，是评分器的偏置。**
>
> `table_score` 的抽取正则 `(?:FROM|JOIN)\s+(\w+)` 会把 `FROM txn_agg` 这种**对 CTE 的
> 引用**算成表名。这不是随机噪声而是系统性偏置，且不对称：本项目的 gold 惯用派生子查询
> （`FROM (` 不匹配 `\w+`，逃过），生成 SQL 用 `WITH` 就中招。q008 恰是最复杂的一题，
> 模型在不同跑次之间会在「CTE 写法」和「子查询写法」之间摇摆——**摇到 CTE 那侧就白扣 0.1**。
>
> 三次跑里有产物可离线重放的是两次（`scripts/replay_p1_scoring.py`，零 LLM 调用）：
>
> | 跑次 | q008 原分 | 重述 | 8 题 avg |
> |---|---:|---:|---|
> | `fbf516f` v3-A | 0.90 | **1.00** | 0.9646 → **0.9771** |
> | `33bcf27` 干净树 | 1.00 | 1.00 | 0.9771 → 0.9771（无变化，作为对照） |
> | `9172baf` 脏树 | 0.933 | 未知 | 该次产物未留存，无法重放 |
>
> 两次可重放的跑**重述后完全相同（0.9771）**。也就是说「0.965~0.977 的实测区间」里，
> 至少 0.9646 那一端是评分器造成的，不是模型质量的差异。中间那次 0.933 与 0.90 相差
> 0.033（不是 0.1），说明它另有原因，但产物没留存，只能存疑不能下结论。
>
> **结论要改的部分**：「波动 100% 集中在 q008」这句仍然成立（其余七题逐次相同），
> 但「波动」的成因至少有一部分要记到评分器头上，而不是全部记给模型。**取权威 baseline
> 的规则不变，README 的数字已按重述值更新为 0.977。**
>
> 这条本身也是个教训：**跑间差异先要排除度量工具，再归因到被测对象。** 我们用了三次跑
> 去论证「q008 不稳定」，却没想过去查一下打分的那把尺子在两种等价写法上给不给同一个分。

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

<a id="adr-015"></a>

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

<a id="adr-016"></a>

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

<a id="adr-017"></a>

### ADR-017: RLAC 从「运行时约定」升级为「带值域的 session 属性注册表」

**Status**: Proposed · 2026-09-02

**Context**:

RLAC 的**执行面**已经完整且方向正确（`metric_resolver.py:60` `RowPolicy`）：

- 条件在 `_render_row_policies` 渲染期注入，排在 `where_parts` 最前
- `RowPolicy` 不进 extractor prompt，**LLM 全程看不到**，也就无从绕过
- fail-closed：`requires` 里的属性没拿到就抛 `MetricResolverError`，绝不静默放行全表
- `metric_lineage.row_policy_issues()` 已做静态自洽性校验，进了 catalog 门禁

但**管理面完全不存在**，且这个机制目前是休眠的：

```
config/metrics.yaml 里声明 row_policies 的指标数： 0
src/ 下传 session_props 的调用方数量：            0（除 metric_resolver 自身）
```

具体缺的三件事：

1. **属性没有注册表**。`requires: ["branch_id"]` 是一句隐式约定——没有任何地方
   声明 `branch_id` 存在、是什么类型、合法取值是什么。写错一个名字，症状是
   运行期 fail-closed 报错，而不是 catalog 门禁在 CI 里拦下。
2. **值没有值域校验**。`_sql_literal`（`metric_resolver.py:315`）只校验 Python
   **类型**（str/int/float/bool）并做引号转义，不校验**取值范围**。调用方传
   `session_props={"branch_id": "BR_ALL"}` 会原样拼进 WHERE——越权与否完全取决于
   调用方自觉。这是当前设计里唯一没有 fail-closed 兜底的环节。
3. **赋值链路没有定义**。session_props 从哪来（SSO claim？组织架构同步？人工配？）、
   谁能改、改了怎么审计——一条都没写。银行落地时这是必答题。

**Decision**:

引入 **session 属性注册表**，把三件事显式化。参考 SQLBot 的三表拆分
（规则本体 / 绑定关系 / 系统变量），但保留我们渲染期 fail-closed 的执行方式。

1. **注册表**（新文件 `config/session_properties.yaml`）：每个属性声明
   `name` / `type` / `domain`（枚举列表、数值区间或 `source: sso_claim` 这类外部来源）
   / `description`。
2. **catalog 门禁扩展**：`scripts/check_metric_catalog.py` 增加一条——
   任何 `row_policies[].requires` 里的属性名必须在注册表中存在，否则 CI 失败。
   把「写错属性名」从运行期错误提前成 CI 错误。
3. **渲染期值域校验**：`_sql_literal` 之前加一道——传入值必须落在注册表声明的
   `domain` 内，否则抛 `MetricResolverError`。**越权值 fail-closed，不是拼进 SQL**。
4. **赋值链路**：明确 session_props 只允许由认证层构造（从已验证的身份令牌派生），
   业务代码不得直接构造或覆写；这条先写进文档约束，接入认证时再落成代码。

**Alternatives considered**:

| 候选 | 为什么没选 |
|---|---|
| **维持现状（隐式约定）** | 值域缺口是实打实的越权面；且零调用方意味着这套机制上线时才第一次被真实使用，风险后置 |
| **像 SQLBot 那样把用户 ID 数组直接绑进规则**（`ds_rules.user_list` JSONB） | 一家分行 3000 人就是往数组里塞 3000 个 id，人员调动要改数组；没有按角色/组织架构绑定这一层，规模化不了 |
| **把权限条件交给 LLM 改写 SQL** | 见下方外部对照——SQLBot 就是这么做的，是 fail-open |
| **在数据库侧做 RLS（Postgres Row Level Security）** | 更强，但要求每个查询会话带上正确的 DB role；与我们「双用户隔离」（ADR-010）的连接池模型冲突，且跨库方言不通。**可作为后续叠加层，不替代本 ADR** |

**Consequences**:

- ✅ 属性名拼写错误从「上线后 fail-closed 报错」提前到 CI 失败
- ✅ 补上唯一的非 fail-closed 环节（越权取值）
- ✅ 给出银行场景可回答的治理链路：属性定义 → 值域 → 赋值来源 → 审计
- ⚠️ 注册表是新的一份需维护的元数据，和 metrics.yaml 一样会有漂移风险——
  靠第 2 条门禁兜住
- ⚠️ 本 ADR 只覆盖**指标模板路径**。P1 的 LLM 生成 SQL 兜底路径不走
  `_render_row_policies`，那条路径上 RLAC 是**不生效的**——这是已知缺口，
  需要单独决策（要么该路径禁用于带 row_policies 的指标域，要么在 SQL 改写层补）

**外部对照（2026-09-02，SQLBot v1.10.1 代码核对）**：

SQLBot 是这批 NL2SQL 开源项目里唯一把企业级数据权限做成产品的，正好给出正反两面。

**值得借鉴的（本 ADR 第 1、4 条的来源）——「系统变量」抽象**
（`backend/apps/datasource/crud/row_permission.py:88`）：管理员定义变量（含 `var_type`
text/number/datetime/kv 与值域）→ 给用户赋值 → 权限规则引用变量。关键是它**校验值域**：
用户被赋的值要和变量定义求交集，交集为空则该条件失效（`row_permission.py:120-136`）。
这正是我们缺的第 2 条。

**明确不抄的——它的行权限执行点**：SQLBot 取到过滤条件后，不是在渲染期注入，而是
**再发一次 LLM 调用请模型改写 SQL**（`apps/chat/task/llm.py:898` `build_table_filter`）。
那次调用的 prompt（`backend/templates/template.yaml:729`）写着：

```
- 如果过滤条件不为空但找不到匹配的表 → {"success":true,"sql":"原SQL"}
```

即「模型没匹配上表名就原样返回未过滤的 SQL，且 success=true」——**一个写进 prompt 的
fail-open 出口**，下游无任何告警。另外三处缺口：改写后的 SQL 不再过表名白名单
（`llm.py:1333` 的校验在改写之前）；嵌入式小助手（assistant type 0/2）整个跳过行权限分支
（`llm.py:1348`）；喂给模型的样例数据 `SELECT ... LIMIT 3` 不带 WHERE
（`apps/datasource/crud/datasource.py:445`），同一份规则在「给人看」的 preview 路径上生效、
在「给模型看」的路径上不生效。

→ 一个 6.7k star、主打企业级数据权限的 ChatBI 产品，其行级权限是**一次可以被模型静默跳过的
对话**。这是「权限边界必须是确定性代码」最直接的外部佐证，也是本 ADR 保留渲染期注入、
只补管理面的理由。

### Update 2026-09-04：RLAC 第一次被真实调用，顺带堵掉「拒绝后走兜底路径」的口子

上面 Context 里那两行「声明 row_policies 的指标数 0 / 传 session_props 的调用方 0」
不再成立。这次做的是**接线**，不是本 ADR 提的注册表（那条仍是 Proposed）：

- `config/metrics.yaml`：`campaign_response_count` / `campaign_conversion_amount`
  两个指标声明 `row_policies`。条件写成
  `(@branch_scope = 'ALL' OR fcr.branch_id = @branch_id)`——总行用 `branch_scope=ALL`
  **显式**越界，而不是靠「不传属性」绕过（不传是拒绝，不是放行）。
- 选这两个指标做首发，是因为它们不在 P1 评测集的命中范围内。fail-closed 是全局
  开关：挂在 `deposit_balance` / `customer_count` 上，任何没传身份的批处理都会
  静默退回 NL2SQL，把已发布的 P1 baseline 一起改掉。
- 穿参链路补齐：`MetricRouter.try_route(question, session_props=...)` →
  `_resolve_to_spec_and_sql` → `render_sql_from_spec`；`P1NL2SQLAgent.run` 加
  `session_props` 形参；Streamlit P1 tab 加「当前登录身份」下拉；
  `run_p1_eval` / `triage_example_pool` 以显式的总行审计身份跑批。
- `_classify_metric_error` 之前把 fail-closed 拒绝归进兜底桶 `unknown_dim`
  ——「维度不认识」和「权限不足」混成一个数，看板上永远看不见权限拒绝。
  新增 `rlac_denied`，并进 `run_p1_eval` 的 `fail_reason_breakdown`。

**首次真实调用的观测**（真 LLM + 真 PG，问「春节储蓄活动一共触达了多少人次？」）：

| 身份 | route | 结果 |
|---|---|---|
| 总行（`branch_scope=ALL`） | `metric` | 31992 |
| 杭州分行（`BR_CITY_0000`） | `metric` | 808 |
| 南京分行（`BR_CITY_0002`） | `metric` | 754 |
| 没有身份 | `metric_denied` | 拒答 |

三个数与直接打库核对一致（`SELECT COUNT(*) ... WHERE campaign_name='春节储蓄活动'`
分别为 31992 / 808 / 754）。

**顺带堵掉的口子**：上面 Consequences 最后一条 ⚠️ 写的「LLM 生成 SQL 兜底路径上
RLAC 不生效」，第一次跑就以最难看的形式兑现了——无身份时语义层 fail-closed 拒绝
渲染，`P1NL2SQLAgent` 却把问题交给 NL2SQL 重答一遍，**返回 13618**。这个数是裸查
出来的：拒绝之后换一条没有权限管控的路把同一个问题答了，等于没有拒绝。

按那条 ⚠️ 里给的两个选项取第一个（**该路径禁用于带 row_policies 的指标域**）：
`fail_reason == "rlac_denied"` 时 `run()` 直接返回 `route="metric_denied"`，
不进 Reflect Loop、不调 SchemaLinker、不出 SQL。其余失败原因（`unknown_dim`、
`no_metric` 等）照旧回退——只关权限这一条路，不牵连降级能力。

留给注册表（本 ADR 主体）的仍然是：属性名的 CI 门禁、取值值域校验、赋值链路。
现在多了一条现实约束——`branch_scope` 这类「作用域开关」属性一旦进注册表，
它的值域必须和「谁有权拿到 ALL」绑在一起，否则等于把越界权交给调用方自觉。

### Update 2026-09-04（二）：把「值存在吗」摘出来做了，注册表本体按触发条件继续挂着

接线跑通后重新评估本 ADR 的四件事，逐条的结论是：

| 本 ADR 的条目 | 现在的判断 | 理由 |
|---|---|---|
| ① 注册表本体 `session_properties.yaml` | **不做** | 全项目只有 2 个属性（`branch_scope` / `branch_id`），来源是三处硬编码。为 2 个属性建一份会漂移的元数据文件，成本大于收益 |
| ② 属性名的 CI 门禁 | **已等价拿到** | `test_every_identity_supplies_all_props_the_catalog_requires`（Streamlit 侧）与 `test_eval_identity_covers_every_session_prop_the_catalog_requires`（评测侧）：从生产 catalog 收集全部 `requires` 逐个核对调用方。`requires` 写错名字 CI 直接红——正是本条要的效果，不需要注册表 |
| ③ 渲染期值域校验 | **拆成两半**：安全那半不做，正确性那半已做 | 见下 |
| ④ 赋值链路 | **做不了** | 全项目没有任何认证层（`streamlit_app/` 下 grep `login`/`auth`/`SSO` 零命中）。session 属性由应用自己硬编码，这条只能等认证 |

**③ 为什么要拆**：本 ADR 原文把它写成越权面（「调用方传 `BR_ALL` 会原样拼进 WHERE」）。
但值全部来自应用自己写死的 dict，**今天不存在不受信输入**——让代码校验自己写死的常量
落不落在自己声明的值域里，是仪式不是防护。而且 2026-09-04 引入 `branch_scope=ALL` 之后，
这条的性质变了：`ALL` 本来就在合法值域内，值域校验拦不住它；能拦住的是「谁有权拿到 ALL」,
那是第 ④ 条。

**但它还有另一半，与安全无关**：`render_domain_probe_sql` 只探 `spec.filters`
（LLM 抽的值），**不探 row_policy 绑的 session 值**。传一个打错字的 `branch_id`，
SQL 合法、跑得出数、返回 0——与「本行确实一个人都没触达」不可区分。这是 ADR-013
已经修过一次的形状（静默 0 行），只是换了个入口，且不需要注册表就能修。

因此只做这一小块：`render_policy_probe_sql(metric, session_props)` 生成
`SELECT 1 FROM <fact> [policy joins] WHERE <权限条件> LIMIT 1`——**只带权限条件**，
不带 time_window / hard_filters / 用户过滤（那几样为空是业务事实，不是身份错）。

**关键取舍：警告，不拦截。** 对 `spec.filters` 来说探不到就退回 NL2SQL 是安全的
替代路径；对权限条件不是——退回等于走一条没有行级权限的路（就是上面那个 13618）。
而拒答又会误伤「确实一行数据都没有」的合法分行。所以探不到只在 `RouteResult
.policy_value_warning` / `P1AgentResult.metric_policy_warning` 上挂一句诊断，
UI 用 `st.warning` 显示，路由结局不变。探针自身报错（超时、连接断）一律不出警告——
把基础设施抖动读成「你的身份配错了」比不提示更糟。

实测（`BR_CITY_00OO`，把 `0` 打成字母 `O`）：结果 0，附
「当前身份的属性（branch_id='BR_CITY_00OO'…）在该指标的数据里一行都没匹配到——
结果为 0 可能是身份值不存在，而不是业务事实」；真实分行则安静。

**本 ADR 主体（① ③安全那半 ④）的触发条件写死为：接入认证 / 多租户。** 在那之前
session 属性不来自外部，注册表没有输入源，做出来是空转。相应地也如实标注一条
现状限制：**demo 里身份是用户自己从下拉框选的，选「总行」就能看全行，演示环境下
RLAC 是可绕过的**——堵这个口子的是认证层，注册表堵不上。


---

<a id="adr-018"></a>
### ADR-018: MCP server 只暴露 P1，身份锁在服务端配置而非 tool 入参

**Status**: Accepted（2026-09-08）

**Context**

排期第 14 周保留的唯一新增能力：把 FinAgent 包成 MCP server，让 Claude Desktop 能用。
包一层本身是小工程，真正要定的是两件事——**暴露什么**，以及**身份从哪来**。

查代码后确认了一个此前没写下来的事实：**P2/P3 并非"没有权限管控"，而是把
`session_props` 丢在了半路**。两者都委托 P1 执行（`p2_analysis_agent.py:85`、
`p3/drill_executor.py:164`），但调用时不传身份。后果分岔：子问题命中带 `row_policies`
的指标 → P1 返回 `metric_denied` → P2 步骤失败 / P3 下钻空，**误打误撞是 fail-closed 的**；
子问题没命中语义层 → 走 NL2SQL 裸查，**完全没有行级管控**。后一条是漏的。

**Decision**

1. **只暴露 P1**，两个 tool：`query_bank_data`（入参只有 `question`）与
   `list_governed_metrics`。P2/P3 不进暴露面——把一个已知缺口摆进新接口，
   等于用更大的门把它散出去。
2. **身份从服务端环境变量读**（`CHATBI_MCP_BRANCH_SCOPE` / `CHATBI_MCP_BRANCH_ID`），
   锁在闭包里，**tool schema 里没有任何身份字段**。让被管的人自己声明自己是谁，
   权限边界就是假的。这与语义层既有约定同源：session 属性只参与渲染、不进 prompt。
3. **fail-closed 不打折**。没配环境变量 = 空 props = 受管控指标被拒；
   配了 `BRANCH` 却没给 `branch_id` 同样拒绝——「没指定分行」绝不能被解释成
   「所有分行都能看」。拒绝后不回退（延续 2026-09-04 在 P1 里堵掉的那条）。
4. **接线不复制**。P1 的生产接线抽到 `agents/p1/wiring.py`，Streamlit 与 MCP 共用，
   `tests/p1/test_p1_wiring_shared.py` 用**同一性**断言（`is`）钉住。理由是本项目
   已经在「双写必然漂移」上吃过两次亏，且两次都不报错、只是两边不一致。
   `ALL` 档的属性直接取自 `wiring.IDENTITIES[HQ_IDENTITY]`，不另写字面量。

**Consequences**

真跑一次（stdio + 真实 MCP 客户端 + 真库真模型），同一个问题「统计营销触达响应数」：

| 身份配置 | route | 结果 |
|---|---|---|
| `SCOPE=ALL` | `metric` | 159500 |
| `SCOPE=BRANCH` + `BRANCH_ID=BR_CITY_0000` | `metric` | 3831 |
| 两个环境变量都不配 | `metric_denied` | 拒绝，`rows=null` |

两个数与直接打 `fct_campaign_response` 逐字符一致；客户端侧看到的 `query_bank_data`
入参确实只有 `question`。

**这条 ADR 顺带补上了 ADR-017 结尾自陈的那个限制的一半。** ADR-017 如实写着
「demo 里身份是用户自己从下拉框选的，选『总行』就能看全行，演示环境下 RLAC 是
可绕过的」。那对 Streamlit 仍然成立，但**对 MCP 入口不成立**：身份在进程启动时定死，
对话里改不了，模型也看不见。这不等于有了认证层——配置文件仍是谁能编辑谁说了算——
但它把「谁来声明身份」从**被管的人**挪到了**部署方**，是认证层缺席时能做到的最强形态。

**真跑暴露的一个测试没覆盖的缺陷**（记下来因为它属于本项目反复出现的那一类）：
Claude Desktop 以裸子进程拉起 server，cwd 由它决定，裸 `load_dotenv()` 找不到仓库
`.env` → `PG_PORT` 退回默认 5432（本项目是 5433）、`DASHSCOPE_API_KEY` 为空。
表现是「连不上库」，而真因是「没读到配置」——排查方向会被带偏。已改为锚仓库根的
绝对路径并补两条守门。**18 条测试全绿也没抓住它，是真跑抓住的。**

**明确不做**：HTTP transport、OAuth、P2/P3 暴露、多租户、把 MCP 塞进 docker-compose。
P2/P3 的暴露触发条件写死为：**先给它们补上 `session_props` 穿参**（约 2 天，含两边测试）。

---

**最后更新**：2026-09-08

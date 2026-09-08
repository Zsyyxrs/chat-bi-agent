# Results Directory

This directory contains evaluation results, logs, and outputs from the chat-bi-agent evaluation framework.

## Structure

- **rca_evaluation_results/** - Root cause attribution evaluation outputs
- **precision_retrieval_results/** - NL2SQL precision retrieval evaluation outputs
- **multi_step_analysis_results/** - Multi-step analysis evaluation outputs
- **logs/** - Evaluation run logs and debug information

## Usage

Results are automatically generated when running evaluation scripts:

```bash
python -m src.chat_bi_agent.eval.rca_evaluator
python -m src.chat_bi_agent.eval.precision_retrieval_evaluator
python -m src.chat_bi_agent.eval.multi_step_analysis_evaluator
```

See [EVALUATION_FRAMEWORK.md](../EVALUATION_FRAMEWORK.md) for detailed evaluation documentation.

## Baseline 对比

| Baseline | Date | Pass Rate | Avg Score | 备注 |
|---|---|---|---|---|
| P1 NL2SQL Agent | 2026-06-02 | 100% (6/6) | 0.712 | 单层 SQLGenerator 内部循环重试 ≤3 次；正则黑名单防御 |
| P2 Validator + Reflector | 2026-06-03 | 100% (6/6) | 0.7625 | sqlglot AST 校验；独立 Reflector；statement_timeout=10s |
| P2 Multi-step Analysis Agent (MVP) | 2026-06-07 | 100% (3/3 sampled) | 0.7403 | Plan-and-Execute；Replan=1；3/8 题取样（单题 5-9 分钟，剩余 5 题延后跑） |

详见 `baseline_p2_validator_reflector_2026-06-03.json`、`baseline_p2_analysis_2026-06-07.json`。

## 窗口函数探针（2026-09-08）

`baseline_p1_eval_2026-09-08.window-probe.json` —— 只跑新增的 q012~q014 三题，
不是一次全量 baseline，**不要拿它的 avg 与全量 baseline 直接比**（口径不同、题数不同）。

下表的 score 已按 2026-09-08 的评分器修复重述（`*.restated.json`），括号内是原报值。
三题全部用了 CTE，因此原先每题都被那个「CTE 名误算成表」的 bug 白扣了 0.08~0.10。

| 题 | 窗口函数族 | 生成的写法 | 行数 | result_match | score |
|---|---|---|---|---|---|
| q012 | 偏移 | `LAG() OVER (ORDER BY 月)` + CASE WHEN 防除零 | 6/6 ✅ | ✅ | **1.000**（原报 0.900） |
| q013 | 排名 | `DENSE_RANK() OVER (PARTITION BY 分行)` | 45/45 ✅ | ✅ | **0.917**（原报 0.837） |
| q014 | 帧 | `SUM() OVER (ORDER BY ... ROWS UNBOUNDED PRECEDING)` + `SUM() OVER ()` | 20/20 ✅ | ✅ | **0.917**（原报 0.817） |

接线：默认档（few-shot off、metric router off、value index on），与 `run_all_evals.py` 同源。
模型 qwen3.7-flash-2026-07-15，三题均 **attempts=1，零反思重试**。

**结论：窗口函数这一整类没有翻车。** 三个族全部一次写对，链路（生成 → sqlglot 校验 →
执行）对窗口语法没有任何阻碍。原先担心的「WHERE 里直接写窗函数」这个经典错法没有出现——
模型规规矩矩套了 CTE 再过滤。

**但跑出两件比分数更值钱的事**：

1. **q013 的 SQL 语义是错的，而现有评分维度一个都抓不住。** 模型写
   `GROUP BY b.branch_name, c.customer_name`——按姓名而非 `customer_id` 聚合。
   库里 5230 个客户只有 3801 个不同姓名，同一分行内同名有 10 组，按姓名聚合会把
   不同客户的交易额并成一笔。这一批 top 3 恰好没撞上同名客户，于是行数 45 对得上、
   `result_match` 判 True、拿了 0.837 分。**行数、结果集、六个评分维度全部为它背书。**
   这正是项目一贯的那类失效：不报错、数字照出、只是错的。

2. **`result_match` 有一个类级误判缺陷（当天已修）。** q012 首跑判 False，实际两边
   金额逐行完全相同，差别只是模型写了 `DATE_TRUNC(...)::DATE`（date）而 gold 留着
   timestamptz。`_normalize_row` 对非数值列走 `str(v)`，
   `"2026-01-01 00:00:00+08:00" != "2026-01-01"`。**任何 gold 返回 timestamp 列的题
   都会被误判成不一致。**

   修在评测器侧（`_canonical_value`：零点时间戳归一到日期，带真实时分秒的照旧逐值比），
   而不是在 gold 里写 cast 迁就它。用首跑记录下来的那条真实 agent SQL 复验：去掉 gold 的
   cast 后 `result_match` 仍为 True。改它是安全的——`result_match` 刻意不计入
   `combined_score`，动不了任何历史分数。上表的 q012 ✅ 是修复后的判定。

### 后续：q013 的「按姓名聚合」已在源头修掉（2026-09-08 当天）

在 `schema_docs.yaml` 的 `customer_name` 列描述里点明「不唯一，聚合到客户粒度必须
GROUP BY customer_id」（`get_ddl_text` 会把列描述送进 prompt，所以这句真的到得了模型眼前；
`tests/schema/test_schema_loader.py` 断言的是 DDL 文本而不是 yaml 原文——注释写在
没人加载的地方等于没写）。重跑 q013：

| | GROUP BY | 窗口 PARTITION BY | score |
|---|---|---|---|
| 改提示前 | `b.branch_name, c.customer_name` ❌ | `branch_name` | 0.837 |
| 改提示后 | `b.branch_id, b.branch_name, c.customer_id, c.customer_name` ✅ | `branch_id` ✅ | **0.837** |

模型不止改了客户分组键，还自己把窗口的 `PARTITION BY` 也从 `branch_name` 换成了
`branch_id`（同名分行同理会撞）——这一步提示里没写。

**分数一个千分位都没动。** SQL 从语义错误变成语义正确，六个评分维度合起来纹丝不动。
这是「评分维度抓不住」最干净的一次演示：不是它们打分打偏了，是它们根本不在
度量这件事。补检测（`group_by_match` 诊断字段）因此仍然必要——源头修的是这一次，
检测补的是下一次。

### 再后续：评分器的 CTE 偏置已修，全部 P1 数字重述（2026-09-08）

上面那张表里 q013 的 0.837、以及正文里「六个维度全部为它背书」的说法，本身还压着
另一个 bug：`table_score` 的抽取正则 `(?:FROM|JOIN)\s+(\w+)` 把 `FROM txn_agg` 这种
**对 CTE 的引用**也算成表名。

这不是随机噪声而是**系统性偏置**，而且不对称——gold 惯用派生子查询（`FROM (` 不匹配
`\w+`，逃过），生成 SQL 用 `WITH` 就中招。四道用 CTE 的题表选择本应全是 1.000：

| 产物 | 原 avg | 重述 avg | 变化来源 |
|---|---:|---:|---|
| `baseline_p1_eval_2026-08-14.json` | 0.9771 | 0.9771 | 无（该次无 CTE，作为对照） |
| `baseline_p1_eval_2026-08-15.json` | 0.9646 | **0.9771** | 仅 q008（0.900 → 1.000） |
| `...2026-09-08.window-probe.json` | 0.8511 | **0.9445** | q012/q013/q014 全部 |

重打分走 `scripts/replay_p1_scoring.py`：**零 LLM 调用**，SQL 取自原产物、重打真库，
因此新旧差异纯粹来自评分器，不掺模型跑间噪声。原产物一律保留，重述结果另写
`*.restated.json`。

**连带修正一条判断**：README 原先写「同一配置反复跑 avg 落在 0.965~0.977，差异全部来自
q008 的跑间抖动」。重述后 08-14 与 08-15 两次跑**都是 0.9771**——那个「回退」不存在。
两次之间真正变的是模型把 q008 换成了 CTE 写法，而评分器为这个**等价**写法扣了 0.1。

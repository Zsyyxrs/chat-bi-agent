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
不是一次全量 baseline，**不要拿它的 avg 与 0.965 直接比**（口径不同、题数不同）。

| 题 | 窗口函数族 | 生成的写法 | 行数 | result_match | score |
|---|---|---|---|---|---|
| q012 | 偏移 | `LAG() OVER (ORDER BY 月)` + CASE WHEN 防除零 | 6/6 ✅ | ✅ | 0.900 |
| q013 | 排名 | `DENSE_RANK() OVER (PARTITION BY 分行)` | 45/45 ✅ | ✅ | 0.837 |
| q014 | 帧 | `SUM() OVER (ORDER BY ... ROWS UNBOUNDED PRECEDING)` + `SUM() OVER ()` | 20/20 ✅ | ✅ | 0.817 |

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

2. **`result_match` 有一个类级误判缺陷（已知未修）。** q012 首跑判 False，实际两边
   金额逐行完全相同，差别只是模型写了 `DATE_TRUNC(...)::DATE`（date）而 gold 留着
   timestamptz。`_normalize_row` 对非数值列走 `str(v)`，
   `"2026-01-01 00:00:00+08:00" != "2026-01-01"`。**任何 gold 返回 timestamp 列的题
   都会被误判成不一致。** 当前只在 q012 的 gold 上加了 `::DATE` 让本题可比，
   没有动评测器。修它是安全的——`result_match` 刻意不计入 `combined_score`，
   改它动不了任何历史分数。

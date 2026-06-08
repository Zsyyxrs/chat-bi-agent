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

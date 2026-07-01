# External Benchmarks — 本地缓存说明

本目录集中存放**外部第三方公开 benchmark** 的本地数据，仅用于本项目做可复现的对外评测。原始数据文件（`benchmarks/bird/` 等子目录）通过 [.gitignore](../.gitignore) 排除入库（体积大 + 有各自的 license），这份 README 记录：数据来源、下载日期、目录布局、许可证要点、以及本项目对应的评测入口。

---

## BIRD — dev split（`benchmarks/bird/`，未入库）

数据集：[BIRD (BIg Bench for LaRge-scale Database Grounded Text-to-SQLs)](https://bird-bench.github.io/) 的 **dev split**，本项目只用 `db_id == "financial"` 子集（106 题 / 8 表）做 P1 NL2SQL 评测。

- 项目内使用范围：只跑 `db_id == "financial"` 子集（106 题），跑 EX（Execution Accuracy），回填 README。
- 其它 10 个 domain 数据库暂时保留但用不到；磁盘紧张可直接删掉。

## 来源与版本

| 项 | 值 |
| --- | --- |
| 官方主页 | https://bird-bench.github.io/ |
| 论文 | Li et al., *Can LLM Already Serve as A Database Interface?*, NeurIPS 2023 — <https://arxiv.org/abs/2305.03111> |
| 代码 / 评测脚本 | https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/bird |
| 下载日期 | 2026-07-01 |
| License | CC BY-SA 4.0（以官网声明为准，商用前请重新核对） |

## 目录内容

```
benchmarks/bird/
├── dev.json                    # 全部 1534 条 dev 题（question / evidence / gold SQL / difficulty / db_id）
├── dev.sql                     # 全部 gold SQL，一行一条，Tab 分隔 db_id
├── dev_tables.json             # 每个 db 的 schema 元信息（表名 / 列名 / 主外键）
├── dev_tied_append.json        # 官方补丁：42 条答案存在并列（tied）的题，评测时按 tied 语义放宽比对
├── dev_databases.zip           # 原始压缩包（≈346 MB，解压后可删）
└── dev_databases/
    ├── financial/              # ← 我们唯一会用的库
    │   ├── financial.sqlite    # 主数据库（gitignore 已通过 *.sqlite 兜底）
    │   └── database_description/  # 每张表一个 CSV，含列注释（列名含义、单位、枚举）
    ├── california_schools/
    ├── card_games/
    ├── codebase_community/
    ├── debit_card_specializing/
    ├── european_football_2/
    ├── formula_1/
    ├── student_club/
    ├── superhero/
    ├── thrombosis_prediction/
    └── toxicology/
```

## Financial 子集统计

- **题数**：106（`dev.json` 中过滤 `db_id == "financial"`）
- **难度分布**：simple 62 / moderate 37 / challenging 7
- **数据库**：源自 PKDD'99 Discovery Challenge 的捷克银行数据集
- **表 × 行数**：

  | 表 | 行数 |
  | --- | ---: |
  | `account` | 4,500 |
  | `card` | 892 |
  | `client` | 5,369 |
  | `disp` | 5,369 |
  | `district` | 77 |
  | `loan` | 682 |
  | `order` | 6,471 |
  | `trans` | 1,056,320 |

## 一条样例

```json
{
  "question_id": 89,
  "db_id": "financial",
  "question": "How many accounts who choose issuance after transaction are staying in East Bohemia region?",
  "evidence": "A3 contains the data of region; 'POPLATEK PO OBRATU' represents for 'issuance after transaction'.",
  "SQL": "SELECT COUNT(T2.account_id) FROM district AS T1 INNER JOIN account AS T2 ON T1.district_id = T2.district_id WHERE T1.A3 = 'east Bohemia' AND T2.frequency = 'POPLATEK PO OBRATU'",
  "difficulty": "moderate"
}
```

字段含义：
- `question`：自然语言问题（英文）。
- `evidence`：官方提供的 hint（列义 / 值域枚举 / 计算公式），BIRD 的定位就是**要求模型能吸收 evidence**，评测阶段应作为 prompt 输入的一部分。
- `SQL`：gold SQL，SQLite 方言。
- `difficulty`：simple / moderate / challenging。

## Git 与体积

- `.gitignore` 已通过 `*.sqlite`、`*.db` 兜底，`financial.sqlite` 不会被误提交。
- 建议在 `.gitignore` 里再显式加一行 `benchmarks/bird/`，避免 `dev.json`（~740 KB）和 `dev_databases.zip`（~346 MB）被误提交，同时也回避 BIRD 数据的二次分发问题。
- 本目录**只作本地缓存**。CI 上如需跑 BIRD 评测，走单独脚本从官网拉取。

## 项目内如何被使用（占位）

- 评测代码位置（待建）：`src/chat_bi_agent/eval/bird_financial/`
- 入口脚本（待建）：`scripts/run_bird_financial.py`
- 评测方式：只跑 P1 NL2SQL agent，指标 = EX（Execution Accuracy，行集合等价，参考官方 `evaluation.py` 的 `execution_accuracy` 实现）。
- 结果落盘：`results/bird_financial_<date>.json`；EX 数字回填 [README.md](../../README.md) 与 [README.en.md](../../README.en.md) 的 Public benchmark 一节。

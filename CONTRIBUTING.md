# 贡献指南 · chat-bi-agent

**中文** | [English](./CONTRIBUTING.en.md)

感谢你有兴趣为 chat-bi-agent 贡献代码！本文档说明本地开发环境搭建、代码规范、提交约定与 PR 流程。

## 开发环境

### 前置要求
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（包管理器 —— 仓库带 `uv.lock`，用 `pip` 装不会锁到同一版本）
- Docker Desktop / OrbStack，需 Docker Compose v2（命令是 `docker compose`，不是老版 `docker-compose`）
- Git

### 本地开发

1. **Fork 并克隆仓库**
   ```bash
   git clone https://github.com/Zsyyxrs/chat-bi-agent.git
   cd chat-bi-agent
   ```

2. **用 uv 安装依赖**
   ```bash
   uv sync --extra dev
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```
   `uv sync` 会读取 `uv.lock` 并复现锁定版本；除非有特殊原因，不要退回到 `pip install -e ".[dev]"`。

3. **配置环境变量**
   ```bash
   cp .env.example .env
   # 至少填写 DASHSCOPE_API_KEY（Qwen）和 LANGFUSE_PUBLIC/SECRET_KEY
   ```
   完整跑通所需环境变量：`DASHSCOPE_API_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`，以及 Postgres 凭据（`PG_HOST`、`PG_PORT`、`PG_DATABASE`、`PG_USER`、`PG_PASSWORD`、`PG_READONLY_USER`、`PG_READONLY_PASSWORD`）。

4. **拉起服务并 seed 数据库**
   ```bash
   # 1) 起 Postgres（跑 eval / seed 的最小依赖）
   docker compose up -d postgres

   # 2) 灌种子数据（首次或重置时执行）
   docker compose --profile seed run --rm seed

   # 3) 起 Streamlit 应用 + Langfuse 栈（http://localhost:8501）
   docker compose up -d app
   ```
   > `seed` 服务挂在 Compose profile 后面，裸的 `docker compose up` **不会**跑它，必须显式加 `--profile seed`。如果想在本地 Python 环境（而非容器）里 seed，运行 `python -m chat_bi_agent.data.seed --host localhost --port 5433 --truncate --with-events`。

   完整 compose 服务清单：`postgres`、`pgadmin`、`langfuse-db`、`clickhouse`、`redis`、`minio`、`minio-bootstrap`、`langfuse-worker`、`langfuse`、`app`、`seed`。拉起 `app` 会自动带上 Langfuse 相关依赖。

### 常用命令（Makefile）

日常操作通过 `make <target>` 走 —— 跑 `make help` 看全部。每个 target 会自动 source `.env`。

| Target | 用途 |
|---|---|
| `make test` | 跑完整 `pytest` 套件 |
| `make lint` / `make fmt` | 对 `src/ tests/ scripts/ streamlit_app/` 跑 `ruff check` / `ruff format` |
| `make eval-all` | 三路径全跑 + 出 markdown 报告 |
| `make eval-p1` / `eval-p2` / `eval-p3` | 单跑某条评估路径（P1 加 `POOL_ARG="--example-pool"` 启用 few-shot） |
| `make eval-bird` / `eval-bird-p1` | BIRD-financial lean baseline / P1 pipeline |
| `make bootstrap-pool` / `promote-pool` | Q-SQL few-shot 池：首次灌注 / 夜间增量提升 |
| `make streamlit` | 本地起 Streamlit（Postgres + Langfuse 需已 up） |

## 项目结构

```
chat-bi-agent/
├── src/chat_bi_agent/
│   ├── agents/              # P1/P2/P3 编排 + 共享 prompt
│   │   ├── p1/              # NL2SQL：schema linking / SQL 生成 / 校验 / reflect
│   │   ├── p2/              # 多步分析
│   │   ├── p3/              # 根因归因
│   │   └── shared/          # 跨 agent 工具
│   ├── data/                # 数据层
│   │   ├── events/          # YAML 事件定义（埋雷因果事件）
│   │   ├── db.py            # SQLAlchemy 模型与连接
│   │   ├── seed.py          # 数据库初始化 CLI
│   │   ├── transaction_generator.py
│   │   ├── dimension_generator.py
│   │   ├── event_loader.py
│   │   ├── propagation_engine.py
│   │   ├── scenario_anchor.py
│   │   └── *.yaml           # P1/P2/P3 评估题库
│   ├── eval/                # 评估器 + BIRD harness
│   │   ├── precision_retrieval_evaluator.py
│   │   ├── multi_step_analysis_evaluator.py
│   │   ├── rca_evaluator.py
│   │   ├── bird_financial/  # BIRD-financial 适配层
│   │   ├── latency_stats.py
│   │   └── run_metadata.py
│   ├── llm/                 # Qwen 客户端 + Langfuse setup / feedback
│   ├── runners/             # `run_p{1,2,3}_eval.py` 入口
│   ├── schema/              # Schema loader + `schema_docs.yaml`
│   ├── viz/                 # 图表推断 + Plotly 渲染
│   └── config.py
├── streamlit_app/           # Streamlit UI（app.py、components/、tabs/）
├── scripts/                 # bootstrap、校准、一次性运维
├── tests/                   # Pytest 套件，按模块分组（p1/p2/p3/data/eval/…）
├── benchmarks/              # BIRD 数据集 + 本地 benchmark fixtures
├── docker/                  # 辅助 Docker 配置
├── config/                  # 应用配置文件
├── results/                 # 评估结果快照
├── docs/                    # 设计文档（见 金融data agent架构设计.md）
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
├── uv.lock
├── DESIGN_DECISIONS.md      # ADR —— 为什么这么选
├── EVALUATION_FRAMEWORK.md  # 三路径评估规约
├── README.md                # 中文（主）
└── README.en.md             # 英文
```

## 代码规范

### 风格
- **格式化**：Ruff（`ruff format`，行长 100）
- **Lint**：Ruff（`ruff check`）
- **Python 版本**：3.11+

### 提交前

```bash
# 格式化 + lint（或直接 `make fmt && make lint`）
ruff format src/ tests/ scripts/ streamlit_app/
ruff check  src/ tests/ scripts/ streamlit_app/

# 跑测试（或 `make test`）
pytest -v

# 覆盖率报告
pytest --cov=src --cov-report=html
```

## Commit 信息

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 并带 scope：

```
<type>(<scope>): <简短描述>
```

常用 type：`feat`、`fix`、`refactor`、`docs`、`test`、`perf`、`chore`、`ci`、`build`。

本仓库历史示例：

- `feat(eval): 三路径评估框架和数据生成系统`
- `fix(compose): pgAdmin 邮箱修复 + CLICKHOUSE_DB 清理 + 挂载 P1 few-shot 数据`
- `chore(deps): 移除 Black，统一到 ruff format —— 单工具管 lint + format`
- `chore(gitignore): dedupe .DS_Store + 补 defensive 规则`
- `fix(makefile): make help 正则支持含数字的 target 名`

描述中英文均可。subject 控制在 72 字符内，上下文和理由写到 commit body 里。

## 测试

### 跑测试
```bash
# 全套
pytest                        # 或 make test

# 单模块
pytest tests/p1/
pytest tests/data/

# 覆盖率
pytest --cov=src --cov-report=html
```

测试按模块分组：`tests/{p1,p2,p3,data,eval,llm,schema,viz,scripts,shared}/`。依赖运行中的 Postgres + seed 数据的集成测试标了 `@pytest.mark.integration`。

### 写测试
- 放到对应的 `tests/<module>/` 子目录
- 文件命名 `test_*.py`
- 函数名要描述意图
- 示例：
  ```python
  def test_transaction_generator_creates_valid_transactions():
      # Test implementation
      pass
  ```

## Pull Request 流程

1. **建 feature 分支**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **按代码规范修改**

3. **补 / 更新测试**

4. **如有需要更新文档**

5. **推分支并开 PR**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **PR 要求**
   - 描述清晰
   - 关联相关 issue
   - 测试全通过
   - 覆盖率不下降
   - 无 lint 违规

## 报 Bug

报告时请：
1. 用清晰、有区分度的标题
2. 写出复现步骤
3. 给出期望行为 vs 实际行为
4. 附 Python 版本和环境信息
5. 贴错误日志

提交 issue：<https://github.com/Zsyyxrs/chat-bi-agent/issues>

## 有问题？

- 📧 邮箱：zhusayi1994@gmail.com
- 📖 说明文档：[README.md](README.md)（中文）与 [README.en.md](README.en.md)（English）
- 🏛️ 设计决策 & ADR：[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)
- 🔍 架构文档：[docs/金融data agent架构设计.md](docs/金融data%20agent架构设计.md)
- 📊 评估框架：[EVALUATION_FRAMEWORK.md](EVALUATION_FRAMEWORK.md)

## 许可

贡献即代表你同意贡献内容按 MIT License 授权。

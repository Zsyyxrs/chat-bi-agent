# Contributing to chat-bi-agent

**English** | [中文](./CONTRIBUTING.md)

Thank you for your interest in contributing to chat-bi-agent! This document provides guidelines and instructions for getting started.

## Development Setup

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (package manager — the repo ships a `uv.lock`, so `pip` will not pin the same versions)
- Docker Desktop / OrbStack with Docker Compose v2 (uses `docker compose`, not `docker-compose` v1)
- Git

### Local Development

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/Zsyyxrs/chat-bi-agent.git
   cd chat-bi-agent
   ```

2. **Install dependencies with uv**
   ```bash
   uv sync --extra dev
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```
   `uv sync` reads `uv.lock` and reproduces the exact locked versions. Prefer this over `pip install -e ".[dev]"` unless you have a specific reason not to.

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Fill in DASHSCOPE_API_KEY (Qwen) and LANGFUSE_PUBLIC/SECRET_KEY at minimum.
   ```
   Keys needed for a full run: `DASHSCOPE_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, plus Postgres creds (`PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`, `PG_READONLY_USER`, `PG_READONLY_PASSWORD`).

4. **Start services and seed the database**
   ```bash
   # 1) Start Postgres (minimum for eval / seeding)
   docker compose up -d postgres

   # 2) Seed data (first time or when resetting)
   docker compose --profile seed run --rm seed

   # 3) Start the Streamlit app + Langfuse stack (http://localhost:8501)
   docker compose up -d app
   ```
   > The `seed` service is gated behind a Compose profile, so a bare `docker compose up` will NOT run it. Trigger it explicitly with `--profile seed`. To seed against a local Python env instead of the container, run `python -m chat_bi_agent.data.seed --host localhost --port 5433 --truncate --with-events`.

   Full compose stack: `postgres`, `pgadmin`, `langfuse-db`, `clickhouse`, `redis`, `minio`, `minio-bootstrap`, `langfuse-worker`, `langfuse`, `app`, `seed`. Bringing up `app` transitively starts the Langfuse dependencies.

### Common commands (Makefile)

Day-to-day work goes through `make <target>` — run `make help` for the full list. Each target auto-sources `.env`.

| Target | Purpose |
|---|---|
| `make test` | Run the full `pytest` suite |
| `make lint` / `make fmt` | `ruff check` / `ruff format` over `src/ tests/ scripts/ streamlit_app/` |
| `make eval-all` | Run P1 + P2 + P3 and emit a markdown report |
| `make eval-p1` / `eval-p2` / `eval-p3` | Run one evaluation path (append `POOL_ARG="--example-pool"` for P1 few-shot) |
| `make eval-bird` / `eval-bird-p1` | BIRD-financial lean baseline / P1 pipeline |
| `make bootstrap-pool` / `promote-pool` | Q-SQL few-shot pool: first-time build / nightly promote |
| `make streamlit` | Start Streamlit locally (Postgres + Langfuse must already be up) |

## Project Structure

```
chat-bi-agent/
├── src/chat_bi_agent/
│   ├── agents/              # P1/P2/P3 orchestration + shared prompts
│   │   ├── p1/              # NL2SQL: schema linking, SQL gen, validate, reflect
│   │   ├── p2/              # Multi-step analysis
│   │   ├── p3/              # Root cause attribution
│   │   └── shared/          # Cross-agent utilities
│   ├── data/                # Data layer
│   │   ├── events/          # YAML event definitions (buried causal events)
│   │   ├── db.py            # SQLAlchemy models and connections
│   │   ├── seed.py          # Database initialization CLI
│   │   ├── transaction_generator.py
│   │   ├── dimension_generator.py
│   │   ├── event_loader.py
│   │   ├── propagation_engine.py
│   │   ├── scenario_anchor.py
│   │   └── *.yaml           # P1/P2/P3 evaluation question banks
│   ├── eval/                # Evaluators + BIRD harness
│   │   ├── precision_retrieval_evaluator.py
│   │   ├── multi_step_analysis_evaluator.py
│   │   ├── rca_evaluator.py
│   │   ├── bird_financial/  # BIRD-financial adapter
│   │   ├── latency_stats.py
│   │   └── run_metadata.py
│   ├── llm/                 # Qwen client + Langfuse setup/feedback
│   ├── runners/             # `run_p{1,2,3}_eval.py` entry points
│   ├── schema/              # Schema loader + `schema_docs.yaml`
│   ├── viz/                 # Chart inference + Plotly renderer
│   └── config.py
├── streamlit_app/           # Streamlit UI (app.py, components/, tabs/)
├── scripts/                 # Bootstrap, calibration, one-off ops
├── tests/                   # Pytest suite, grouped by module (p1/p2/p3/data/eval/…)
├── benchmarks/              # BIRD dataset + local benchmark fixtures
├── docker/                  # Auxiliary Docker configs
├── config/                  # App configuration files
├── results/                 # Evaluation output snapshots
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
├── uv.lock
├── DESIGN_DECISIONS.md      # ADRs — why we chose what we chose
├── EVALUATION_FRAMEWORK.md  # Three-path eval spec
├── README.md                # Chinese (main)
└── README.en.md             # English
```

## Code Standards

### Style Guide
- **Formatter**: Ruff (`ruff format`, line length: 100)
- **Linter**: Ruff (`ruff check`)
- **Python Version**: 3.11+

### Before Committing

```bash
# Format + lint (or just `make fmt && make lint`)
ruff format src/ tests/ scripts/ streamlit_app/
ruff check  src/ tests/ scripts/ streamlit_app/

# Run tests (or `make test`)
pytest -v

# Generate coverage report
pytest --cov=src --cov-report=html
```

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) with a scope:

```
<type>(<scope>): <short description>
```

Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `perf`, `chore`, `ci`, `build`.

Examples from this repo's history:

- `feat(eval): 三路径评估框架和数据生成系统`
- `fix(compose): pgAdmin 邮箱修复 + CLICKHOUSE_DB 清理 + 挂载 P1 few-shot 数据`
- `chore(deps): 移除 Black，统一到 ruff format —— 单工具管 lint + format`
- `chore(gitignore): dedupe .DS_Store + 补 defensive 规则`
- `fix(makefile): make help 正则支持含数字的 target 名`

Descriptions may be in English or Chinese. Keep the subject line under ~72 characters; put context and rationale in the commit body.

## Testing

### Running Tests
```bash
# Unit tests (no Postgres needed — this is what CI's `test` job runs)
pytest -m "not integration"

# All tests; integration tests skip cleanly when PG is unreachable
pytest                        # or: make test

# One module
pytest tests/p1/
pytest tests/data/

# With coverage (CI gate is 72%, scoped to src/chat_bi_agent)
pytest --cov=src/chat_bi_agent --cov-report=html
```

Tests are grouped by module under `tests/{p1,p2,p3,data,eval,llm,schema,viz,scripts,shared}/`. Integration tests that require a running Postgres + seed data are marked `@pytest.mark.integration`.

**Running the integration tests** needs real data, and the seed must be fixed — 43 gold-SQL
row-count guards assert exact row counts:

```bash
docker compose up -d postgres
python -m chat_bi_agent.data.seed --rows 100000 --seed 42 --with-events --truncate
pytest -m integration
```

Whether they skip is decided by an actual `SELECT 1` probe, not by the presence of `PG_HOST`
(that variable is always set in `.env`, so using it as the switch makes anyone without Docker hit
connection errors instead of a clean skip).

**Your PR runs three CI jobs**: `test` (ruff + unit tests + 72% coverage gate), `integration`
(starts Postgres, seeds it, runs the 46 integration tests), and `audit` (`pip-audit` dependency
vulnerability scan). None of them are allowed to fail.

### Writing Tests
- Place tests under the matching `tests/<module>/` directory
- Name test files as `test_*.py`
- Use descriptive test function names
- Example:
  ```python
  def test_transaction_generator_creates_valid_transactions():
      # Test implementation
      pass
  ```

## Pull Request Process

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the code standards

3. **Write/update tests** for new functionality

4. **Update documentation** if needed

5. **Push and create a PR**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **PR Requirements**
   - Clear description of changes
   - Link related issues if applicable
   - All tests pass
   - Code coverage maintained or improved
   - No style/lint violations

## Reporting Issues

When reporting bugs:
1. Use a clear, descriptive title
2. Describe the exact steps to reproduce
3. Provide expected vs actual behavior
4. Include Python version and environment details
5. Attach error logs if applicable

File issues at: <https://github.com/Zsyyxrs/chat-bi-agent/issues>

## Questions or Need Help?

- 📧 Email: zhusayi1994@gmail.com
- 📖 Documentation: See [README.md](README.md) (中文) and [README.en.md](README.en.md) (English)
- 🏛️ Design decisions & ADRs: [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)
- 📊 Evaluation: [EVALUATION_FRAMEWORK.md](EVALUATION_FRAMEWORK.md)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

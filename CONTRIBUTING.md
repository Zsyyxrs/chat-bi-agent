# Contributing to chat-bi-agent

Thank you for your interest in contributing to chat-bi-agent! This document provides guidelines and instructions for getting started.

## Development Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 13+ or Docker
- Git

### Local Development

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/Zsyyxrs/chat-bi-agent.git
   cd chat-bi-agent
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -e .
   pip install -e ".[dev]"
   ```

4. **Start services and seed the database**
   ```bash
   # 1) Start PostgreSQL
   docker compose up -d postgres

   # 2) Seed data (first time or when resetting)
   docker compose --profile seed run --rm seed

   # 3) Start the Streamlit app (http://localhost:8501)
   docker compose up -d app
   ```
   > The `seed` service is gated behind a Compose profile, so a bare `docker compose up` will NOT run it. Trigger it explicitly with `--profile seed`. To seed against a local Python env instead of the container, run `python -m chat_bi_agent.data.seed --host localhost --port 5432 --truncate --with-events`.

## Project Structure

```
src/chat_bi_agent/
├── data/                          # Data layer
│   ├── events/                    # YAML event definitions
│   ├── db.py                      # Database models and connections
│   ├── seed.py                    # Database initialization
│   ├── transaction_generator.py   # Synthetic data generation
│   ├── dimension_generator.py     # Dimension data generation
│   ├── event_loader.py            # Event YAML parser
│   └── propagation_engine.py      # Event-driven mutations
└── eval/                          # Evaluation framework
    ├── rca_evaluator.py           # Root cause attribution eval
    ├── precision_retrieval_evaluator.py
    └── multi_step_analysis_evaluator.py

tests/                            # Test suite
results/                          # Evaluation outputs
docker/                           # Docker configurations
```

## Code Standards

### Style Guide
- **Formatter**: Ruff (`ruff format`, line length: 100)
- **Linter**: Ruff (`ruff check`)
- **Python Version**: 3.11+

### Before Committing

```bash
# Format code
ruff format src/

# Check code quality
ruff check src/

# Run tests
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
# All tests
pytest

# Specific file
pytest tests/test_generator.py

# With coverage
pytest --cov=src --cov-report=html
```

### Writing Tests
- Place tests in `tests/` directory
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

## Questions or Need Help?

- 📧 Email: zhusayi1994@gmail.com
- 📖 Documentation: See [README.md](README.md) and [README.zh.md](README.zh.md)
- 🔍 Architecture: Read [金融data agent架构设计.md](金融data%20agent架构设计.md)
- 📊 Evaluation: See [EVALUATION_FRAMEWORK.md](EVALUATION_FRAMEWORK.md)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

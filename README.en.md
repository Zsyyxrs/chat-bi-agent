# chat-bi-agent

[中文](./README.md) | **English**

> Banking BI self-service agent — Compress "submit request → wait for scheduling → develop report → find data → manual root cause analysis" into "ask once → get data instantly → auto attribution → ask follow-ups"

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

## ✨ Features

- **Precision Data Retrieval (P1)**: Direct access to banking data via natural language queries
  - Real-time metric lookup with `NL2SQL`
  - Automatic result visualization
  - Confidence score and data lineage tracking

- **Multi-step Analysis (P2)**: Intelligent BI assistant for business users
  - Automatic metric decomposition and trend analysis
  - Smart follow-up question suggestions
  - Insight cards with actionable recommendations

- **Root Cause Attribution (P3)**: Auto-attribution engine for anomaly analysis
  - Dimensional drill-down analysis
  - YoY and MoM comparison
  - Contribution analysis and anomaly detection
  - Smart hypothesis verification

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL 13+
- Docker & Docker Compose (optional)

### Installation

```bash
# Clone the repository
git clone https://github.com/Zsyyxrs/chat-bi-agent.git
cd chat-bi-agent

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
pip install -e ".[dev]"  # For development
```

### Database Setup

```bash
# Option 1: Using Docker Compose
docker-compose up -d

# Option 2: Manual PostgreSQL setup
createdb chat_bi_agent
python -m src.chat_bi_agent.data.seed --help
```

### Run Demo Seed Data

```bash
# Generate sample banking data
python -m src.chat_bi_agent.data.seed \
  --num-customers 100 \
  --num-months 12 \
  --with-events

# Run evaluation framework
python -m src.chat_bi_agent.eval.rca_evaluator
```

## 📖 Documentation

- **[Architecture Design](./金融data%20agent架构设计.md)** (Chinese) - Complete system design and business logic
- **[Evaluation Framework](./EVALUATION_FRAMEWORK.md)** (Chinese) - Three-phase evaluation strategy and metrics
- **[Contributing Guide](./CONTRIBUTING.md)** - Development setup and contribution process

## 🏗 Architecture

```
┌─────────────────────────────────────────┐
│    Business User Interface (Web)        │
│  Metric Marketplace │ Q&A │ Insights   │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│    LangGraph Multi-Agent Orchestrator   │
│ ┌──────────┐  ┌──────────┐ ┌─────────┐ │
│ │ Planner  │→ │ Router   │→│Data Ret.│ │
│ └──────────┘  └──────────┘ └─────────┘ │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│   PostgreSQL Data Warehouse             │
│ • Banking transactions & dimensions     │
│ • Event propagation for attribution     │
│ • Real-time metric computation          │
└─────────────────────────────────────────┘
```

### Core Modules

- **`data/`** - Data generation, seeding, and event propagation
  - `seed.py` - Database initialization with banking mock data
  - `transaction_generator.py` - Synthetic transaction generation
  - `event_loader.py` - Event library YAML parser
  - `propagation_engine.py` - Event-driven data mutation

- **`eval/`** - Evaluation framework for three agent paths
  - `rca_evaluator.py` - Root cause attribution evaluation
  - `precision_retrieval_evaluator.py` - NL2SQL accuracy assessment
  - `multi_step_analysis_evaluator.py` - Complex query handling

## 🛠 Tech Stack

- **Language**: Python 3.10+
- **Database**: PostgreSQL 13+
- **ORM**: SQLAlchemy 2.0+
- **CLI**: Click 8.1+
- **Data Generation**: Faker 20.0+
- **Config**: PyYAML 6.0+
- **Testing**: Pytest 7.0+
- **Code Quality**: Black, Ruff

## 📊 Development

### Run Tests

```bash
pytest -v
pytest --cov=src --cov-report=html  # With coverage report
```

### Code Quality Checks

```bash
black src/
ruff check src/
```

### Project Structure

```
chat-bi-agent/
├── src/chat_bi_agent/
│   ├── data/
│   │   ├── events/           # YAML event library
│   │   ├── db.py
│   │   ├── seed.py
│   │   ├── transaction_generator.py
│   │   └── propagation_engine.py
│   ├── eval/
│   │   ├── rca_evaluator.py
│   │   ├── precision_retrieval_evaluator.py
│   │   └── multi_step_analysis_evaluator.py
│   └── __init__.py
├── docker/
├── tests/
├── results/                  # Evaluation results and logs
├── pyproject.toml
├── docker-compose.yml
└── README.md
```

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed guidelines.

## 📄 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## 👤 Authors

- **Shangyi Zhu** - *Initial work*

## 📧 Contact

For questions or feedback, please reach out to zhusayi1994@gmail.com

---

**Last Updated**: May 2026

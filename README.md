# chat-bi-agent

**中文** | [English](./README.en.md)

> 面向银行业务人员的对话式 BI 智能体——把"提需求→排期→开发报表→看报表→找数→人工归因"的传统链路，压缩成"一句话提问→直接出数→自动归因→可追问"

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

## ✨ 核心功能

- **精准取数（P1）**：通过自然语言精准获取银行数据
  - 实时指标查询和 NL2SQL 转换
  - 自动结果可视化
  - 置信度评分和数据血缘追溯

- **多步分析（P2）**：面向业务人员的智能 BI 助手
  - 自动拆解和趋势分析
  - 智能追问建议
  - 可视化洞察卡片和行动建议

- **归因分析（P3）**：自动根因归因引擎
  - 多维度下钻分析
  - 同比和环比对比
  - 贡献度分析和异常检测
  - 智能假设验证

## 🚀 快速开始

### 前置要求

- Python 3.10+
- PostgreSQL 13+
- Docker & Docker Compose（可选）

### 安装

```bash
# 克隆仓库
git clone https://github.com/Zsyyxrs/chat-bi-agent.git
cd chat-bi-agent

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -e .
pip install -e ".[dev]"  # 开发依赖
```

### 数据库配置

```bash
# 方式一：使用 Docker Compose
docker-compose up -d

# 方式二：手动配置 PostgreSQL
createdb chat_bi_agent
python -m src.chat_bi_agent.data.seed --help
```

### 运行示例数据

```bash
# 生成银行模拟数据
python -m src.chat_bi_agent.data.seed \
  --num-customers 100 \
  --num-months 12 \
  --with-events

# 运行评估框架
python -m src.chat_bi_agent.eval.rca_evaluator
```

## 📖 文档

- **[架构设计](./金融data%20agent架构设计.md)** - 完整系统设计和业务逻辑
- **[评估框架](./EVALUATION_FRAMEWORK.md)** - 三路径评估策略和指标体系
- **[贡献指南](./CONTRIBUTING.md)** - 开发设置和贡献流程

## 🏗 系统架构

```
┌─────────────────────────────────────────┐
│    业务人员工作台（Web）                 │
│  指标广场 │ 对话式问答 │ 洞察卡片       │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│    LangGraph 多智能体编排                │
│ ┌──────────┐  ┌──────────┐ ┌─────────┐ │
│ │ 规划器   │→ │ 路由器   │→│取数智能体│ │
│ └──────────┘  └──────────┘ └─────────┘ │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│   PostgreSQL 数据仓库                    │
│ • 银行交易和维度数据                     │
│ • 事件传播和归因计算                     │
│ • 实时指标计算                          │
└─────────────────────────────────────────┘
```

### 核心模块

- **`data/`** - 数据生成、种子和事件传播
  - `seed.py` - 数据库初始化和模拟数据
  - `transaction_generator.py` - 合成交易生成
  - `event_loader.py` - 事件库 YAML 解析
  - `propagation_engine.py` - 事件驱动数据变更

- **`eval/`** - 三路径评估框架
  - `rca_evaluator.py` - 根因分析评估
  - `precision_retrieval_evaluator.py` - NL2SQL 准确度
  - `multi_step_analysis_evaluator.py` - 复杂查询处理

## 🛠 技术栈

- **语言**：Python 3.10+
- **数据库**：PostgreSQL 13+
- **ORM**：SQLAlchemy 2.0+
- **CLI**：Click 8.1+
- **数据生成**：Faker 20.0+
- **配置**：PyYAML 6.0+
- **测试**：Pytest 7.0+
- **代码质量**：Black, Ruff

## 📊 开发指南

### 运行测试

```bash
pytest -v
pytest --cov=src --cov-report=html  # 包含覆盖率报告
```

### 代码质量检查

```bash
black src/
ruff check src/
```

### 项目结构

```
chat-bi-agent/
├── src/chat_bi_agent/
│   ├── data/
│   │   ├── events/           # YAML 事件库
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
├── results/                  # 评估结果和日志
├── pyproject.toml
├── docker-compose.yml
└── README.md
```

## 🤝 贡献指南

欢迎提交贡献！请按以下步骤：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 开启 Pull Request

详见 [CONTRIBUTING.md](./CONTRIBUTING.md)

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 👤 作者

- **Shangyi Zhu** - *项目初始工作*

## 📧 联系方式

如有问题或反馈，欢迎联系 zhusayi1994@gmail.com

---

**最后更新**：2026 年 5 月

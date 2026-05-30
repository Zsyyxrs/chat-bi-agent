# Chat BI Agent: 三路径评估框架

## 概述

chat-bi-agent 项目包含一个全面的评估框架，覆盖三个分析能力路径，每个路径都有专门的问题集和评估器模块。

---

## 路径 1: 精准取数 (P1 / Precision Data Retrieval)

**目标：** 评估 NL2SQL Agent 将自然语言查询翻译为准确 SQL 并从银行数据仓库检索精确数据的能力。

### 问题集
📄 **文件：** `src/chat_bi_agent/data/precision_retrieval_evaluation.yaml`
- **总问题数：** 8 题 (`precision_q001` - `precision_q008`)
- **问题类型：**
  - 基础维度导航 (2 题)
  - 时间窗口过滤 (2 题)
  - 多表关联与维度导航 (2 题)
  - 复杂聚合和排序 (2 题)

### 评估维度
| 维度 | 权重 | 测量内容 |
|------|------|---------|
| 表选择 | 20% | 是否选择了正确的表 |
| 过滤准确度 | 25% | WHERE 子句条件的准确性 |
| 列选择 | 15% | 是否选择了正确的 SELECT 列 |
| 聚合函数 | 15% | GROUP BY 和聚合函数是否正确 |
| 结果行数 | 15% | 返回结果是否在预期范围内 |
| SQL 语法 | 10% | 查询是否可以执行无误 |

### 通过标准
- **单题：** combined_score ≥ 0.7
- **整体：** pass_rate ≥ 70%

### 评估器模块
📦 **类：** `PrecisionRetrievalEvaluator`
📄 **文件：** `src/chat_bi_agent/eval/precision_retrieval_evaluator.py`

```python
from chat_bi_agent.eval.precision_retrieval_evaluator import PrecisionRetrievalEvaluator

evaluator = PrecisionRetrievalEvaluator()
score = evaluator.evaluate_response(
    question_id="precision_q001",
    generated_sql="SELECT ... FROM dim_customer WHERE ...",
    actual_results=[...],
    execution_error=None
)
```

### 示例问题
1. **precision_q001：** 查询上海分行的高净值客户（含过滤条件）
2. **precision_q003：** 按特定日期过滤交易
3. **precision_q005：** 多分行存款余额聚合与时间提取
4. **precision_q008：** 时间窗口分析与百分比变化计算

---

## 路径 2: 多步分析 (P2 / Multi-step Analysis)

**目标：** 评估 Analysis Agent 进行多步骤推理、识别模式和综合业务洞察的能力。

### 问题集
📄 **文件：** `src/chat_bi_agent/data/multi_step_analysis_evaluation.yaml`
- **总问题数：** 8 题 (`multi_step_q001` - `multi_step_q008`)
- **问题类型：**
  - 时间对比分析 (2 题)
  - 产品生命周期分析 (2 题)
  - 跨维度关联分析 (2 题)
  - 预测与趋势外推 (2 题)

### 评估维度
| 维度 | 权重 | 测量内容 |
|------|------|---------|
| 步骤完整性 | 20% | 是否完成了必要的分析步骤 |
| 多指标覆盖 | 20% | 是否覆盖了多个关键指标 |
| 洞察准确度 | 25% | 发现的洞察与预期的相符程度 |
| 推理质量 | 20% | 逻辑推理的严谨性和因果关系 |
| 业务相关性 | 15% | 结论与业务运营的贴切度 |

### 通过标准
- **单题：** combined_score ≥ 0.7
- **整体：** pass_rate ≥ 70%

### 评估器模块
📦 **类：** `MultiStepAnalysisEvaluator`
📄 **文件：** `src/chat_bi_agent/eval/multi_step_analysis_evaluator.py`

```python
from chat_bi_agent.eval.multi_step_analysis_evaluator import MultiStepAnalysisEvaluator

evaluator = MultiStepAnalysisEvaluator()
score = evaluator.evaluate_response(
    question_id="multi_step_q001",
    agent_response="...",
    mentioned_steps=["step1", "step2", ...],
    mentioned_metrics=["total_amount", "daily_average", ...],
    extracted_insights=[...]
)
```

### 示例问题
1. **multi_step_q001：** 对比春节前后现金支取行为（总额、日均、客户数、渠道分布）
2. **multi_step_q003：** 分析理财产品到期前后的客户赎回和续作行为
3. **multi_step_q006：** 跨事件对比：产品到期 vs 季节性现象的传导机制
4. **multi_step_q008：** 设计客户流失风险预警模型与干预策略

---

## 路径 3: 根因分析 (P3 / Root Cause Attribution)

**目标：** 评估 RCA Agent 从埋雷事件模式中识别根因并将观察到的业务指标变化归因于底层因素的能力。

### 问题集
📄 **文件：** `src/chat_bi_agent/data/attribution_evaluation.yaml`
- **总问题数：** 8 题 (`attribution_q001` - `attribution_q008`)
- **问题类型：**
  - 直接事件归因 (2 题)
  - 二阶指标分析 (2 题)
  - 多事件干扰下的信号分离 (1 题)
  - 预测性洞察生成 (1 题)
  - 困难题与干扰模式 (2 题)

### 评估维度
| 维度 | 权重 | 测量内容 |
|------|------|---------|
| 事件命中 | 40% | 是否正确识别了根因事件 |
| 维度回忆 | 30% | 是否识别了关键受影响维度 |
| 结论相似度 | 20% | 与期望根因的语义匹配度 |
| 幻觉惩罚 | 10% | 是否存在事实错误或自相矛盾 |

### 通过标准
- **单题：** combined_score ≥ 0.7
- **整体：** pass_rate ≥ 70%

### 评估器模块
📦 **类：** `RCAEvaluator`
📄 **文件：** `src/chat_bi_agent/eval/rca_evaluator.py`

```python
from chat_bi_agent.eval.rca_evaluator import RCAEvaluator

evaluator = RCAEvaluator()
score = evaluator.evaluate_response(
    question_id="attribution_q001",
    agent_response="...",
    agent_extracted_dimensions={"branch_id": "BR_CITY_0006", ...},
    agent_identified_event="anxin_90_expire",
    agent_conclusion="..."
)
```

### 示例问题
1. **attribution_q001：** 上海分行高净值客户存款在 2026-05-14 下降 8%，原因是什么？
2. **attribution_q003：** 全行 ATM 和柜面现金支取在春节期间增加 25%，是异常吗？
3. **attribution_q005：** LPR 下调后贷款申请量上升，政策传导的滞后期是多久？
4. **attribution_q006：** 七夕营销活动驱动了定期存款增长 12%，影响范围是哪些分行？

### 埋雷事件（数据生成）
四个真实世界事件被嵌入到种子数据中，创建可检测的因果模式：

1. **anxin_90_expire** (2026-05-14)
   - 产品到期触发：58% 赎回率，42% 续作率
   - 受影响对象：高净值客户，上海分行
   - 指标变化：零售存款 ↓8.5%，AUM ↓3.2%

2. **spring_festival_withdrawal** (2026-02-15 至 2026-02-23)
   - 季节性现金支取高峰：交易量 +25%
   - 受影响对象：BASIC/MASS 层级，ATM/COUNTER 渠道
   - 指标变化：日均余额 ↓18%

3. **lpr_cut_q2** (2026-06-20)
   - 政策驱动的贷款需求：申请量 +12%（7-14 天延迟）
   - 受影响对象：消费贷款（快速响应），企业贷款（缓慢响应）
   - 指标变化：贷款申请 ↑12%，贷款余额 ↑5.5%

4. **qixi_deposit_campaign** (2026-08-10)
   - 营销驱动的存款增长：+12%（目标分行）
   - 受影响对象：MASS/AFFLUENT 层级，杭州/南京分行
   - 指标变化：定期存款 ↑12%，2-5 天响应延迟

---

## 数据生成与事件传导

所有三个评估框架都依赖一个包含 299,476 行真实银行数据的种子数据库：

### 种子化过程
```bash
cd /Users/zhushangyi/CourseData/LLM_Projects/chat-bi-agent

# 启用事件传导种子化（用于所有三个评估路径）
python -m chat_bi_agent.data.seed \
    --host localhost \
    --port 5432 \
    --database chatbi \
    --truncate \
    --rows 100000 \
    --with-events
```

### 事件传导引擎
📦 **类：** `PropagationEngine`
📄 **文件：** `src/chat_bi_agent/data/propagation_engine.py`

特性：
- **延迟语义：** 事件仅在 `delay_days` 之后影响数据
- **渐进语义：** 效应在 `ramp_days` 内逐步增加（线性或指数）
- **抽样机制：** 效应只应用于账户/客户的子集
- **多表支持：** 规则可以针对 fct_transaction、fct_balance_daily 或 fct_holding

### 事件配置
📄 **文件：** `src/chat_bi_agent/data/events/`
- 每个事件定义为 YAML 格式，包含传导规则
- 规则指定：target_table、target_column、delta(%)、delay_days、ramp_days

---

## 运行评估

### 单路径评估
```python
# P1：精准取数
from chat_bi_agent.eval.precision_retrieval_evaluator import PrecisionRetrievalEvaluator
evaluator = PrecisionRetrievalEvaluator()
results = [{"question_id": "precision_q001", "generated_sql": "...", ...}]
eval_result = evaluator.evaluate_batch(results)
print(eval_result.summary())

# P2：多步分析
from chat_bi_agent.eval.multi_step_analysis_evaluator import MultiStepAnalysisEvaluator
evaluator = MultiStepAnalysisEvaluator()
results = [{"question_id": "multi_step_q001", "agent_response": "...", ...}]
eval_result = evaluator.evaluate_batch(results)
print(eval_result.summary())

# P3：根因分析
from chat_bi_agent.eval.rca_evaluator import RCAEvaluator
evaluator = RCAEvaluator()
results = [{"question_id": "attribution_q001", "agent_response": "...", ...}]
eval_result = evaluator.evaluate_batch(results)
print(eval_result.summary())
```

### 访问问题集
```python
# 加载路径的所有问题
evaluator = PrecisionRetrievalEvaluator()
questions = evaluator.questions
print(f"总问题数：{len(questions)}")

# 获取特定问题
question = evaluator.get_question("precision_q001")
print(question["question"])
print("期望 SQL：", question["expected_sql"])
```

---

## 数据模式概览

### 维度表 (6 个)
- `dim_branch` (50 行)：4 级分层结构（总行 → 省行 → 城市行 → 支行）
- `dim_customer` (5,000 行)：4 个客户等级（HIGH_NET_WORTH、AFFLUENT、MASS、BASIC）
- `dim_product` (91 行)：产品分类（理财、存款、贷款、保险、信用卡）
- `dim_account` (10,000 行)：账户类型及关联产品
- `dim_date` (730 行)：2025-01 至 2026-12，包含节假日/月末标志
- (24 个月索引在事实表上)

### 事实表 (5 个)
- `fct_transaction` (100,000 行)：日交易数据，含交易类型（存款、支取、转账、支付、利息、费用）
- `fct_balance_daily` (~51,600 行)：按账户日终余额快照
- `fct_holding` (3,000 行)：理财/基金持有人快照
- `fct_risk_event` (3 行)：风险事件（低频率）
- `fct_campaign_response` (~129,500 行)：营销活动交互数据

---

## 成功标准

| 路径 | 成功指标 | P70 目标 | P90 目标 |
|------|---------|---------|---------|
| **P1 (精准取数)** | SQL 准确度与结果正确性 | 70%+ 题目 ≥0.7 分 | 90%+ 题目 ≥0.7 分 |
| **P2 (多步分析)** | 多步推理与洞察质量 | 70%+ 题目 ≥0.7 分 | 90%+ 题目 ≥0.7 分 |
| **P3 (根因分析)** | 根因识别与维度回忆 | 70%+ 题目 ≥0.7 分 | 90%+ 题目 ≥0.7 分 |

---

## 文件清单

```
src/chat_bi_agent/
├── data/
│   ├── precision_retrieval_evaluation.yaml    # P1 问题集 (8 题)
│   ├── multi_step_analysis_evaluation.yaml    # P2 问题集 (8 题)
│   ├── attribution_evaluation.yaml            # P3 问题集 (8 题)
│   ├── seed.py                                # 数据生成编排器
│   ├── transaction_generator.py               # 事实表生成器
│   ├── dimension_generator.py                 # 维度表生成器
│   ├── propagation_engine.py                  # 事件传导逻辑
│   ├── event_loader.py                        # YAML 事件解析器
│   └── events/
│       ├── anxin_90_expire.yaml
│       ├── spring_festival_withdrawal.yaml
│       ├── lpr_cut_q2.yaml
│       └── qixi_deposit_campaign.yaml
└── eval/
    ├── precision_retrieval_evaluator.py       # P1 评估器
    ├── multi_step_analysis_evaluator.py       # P2 评估器
    └── rca_evaluator.py                       # P3 评估器
```

---

## 后续步骤

1. **实现 Agent**
   - NL2SQL Agent (P1)：将自然语言转化为 SQL
   - 分析 Agent (P2)：从查询结果进行多步推理
   - 根因分析 Agent (P3)：从数据模式中识别根因

2. **集成测试**
   - 针对实现的 Agent 运行完整评估套件
   - 监控三条路径的通过率
   - 识别失败模式并改进训练数据

3. **性能优化**
   - 缓存常用查询 (P1)
   - 并行化多步查询 (P2)
   - 优化事件模式检测 (P3)

4. **监控与部署**
   - 在生产环境中跟踪评估指标
   - 实现反馈循环以持续改进
   - 记录边界情况和已知限制

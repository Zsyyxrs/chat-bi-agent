"""P1 Precision Data Retrieval Evaluator: assess NL2SQL accuracy of Retrieval Agent."""

import re
import textwrap
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

import sqlglot
import yaml

AGG_PATTERN = re.compile(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)


def _jaccard(a: set, b: set) -> float:
    """|A ∩ B| / |A ∪ B|；并集为空时返回 1.0（双方都空视为 vacuous match）。"""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


@dataclass
class PrecisionScore:
    """单个精准取数问题的评估分数。"""

    question_id: str
    sql_syntactically_correct: bool = False  # SQL 语法是否正确
    table_score: float = 0.0  # 0-1: 表选择 Jaccard 相似度 |∩|/|∪|
    filter_accuracy: float = 0.0  # 0-1: 过滤条件的准确度
    column_score: float = 0.0  # 0-1: 列选择 Jaccard 相似度 |∩|/|∪|
    aggregation_correct: bool = False  # 聚合函数是否正确（如适用）
    result_count_correct: bool = False  # 返回行数是否在预期范围内
    response_time_seconds: float = 0.0
    # 诊断字段：与 gold SQL 的结果集是否一致。None = 未评估（没注入 executor / gold 跑不通）。
    # **刻意不计入 combined_score**——现有权重和为 1.0，加进去会改变所有历史分数、
    # 废掉 baseline 可比性。它的作用是让"语义不忠实"可见：mr_n12 丢掉 Top-5 时
    # 表/过滤/聚合全对，总分只扣 0.075，靠分数根本发现不了。
    result_match: bool | None = None
    # 诊断字段：gold 的分组键是否**全部**出现在生成 SQL 的分组键里。
    # None = 未评估（任一侧解析失败，或两侧都没有 GROUP BY）。
    # 与 result_match 一样**刻意不计入 combined_score**。理由见 2026-09-08 的 q013：
    # 模型按 customer_name 而非 customer_id 聚合（姓名不唯一，会并掉不同客户），
    # 行数、result_match、六个维度全部为它背书，拿 0.837；随后修对分组键，
    # **分数仍是 0.837**，一个千分位都没动——现有维度根本不在度量聚合粒度。
    # 用子集而非相等：修对后的 SQL 多带了对 id 函数依赖的 name 列，一起分组无害；
    # 反方向（多分一个键把行拆细）由 result_count 与 result_match 承担。
    group_by_match: bool | None = None

    @property
    def combined_score(self) -> float:
        """综合评分：0-1，权重分配。"""
        weights = {
            "table_selection": 0.2,  # 最重要：是否选择了正确的表
            "filter_accuracy": 0.25,  # 次重要：过滤条件准确度
            "column_selection": 0.15,  # 重要：列选择
            "aggregation": 0.15,  # 聚合正确性
            "result_count": 0.15,  # 结果数量是否合理
            "syntax": 0.1,  # 语法正确性
        }

        score = (
            self.table_score * weights["table_selection"]
            + self.filter_accuracy * weights["filter_accuracy"]
            + self.column_score * weights["column_selection"]
            + (1.0 if self.aggregation_correct else 0.0) * weights["aggregation"]
            + (1.0 if self.result_count_correct else 0.0) * weights["result_count"]
            + (1.0 if self.sql_syntactically_correct else 0.0) * weights["syntax"]
        )

        return max(0.0, score)


@dataclass
class PrecisionEvaluation:
    """完整评估结果集。"""

    total_questions: int = 0
    scores: list[PrecisionScore] = field(default_factory=list)
    passed_questions: int = 0  # combined_score >= 0.7

    @property
    def pass_rate(self) -> float:
        """通过率（>= 0.7 分为通过）。"""
        if self.total_questions == 0:
            return 0.0
        return self.passed_questions / self.total_questions

    @property
    def avg_score(self) -> float:
        """平均分数。"""
        if not self.scores:
            return 0.0
        return sum(s.combined_score for s in self.scores) / len(self.scores)

    def summary(self) -> str:
        """生成评估摘要。"""
        return textwrap.dedent(f"""\
            P1 Precision Retrieval Evaluation Summary
            ==========================================
            Total Questions: {self.total_questions}
            Passed (>= 0.7): {self.passed_questions}
            Pass Rate: {self.pass_rate:.1%}
            Average Score: {self.avg_score:.3f}

            Details:
            --------
        """)


_TABLE_RE = re.compile(r"(?:FROM|JOIN)\s+(\w+)", re.IGNORECASE)


def tables_in_sql(sql: str) -> set[str]:
    """抽出 SQL 真正读到的表名（小写），**排除同查询内定义的 CTE**。

    2026-09-08 修：原实现是正则 `(?:FROM|JOIN)\\s+(\\w+)`，把 `FROM txn_agg`
    这种对 CTE 的引用也算成表。后果不是随机噪声而是**系统性偏置**——它惩罚
    「用 CTE 写」这个风格选择，且不对称：gold 惯用派生子查询（`FROM (` 不匹配
    `\\w+`，逃过），生成 SQL 用 `WITH` 就中招。table_score 权重 0.2 且计入
    combined_score，四道用 CTE 的题（q008/q012/q013/q014）表选择本应满分却被
    算成 0.5~0.6，各扣 0.08~0.10；已发布的 P1 baseline 因此被低估
    （0.9646 实为 0.9771）。偏置还随题目难度上升——分析型 SQL 天然用 CTE。

    解析失败时**退回老正则**而不是返回空集合：空集合会让 Jaccard 变 0、
    把 table_score 打到底，等于因为「我解析不了」而判它零分。
    """
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        tree = None
    if tree is None:
        return {m.lower() for m in _TABLE_RE.findall(sql or "")}
    ctes = {c.alias_or_name.lower() for c in tree.find_all(sqlglot.exp.CTE)}
    return {t.name.lower() for t in tree.find_all(sqlglot.exp.Table)} - ctes


def group_by_keys(sql: str) -> set[str] | None:
    """把 SQL 里所有 GROUP BY 的分组键归一成**裸列名集合**。

    用途见 `PrecisionScore.group_by_match`：抓「聚合粒度错了但结果看起来对」。

    三处归一都是必要的，否则这个诊断对本项目的 gold 直接失效：
    - **剥表别名**：gold 写 `ft.branch_id`、生成 SQL 写 `t.branch_id`，别名不同不是粒度不同。
    - **解析序号**：gold 里大量 `GROUP BY 1, 2`，不按 SELECT 列表回解就什么都拿不到。
    - **穿透所有 scope**：分组常发生在子查询/CTE 里，只看最外层等于什么都看不到。

    表达式（如 `DATE_TRUNC('month', dt)`）取它引用到的全部列名——按月分组的粒度
    取决于 dt。解析失败返回 None（未知），没有 GROUP BY 返回空集合。
    """
    try:
        trees = sqlglot.parse(sql, read="postgres")
    except Exception:
        return None
    if not trees or any(t is None for t in trees):
        return None

    keys: set[str] = set()
    for tree in trees:
        for select in tree.find_all(sqlglot.exp.Select):
            group = select.args.get("group")
            if group is None:
                continue
            projections = select.expressions
            for key in group.expressions:
                target = key
                # GROUP BY 1 → 回解成 SELECT 列表第 1 项
                if isinstance(key, sqlglot.exp.Literal) and key.is_int:
                    idx = int(key.name) - 1
                    if not (0 <= idx < len(projections)):
                        continue
                    target = projections[idx]
                for col in target.find_all(sqlglot.exp.Column):
                    keys.add(col.name)
    return keys


def _compare_group_by(gold_sql: str, generated_sql: str) -> bool | None:
    """gold 的分组键是否全部出现在生成 SQL 的分组键里。任一端不可解就返 None。

    两边都没有 GROUP BY 时同样返 None 而不是 True——这个诊断对非聚合题没有意义，
    报 True 会让「已检查且通过」的题数虚高。
    """
    gold_keys = group_by_keys(gold_sql or "")
    gen_keys = group_by_keys(generated_sql or "")
    if gold_keys is None or gen_keys is None:
        return None
    if not gold_keys and not gen_keys:
        return None
    return gold_keys.issubset(gen_keys)


def _canonical_value(v: object) -> str:
    """单个值归一成字符串。浮点抹末位噪声，日期/时间戳抹表示差异。

    日期这一支是 2026-09-08 补的。此前非数值列一律走 str(v)，于是 gold 的
    `DATE_TRUNC('month', dt)`（timestamptz）与生成 SQL 的同式 `::DATE`（date）
    在**金额逐行完全相同**时仍被判为不一致——"2026-01-01 00:00:00+08:00" 与
    "2026-01-01" 字符串不等。这是类级误判，凡 gold 返回 timestamp 列的题都会中招。

    只归一「零点时间戳 → 日期」这一种情形，不敢再宽：带真实时分秒的值必须照旧
    逐值比，否则会把「按天聚合」和「按小时聚合」判成相同。代价是零点时间戳的时区
    被丢弃——两个结果集都来自同一个库同一次会话，跨时区混比不是真实场景，
    而 date/timestamp 表示差异是每道日期题都会碰上的。
    """
    if isinstance(v, float):
        return f"{round(v, 4):.4f}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{float(v):.4f}"
    # datetime 是 date 的子类，必须先判它
    if isinstance(v, datetime):
        if v.time() == time(0, 0):
            return v.date().isoformat()
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _normalize_row(row: dict) -> tuple:
    """把一行归一成可比较的值元组：忽略列名与列序。

    忽略列名是必要的——gold 与生成 SQL 的别名经常不同
    （branch_id vs bid、avg_balance vs avg_deposit_balance）。
    """
    return tuple(sorted(_canonical_value(v) for v in row.values()))


def _result_sets_equal(gold: list[dict], actual: list[dict]) -> bool:
    """行序无关的多重集比较。"""
    from collections import Counter

    return Counter(_normalize_row(r) for r in gold) == Counter(_normalize_row(r) for r in actual)


class PrecisionRetrievalEvaluator:
    """
    P1 (Precision Data Retrieval) NL2SQL Agent 评估器。

    工作流程：
    1. 加载 precision_retrieval_evaluation.yaml 中的问题
    2. 对每个问题运行 NL2SQL Agent（生成 SQL）
    3. 执行生成的 SQL 并获取结果
    4. 与期望答案对比，评估：
       - SQL 语法正确性
       - 表选择正确性
       - 过滤条件准确度
       - 返回列是否正确
       - 聚合函数是否正确
       - 返回行数是否在合理范围
    5. 聚合为最终评分
    """

    def __init__(self, gold_executor=None):
        self.eval_dir = Path(__file__).parent.parent / "data"
        self.questions = self._load_evaluation_questions()
        # 注入后才跑 gold SQL 做结果集比对；不注入则 result_match 恒为 None
        self.gold_executor = gold_executor

    def _load_evaluation_questions(self) -> list[dict]:
        """从 YAML 加载评估问题。"""
        eval_file = self.eval_dir / "precision_retrieval_evaluation.yaml"
        if not eval_file.exists():
            return []

        with open(eval_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return data.get("evaluation_questions", [])

    def get_question(self, question_id: str) -> Optional[dict]:
        """按 ID 获取单个问题。"""
        for q in self.questions:
            if q.get("id") == question_id:
                return q
        return None

    def evaluate_response(
        self,
        question_id: str,
        generated_sql: str,
        actual_results: list[dict] = None,
        execution_error: Optional[str] = None,
    ) -> PrecisionScore:
        """
        评估 Agent 对单个问题的回答。

        Args:
            question_id: 问题 ID
            generated_sql: Agent 生成的 SQL
            actual_results: 执行 SQL 返回的结果列表
            execution_error: 执行时的错误信息（如果有）

        Returns:
            PrecisionScore: 评分对象
        """
        question = self.get_question(question_id)
        if not question:
            raise ValueError(f"Question {question_id} not found")

        score = PrecisionScore(question_id=question_id)

        # 1. SQL 语法正确性 (sql_syntactically_correct)
        if execution_error:
            score.sql_syntactically_correct = False
        else:
            score.sql_syntactically_correct = True

        # 2. 表选择 (table_score: Jaccard 相似度)
        expected_tables = self._extract_tables_from_expected_sql(question.get("expected_sql", ""))
        actual_tables = self._extract_tables_from_sql(generated_sql)
        score.table_score = _jaccard(expected_tables, actual_tables)

        # 3. 过滤条件准确度 (filter_accuracy)
        expected_filters = question.get("expected_filters", [])
        sql_lower = generated_sql.lower()
        filter_matches = 0
        total_filters = 0

        for filter_cond in expected_filters or []:
            for value in filter_cond.values():
                values = value if isinstance(value, list) else [value]
                for v in values:
                    total_filters += 1
                    if re.search(rf"\b{re.escape(str(v).lower())}\b", sql_lower):
                        filter_matches += 1

        if total_filters:
            score.filter_accuracy = filter_matches / total_filters
        else:
            # 无期望过滤条件 = 空真为真，与 _jaccard 双空匹配语义一致
            score.filter_accuracy = 1.0

        # 4. 列选择 (column_score: Jaccard 相似度)
        # 期望列为空 = 全是别名（聚合题），豁免本项检查
        expected_columns = set(question.get("expected_result_columns", []))
        if not expected_columns:
            score.column_score = 1.0
        elif actual_results:
            actual_columns = set(actual_results[0].keys())
            # 剥掉 SELECT 里 AS 别名的输出列（聚合/计算列），与 expected 对齐到"真实 schema 列"
            aliased = {m.lower() for m in re.findall(r"\bAS\s+(\w+)", generated_sql, re.IGNORECASE)}
            actual_real = {c for c in actual_columns if c.lower() not in aliased}
            score.column_score = _jaccard(expected_columns, actual_real)
        # else: 期望非空但无实际结果 → 保持默认 0.0（漏选）

        # 5. 聚合函数正确性 (aggregation_correct)
        expected_has_agg = bool(AGG_PATTERN.search(question.get("expected_sql", "")))
        generated_has_agg = bool(AGG_PATTERN.search(generated_sql))
        score.aggregation_correct = expected_has_agg == generated_has_agg

        # 6. 结果行数正确性 (result_count_correct)
        expected_count_range = question.get("expected_result_count", {})
        if expected_count_range and actual_results is not None:
            actual_count = len(actual_results)
            min_count = expected_count_range.get("min", 0)
            max_count = expected_count_range.get("max", float("inf"))
            score.result_count_correct = min_count <= actual_count <= max_count

        # 7. 结果集比对（诊断，不进总分）
        score.result_match = self._compare_with_gold(question, actual_results, execution_error)

        # 8. 分组键比对（诊断，不进总分）
        score.group_by_match = _compare_group_by(question.get("expected_sql", ""), generated_sql)

        return score

    def _compare_with_gold(
        self, question: dict, actual_results: list[dict] | None, execution_error
    ) -> bool | None:
        """跑 gold SQL 与实际结果比对。任何一端不可用就返 None，不下结论。"""
        if self.gold_executor is None or execution_error or actual_results is None:
            return None
        gold_sql = question.get("expected_sql")
        if not gold_sql:
            return None
        gold_rows, gold_err = self.gold_executor.execute(gold_sql)
        if gold_err is not None or gold_rows is None:
            return None  # gold 自己跑不通，不能据此判 agent
        return _result_sets_equal(gold_rows, actual_results)

    def _extract_tables_from_sql(self, sql: str) -> set[str]:
        """抽表名。gold 与生成 SQL **必须同源**——原本这里是两个函数体逐字相同的
        方法，改一个漏一个就会让两侧按不同规则抽表，Jaccard 直接失去意义。"""
        return tables_in_sql(sql)

    # gold 侧沿用同一个实现（保留旧名，调用方无需改动）
    _extract_tables_from_expected_sql = _extract_tables_from_sql

    def evaluate_batch(self, results: list[dict]) -> PrecisionEvaluation:
        """
        批量评估多个问题的回答。

        Args:
            results: 包含 question_id, generated_sql, actual_results 等的结果列表

        Returns:
            PrecisionEvaluation: 完整评估结果
        """
        evaluation = PrecisionEvaluation(total_questions=len(results))

        for result in results:
            score = self.evaluate_response(
                question_id=result.get("question_id"),
                generated_sql=result.get("generated_sql", ""),
                actual_results=result.get("actual_results"),
                execution_error=result.get("execution_error"),
            )
            evaluation.scores.append(score)

            if score.combined_score >= 0.7:
                evaluation.passed_questions += 1

        return evaluation

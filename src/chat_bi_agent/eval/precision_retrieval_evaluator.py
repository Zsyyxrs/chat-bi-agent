"""P1 Precision Data Retrieval Evaluator: assess NL2SQL accuracy of Retrieval Agent."""

import yaml
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class PrecisionScore:
    """单个精准取数问题的评估分数。"""

    question_id: str
    sql_syntactically_correct: bool = False  # SQL 语法是否正确
    table_selection_correct: bool = False  # 是否选择了正确的表
    filter_accuracy: float = 0.0  # 0-1: 过滤条件的准确度
    column_selection_correct: bool = False  # 返回列是否正确
    aggregation_correct: bool = False  # 聚合函数是否正确（如适用）
    result_count_correct: bool = False  # 返回行数是否在预期范围内
    response_time_seconds: float = 0.0

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
            (1.0 if self.table_selection_correct else 0.0) * weights["table_selection"]
            + self.filter_accuracy * weights["filter_accuracy"]
            + (1.0 if self.column_selection_correct else 0.0) * weights["column_selection"]
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
        return f"""
P1 Precision Retrieval Evaluation Summary
==========================================
Total Questions: {self.total_questions}
Passed (>= 0.7): {self.passed_questions}
Pass Rate: {self.pass_rate:.1%}
Average Score: {self.avg_score:.3f}

Details:
--------
"""


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

    def __init__(self):
        self.eval_dir = Path(__file__).parent.parent / "data"
        self.questions = self._load_evaluation_questions()

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

        # 2. 表选择正确性 (table_selection_correct)
        expected_tables = self._extract_tables_from_expected_sql(
            question.get("expected_sql", "")
        )
        actual_tables = self._extract_tables_from_sql(generated_sql)
        if expected_tables and actual_tables:
            score.table_selection_correct = len(expected_tables & actual_tables) > 0

        # 3. 过滤条件准确度 (filter_accuracy)
        expected_filters = question.get("expected_filters", [])
        filter_matches = 0
        if expected_filters:
            for filter_cond in expected_filters:
                for key, value in filter_cond.items():
                    if isinstance(value, list):
                        for v in value:
                            if str(v).lower() in generated_sql.lower():
                                filter_matches += 1
                    else:
                        if str(value).lower() in generated_sql.lower():
                            filter_matches += 1
            total_filters = sum(
                len(fc.values()) if isinstance(list(fc.values())[0], list) else 1
                for fc in expected_filters
                for fc_val in fc.values()
            )
            score.filter_accuracy = min(1.0, filter_matches / max(1, total_filters))

        # 4. 列选择正确性 (column_selection_correct)
        expected_columns = set(question.get("expected_result_columns", []))
        if expected_columns and actual_results:
            actual_columns = set(actual_results[0].keys()) if actual_results else set()
            score.column_selection_correct = len(expected_columns & actual_columns) > 0

        # 5. 聚合函数正确性 (aggregation_correct)
        expected_sql = question.get("expected_sql", "").upper()
        if any(agg in expected_sql for agg in ["COUNT", "SUM", "AVG", "MIN", "MAX"]):
            if any(agg in generated_sql.upper() for agg in ["COUNT", "SUM", "AVG", "MIN", "MAX"]):
                score.aggregation_correct = True
        else:
            score.aggregation_correct = True

        # 6. 结果行数正确性 (result_count_correct)
        expected_count_range = question.get("expected_result_count", {})
        if expected_count_range and actual_results is not None:
            actual_count = len(actual_results)
            min_count = expected_count_range.get("min", 0)
            max_count = expected_count_range.get("max", float("inf"))
            score.result_count_correct = min_count <= actual_count <= max_count

        return score

    def _extract_tables_from_expected_sql(self, sql: str) -> set[str]:
        """从期望的 SQL 中提取表名。"""
        import re

        # 简单的正则表达式，提取 FROM 和 JOIN 后的表名
        pattern = r"(?:FROM|JOIN)\s+(\w+)"
        matches = re.findall(pattern, sql, re.IGNORECASE)
        return set(m.lower() for m in matches)

    def _extract_tables_from_sql(self, sql: str) -> set[str]:
        """从生成的 SQL 中提取表名。"""
        import re

        pattern = r"(?:FROM|JOIN)\s+(\w+)"
        matches = re.findall(pattern, sql, re.IGNORECASE)
        return set(m.lower() for m in matches)

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

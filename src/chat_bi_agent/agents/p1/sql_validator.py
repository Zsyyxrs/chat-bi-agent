"""sqlglot AST 静态校验：只放行 SELECT/WITH，拒 DML/DDL。"""

from dataclasses import dataclass

import sqlglot
from langfuse import observe
from sqlglot import expressions as exp


@dataclass
class ValidationResult:
    ok: bool
    error: str | None


_FORBIDDEN_EXPRS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Copy,
    exp.Merge,
    exp.Command,
)


class SQLValidator:
    """sqlglot AST 静态检查。无副作用、无 LLM、可纯单元测试。

    ``dialect`` controls the sqlglot parser dialect. Defaults to postgres to
    preserve backward compat; set to ``"sqlite"`` for BIRD-style benchmarks.
    """

    def __init__(self, dialect: str = "postgres"):
        self.dialect = dialect

    @observe(name="sql_validation")
    def validate(self, sql: str) -> ValidationResult:
        try:
            parsed = sqlglot.parse(sql, dialect=self.dialect)
        except sqlglot.errors.ParseError as e:
            return ValidationResult(ok=False, error=f"sqlglot 解析失败: {e}")

        if not parsed or all(p is None for p in parsed):
            return ValidationResult(ok=False, error="sqlglot 解析结果为空")

        for stmt in parsed:
            if stmt is None:
                continue
            if not isinstance(stmt, (exp.Select, exp.With, exp.Subquery)):
                return ValidationResult(
                    ok=False,
                    error=f"顶层节点非 SELECT/WITH：{type(stmt).__name__}",
                )
            for node in stmt.walk():
                if isinstance(node, _FORBIDDEN_EXPRS):
                    return ValidationResult(
                        ok=False,
                        error=f"包含禁止操作：{type(node).__name__}",
                    )

        return ValidationResult(ok=True, error=None)

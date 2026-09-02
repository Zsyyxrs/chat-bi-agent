"""sqlglot AST 静态校验：只放行 SELECT/WITH，拒 DML/DDL。"""

from dataclasses import dataclass

import sqlglot
from sqlglot import expressions as exp

from chat_bi_agent.obs.span_kind import observe_local


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

# 顶层白名单只管「语句是什么类型」，管不到「SELECT 里调了什么函数」。
# 这些函数从 catalog 之外读字节，绕过语义层的全部治理：
#   - 本地文件：pg_read_file / lo_import  → 路径穿越
#   - 对象存储 / URL：read_parquet / read_csv  → SSRF、数据外泄
#   - 其它库：dblink / postgres_fdw  → 横向移动
# 按函数名匹配（大小写不敏感），不区分方言——名字撞车的代价远小于漏一个。
#
# 注意这是**黑名单**，不是 fail-closed 白名单：投影 / WHERE / 子查询位置
# 无法穷举所有合法标量函数，所以新出现的读取型函数需要往这里补。
# FROM/JOIN 位置的表引用另有 catalog 侧的白名单兜底。
_FORBIDDEN_FUNCS = frozenset(
    {
        # PostgreSQL — 文件读写
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        # PostgreSQL — 跨库
        "dblink",
        "dblink_exec",
        "postgres_fdw_handler",
        # DuckDB / 通用 — 文件与对象存储
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_blob",
        "read_text",
        "postgres_scan",
        "mysql_scan",
        "sqlite_scan",
        # SQLite
        "readfile",
        "writefile",
        "load_extension",
        # ── 以下为 2026-09-02 对照 SQLBot 分库危险函数表补齐 ──
        # （SQLBot backend/apps/db/db.py:1025 `DS_SPECIFIC_DANGEROUS_FUNCTIONS`）
        # MySQL — 读本地文件
        "load_file",
        # SQL Server — 命令执行 / 动态 SQL / 外部数据源
        "xp_cmdshell",
        "xp_dirtree",
        "xp_fileexist",
        "sp_executesql",
        "openrowset",
        "opendatasource",
        "openquery",
        # PostgreSQL — 原黑名单漏掉的文件面与进程面
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_tmpdir",
        "pg_logdir_ls",
        "pg_file_read",
        "pg_file_write",
        "pg_file_unlink",
        "pg_current_logfile",
        "pg_reload_conf",
        "pg_terminate_backend",
        "dblink_connect",
        # 侦察类：对指标口径驱动的 BI 查询零业务价值，只泄露库版本/账号/网络面。
        # 注意 `user` / `session_user` 裸写会被 sqlglot 解析成 Column 而非 Func，
        # 这里拦不到——放进来只会误伤名为 user 的业务列，故**有意不收**。
        "version",
        "current_user",
        "current_database",
        "current_schema",
        "pg_backend_pid",
        "inet_server_addr",
        "inet_server_port",
    }
)

# Oracle/MSSQL 风格的 `包名.函数名` 调用，在 sqlglot 里是 Dot(Identifier, Anonymous)，
# Anonymous 只带方法名（`utl_http.request` → `request`）。按函数名匹配必然漏，
# 而 `request` / `sleep` 这类通用词进黑名单又会大面积误伤——只能按**包名前缀**拦。
_FORBIDDEN_QUALIFIERS = frozenset(
    {
        "utl_http",  # 外发 HTTP → 数据外泄 / SSRF
        "utl_file",  # 读写服务器文件
        "utl_tcp",
        "utl_smtp",
        "utl_inaddr",  # DNS 外带
        "dbms_pipe",
        "dbms_lock",  # sleep → 时间盲注 / DoS
        "dbms_scheduler",
        "dbms_java",
        "dbms_xmlgen",
    }
)


def _leftmost_identifier(node: exp.Dot) -> str:
    """取 Dot 链最左端的标识符名（`a.b.c()` → `a`），小写。"""
    cur: exp.Expression = node
    while isinstance(cur, exp.Dot):
        cur = cur.this
    if isinstance(cur, exp.Identifier):
        return (cur.name or "").lower()
    return ""


class SQLValidator:
    """sqlglot AST 静态检查。无副作用、无 LLM、可纯单元测试。

    ``dialect`` controls the sqlglot parser dialect. Defaults to postgres to
    preserve backward compat; set to ``"sqlite"`` for BIRD-style benchmarks.
    """

    def __init__(self, dialect: str = "postgres"):
        self.dialect = dialect

    @observe_local(name="sql_validation")
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
                if isinstance(node, exp.Dot):
                    qualifier = _leftmost_identifier(node)
                    if qualifier in _FORBIDDEN_QUALIFIERS:
                        return ValidationResult(
                            ok=False,
                            error=f"包含禁止函数包：{qualifier}",
                        )
                if isinstance(node, exp.Func):
                    name = (node.name or node.sql_name() or "").lower()
                    if name in _FORBIDDEN_FUNCS:
                        return ValidationResult(
                            ok=False,
                            error=f"包含禁止函数：{name}",
                        )

        return ValidationResult(ok=True, error=None)

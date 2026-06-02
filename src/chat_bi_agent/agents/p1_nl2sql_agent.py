"""P1 NL2SQL Agent：编排 SchemaLinker → SQLGenerator → SQLExecutor。

通过 @observe 装饰让全过程在 Langfuse 形成一个完整 trace。
"""

import time
from dataclasses import dataclass

from langfuse import observe

from chat_bi_agent.agents.schema_linker import SchemaLinker
from chat_bi_agent.agents.sql_executor import SQLExecutor
from chat_bi_agent.agents.sql_generator import SQLGenerator
from chat_bi_agent.schema.loader import SchemaLoader


@dataclass
class P1AgentResult:
    question_id: str
    sql: str | None
    rows: list[dict] | None
    execution_error: str | None
    schema_link_top_k: list[str]
    thought: str
    attempts: int
    total_latency_ms: int


class P1NL2SQLAgent:
    """单例式，构造时一次性加载 schema + 构建 embedding 索引。"""

    def __init__(self, top_k: int = 4):
        self.loader = SchemaLoader()
        self.loader.load()
        self.loader.build_index()
        self.schema_linker = SchemaLinker(loader=self.loader, top_k=top_k)
        self.sql_generator = SQLGenerator()
        self.sql_executor = SQLExecutor()

    @observe(name="p1_nl2sql_run")
    def run(self, question_id: str, question: str) -> P1AgentResult:
        start = time.perf_counter()

        matches = self.schema_linker.link(question)
        if not matches:
            raise RuntimeError(f"SchemaLinker 未召回任何表，question: {question!r}")
        top_names = [m.name for m in matches]

        schema_ddl = "\n\n".join(self.loader.get_ddl_text(name) for name in top_names)

        gen_result = self.sql_generator.generate(
            question=question,
            schema_ddl=schema_ddl,
            execute_fn=self.sql_executor.execute,
        )

        elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))

        return P1AgentResult(
            question_id=question_id,
            sql=gen_result.sql,
            rows=gen_result.rows,
            execution_error=gen_result.error,
            schema_link_top_k=top_names,
            thought=gen_result.thought,
            attempts=gen_result.attempts,
            total_latency_ms=elapsed_ms,
        )

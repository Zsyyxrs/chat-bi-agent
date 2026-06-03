"""ReportWriter: question + plan + facts + insights → final natural-language report."""

import json
from dataclasses import asdict

from langfuse import observe

from chat_bi_agent.agents.p2.prompts.report_writer_system import (
    REPORT_WRITER_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p2.types import Fact, Insight, P2Plan
from chat_bi_agent.llm import qwen_client


class ReportWriter:
    """Produce the final natural-language report (the evaluator's agent_response)."""

    @observe(name="p2_report_writer")
    def write(
        self,
        question: str,
        plan: P2Plan,
        facts: list[Fact],
        insights: list[Insight],
    ) -> str:
        user_prompt = self._build_user_prompt(question, plan, facts, insights)
        chat_result = qwen_client.chat(
            system_prompt=REPORT_WRITER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return chat_result.content.strip()

    def _build_user_prompt(
        self,
        question: str,
        plan: P2Plan,
        facts: list[Fact],
        insights: list[Insight],
    ) -> str:
        plan_summary = "\n".join(
            f"- {s.id} ({s.rationale}): {s.question}" for s in plan.steps
        )
        facts_json = json.dumps(
            [asdict(f) for f in facts], ensure_ascii=False, indent=2, default=str,
        )
        insights_lines = "\n".join(
            f"- [{ins.confidence}] {ins.statement}" for ins in insights
        )
        return (
            f"用户原始问题：{question}\n\n"
            f"计划步骤（按执行顺序）：\n{plan_summary}\n\n"
            f"已抽取的 Facts：\n```json\n{facts_json}\n```\n\n"
            f"综合 Insights（必须在报告中复述其中的数值与关键词）：\n{insights_lines}\n\n"
            f"请按 system prompt 的三段结构撰写报告。"
        )

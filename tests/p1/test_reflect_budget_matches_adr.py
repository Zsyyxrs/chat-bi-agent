"""守门：Reflect 预算必须与 ADR-006「单次重试」一致。

2026-08-15 排查发现的静默偏离：ADR-006 的 Decision 是「Reflector 只做 1 次重试」，
Alternatives 里明确否决了「多次重试（e.g. 3 次）」，但代码里 MAX_ATTEMPTS 长期是 3，
配合 range(1, MAX_ATTEMPTS + 1) 实际跑 1 初次 + 2 次重试——正是被否决的那个方案。

翻全部 results/ 产物：attempts=3 出现 27 次、成功 0 次；attempts=2 出现 23 次、
成功 13 次。第二次重试一次都没救回来过，只是白打 LLM 调用。

偏离方式是静默的：多跑一次不报错，只是慢一点、贵一点，分数还一模一样，
所以没有任何信号会提示「实现和决策对不上」。这里钉死数值当守门。
"""

from chat_bi_agent.agents.p1.nl2sql_agent import MAX_ATTEMPTS
from chat_bi_agent.agents.p1.reflector import Reflector

# 1 次初始生成 + 1 次重试。改这个数字前请先更新 ADR-006 并给出实测依据。
EXPECTED_TOTAL_ATTEMPTS = 2


def test_agent_reflect_budget_matches_adr_006():
    assert MAX_ATTEMPTS == EXPECTED_TOTAL_ATTEMPTS, (
        f"MAX_ATTEMPTS={MAX_ATTEMPTS}，但 ADR-006 决定的是「1 次重试」"
        f"（即总共 {EXPECTED_TOTAL_ATTEMPTS} 次尝试）。若确实要改预算，"
        f"先更新 ADR-006 的 Decision 并附实测收益，再改这里。"
    )


def test_reflector_default_matches_agent_budget():
    """Reflector 自己的默认值也要一致——否则没显式传参的调用方会拿到另一套预算。"""
    assert Reflector().max_attempts == EXPECTED_TOTAL_ATTEMPTS

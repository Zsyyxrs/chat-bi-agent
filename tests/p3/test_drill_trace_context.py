"""并行 drill 的上下文传播守门。

2026-09-01 实测：`run_drill_down` 用 ThreadPoolExecutor 并行跑 drill，而
ThreadPoolExecutor 默认**不把 contextvars 传给 worker 线程**。Langfuse v3 的
trace 上下文正是靠 contextvars（OTel context）传的，所以每个 drill 的
`p1_nl2sql_run` 都变成了独立 root trace，P3 的 trace 树一直是断的——
而 ADR-002 把「需要细粒度 tracing，便于 debug P3 单题失败」列为选型理由之一。

这里不断言 Langfuse trace_id：测试环境不一定配了 key，`get_current_trace_id()`
两边都返回 None 会让断言**空过**（"有测试、还是绿的"）。改为直接断言底层机制
——contextvar 本身是否跨线程继承。trace 上下文就是靠它传的，测它更稳也更准。

Barrier 是关键：不强制并发重叠，`ctx.run()` 共用同一个 Context 的写法能骗过测试
（快任务不会真正重叠，不触发 "context is already entered"）。
"""

import threading
from contextvars import ContextVar

import pytest

from chat_bi_agent.agents.p3.drill_executor import run_drill_down
from chat_bi_agent.agents.p3.types import DrillRequest
from tests.p3.conftest import FakeP1Agent

_probe_var: ContextVar[str] = ContextVar("_probe_var")

_REQUESTS = [
    DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解"),
    DrillRequest(dimension="customer_tier", nl_question="按 customer_tier 拆解"),
]


class _CtxCapturingP1Agent(FakeP1Agent):
    """在 worker 线程里读 contextvar，并用 Barrier 强制两个 drill 真正重叠。"""

    def __init__(self, barrier: threading.Barrier):
        super().__init__()
        self.barrier = barrier
        self.seen: dict[str, str | None] = {}

    def run(self, question_id: str, question: str):
        # 不重叠就在这里超时炸掉——沉默地串行执行会让本测试失去意义
        self.barrier.wait()
        self.seen[question_id] = _probe_var.get(None)
        return super().run(question_id, question)


def test_parallel_drills_inherit_parent_context():
    """并行 drill 必须继承父线程的 contextvar（= Langfuse trace 上下文的载体）。"""
    _probe_var.set("parent-value")
    agent = _CtxCapturingP1Agent(threading.Barrier(len(_REQUESTS), timeout=10))

    run_drill_down(question_id="qid", requests=_REQUESTS, p1_agent=agent)

    assert agent.seen == {
        "qid__drill_0": "parent-value",
        "qid__drill_1": "parent-value",
    }


def test_parallel_drills_do_not_share_one_context_object():
    """每个任务必须拿到独立的 Context 拷贝。

    共用一份 Context 的写法在快任务下能通过上面那条测试，但真实 drill 是
    30-60s 的 LLM 往返、必然重叠，届时会抛
    `RuntimeError: cannot enter context: ... is already entered`。
    Barrier 强制重叠把这个潜伏 bug 变成确定性失败。
    """
    _probe_var.set("parent-value")
    agent = _CtxCapturingP1Agent(threading.Barrier(len(_REQUESTS), timeout=10))

    try:
        run_drill_down(question_id="qid", requests=_REQUESTS, p1_agent=agent)
    except RuntimeError as exc:  # pragma: no cover - 仅在实现回退时触发
        if "already entered" in str(exc):
            pytest.fail(f"并行 drill 共用了同一个 Context 对象: {exc}")
        raise

    assert len(agent.seen) == len(_REQUESTS)

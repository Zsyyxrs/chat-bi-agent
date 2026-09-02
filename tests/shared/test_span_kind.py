"""每个 span 必须声明自己是「本地确定性操作」还是「模型推理」。

2026-09-02：读 SQLBot 时发现它的 `chat_log` 有一个 `local_operation` 布尔量
（backend/apps/chat/curd/chat.py:869 `start_log(..., local_operation=True)`），
用来区分「模型慢」和「检索/执行慢」。我们的 Langfuse span 没有这个维度——
一条 trace 里 sql_generation / sql_execution 并排躺着，看总耗时分不出
该优化 prompt 还是该优化 SQL。

这里锁三件事：
1. `observe_local` / `observe_llm` 真的把 local_operation 写到当前 span 上；
2. 打标**不能改写 observation type**——Langfuse SDK 的 update_current_span
   会把 generation/embedding 降级成 span，打掉 token/cost 归因；
3. src 下不再有裸 `@observe(`——否则新增路径会悄悄漏掉这个维度
   （同 catalog 静态门禁的思路：「新增路径忘了接闸」必须是 CI 失败）。
"""

import re
from pathlib import Path
from unittest.mock import patch

import chat_bi_agent
from chat_bi_agent.obs.span_kind import (
    LOCAL_OPERATION_ATTR,
    _stamp,
    observe_llm,
    observe_local,
)

_TYPE_ATTR = "langfuse.observation.type"


def _run_under_otel_span(fn, observation_type: str = "span"):
    """在一个带 observation.type 的真实 OTel span 里跑 fn，返回导出的 span。"""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    with tracer.start_as_current_span("probe") as span:
        span.set_attribute(_TYPE_ATTR, observation_type)
        fn()
    return exporter.get_finished_spans()[0]


# ── _stamp：真实 OTel span 上的行为 ──────────────────────────────────


def test_stamp_真为真_假为假():
    assert _run_under_otel_span(lambda: _stamp(True)).attributes[LOCAL_OPERATION_ATTR] is True
    assert _run_under_otel_span(lambda: _stamp(False)).attributes[LOCAL_OPERATION_ATTR] is False


def test_stamp_在_otel_异常时静默降级():
    """打标失败不能阻塞主流程——和 _tag_step_span 同一约定。"""
    with patch(
        "chat_bi_agent.obs.span_kind.otel_trace.get_current_span",
        side_effect=RuntimeError("otel down"),
    ):
        _stamp(True)  # 不抛即通过


def test_stamp_在_span_未采样时跳过():
    with patch(
        "chat_bi_agent.obs.span_kind.otel_trace.get_current_span",
        return_value=None,
    ):
        _stamp(True)


# ── 装饰器接线 ──────────────────────────────────────────────────────
# @observe 建的是 Langfuse 自己 tracer provider 上的子 span，测试侧的
# in-memory exporter 抓不到；这里只锁「装饰器把哪个值交给 _stamp」，
# _stamp 落到真实 span 上的行为由上面三条覆盖。


def _stamped_value(fn) -> bool:
    seen: list[bool] = []
    with patch("chat_bi_agent.obs.span_kind._stamp", side_effect=seen.append):
        fn()
    assert len(seen) == 1, f"应恰好打标一次，实际 {len(seen)} 次"
    return seen[0]


def test_observe_local_交给_stamp_的是真():
    @observe_local(name="fake_local")
    def f():
        return 42

    assert _stamped_value(f) is True


def test_observe_llm_交给_stamp_的是假():
    @observe_llm(name="fake_llm")
    def f():
        return 42

    assert _stamped_value(f) is False


def test_装饰器不改变返回值与异常():
    @observe_local(name="fake")
    def ok():
        return "v"

    @observe_local(name="fake")
    def boom():
        raise ValueError("x")

    assert ok() == "v"
    try:
        boom()
    except ValueError as e:
        assert str(e) == "x"
    else:
        raise AssertionError("异常被吞了")


# ── 打标不能破坏 generation 语义 ──────────────────────────────────────
# Langfuse SDK 3.15 的 `update_current_span` 会构造 LangfuseSpan(as_type="span")，
# 而 LangfuseObservationWrapper.__init__ 无条件执行
#   otel_span.set_attribute(OBSERVATION_TYPE, as_type)
# → 在 generation observation 里调它会把类型降级成 span，
#   qwen_chat / qwen_embed 的 token 与 cost 归因（51ce65b 那次的成果）当场失效。
# 所以打标必须直接写 OTel 属性，不能借道 SDK 的 update_current_* 系列。


def test_打标不把_generation_降级成_span():
    span = _run_under_otel_span(lambda: _stamp(False), "generation")
    assert span.attributes[_TYPE_ATTR] == "generation", "generation 被降级——token/cost 归因会丢"
    assert span.attributes[LOCAL_OPERATION_ATTR] is False


def test_打标不把_embedding_降级():
    span = _run_under_otel_span(lambda: _stamp(False), "embedding")
    assert span.attributes[_TYPE_ATTR] == "embedding"


# ── 守门 ────────────────────────────────────────────────────────────


def test_src_下没有未分类的裸_observe():
    """新增 span 必须走 observe_local / observe_llm。

    裸 `@observe(` 会得到一个没有 local_operation 维度的 span，
    在 Langfuse 里按该维度筛选时会静默消失。
    """
    src = Path(chat_bi_agent.__file__).parent
    bare = re.compile(r"^\s*@observe\(")
    offenders: list[str] = []
    for py in sorted(src.rglob("*.py")):
        if py.name == "span_kind.py":  # 定义处自己要用 langfuse 的 observe
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if bare.match(line):
                offenders.append(f"{py.relative_to(src)}:{i}")
    assert not offenders, (
        "以下 span 未声明 local_operation，请改用 observe_local / observe_llm：\n  "
        + "\n  ".join(offenders)
    )

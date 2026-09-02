"""给每个 Langfuse span 打上「本地确定性操作 vs 模型推理」的维度。

判据：``local_operation=True`` ⟺ **该 span 的耗时里不含任何模型推理**
（chat 与 embedding 都算）。按这个定义：

- ``sql_validation`` / ``sql_execution`` → True，纯 AST 与 DB 往返
- ``schema_linking`` → False，它内部调 ``qwen_client.embed``
- ``p1_eval_batch`` 等编排根 span → False，覆盖了下游全部模型调用

没有这个维度时，一条 trace 里 `sql_generation`（模型）和 `sql_execution`
（本地）并排躺着，看总耗时分不出该优化 prompt 还是该优化 SQL。
按 ``local_operation=True`` 过滤即可得到纯基础设施耗时。

外部参考：SQLBot 的 `chat_log.local_operation`
（backend/apps/chat/curd/chat.py:869），同一动机、同一字段。

用法：把裸 ``@observe(...)`` 换成 ``@observe_local(...)`` / ``@observe_llm(...)``。
``tests/shared/test_span_kind.py`` 守门，src 下不允许再出现裸 ``@observe(``。

实现注记——为什么不用 SDK 的 ``update_current_span``：
Langfuse 3.15 的 ``update_current_span`` 会构造 ``LangfuseSpan(as_type="span")``，
而 ``LangfuseObservationWrapper.__init__`` 无条件执行
``otel_span.set_attribute(OBSERVATION_TYPE, as_type)``——在 generation/embedding
observation 里调它会把类型**降级成 span**，token 与 cost 归因当场失效。
``update_current_generation`` 同理会把 embedding 改写成 generation。
所以这里直接写 OTel 属性，只加 metadata，不碰 observation type。
"""

from collections.abc import Callable
from functools import wraps
from typing import Any

from langfuse import observe
from opentelemetry import trace as otel_trace

try:  # 私有路径，跨 SDK 版本兜底
    from langfuse._client.attributes import LangfuseOtelSpanAttributes

    _METADATA_PREFIX = LangfuseOtelSpanAttributes.OBSERVATION_METADATA
except Exception:  # pragma: no cover
    _METADATA_PREFIX = "langfuse.observation.metadata"

#: Langfuse 把 dict metadata 拍平成 `<prefix>.<key>`，bool 原样落属性。
LOCAL_OPERATION_ATTR = f"{_METADATA_PREFIX}.local_operation"


def _stamp(local: bool) -> None:
    """写当前 OTel span 的 local_operation 属性。

    Langfuse 未配置 / span 未在采样时静默跳过，绝不阻塞主流程。
    """
    try:
        span = otel_trace.get_current_span()
        if span is None or not span.is_recording():
            return
        span.set_attribute(LOCAL_OPERATION_ATTR, local)
    except Exception:
        pass


def _observer(local: bool) -> Callable[..., Callable[[Callable], Callable]]:
    def decorator(*, name: str, **observe_kwargs: Any) -> Callable[[Callable], Callable]:
        def wrap(fn: Callable) -> Callable:
            @wraps(fn)
            def inner(*args: Any, **kwargs: Any) -> Any:
                # span 由外层 observe 建好后才执行到这里，此时打标才挂得上
                _stamp(local)
                return fn(*args, **kwargs)

            return observe(name=name, **observe_kwargs)(inner)

        return wrap

    return decorator


#: 本地确定性操作：解析、校验、SQL 执行、渲染。
observe_local = _observer(True)

#: 含模型推理的环节（chat / embedding，以及覆盖它们的编排 span）。
observe_llm = _observer(False)

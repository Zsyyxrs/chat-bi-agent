"""Phase 1 验证：通义 chat + embedding 都能调通，Langfuse 能收到 trace。

运行：
    python scripts/hello_qwen.py

期望：
    - 控制台打印一句中文回答和 embedding 的维度
    - Langfuse UI (http://localhost:3000) 出现 1 条 trace
"""

from dotenv import load_dotenv

load_dotenv()

from langfuse import observe

from chat_bi_agent.llm import qwen_client  # noqa: E402
from chat_bi_agent.llm.langfuse_setup import flush, get_client


@observe(name="hello_qwen_chat")
def call_chat() -> str:
    result = qwen_client.chat(
        system_prompt="你是一个银行业务助手。",
        user_prompt="用一句话解释什么是 AUM。",
    )
    return result.content


@observe(name="hello_qwen_embed")
def call_embed() -> int:
    vectors = qwen_client.embed(["这是一段测试文本"])
    return len(vectors[0])


@observe(name="hello_qwen_main")
def main() -> None:
    # 触发 Langfuse client 初始化
    get_client()

    print("=== 通义 chat ===")
    answer = call_chat()
    print(answer)
    print()

    print("=== 通义 embedding ===")
    dim = call_embed()
    print(f"embedding 维度: {dim}")


if __name__ == "__main__":
    try:
        main()
    finally:
        flush()
        print("\n✅ 完成。请打开 http://localhost:3000 检查 trace 是否出现。")

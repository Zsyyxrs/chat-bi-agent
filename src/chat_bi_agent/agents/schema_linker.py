"""向量检索 Schema Linker：基于 cosine 相似度从 SchemaLoader 取 Top-K 表。"""

import math
from dataclasses import dataclass

from langfuse import observe

from chat_bi_agent.llm import qwen_client
from chat_bi_agent.schema.loader import SchemaLoader


@dataclass
class TableMatch:
    name: str
    score: float
    domain: str


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SchemaLinker:
    def __init__(self, loader: SchemaLoader, top_k: int = 4):
        self.loader = loader
        self.top_k = top_k
        # 确保 loader 已 build_index
        if not loader.docs or loader.docs[0].embedding is None:
            raise RuntimeError("SchemaLoader 需要先 load() + build_index()")

    @observe(name="schema_linking")
    def link(self, question: str) -> list[TableMatch]:
        q_vec = qwen_client.embed([question])[0]
        scored = [
            TableMatch(name=d.name, score=_cosine(q_vec, d.embedding), domain=d.domain)
            for d in self.loader.docs
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[: self.top_k]

"""Q-SQL few-shot 检索：从 JSONL 池按 cosine 相似度取 Top-K 相似历史问答对。

设计要点：
- Pool 是内存态 list[QAExample]，按 example_id 去重（sha1(question||sql)[:12]）
- Embedding 在 bootstrap 期一次性算好并落盘，检索期只对 query 做一次 embed
- Retriever 过滤：dialect 严格匹配、tag allow/block、exclude ids/texts、min_similarity 阈值
- BIRD 场景必须传 exclude_question_texts 防止 dev 集自泄题
- 写入路径原子：save() 全量重写到 tmp 再 rename
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class QAExample:
    example_id: str
    question: str
    sql: str
    dialect: str
    source: str
    tags: list[str] = field(default_factory=list)
    ts: str = ""
    embedding: list[float] | None = None


def compute_example_id(question: str, sql: str) -> str:
    """去重键：question + sql 决定，只要问题或 SQL 变了就是新条目。"""
    h = hashlib.sha1((question + "|||" + sql).encode("utf-8"))
    return h.hexdigest()[:12]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class ExamplePool:
    """JSONL 存储的 Q-SQL 池，按 example_id 去重。"""

    def __init__(self, examples: list[QAExample]):
        self._examples: list[QAExample] = []
        self._ids: set[str] = set()
        for ex in examples:
            if ex.example_id not in self._ids:
                self._examples.append(ex)
                self._ids.add(ex.example_id)

    @property
    def examples(self) -> list[QAExample]:
        return list(self._examples)

    def __len__(self) -> int:
        return len(self._examples)

    @classmethod
    def load(cls, path: Path) -> ExamplePool:
        if not path.exists():
            return cls([])
        examples: list[QAExample] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            examples.append(QAExample(**data))
        return cls(examples)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：tmp + rename，防止半写状态被下次 load
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".pool_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for ex in self._examples:
                    f.write(json.dumps(asdict(ex), ensure_ascii=False) + "\n")
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def add(self, example: QAExample) -> bool:
        """Returns True if added, False if example_id already present."""
        if example.example_id in self._ids:
            return False
        self._examples.append(example)
        self._ids.add(example.example_id)
        return True


class ExampleRetriever:
    def __init__(
        self,
        pool: ExamplePool,
        dialect: str,
        embed_fn: Callable[[list[str]], list[list[float]]],
        min_similarity: float = 0.75,
        max_k: int = 3,
        allowed_tags: list[str] | None = None,
        blocked_tags: list[str] | None = None,
        leak_guard_similarity: float | None = None,
    ):
        self.pool = pool
        self.dialect = dialect
        self.embed_fn = embed_fn
        self.min_similarity = min_similarity
        self.max_k = max_k
        self.allowed_tags = set(allowed_tags) if allowed_tags else None
        self.blocked_tags = set(blocked_tags) if blocked_tags else set()
        # 评测专用：剔除与当前问题过于相似的池内条目，防近似泄题。
        # 默认关——生产环境里"历史相似问题的 Q-SQL"正是 few-shot 的价值所在。
        self.leak_guard_similarity = leak_guard_similarity

    def _passes_static_filters(self, ex: QAExample) -> bool:
        if ex.dialect != self.dialect:
            return False
        tag_set = set(ex.tags)
        if self.blocked_tags & tag_set:
            return False
        if self.allowed_tags is not None and not (self.allowed_tags & tag_set):
            return False
        if ex.embedding is None:
            return False
        return True

    def retrieve(
        self,
        question: str,
        k: int | None = None,
        exclude_example_ids: set[str] | None = None,
        exclude_question_texts: set[str] | None = None,
    ) -> list[tuple[QAExample, float]]:
        candidates = [ex for ex in self.pool.examples if self._passes_static_filters(ex)]
        if exclude_example_ids:
            candidates = [ex for ex in candidates if ex.example_id not in exclude_example_ids]
        if exclude_question_texts:
            excl = {q.strip() for q in exclude_question_texts}
            candidates = [ex for ex in candidates if ex.question.strip() not in excl]
        if not candidates:
            return []

        q_vec = self.embed_fn([question])[0]
        scored: list[tuple[QAExample, float]] = []
        for ex in candidates:
            score = _cosine(q_vec, ex.embedding or [])
            if score >= self.min_similarity:
                scored.append((ex, score))
        if self.leak_guard_similarity is not None:
            # 与当前问题相似度过高 = 近似副本，喂回去等于开卷考试
            scored = [t for t in scored if t[1] < self.leak_guard_similarity]
        scored.sort(key=lambda t: t[1], reverse=True)
        limit = k if k is not None else self.max_k
        return scored[:limit]

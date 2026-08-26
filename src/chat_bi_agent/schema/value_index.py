"""值检索：把用户问题里的自然表述映射回库中实际取值及其所属列。

背景（CHESS 论文 Figure 1 第一类挑战）：用户说的词和库里存的值经常对不上，
而且「该去哪一列找这个值」本身就不显然。典型例子——用户问「上海分行」，
dim_branch 里并没有 branch_name='上海分行' 的行，BR_CITY_0006 叫「浦东分行」，
只有 province='上海'。不把这层映射喂给模型，它只能猜列。

与 CHESS 的差异：CHESS 用 LSH + embedding 分层检索，是为百万行唯一值准备的；
本项目被索引的维度取值只有几百条，直接扫一遍即可，不引入额外的 LLM 调用与索引结构。
"""

import json
from dataclasses import dataclass
from pathlib import Path

# 允许 1 处编辑的最短值长度。短值放宽会大量误命中——「上海」允许 1 编辑就会被
# 「上班」「北海」勾出来，而 4 字以上的机构名（浦东分行/浦东支行）差一个字仍是同一个实体。
_FUZZY_MIN_LEN = 4

# 离线快照默认落点。与 schema_docs.yaml 同目录，随包一起分发。
DEFAULT_CACHE_PATH = Path(__file__).parent / "value_index.json"


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_contains(question: str, value: str) -> bool:
    """问题中是否存在与 value 等长、且编辑距离 ≤1 的片段。"""
    width = len(value)
    for start in range(len(question) - width + 1):
        if _levenshtein(question[start : start + width], value) <= 1:
            return True
    return False


@dataclass(frozen=True)
class ValueHit:
    """问题中命中的一个库内取值。"""

    table: str
    column: str
    value: str


class ValueIndex:
    """{"表名.列名": [取值, ...]} → 按问题文本反查命中的取值。"""

    def __init__(self, values: dict[str, list[str]]):
        self._columns: list[tuple[str, str, list[str]]] = []
        for qualified, vals in values.items():
            table, _, column = qualified.partition(".")
            self._columns.append((table, column, vals))

    @property
    def columns(self) -> list[str]:
        """被索引的列，形如 ["dim_branch.province", ...]。评测产物记录用。"""
        return [f"{table}.{column}" for table, column, _ in self._columns]

    @property
    def n_values(self) -> int:
        return sum(len(vals) for _, _, vals in self._columns)

    @classmethod
    def from_cache(cls, path: Path) -> "ValueIndex":
        """读离线快照。缺文件时退化成空索引——值检索是增强，不是 P1 的硬依赖。"""
        path = Path(path)
        if not path.exists():
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def lookup(self, question: str) -> list[ValueHit]:
        """返回问题中命中的库内取值。

        模糊匹配只作为**每列的兜底**：该列一旦有精确命中就不再启用。中文里差一个字
        通常是另一个实体（杭州分行/广州分行/苏州分行两两编辑距离都是 1，定期存款与
        活期存款亦然），无条件放开会把同列的兄弟实体成片拖进来当示例，反而误导模型。
        """
        hits: list[ValueHit] = []
        for table, column, vals in self._columns:
            exact = [v for v in vals if v in question]
            if exact:
                hits.extend(ValueHit(table, column, v) for v in exact)
                continue
            hits.extend(
                ValueHit(table, column, v)
                for v in vals
                if len(v) >= _FUZZY_MIN_LEN and _fuzzy_contains(question, v)
            )
        return hits

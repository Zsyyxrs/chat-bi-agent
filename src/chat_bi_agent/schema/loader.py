"""加载 schema_docs.yaml + 构建内存 embedding 索引。

SchemaLoader 是一次性的：load() 读 YAML，build_index() 调通义 embedding。
之后整个进程通过 loader.docs 访问全部 TableDoc。

注：schema_docs.yaml 故意省略了 dim_* 表的 create_time / update_time 列
（ETL 系统字段，BI 查询不会用到），如需要查询请直接查 information_schema。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from chat_bi_agent.llm import qwen_client

DEFAULT_YAML_PATH = Path(__file__).parent / "schema_docs.yaml"


@dataclass
class TableDoc:
    """单张表的元数据 + 向量。"""

    name: str
    type: str  # dimension / fact
    domain: str
    description: str
    primary_key: str
    columns: list[dict]
    embed_text: str
    foreign_keys: list[str] = field(default_factory=list)
    embedding: list[float] | None = None


class SchemaLoader:
    def __init__(self, yaml_path: Path | None = None):
        self.yaml_path = yaml_path or DEFAULT_YAML_PATH
        self.docs: list[TableDoc] = []

    def load(self) -> None:
        with open(self.yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.docs = [
            TableDoc(
                name=t["name"],
                type=t["type"],
                domain=t["domain"],
                description=t["description"],
                primary_key=t["primary_key"],
                columns=t["columns"],
                embed_text=t["embed_text"].strip(),
                foreign_keys=t.get("foreign_keys", []),
            )
            for t in data["tables"]
        ]

    def build_index(self) -> None:
        """对所有表的 embed_text 批量做 embedding，结果写回 docs."""
        if not self.docs:
            raise RuntimeError("先调用 load()")
        texts = [d.embed_text for d in self.docs]
        vectors = qwen_client.embed(texts)
        for doc, vec in zip(self.docs, vectors):
            doc.embedding = vec

    def get_doc(self, table_name: str) -> TableDoc:
        for d in self.docs:
            if d.name == table_name:
                return d
        raise KeyError(f"未找到表 {table_name}")

    def get_ddl_text(self, table_name: str, value_hits: list | None = None) -> str:
        """把表元数据格式化成给 LLM 看的 DDL 风格文本。

        `value_hits` 是 ValueIndex 对本次问题检索到的库内实际取值（ValueHit 列表，
        可含其他表的命中，本方法自行过滤）。命中的取值以 `-- examples:` 挂在对应列后，
        用途是告诉模型「这个词在库里长这样、存在哪一列」——例如问题里的「上海」实际
        落在 dim_branch.province 而非 city。只注入命中项，不灌整列枚举。
        """
        d = self.get_doc(table_name)
        examples: dict[str, list[str]] = {}
        for hit in value_hits or []:
            if hit.table == table_name:
                examples.setdefault(hit.column, []).append(hit.value)

        lines = [f"-- {d.description}（domain: {d.domain}）"]
        lines.append(f"CREATE TABLE {d.name} (")
        col_lines = []
        for c in d.columns:
            line = f"    {c['name']} {c['type']}  -- {c['description']}"
            hit_values = examples.get(c["name"])
            if hit_values:
                line += "  -- examples: " + ", ".join(hit_values)
            col_lines.append(line)
        lines.append(",\n".join(col_lines))
        lines.append(f"    PRIMARY KEY ({d.primary_key})")
        lines.append(");")
        if d.foreign_keys:
            for fk in d.foreign_keys:
                lines.append(f"-- FK: {fk}")
        return "\n".join(lines)

"""在 **AST** 上断言，而不是在源码文本上 grep。

2026-08-18 实测的教训：`assert "字段名" in src` 这类守门是假的。把 `run_p2_eval` 里的真实
payload 字段行删掉、只留上方注释，3 个守门测试**全绿**——因为字段名在注释里也出现。
那是「声称在守门、实际没守」，比原缺陷更隐蔽：有测试、还是绿的，反而让人以为已经防住了。

优先顺序：
  1. 能直接调函数断言返回值的，就调函数（最强，见 `run_p2_eval.build_payload`）；
  2. 调不动的（如 streamlit 页面里的构造点），退到 AST——它至少能区分代码与注释/字符串；
  3. 文本 grep 只用于「这个字面量不该出现」这类反向断言。
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def call_keywords(source_path: Path | str) -> list[tuple[str, str]]:
    """列出文件里所有函数调用的关键字实参，返回 [(关键字名, 实参源码), ...]。

    只看真实的 `ast.Call` 节点，注释与字符串字面量不会命中。
    """
    path = Path(source_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg is not None:
                    out.append((kw.arg, ast.unparse(kw.value)))
    return out


def has_call_keyword(source_path: Path | str, name: str, value: str) -> bool:
    """文件里是否存在「以 name=value 形式传参」的真实调用。"""
    return (name, value) in call_keywords(source_path)


def dict_string_keys(source_path: Path | str) -> set[str]:
    """文件里所有字典字面量用到的字符串键——用于断言 payload 字段确实被构造。"""
    path = Path(source_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys

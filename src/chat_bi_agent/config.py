"""可调运行参数加载器。

读取仓库根目录的 config/local.yaml；文件缺失或键缺失时落回 _DEFAULTS。
新增可调字段：在 _DEFAULTS 添加 → 同步 local.example.yaml → 在调用方读取常量。
"""

from pathlib import Path
from typing import Any

import yaml

_DEFAULTS: dict[str, dict[str, Any]] = {
    "llm": {
        "chat_model": "qwen3.6-plus-2026-04-02",
        "embed_model": "text-embedding-v4",
        "embed_dim": 1024,
        "default_temperature": 0.1,
    },
    "retrieval": {
        "top_k_planner": 8,
        "top_k_nl2sql": 4,
    },
    "db": {
        "statement_timeout_ms": 10_000,
    },
}


def _repo_root() -> Path:
    # 本文件位于 src/chat_bi_agent/config.py，仓库根 = parents[2]
    return Path(__file__).resolve().parents[2]


def _load() -> dict[str, dict[str, Any]]:
    path = _repo_root() / "config" / "local.yaml"
    if not path.exists():
        return {section: dict(values) for section, values in _DEFAULTS.items()}
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    merged: dict[str, dict[str, Any]] = {}
    for section, defaults in _DEFAULTS.items():
        section_loaded = loaded.get(section) or {}
        merged[section] = {**defaults, **section_loaded}
    return merged


_cfg = _load()

# LLM
CHAT_MODEL: str = _cfg["llm"]["chat_model"]
EMBED_MODEL: str = _cfg["llm"]["embed_model"]
EMBED_DIM: int = int(_cfg["llm"]["embed_dim"])
DEFAULT_TEMPERATURE: float = float(_cfg["llm"]["default_temperature"])

# Retrieval
TOP_K_PLANNER: int = int(_cfg["retrieval"]["top_k_planner"])
TOP_K_NL2SQL: int = int(_cfg["retrieval"]["top_k_nl2sql"])

# DB
PG_STATEMENT_TIMEOUT_MS: int = int(_cfg["db"]["statement_timeout_ms"])

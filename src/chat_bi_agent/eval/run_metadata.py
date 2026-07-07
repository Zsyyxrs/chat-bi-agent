"""统一的 run metadata 构造器：runner 落 result JSON 时统一注入。

目的：解决"跨 run 归因错误"这类方法学 bug——两次 run 只有报出的 EX 不一样时，
不用再手动 grep model / 日期猜差异；打开两份 result 直接比 metadata 字段就知道
是不是同一个代码/配置/pool 版本。

字段：
- commit_hash / commit_hash_full: 当前 HEAD（短 12 位 + 全）
- commit_dirty: `git status --porcelain` 非空 → 说明有未提交改动，run 不可复现
- config_hash: `md5(config/local.yaml)`；没有就 None
- python_version / host: 排除环境差异
- run_ts_utc: run 启动时间戳
- extra_paths: 可选 dict，允许 runner 追加相关文件 hash（例如 example pool、
  BIRD dev.json）

用法：
    from chat_bi_agent.eval.run_metadata import build_run_metadata
    meta = build_run_metadata(extra_paths={"pool_hash": Path("data/example_pool_bird.jsonl")})
    result_doc["run_metadata"] = meta
"""

from __future__ import annotations

import datetime as dt
import hashlib
import platform
import socket
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "local.yaml"


def md5_file(path: Path) -> str | None:
    """Compute md5 of a file. Returns None if file doesn't exist."""
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head() -> tuple[str, bool]:
    """Return (commit_full_sha, is_dirty). Falls back to ('unknown', False) if git missing."""
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown", False
    try:
        porcelain = subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        porcelain = ""
    return sha, bool(porcelain.strip())


def build_run_metadata(
    extra_paths: dict[str, Path] | None = None,
) -> dict:
    sha, dirty = _git_head()
    meta: dict = {
        "commit_hash": sha[:12] if sha != "unknown" else "unknown",
        "commit_hash_full": sha,
        "commit_dirty": dirty,
        "config_hash": md5_file(_DEFAULT_CONFIG),
        "python_version": platform.python_version(),
        "host": socket.gethostname(),
        "run_ts_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    if extra_paths:
        for name, p in extra_paths.items():
            meta[name] = md5_file(Path(p))
    return meta

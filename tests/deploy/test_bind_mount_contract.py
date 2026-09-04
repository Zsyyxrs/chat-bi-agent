"""compose 的 bind-mount 源必须在全新 clone 里就存在。

2026-09-04 实测的坑：`app` / `seed` 挂了 `./config/local.yaml`，而这个文件在
`.gitignore` 里——全新 clone 没有它。Docker 遇到不存在的 bind 源**不会报错**，
它在宿主机建一个同名目录，于是容器里 `/app/config/local.yaml` 是个目录：

    config.py: path.exists() -> True（目录也算存在）
               path.open()   -> IsADirectoryError: [Errno 21]

容器 import 期就崩，日志里只有这一行，没人猜得到病根是「少 cp 了一个文件」。

两道闸都要在：
  1. 挂载点本身指向 clone 里必然存在的路径（本文件上半部分）；
  2. 就算路径是目录，读取方也得优雅降级而不是抛（下半部分）——
     因为 bind 源被建成目录这件事，人手动也能造出来。
"""

import subprocess

import pytest
import yaml

from tests.ast_probe import REPO_ROOT


def _host_bind_sources() -> list[tuple[str, str]]:
    """compose 里所有相对路径 bind 源，返回 [(service, host_path), ...]。"""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    out = []
    for svc, spec in compose["services"].items():
        for vol in spec.get("volumes") or []:
            if not isinstance(vol, str):
                continue
            host = vol.split(":", 1)[0]
            if host.startswith("./"):
                out.append((svc, host))
    return out


def _tracked_by_git(rel: str) -> bool:
    """全新 clone 里会不会有这个路径——目录只要含任一 tracked 文件即可。"""
    done = subprocess.run(
        ["git", "ls-files", "--", rel],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(done.stdout.strip())


def test_compose_has_bind_mounts_to_check():
    assert _host_bind_sources(), "compose 里没有相对路径 bind 源，本文件失去意义"


@pytest.mark.parametrize("service,host_path", _host_bind_sources())
def test_bind_source_exists_in_a_fresh_clone(service, host_path):
    """挂一个 gitignore 掉的路径 = 让 Docker 在别人机器上凭空造目录。"""
    rel = host_path[2:]
    assert _tracked_by_git(rel), (
        f"服务 {service} 挂载了 {host_path}，但它在全新 clone 里不存在"
        f"（git 未跟踪）。Docker 会把它建成目录，容器读到目录而不是文件。"
        f"改挂一个仓库里必然存在的目录，或别挂它。"
    )


@pytest.mark.parametrize("service,host_path", _host_bind_sources())
def test_bind_source_is_a_directory(service, host_path):
    """挂目录而不是单文件：目录进了 git 就一定存在，单文件可能被 ignore 掉。"""
    rel = host_path[2:]
    assert (REPO_ROOT / rel).is_dir(), (
        f"服务 {service} 挂的是单文件 {host_path}——改挂它所在的目录，"
        f"这样文件缺失时容器里就是「文件不存在」（可优雅降级），"
        f"而不是「文件是个目录」（抛异常）。"
    )


# ---- 就算真被建成目录，读取方也不能炸 ----


def test_config_falls_back_to_defaults_when_local_yaml_is_a_directory(tmp_path, monkeypatch):
    from chat_bi_agent import config

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.yaml").mkdir()  # Docker 造出来的那种
    monkeypatch.setattr(config, "_repo_root", lambda: tmp_path)

    loaded = config._load()
    assert loaded["llm"]["chat_model"] == config._DEFAULTS["llm"]["chat_model"]


def test_example_pool_retriever_degrades_when_pool_path_is_a_directory(tmp_path, monkeypatch):
    from streamlit_app.tabs import p1_nl2sql

    pool_dir = tmp_path / "example_pool_prod.jsonl"
    pool_dir.mkdir()
    monkeypatch.setattr(p1_nl2sql, "_PROD_POOL_PATH", pool_dir)

    assert p1_nl2sql._build_retriever_if_available() is None

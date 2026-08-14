"""守门：run_all_evals 的 baseline glob pattern 必须匹配各 runner 的真实输出名。

为什么需要这个测试——2026-08-14 查出的静默失配：run_all_evals 里 P1 的 pattern 写的是
`baseline_p2_validator_reflector_*.json`（更早的历史文件名），而 run_p1_eval 实际写的是
`baseline_p1_eval_<date>.json`。两者从来对不上，结果是**跑完 P1 却报出 2026-06-03 那份
旧 baseline 的数字，新写的结果被静默丢弃**——一键报告在结构上就不可能显示新的 P1 成绩，
「P1 6 题 1.000」因此在报告里活了两个月没人发现。

失配不报错、不告警：glob 匹配到旧文件就照常出报告，数字看起来完全正常。所以只能靠
守门主动去撞。

这里不连库、不跑 LLM——纯粹比对「runner 会写出什么名字」与「报告会去找什么名字」。
"""

import fnmatch
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.eval_diff import PHASE_PATTERNS as DIFF_PATTERNS
from scripts.run_all_evals import PHASES

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNERS_DIR = REPO_ROOT / "src" / "chat_bi_agent" / "runners"

# 同一份耦合在两个脚本里各写了一遍。首轮只修/只测了 run_all_evals，eval_diff 里
# 一模一样的失配就没被抓到——守门必须覆盖全部副本，否则等于只锁了一扇门。
SOURCES = {
    "run_all_evals": {p: cfg["pattern"] for p, cfg in PHASES.items()},
    "eval_diff": dict(DIFF_PATTERNS),
}

# runner 源码里默认输出名的 f-string，形如 f"baseline_p1_eval_{date_str}{suffix}.json"
_FSTRING = re.compile(r'f"(baseline_[a-z0-9_]*\{[^"]*)\.json"')


def _default_output_names(module: str) -> list[str]:
    """从 runner 源码里抽默认输出文件名，把占位符展开成具体值。

    读源码而不是调函数，是为了不触发 runner 的 import 副作用（load_dotenv、
    Langfuse 客户端初始化、真实 LLM 配置校验）。
    """
    src = (RUNNERS_DIR / f"{module.rsplit('.', 1)[-1]}.py").read_text(encoding="utf-8")
    today = datetime.now(UTC).date().isoformat()
    names = []
    for raw in _FSTRING.findall(src):
        # 占位符只有两类：日期，以及可选后缀（few-shot 等）。后缀两种取值都要覆盖。
        base = raw.replace("{date_str}", today)
        for suffix_value in ("", "_fewshot"):
            names.append(re.sub(r"\{[a-z_]+\}", suffix_value, base) + ".json")
    return names


@pytest.mark.parametrize(
    "source,phase",
    [(s, p) for s, pats in sorted(SOURCES.items()) for p in sorted(pats)],
)
def test_report_pattern_matches_runner_output(source, phase):
    pattern = SOURCES[source][phase]
    candidates = _default_output_names(PHASES[phase]["module"])

    assert candidates, f"没能从 {phase} 的 runner 源码里抽出默认输出名，正则可能过期了"
    assert any(fnmatch.fnmatch(name, pattern) for name in candidates), (
        f"{source}/{phase}: pattern `{pattern}` 匹配不上 runner 的默认输出名 "
        f"{candidates}。后果是跑完该 phase 却报出旧 baseline 的数字，新结果被静默丢弃。"
    )


def test_both_scripts_agree_on_patterns():
    """两个脚本对同一 phase 的 pattern 必须一致，否则报告与 diff 会看向不同文件。"""
    for phase in sorted(set(SOURCES["run_all_evals"]) & set(SOURCES["eval_diff"])):
        a, b = SOURCES["run_all_evals"][phase], SOURCES["eval_diff"][phase]
        assert a == b, f"{phase}: run_all_evals 用 `{a}`，eval_diff 用 `{b}`，两者不一致"

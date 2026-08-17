"""守门：runner 的输出文件名日期必须取自当天，不能写死。

2026-08-17 实际触发的破坏性 bug：run_p2_eval 里 `OUTPUT_DATE = "2026-06-07"` 是硬编码
字符串（P1/P3 都用 `datetime.now(UTC)`），于是**每一次 P2 跑批都会覆盖 2026-06-07
那份历史 baseline**——而且终端打印的仍是 `Wrote baseline JSON → ...2026-06-07.json`，
看起来一切正常，没有任何信号提示你刚刚销毁了唯一的对照组。

复跑 P2 时真的覆盖了，靠该文件已被 git 跟踪才恢复。若当时顺手 commit，那份 baseline
就永久丢了，而且事后无法从任何地方重建——它是两个多月前的 agent 版本产出的。

这条守门读 runner 源码，断言输出路径的日期部分来自 datetime 而非字面量。
"""

import re
from pathlib import Path

import pytest

RUNNERS_DIR = Path(__file__).resolve().parents[2] / "src" / "chat_bi_agent" / "runners"
RUNNERS = ["run_p1_eval.py", "run_p2_eval.py", "run_p3_eval.py"]

# 形如 OUTPUT_DATE = "2026-06-07" / date_str = '2026-06-07'
_HARDCODED_DATE_ASSIGN = re.compile(r"""^\s*[A-Za-z_]+\s*=\s*["']\d{4}-\d{2}-\d{2}["']""", re.M)
# 输出文件名里直接嵌字面日期，形如 f"baseline_x_2026-06-07.json"
_LITERAL_DATE_IN_NAME = re.compile(r"""["'][^"']*baseline[^"']*\d{4}-\d{2}-\d{2}[^"']*\.json["']""")


@pytest.mark.parametrize("runner", RUNNERS)
def test_runner_does_not_hardcode_output_date(runner):
    src = (RUNNERS_DIR / runner).read_text(encoding="utf-8")

    bad_assign = _HARDCODED_DATE_ASSIGN.findall(src)
    assert not bad_assign, (
        f"{runner} 把日期写死成常量 {bad_assign}。后果是每次跑批都覆盖那一天的 baseline，"
        f"且提示信息看不出异常——请改用 datetime.now(UTC)。"
    )

    bad_literal = _LITERAL_DATE_IN_NAME.findall(src)
    assert not bad_literal, (
        f"{runner} 的输出文件名里嵌了字面日期 {bad_literal}，会反复覆盖同一份产物。"
    )


@pytest.mark.parametrize("runner", RUNNERS)
def test_runner_derives_output_date_from_clock(runner):
    """正面断言：输出日期确实来自 datetime，避免上面两条正则被绕过后静默通过。"""
    src = (RUNNERS_DIR / runner).read_text(encoding="utf-8")
    assert "datetime.now(UTC)" in src, f"{runner} 未见 datetime.now(UTC)，输出日期来源可疑"

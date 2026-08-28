"""triage_example_pool.py 的纯函数单测：口径漂移检测。

存在理由：2026-08-28 那次分流里，A 档 8 条有 2 条是**跑得通但数字错**的假阳性
（漏了 `response_type = 'CONVERTED'`）。执行反馈捕不到这种错，只能靠比对
gold 与生成 SQL 的字面量。这个函数把那次的人工比对固化下来，catalog 每次
扩容重跑分流时自动告警。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "triage_example_pool.py"
_spec = importlib.util.spec_from_file_location("triage_example_pool", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

caliber_drift = _mod.caliber_drift


def test_identical_caliber_has_no_drift():
    gold = "SELECT COUNT(*) FROM t WHERE severity = 'CRITICAL'"
    gen = "SELECT COUNT(*) AS c FROM t x WHERE x.severity = 'CRITICAL'"
    assert caliber_drift(gold, gen) == []


def test_flags_filter_the_generator_dropped():
    """idx27 那一类：gold 有 CONVERTED 口径，生成的 SQL 没有——结果偏大。"""
    gold = "SELECT SUM(amt) FROM t WHERE response_type = 'CONVERTED'"
    gen = "SELECT SUM(amt) FROM t WHERE campaign_name = '七夕营销活动'"
    drift = caliber_drift(gold, gen)
    assert any("CONVERTED" in d and "gold" in d for d in drift), drift


def test_flags_filter_the_generator_invented():
    """反方向同样要报：生成的 SQL 多了一个 gold 没有的口径，结果偏小。"""
    gold = "SELECT SUM(amt) FROM t"
    gen = "SELECT SUM(amt) FROM t WHERE campaign_name = '七夕营销活动'"
    drift = caliber_drift(gold, gen)
    assert any("七夕营销活动" in d for d in drift), drift


def test_date_boundary_equivalence_is_not_drift():
    """idx26 那一类：`< DATE '2027-01-01'` 与 `<= DATE '2026-12-31'` 等价。

    半开区间与闭区间写法不同但语义相同，逐字面量比对判不了；时间窗在 spec 里
    本来就看得见，所以日期字面量一律不参与比对。
    """
    gold = "SELECT COUNT(*) FROM t WHERE dt >= DATE '2026-01-01' AND dt < DATE '2027-01-01'"
    gen = "SELECT COUNT(*) FROM t WHERE dt >= DATE '2026-01-01' AND dt <= DATE '2026-12-31'"
    assert caliber_drift(gold, gen) == []


def test_build_router_wires_probe_fn_when_given():
    """分流必须能按生产接线跑：production 的 MetricRouter 是带值域探针的。

    不注入 probe_fn 时 resolve 少一层「值不存在就退回 NL2SQL」，分档偏宽松。
    拿偏宽松的分档去决定「哪些题移出 few-shot 池」会两头落空——移出去了，
    生产上却因为探针拒绝而退回 NL2SQL，且已经没有 few-shot 兜底。
    """
    catalog = _mod.MetricCatalog.from_yaml(
        Path(__file__).resolve().parents[2] / "config" / "metrics.yaml"
    )
    probe = lambda sql: ([{"probe": 1}], None)  # noqa: E731
    router = _mod.build_router(
        catalog=catalog, embed_fn=lambda texts: [[1.0] for _ in texts], probe_fn=probe
    )
    assert router.probe_fn is probe


def test_build_router_leaves_probe_fn_none_by_default():
    catalog = _mod.MetricCatalog.from_yaml(
        Path(__file__).resolve().parents[2] / "config" / "metrics.yaml"
    )
    router = _mod.build_router(catalog=catalog, embed_fn=lambda texts: [[1.0] for _ in texts])
    assert router.probe_fn is None


# --- 分流执行：把语义层已接住的题移出 few-shot 池 -------------------------------

_A_CLEAN = {"idx": 0, "example_id": "aaa", "routed": True, "prefilter_hit": True, "drift": []}
_A_DRIFT = {"idx": 1, "example_id": "bbb", "routed": True, "prefilter_hit": True, "drift": ["x"]}
_B = {"idx": 2, "example_id": "ccc", "routed": False, "prefilter_hit": True, "drift": []}
_C = {"idx": 3, "example_id": "ddd", "routed": False, "prefilter_hit": False, "drift": []}
_POOL = [{"example_id": e} for e in ("aaa", "bbb", "ccc", "ddd")]


def test_split_pool_migrates_only_clean_a_tier():
    """B/C 档留在池子里——语义层接不住它们，移出去等于既没模板也没 few-shot。"""
    keep, migrate = _mod.split_pool(_POOL, [_A_CLEAN, _A_DRIFT, _B, _C], adjudicated=set())
    assert [r["example_id"] for r in migrate] == ["aaa"]
    assert [r["example_id"] for r in keep] == ["bbb", "ccc", "ddd"]


def test_split_pool_keeps_drifted_a_tier_by_default():
    """漂移未裁决就留下：漂移可能意味着 catalog 有缺口，生成的 SQL 是错的。"""
    _, migrate = _mod.split_pool(_POOL, [_A_DRIFT], adjudicated=set())
    assert migrate == []


def test_split_pool_migrates_adjudicated_drift():
    """人已裁决「以 catalog 为准」的漂移条目照常移出——此时 gold 才是错的那个。"""
    keep, migrate = _mod.split_pool(_POOL, [_A_DRIFT], adjudicated={"bbb"})
    assert [r["example_id"] for r in migrate] == ["bbb"]
    assert "bbb" not in [r["example_id"] for r in keep]


def test_split_pool_rejects_unknown_adjudicated_id():
    """裁决名单打错字不能静默失效——那会让一条本该移出的题悄悄留在池里。"""
    import pytest

    with pytest.raises(ValueError, match="zzz"):
        _mod.split_pool(_POOL, [_A_DRIFT], adjudicated={"zzz"})

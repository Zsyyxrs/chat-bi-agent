"""ValueIndex：把用户问题里的自然表述映射回库中实际取值及其所属列。

动机见 CHESS 论文 Figure 1 第一类挑战：用户问「上海分行」，而 dim_branch
里根本没有叫「上海分行」的行——BR_CITY_0006 的 branch_name 是「浦东分行」，
province 才是「上海」。不给模型这个映射，它只能猜。
"""

from chat_bi_agent.schema.value_index import ValueHit, ValueIndex


def _index() -> ValueIndex:
    return ValueIndex(
        {
            "dim_branch.province": ["上海", "浙江", "江苏"],
            "dim_branch.city": ["浦东", "黄浦", "杭州", "南京"],
            "dim_branch.branch_name": ["浦东分行", "杭州分行", "南京分行"],
            "dim_product.product_subcategory": ["活期", "定期", "T+0"],
        }
    )


def test_lookup_maps_shanghai_to_province_not_branch_name():
    """「上海分行」里的「上海」只存在于 province 列，不能落到 branch_name。"""
    hits = _index().lookup("上海分行 2026 年 2 月的定期存款余额")

    assert ValueHit("dim_branch", "province", "上海") in hits
    assert not [h for h in hits if h.column == "branch_name"]


def test_lookup_tolerates_one_character_typo():
    """用户写「浦东支行」，库里存的是「浦东分行」——差一个字也该命中。"""
    hits = _index().lookup("浦东支行的客户数")

    assert ValueHit("dim_branch", "branch_name", "浦东分行") in hits


def test_lookup_does_not_fuzzy_match_short_values():
    """短值放宽会大量误命中：「北海」不该把 province='上海' 勾出来。"""
    hits = _index().lookup("北海道分公司的余额")

    assert not [h for h in hits if h.value == "上海"]


def test_from_cache_reads_json_snapshot(tmp_path):
    cache = tmp_path / "value_index.json"
    cache.write_text(
        '{"dim_branch.province": ["上海"], "dim_branch.city": ["浦东"]}', encoding="utf-8"
    )

    hits = ValueIndex.from_cache(cache).lookup("上海地区的存款")

    assert hits == [ValueHit("dim_branch", "province", "上海")]


def test_from_cache_missing_file_degrades_to_empty_index(tmp_path):
    """缓存没建时 P1 仍须能跑——值检索只是增强，不是硬依赖。"""
    index = ValueIndex.from_cache(tmp_path / "absent.json")

    assert index.lookup("上海分行的存款") == []


def test_exact_hit_suppresses_fuzzy_noise_in_same_column():
    """中文里差一个字往往是**另一个实体**：杭州分行 / 广州分行 / 苏州分行 编辑距离都是 1。

    问「杭州分行」时既然已精确命中，就不该再把另外三个州拖进来当示例。
    """
    index = ValueIndex({"dim_branch.branch_name": ["杭州分行", "广州分行", "苏州分行", "贵州分行"]})

    hits = index.lookup("杭州分行有多少大众客户")

    assert [h.value for h in hits] == ["杭州分行"]


def test_fuzzy_still_applies_when_column_has_no_exact_hit():
    """没有精确命中时，模糊匹配仍要兜住错别字。"""
    index = ValueIndex({"dim_branch.branch_name": ["浦东分行", "黄浦分行"]})

    assert [h.value for h in index.lookup("浦东支行的账户数")] == ["浦东分行"]


def test_exposes_size_for_run_metadata():
    """评测产物要记录索引规模，供 verify_ab 做跨 run 归因。"""
    index = _index()

    assert index.columns == [
        "dim_branch.province",
        "dim_branch.city",
        "dim_branch.branch_name",
        "dim_product.product_subcategory",
    ]
    assert index.n_values == 13

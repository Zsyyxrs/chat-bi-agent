"""共享引导块：每个 tab 顶部的「这个 tab 能问什么」+ 示例问题按钮。

为什么做进 app 而不是写一份独立用户手册：要回答的问题（该问什么、走错了该去
哪个 tab、要等多久、👍/👎 点了会怎样）全是**用的当下**才想知道的，切出去翻
markdown 没人会做。

示例问题一律取自 `src/chat_bi_agent/data/*_evaluation.yaml`，并且**必须在
results/ 的 baseline 里真跑过、分数不低于该题集中位数**——给一条答不对的示例
比不给更糟。tests/streamlit/test_tab_guide.py 两头都守。

这条规矩是 2026-09-04 走查换来的：当时只校验「题面出自题集」，结果 P1 的第一
条示例是 precision_q005（全集最低 0.8167），实跑漏掉 gold 要求的
account_type='SAVING' 过滤，报出的是全部账户类型的合计；另一条 precision_q009
根本不在任何 baseline 里。题面合法 ≠ 答得对。
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class TabGuide:
    """一个 tab 的自我介绍。

    good_at / not_for 成对出现：只说「适合什么」，用户走错时仍然不知道该去哪，
    所以 not_for 必须点名另一个 tab（测试会守这条）。
    """

    good_at: str
    not_for: str
    tips: tuple[str, ...]
    examples: tuple[str, ...]


TAB_GUIDES: dict[str, TabGuide] = {
    "p1": TabGuide(
        good_at=(
            "**一条 SQL 能答完的确定性查询**：某个指标 + 时间 + 分行 / 客户等级 / "
            "渠道之类的过滤或分组，要的是一个数或一张表。"
        ),
        not_for=(
            "问「为什么」「什么原因」——那是 **P3**；要拆成好几步、前后对比再下结论——那是 **P2**。"
        ),
        tips=(
            "问题里带分行时会走行级权限：换上方「当前登录身份」再问同一句，数会变——"
            "这是设计如此，不是 bug。",
            "命中语义层指标时会显示「模板 SQL，口径固定」，并摊开它对问题的理解；"
            "理解错了当场就能看出来。",
            "实测中位约 12 秒；带多重过滤或排序的题会到 40 秒以上。",
        ),
        examples=(
            "查询上海分行（BR_CITY_0006）所有高净值客户的客户 ID、姓名和客户等级。",
            "找出 2026 年 2 月 15-23 日期间，交易渠道为 ATM 或 COUNTER 的现金支取交易。 "
            "返回交易日期、账户 ID、金额和交易渠道。",
            "分别统计杭州（BR_CITY_0000）和南京（BR_CITY_0002）两个分行的大众客户层级客户数量。",
        ),
    ),
    "p2": TabGuide(
        good_at=(
            "**一个问题要拆成好几步才能答的分析**：事件前后对比、多个客户群体的差异、"
            "某次活动的效果评估——会先出计划，再分步跑 SQL，最后汇总成报告。"
        ),
        not_for=("只想拿一个数——**P1** 快得多；已经知道指标掉了、只想要根因——**P3** 更直接。"),
        tips=(
            "把要对比的两个时间段和要看的口径写清楚，计划的质量直接决定报告的质量。",
            "一次要跑多条 SQL 加多轮 LLM，**实测中位约 8 分钟**，别以为卡死了去刷页面。",
        ),
        examples=(
            "对比春节前（2 月 1-14 日）和春节假期（2 月 15-23 日）的现金支取行为。 "
            "包括总额、日均、涉及的客户数量和主要渠道分布。",
            "分析 LPR 下调（2026-06-20）前后的贷款申请趋势。 "
            "对比政策发布前 7 天、发布后 7 天和发布后 14 天的贷款申请量、平均金额和申请来源。",
            "安鑫 90 天理财产品（PROD_WEA_0000）在到期日（2026-05-14）前后的持有人行为分析。 "
            "包括：到期前 7 天的持有人数、到期后赎回率、续作率和资金流向。",
        ),
    ),
    "p3": TabGuide(
        good_at=(
            "**已经看到某个指标异动，想知道根因**：会先锚定事实（涨跌多少）、"
            "下钻维度找主要贡献者、匹配业务事件，最后给一段归因叙事。"
        ),
        not_for=("只想拿数不问原因——**P1**；要做多步对比但结论不是「因为什么」——**P2**。"),
        tips=(
            "**指标 + 时间范围 + 方向**（上升 / 下降）三样齐全时命中率最高，"
            "「最近怎么样」这类问法锚不住事实。",
            "库里埋了春节现金支取、安鑫产品到期、LPR 下调、七夕营销等业务事件，"
            "问到这些时间窗附近才有事件可匹配。",
            "含两段式 LLM 归因，**实测中位约 8 分钟**。",
        ),
        examples=(
            "上海浦东分行（BR_CITY_0006）的高净值客户活期存款余额在五月中旬出现明显下降。 "
            "请分析主要原因和影响客户群体。",
            "2 月 15-23 日期间，我们观察到全行 ATM 和柜面的现金支取明显增加。 "
            "这是异常波动吗？如果是，根因是什么？主要涉及哪些客户群体？",
            "8 月中旬，杭州和南京分行的定期存款余额出现明显增长。这是市场 "
            "竞争导致的定期存款流入吗？还是我们的营销活动产生了效果？",
        ),
    ),
}


def prefill_question(session_state, input_key: str, question: str) -> None:
    """把示例问题写进输入框的 session_state。

    直接覆盖：点示例是明确意图，不做「已有内容就不动」的猜测——那会让按钮
    看起来点了没反应。

    调用方必须保证此时 text_area 尚未实例化，否则 Streamlit 会拒绝改写已建 widget
    的 state（见 render_guide_block 的位置约束）。
    """
    session_state[input_key] = question


def render_guide_block(tab_key: str, *, input_key: str) -> None:
    """渲染引导 expander + 示例问题按钮。

    **必须放在对应 text_area 之前**：Streamlit 不允许 widget 实例化后再改它的
    session_state，顺序反了会在点按钮时抛 StreamlitAPIException——只在运行时炸，
    所以由 AST 测试守住顺序。
    """
    guide = TAB_GUIDES[tab_key]
    with st.expander("这个 tab 适合问什么？", expanded=False):
        st.markdown(f"**适合**：{guide.good_at}")
        st.markdown(f"**换个 tab**：{guide.not_for}")
        for tip in guide.tips:
            st.markdown(f"- {tip}")

        st.markdown(
            "**试试这些**（取自评测题集，均有 ground truth 对照，"
            "且 baseline 分数不低于该题集中位数）："
        )
        for i, example in enumerate(guide.examples):
            if st.button(example, key=f"{tab_key}_example_{i}", use_container_width=True):
                prefill_question(st.session_state, input_key, example)
                st.rerun()

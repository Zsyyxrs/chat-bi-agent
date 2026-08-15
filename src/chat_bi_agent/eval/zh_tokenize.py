"""中文分词工具，供各 evaluator 共用。

从 rca_evaluator 提取——P3 早就解决了「中文不能按空格切」这个问题，但 P2 的
evaluator 一直在用 `.split()`，等于没分词：中文句子切出来只有 1 个 token，
比对退化成「整句逐字出现才算命中」。2026-08-15 排查时发现并提到这里共用。
"""

import jieba

_PUNCT_CHARS = set("，。！？；：、（）《》“”‘’【】…—·,.!?;:()<>\"'[]{}/\\|~`@#$%^&*-_+= \t\n")

# 中文停用词：保守列表，只剔除几乎不携带业务语义的功能词。
# 避免过度剔除（"月"/"日"/"年" 在日期上下文是有意义的，故保留）。
_ZH_STOPWORDS: frozenset[str] = frozenset(
    {
        "的",
        "了",
        "是",
        "在",
        "与",
        "和",
        "也",
        "都",
        "还",
        "个",
        "上",
        "下",
        "对",
        "而",
        "及",
        "或",
        "被",
        "把",
        "从",
        "到",
        "为",
        "以",
        "于",
        "并",
        "就",
        "很",
        "更",
        "最",
        "会",
        "能",
        "可",
        "要",
        "有",
        "无",
        "不",
        "没",
        "这",
        "那",
        "其",
        "之",
        "所",
        "者",
        "them",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "at",
        "is",
        "are",
        "was",
        "were",
        "and",
        "or",
        "for",
        "with",
        "by",
        "as",
        "it",
        "that",
        "this",
        "be",
        "been",
        "出",
        "出现",
        "存在",
        "进行",
        "通过",
        "根据",
        "以及",
        "等等",
        "方面",
        "情况",
        "问题",
        "相关",
        "一些",
        "一个",
        "可能",
        "需要",
        "使用",
        "包括",
    }
)


def tokenize_zh(text: str) -> set[str]:
    """jieba `cut_for_search` 分词 + 去标点 + 去停用词，返回小写 token 集合。

    用 `cut_for_search` 而非 `lcut`：长复合词会被进一步切开
    （"理财产品" → {"理财", "产品", "理财产品"}），增加重叠命中面。
    """
    if not text:
        return set()
    tokens = jieba.cut_for_search(text.lower())
    return {
        t
        for t in tokens
        if t.strip() and not all(c in _PUNCT_CHARS for c in t) and t not in _ZH_STOPWORDS
    }


def overlap_ratio(expected: str, actual: str) -> float:
    """expected 的内容词有多大比例出现在 actual 里（召回口径，非对称）。

    刻意用召回而非 Jaccard：actual 是一整段分析叙述，长度远大于单条 expected
    insight，用 Jaccard 会被 actual 的长度稀释成接近 0，无法区分「说到了」
    和「没说到」。
    """
    exp_tokens = tokenize_zh(expected)
    if not exp_tokens:
        return 0.0
    return len(exp_tokens & tokenize_zh(actual)) / len(exp_tokens)

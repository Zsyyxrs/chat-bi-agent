"""守门：P2 rubric LLM judge —— 判分、降级与权重归一。

背景（ADR-015 → ADR-016）：2026-08-17 把 `reasoning_quality` / `business_relevance`
移出 P2 总分，因为它们的判据是「数中文连接词」「数业务名词」，任何通顺的中文分析都
自动满分——35% 权重是常数而非测量。删除是对的，但留下一个真实缺口：**推理链条与业务
可落地性此后完全没有被度量**。

本轮照 P3 的 `_llm_judge_conclusion` 补上 rubric LLM judge。关键差别在于**有没有锚**：
被删的两维锚在通用词表上（对任何题目都一样），judge 的四维锚在每道题 YAML 里人工写死的
`analysis_steps` / `expected_insights` / `evaluation_criteria` 上（每题不同，且写在 agent
跑之前）。这仍比 P1 的 gold SQL、P3 的事件库弱——它是「人写的 rubric 文本 + LLM 判读」，
所以确定性维度必须过半。

权重当日调过两次：先是 insight 0.35 / step 0.25 / metric 0.15 / rubric 0.25，随后
`step_completeness` 被证伪（数计划节点而非判步骤有没有做，见
`TestWeights::test_step_completeness_is_not_scored`）并降为诊断，步骤判定整体搬进 judge 的
`step_fidelity`，权重变为 insight 0.45 / metric 0.20 / rubric 0.35。

本文件锁死三件最容易悄悄退化的事：
  1. judge 失败**不得**回退到被删的关键词启发式——那等于把缺陷原样请回来；
  2. judge 缺席时权重必须归一，且必须可辨（`rubric_available`），否则一次 judge 全挂的
     运行会打出一批凭空低 25% 的分数，看起来像 agent 退化；
  3. prompt 必须真的带上每题的人工锚点，否则四维退化成「这段中文读着顺不顺」——
     也就是被删两维的老毛病换个壳。
"""

import pytest

from chat_bi_agent.eval.multi_step_analysis_evaluator import (
    JUDGE_DIMS,
    SCORED_WEIGHTS,
    AnalysisScore,
    MultiStepAnalysisEvaluator,
)

# 评测集里真实存在的题，带 analysis_steps / expected_insights / evaluation_criteria
QID = "multi_step_q001"


def _rubric(**overrides) -> dict:
    r = dict.fromkeys(JUDGE_DIMS, 1.0)
    r.update(overrides)
    r["method"] = "llm_judge"
    return r


class _FakeLLM:
    """记录收到的 prompt，返回可控 payload。"""

    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.0):
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "temperature": temperature}
        )

        class _R:
            content = self.content

        return _R()


class _BrokenLLM:
    def chat(self, **kwargs):
        raise RuntimeError("judge 端点挂了")


class _ForbiddenLLM:
    """任何调用都算失败——用于证明某条路径确实没碰 LLM。"""

    def chat(self, **kwargs):
        raise AssertionError("这条路径不应该调用 LLM")


# ---------------------------------------------------------------- 权重与归一


class TestWeights:
    def test_scored_weights_sum_to_one(self):
        assert abs(sum(SCORED_WEIGHTS.values()) - 1.0) < 1e-9

    def test_deterministic_dims_still_carry_the_majority(self):
        """确定性维度必须过半：judge 是人写 rubric + LLM 判读，锚比 gold SQL 弱。

        上限 2026-08-17 由 0.30 放宽到 0.35：`step_completeness` 降为诊断后，**步骤判定
        整体搬进了 judge 的 `step_fidelity`**，judge 份额上升是这次搬迁的直接结果，不是
        因为更信任 LLM 判读。仍要求确定性侧过半。
        """
        judge_w = SCORED_WEIGHTS["analysis_rubric"]
        assert judge_w <= 0.35, f"judge 权重 {judge_w} 过高，总分会主要由 LLM 判读决定"
        assert sum(v for k, v in SCORED_WEIGHTS.items() if k != "analysis_rubric") > 0.50

    def test_insight_accuracy_is_the_heaviest_dim(self):
        """insight_accuracy 是唯一有硬 ground truth 的维度（expected_insights）。"""
        others = {k: v for k, v in SCORED_WEIGHTS.items() if k != "insight_accuracy"}
        assert SCORED_WEIGHTS["insight_accuracy"] >= max(others.values())

    def test_step_completeness_is_not_scored(self):
        """回归：`step_completeness` 数的是计划节点数，不是步骤有没有做。

        2026-08-17 实测 q001：agent 只规划 2 步（YAML 有 5 步）→ 0.40，而 judge 拿同一份
        `analysis_steps` 判内容给 1.00。人工核对回答全文，5 步的实质内容全部覆盖
        （节前基线 / 假期数据 / 日均与增长率 / 按渠道分别统计 / 对比总结），**judge 是对的**。
        它罚的是「把 5 步并成 2 步做完」，即计划粒度，与维度名声称的「步骤完整性」无关。

        换成内容词召回也不行（实测 0.553/0.337/0.350，比数节点更低）：期望步骤是指令式
        文本、带表名（「从 fct_holding 查询…」），agent 用业务语言报结果，不会复述表名。
        那是另一个错的测法，不是修好。故降为诊断，步骤判定由 judge 的 step_fidelity 承担。
        """
        assert "step_completeness" not in SCORED_WEIGHTS


class TestCombinedScore:
    def test_full_marks_with_rubric(self):
        s = AnalysisScore(
            question_id=QID,
            step_completeness=1.0,
            multi_metric_coverage=1.0,
            insight_accuracy=1.0,
            analysis_rubric=_rubric(),
        )
        assert s.combined_score == pytest.approx(1.0)

    def test_renormalizes_when_rubric_missing(self):
        """judge 缺席时确定性三维满分仍应是 1.0，不是 0.75。

        不归一的话，一次 judge 全挂的运行会打出一批凭空低 25% 的分数，而产物看起来
        跟正常运行一模一样——正是本项目反复栽的那类静默失效。
        """
        s = AnalysisScore(
            question_id=QID,
            step_completeness=1.0,
            multi_metric_coverage=1.0,
            insight_accuracy=1.0,
            analysis_rubric=None,
        )
        assert s.combined_score == pytest.approx(1.0)
        assert s.rubric_available is False

    def test_rubric_absence_is_detectable(self):
        with_rubric = AnalysisScore(question_id=QID, analysis_rubric=_rubric())
        without = AnalysisScore(question_id=QID, analysis_rubric=None)
        assert with_rubric.rubric_available is True
        assert without.rubric_available is False

    def test_rubric_score_averages_the_four_dims(self):
        s = AnalysisScore(question_id=QID, analysis_rubric=_rubric(**{JUDGE_DIMS[0]: 0.0}))
        assert s.rubric_score == pytest.approx(0.75)

    def test_method_key_excluded_from_rubric_average(self):
        """rubric dict 里混了 'method' 字符串，不能被当成一维参与平均。"""
        s = AnalysisScore(question_id=QID, analysis_rubric=_rubric())
        assert s.rubric_score == pytest.approx(1.0)

    def test_zero_rubric_drags_score_down(self):
        """judge 判 0 与 judge 缺席必须给出不同的分——否则「判差」和「没判」不可辨。"""
        judged_zero = AnalysisScore(
            question_id=QID,
            step_completeness=1.0,
            multi_metric_coverage=1.0,
            insight_accuracy=1.0,
            analysis_rubric=dict.fromkeys(JUDGE_DIMS, 0.0),
        )
        absent = AnalysisScore(
            question_id=QID,
            step_completeness=1.0,
            multi_metric_coverage=1.0,
            insight_accuracy=1.0,
            analysis_rubric=None,
        )
        assert judged_zero.combined_score < absent.combined_score

    def test_deleted_dims_stay_out_of_the_score(self):
        """回归 ADR-015/016：三个诊断字段都不得进总分。

        judge 上线后最自然的错误动作，就是顺手把它们加回去。
        """
        base = AnalysisScore(question_id=QID, analysis_rubric=_rubric())
        loud = AnalysisScore(
            question_id=QID,
            analysis_rubric=_rubric(),
            reasoning_quality=1.0,
            business_relevance=1.0,
            step_completeness=1.0,
        )
        assert base.combined_score == pytest.approx(loud.combined_score)

    def test_step_completeness_still_computed_as_diagnostic(self):
        """降为诊断不等于停止计算——它仍是「计划粒度」的有用信号，只是不计分。"""
        ev = MultiStepAnalysisEvaluator(use_llm_judge=False)
        score = ev.evaluate_response(
            question_id=QID,
            agent_response="回答",
            mentioned_steps=["a", "b"],
        )
        # q001 的 YAML 有 5 步，报了 2 步 → 2/5
        assert score.step_completeness == pytest.approx(0.4)


# ---------------------------------------------------------------- judge 本身


class TestJudgeParsing:
    def _ev(self, content: str):
        fake = _FakeLLM(content)
        return MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=fake), fake

    def test_parses_fenced_json(self):
        payload = ", ".join(f'"{d}": 0.5' for d in JUDGE_DIMS)
        ev, _ = self._ev(f"废话废话\n```json\n{{{payload}}}\n```\n结束")
        avg, rubric = ev._llm_judge_analysis(QID, "agent 的回答")
        assert avg == pytest.approx(0.5)
        assert rubric["method"] == "llm_judge"

    def test_parses_bare_json(self):
        payload = ", ".join(f'"{d}": 1.0' for d in JUDGE_DIMS)
        ev, _ = self._ev(f"{{{payload}}}")
        avg, rubric = ev._llm_judge_analysis(QID, "agent 的回答")
        assert avg == pytest.approx(1.0)

    def test_clips_out_of_range_values(self):
        payload = ", ".join(f'"{d}": 7.5' for d in JUDGE_DIMS)
        ev, _ = self._ev(f"```json\n{{{payload}}}\n```")
        avg, rubric = ev._llm_judge_analysis(QID, "回答")
        assert avg == pytest.approx(1.0)
        assert all(rubric[d] == 1.0 for d in JUDGE_DIMS)

    def test_missing_dim_defaults_to_zero(self):
        ev, _ = self._ev('```json\n{"%s": 1.0}\n```' % JUDGE_DIMS[0])
        avg, rubric = ev._llm_judge_analysis(QID, "回答")
        assert avg == pytest.approx(1.0 / len(JUDGE_DIMS))

    def test_temperature_is_zero(self):
        payload = ", ".join(f'"{d}": 1.0' for d in JUDGE_DIMS)
        ev, fake = self._ev(f"```json\n{{{payload}}}\n```")
        ev._llm_judge_analysis(QID, "回答")
        assert fake.calls[0]["temperature"] == 0.0


class TestJudgeFallback:
    def test_broken_llm_yields_none_rubric_not_a_fake_score(self):
        """judge 挂掉必须返回 rubric=None，让该维退出计分并被记账。

        绝不能回退到被删的关键词启发式（数「因此/所以」「客户/分行」）——那等于把
        ADR-015 修掉的缺陷原样请回来，而且这次还藏在 fallback 分支里，平时看不见。
        """
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=_BrokenLLM())
        avg, rubric = ev._llm_judge_analysis(QID, "因此所以由于导致客户分行产品风险收益")
        assert rubric is None
        assert avg is None

    def test_unparseable_output_yields_none(self):
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=_FakeLLM("我拒绝输出 JSON"))
        avg, rubric = ev._llm_judge_analysis(QID, "回答")
        assert rubric is None

    def test_disabled_judge_never_builds_a_client(self):
        ev = MultiStepAnalysisEvaluator(use_llm_judge=False)
        score = ev.evaluate_response(question_id=QID, agent_response="回答")
        assert score.analysis_rubric is None
        assert score.rubric_available is False


class TestJudgePromptAnchors:
    """prompt 必须带上每题的人工锚点。

    没有锚点，四维就退化成「这段中文读着顺不顺」——即被删两维的老毛病换个壳。
    """

    def _capture_user_prompt(self) -> str:
        payload = ", ".join(f'"{d}": 1.0' for d in JUDGE_DIMS)
        fake = _FakeLLM(f"```json\n{{{payload}}}\n```")
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=fake)
        ev._llm_judge_analysis(QID, "agent 的回答")
        return fake.calls[0]["user"]

    def test_prompt_carries_analysis_steps(self):
        assert "查询 2 月 1-14 日期间 WITHDRAW" in self._capture_user_prompt()

    def test_prompt_carries_expected_insights(self):
        assert "现金支取量增加约 25%" in self._capture_user_prompt()

    def test_prompt_carries_per_question_criteria(self):
        assert "Agent 是否正确对比了两个时间段" in self._capture_user_prompt()

    def test_prompt_carries_agent_response(self):
        assert "agent 的回答" in self._capture_user_prompt()

    def test_system_prompt_declares_all_four_dims(self):
        payload = ", ".join(f'"{d}": 1.0' for d in JUDGE_DIMS)
        fake = _FakeLLM(f"```json\n{{{payload}}}\n```")
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=fake)
        ev._llm_judge_analysis(QID, "回答")
        system = fake.calls[0]["system"]
        for dim in JUDGE_DIMS:
            assert dim in system, f"system prompt 未声明 {dim}，judge 不会输出该键"


class _SequenceLLM:
    """每次调用返回序列里的下一个 payload（用完后重复最后一个）。"""

    def __init__(self, payloads: list[dict | None]):
        self.payloads = payloads
        self.n = 0

    def chat(self, **kwargs):
        p = self.payloads[min(self.n, len(self.payloads) - 1)]
        self.n += 1
        if p is None:
            raise RuntimeError("这一次判飞了")
        body = ", ".join(f'"{k}": {v}' for k, v in p.items())

        class _R:
            content = f"```json\n{{{body}}}\n```"

        return _R()


class TestSelfConsistency:
    """逐维中位数：单次判分的摆幅实测可达 0.31，与真实退化同量级。"""

    @staticmethod
    def _payload(val: float) -> dict:
        return dict.fromkeys(JUDGE_DIMS, val)

    def test_takes_median_not_mean(self):
        llm = _SequenceLLM([self._payload(1.0), self._payload(1.0), self._payload(0.0)])
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=llm, judge_samples=3)
        avg, rubric = ev._llm_judge_analysis(QID, "回答")
        # 中位数 = 1.0；均值会是 0.667。取中位数才能让一次判飞不拖走整题。
        assert avg == pytest.approx(1.0)

    def test_samples_count_recorded(self):
        llm = _SequenceLLM([self._payload(0.5)])
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=llm, judge_samples=3)
        _avg, rubric = ev._llm_judge_analysis(QID, "回答")
        assert rubric["samples"] == 3
        assert llm.n == 3

    def test_partial_failure_still_yields_rubric(self):
        """3 次里挂 1 次不该让整维退出计分——那是把噪声升级成缺失。"""
        llm = _SequenceLLM([None, self._payload(1.0), self._payload(1.0)])
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=llm, judge_samples=3)
        avg, rubric = ev._llm_judge_analysis(QID, "回答")
        assert rubric is not None
        assert rubric["samples"] == 2

    def test_total_failure_yields_none(self):
        llm = _SequenceLLM([None])
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=llm, judge_samples=3)
        avg, rubric = ev._llm_judge_analysis(QID, "回答")
        assert rubric is None and avg is None

    def test_samples_key_excluded_from_rubric_average(self):
        """`samples` 是 int，混进 4 维平均会把分数拉飞。"""
        llm = _SequenceLLM([self._payload(1.0)])
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=llm, judge_samples=3)
        score = ev.evaluate_response(question_id=QID, agent_response="回答")
        assert score.rubric_score == pytest.approx(1.0)

    def test_single_sample_mode_makes_one_call(self):
        llm = _SequenceLLM([self._payload(1.0)])
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=llm, judge_samples=1)
        ev._llm_judge_analysis(QID, "回答")
        assert llm.n == 1


class TestPrecomputedRubric:
    """离线重放：产物里存了 rubric 就直接复用，不再花钱重判。"""

    def test_precomputed_rubric_bypasses_llm(self):
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=_ForbiddenLLM())
        score = ev.evaluate_response(
            question_id=QID,
            agent_response="回答",
            precomputed_rubric=_rubric(),
        )
        assert score.rubric_available is True
        assert score.rubric_score == pytest.approx(1.0)

    def test_precomputed_rubric_used_verbatim(self):
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=_ForbiddenLLM())
        given = _rubric(**{JUDGE_DIMS[1]: 0.25})
        score = ev.evaluate_response(
            question_id=QID, agent_response="回答", precomputed_rubric=given
        )
        assert score.analysis_rubric[JUDGE_DIMS[1]] == pytest.approx(0.25)


class TestEvaluateResponseIntegration:
    def test_rubric_lands_on_the_score_object(self):
        payload = ", ".join(f'"{d}": 0.75' for d in JUDGE_DIMS)
        fake = _FakeLLM(f"```json\n{{{payload}}}\n```")
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=fake)
        score = ev.evaluate_response(question_id=QID, agent_response="回答")
        assert score.rubric_score == pytest.approx(0.75)
        assert score.analysis_rubric["method"] == "llm_judge"

    def test_batch_carries_rubric(self):
        payload = ", ".join(f'"{d}": 1.0' for d in JUDGE_DIMS)
        fake = _FakeLLM(f"```json\n{{{payload}}}\n```")
        ev = MultiStepAnalysisEvaluator(use_llm_judge=True, llm_client=fake)
        result = ev.evaluate_batch([{"question_id": QID, "agent_response": "回答"}])
        assert result.scores[0].rubric_available is True


class TestRunnerPersistsRubric:
    """产物层面的契约——rubric 不落盘就没法离线重放，也没法事后复核判分。"""

    @staticmethod
    def _runner_src() -> str:
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[2]
            / "src"
            / "chat_bi_agent"
            / "runners"
            / "run_p2_eval.py"
        ).read_text(encoding="utf-8")

    def test_runner_persists_analysis_rubric(self):
        assert "analysis_rubric" in self._runner_src(), (
            "run_p2_eval 未落盘 analysis_rubric——离线重放将无法复现总分，"
            "每次改评分器又得重跑一轮（单题 300~500s）。"
        )

    def test_runner_accounts_for_judge_fallback(self):
        src = self._runner_src()
        assert "rubric_unavailable" in src, (
            "run_p2_eval 未记账 judge 降级的题目——judge 全挂时产物看起来与正常运行相同，"
            "只是分数口径悄悄换了（三维归一 vs 四维）。"
        )

    def test_report_surfaces_rubric_degradation(self):
        """产物里记了账，一键报告上也必须看得见，否则等于没记（同 ADR-015 的残缺运行）。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2] / "scripts" / "run_all_evals.py").read_text(
            encoding="utf-8"
        )
        assert "rubric_unavailable_questions" in src, (
            "run_all_evals 未读取 rubric_unavailable_questions，报告看不出口径差异"
        )


class TestRescoredArtifactProvenance:
    """重评产物必须把两条出处链分开：agent 那次跑 vs 评分器。

    混淆过一次就再也说不清「分数变化里多少来自评分器、多少来自 agent」。而 agent 跑间
    本身有波动（实测 q001 两轮 0.700 / 0.679），所以改评分器后重跑 agent 会把两个变量
    搅在一起——这正是重评存在的理由。
    """

    @staticmethod
    def _artifact():
        import json
        from pathlib import Path

        p = (
            Path(__file__).resolve().parents[2]
            / "results"
            / "baseline_p2_analysis_2026-08-17_rescored.json"
        )
        if not p.exists():
            pytest.skip("重评产物不在仓库中")
        return json.loads(p.read_text(encoding="utf-8"))

    def test_agent_run_provenance_preserved(self):
        d = self._artifact()
        assert d.get("ran_at"), "ran_at 被抹掉了——agent 那次跑的时间无从追溯"
        assert d["run_metadata"].get("commit_hash"), "run_metadata 必须仍描述 agent 那次跑"

    def test_scorer_provenance_recorded_separately(self):
        d = self._artifact()
        assert d.get("rescored_from"), "没记来源产物，无法复现这次重评"
        assert d.get("rescorer_metadata", {}).get("commit_hash"), "没记评分器出处"
        assert d["rescorer_metadata"]["commit_hash"] != d["run_metadata"]["commit_hash"] or d.get(
            "rescored_at"
        ), "两条出处不可辨"

    def test_declares_whether_judge_was_rerun(self):
        """复用存下的 rubric 与重跑 judge 是两回事，产物必须自己说清。"""
        assert "rescored_judge_rerun" in self._artifact()

    def test_script_keeps_the_two_chains_separate(self):
        """源码层契约：重评不得覆盖 run_metadata。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2] / "scripts" / "replay_p2_scoring.py").read_text(
            encoding="utf-8"
        )
        assert '"rescorer_metadata"' in src
        assert 'out["run_metadata"]' not in src, (
            "重评脚本覆盖了 run_metadata——那描述的是 agent 那次跑，不是评分器"
        )


def test_no_unit_test_constructs_the_evaluator_bare():
    """单元测试里裸构造 evaluator 会真的打网络请求——整个 suite 会挂住。

    2026-08-17 实测：judge 默认 True 上线后，两处不带 `use_llm_judge` 的构造让
    `pytest -m "not integration"` 跑过 120s 仍未结束，且不报错、看不出在等什么。
    默认 True 是对的（与 P3 一致，避免谁忘了开就静默少一维），代价就是这条守门。
    """
    import re as _re
    from pathlib import Path

    # 名字拼接而非写全，否则这行自己会被下面的扫描命中。
    bare_ctor = _re.compile("MultiStepAnalysis" + r"Evaluator\(\s*\)")

    tests_dir = Path(__file__).resolve().parent.parent
    offenders = []
    for path in tests_dir.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if bare_ctor.search(line):
                offenders.append(f"{path.relative_to(tests_dir)}:{lineno}")
    assert not offenders, (
        "以下测试裸构造 MultiStepAnalysisEvaluator（默认 use_llm_judge=True，会真的调 LLM）："
        + ", ".join(offenders)
        + "。单元测试请显式传 use_llm_judge=False 或注入 fake client。"
    )

"""守门：评测集里声明的 expected_result_count 必须与 gold SQL 真打 PG 的行数一致。

为什么需要这个测试——2026-08-14 查出的静默失效：precision 题集 q001/q003/q004 的
expected_result_count 还停在初版种子数据的行数（2/49/1），reseed 之后真实行数是
29/674/88。result_count 在 combined_score 里占 0.15 权重，于是**生成的 SQL 逐字符
正确也会被扣 0.15**——不报错、不告警，只是分数悄悄变低。README 上的「P1 6 题
1.000」因此两个月不可复现，没人发现。

失效方式是静默压分而非崩溃，所以只能靠守门主动去撞。改 dimension_generator 或
重新 seed 之后这个漂移一定会再来，届时应当是 CI 红灯而不是分数悄悄掉。

前置：docker compose 已起，chatbi-pg healthy，且已灌种子数据。
未配 PG_HOST 时整个模块 skip；也可用 `pytest -m "not integration"` 绕开。
"""

from pathlib import Path

import pytest
import yaml
from dotenv import load_dotenv

from chat_bi_agent.agents.shared.sql_executor import SQLExecutor

# SQLExecutor 从环境变量读连接参数（PG_PORT 默认 5432，本项目实际 5433），
# 不加载 .env 的话会连错端口/用户，失败原因会伪装成「gold SQL 跑不通」。
load_dotenv()


def _pg_available() -> bool:
    """探真实连通性而不是看 PG_HOST 有没有值。

    .env 里 PG_HOST 恒有值，用它当开关会让没起 docker 的人撞连接错误而不是跳过。
    """
    _, err = SQLExecutor().execute("SELECT 1")
    return err is None


_PG_UP = _pg_available()

DATA_DIR = Path(__file__).resolve().parents[2] / "src" / "chat_bi_agent" / "data"
EVAL_SETS = ["precision_retrieval_evaluation.yaml", "metric_routing_evaluation.yaml"]


def _load_cases() -> list[tuple[str, str, str, dict]]:
    """展开成 (yaml 名, 题 id, gold sql, 期望行数区间)；只收声明了区间的题。"""
    cases = []
    for name in EVAL_SETS:
        path = DATA_DIR / name
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for q in data.get("evaluation_questions", []):
            rng = q.get("expected_result_count")
            sql = q.get("expected_sql")
            if rng and sql and rng.get("min") is not None:
                cases.append((name, q["id"], sql, rng))
    return cases


_CASES = _load_cases()


@pytest.mark.integration
@pytest.mark.parametrize(
    "yaml_name,qid,gold_sql,expected",
    _CASES,
    ids=[f"{n.split('_')[0]}-{q}" for n, q, _, _ in _CASES],
)
def test_gold_sql_row_count_matches_declaration(yaml_name, qid, gold_sql, expected):
    if not _PG_UP:
        pytest.skip("Postgres 不可达（docker compose 未起？），跳过集成测试")

    rows, err = SQLExecutor().execute(gold_sql)
    assert err is None, f"{yaml_name}::{qid} 的 gold SQL 跑不通：{err}"
    assert rows is not None

    lo, hi = expected["min"], expected["max"]
    actual = len(rows)
    assert lo <= actual <= hi, (
        f"{yaml_name}::{qid} 声明 {lo}-{hi} 行，gold SQL 实际返回 {actual} 行。"
        f"种子数据或 gold SQL 变过——按实测回填 expected_result_count，"
        f"否则正确的 SQL 会被静默扣掉 result_count 那档的 0.15 权重。"
    )


def test_guard_covers_both_eval_sets():
    """防止题集改名/搬家后守门静默退化成零用例。"""
    covered = {name for name, _, _, _ in _CASES}
    assert covered == set(EVAL_SETS), f"守门未覆盖全部题集，实际覆盖：{covered}"
    assert len(_CASES) >= 40, f"用例数异常偏少（{len(_CASES)}），题集可能没被正确加载"

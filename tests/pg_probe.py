"""集成测试的 Postgres 连通性探针。

**为什么不看 `PG_HOST`：** `.env` 里 PG_HOST 恒有值。拿「配置变量存在」当「服务可达」的
证据，会让没起 docker compose 的人撞一堆连接错误而不是干净跳过——2026-08-17 实测，
`pytest tests/` 在 Docker 未启动时报 4 个 FAILED，失败信息是
`connection to server at "localhost" (::1), port 5433 failed: Connection refused`，
看起来像代码坏了。

这是本项目反复出现的那一族缺陷（声明的行为与实际行为不符，且失效不响）的测试侧变体：
配置说「PG 配好了」，实际连不上，而判据只查了配置。

`tests/eval/test_gold_sql_row_counts.py` 先解决过一次，这里提取共用。
"""

import os

from dotenv import load_dotenv

from chat_bi_agent.agents.shared.sql_executor import SQLExecutor

# SQLExecutor 从环境变量读连接参数（PG_PORT 默认 5432，本项目实际 5433），
# 不加载 .env 的话会连错端口/用户，失败原因会伪装成「SQL 跑不通」。
load_dotenv()

SKIP_REASON = "Postgres 不可达（docker compose 未起？），跳过集成测试"


def pg_available() -> bool:
    """真打一次 `SELECT 1`，而不是看 PG_HOST 有没有值。"""
    if not os.environ.get("PG_HOST"):
        return False
    _, err = SQLExecutor().execute("SELECT 1")
    return err is None


# 模块级求值：一次探测供整个 session 复用，避免每个用例都建连接。
PG_UP = pg_available()

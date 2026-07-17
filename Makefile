# chat-bi-agent — 日常任务入口
# 用法: make <target>；跑 `make help` 列全部。
# 约定：所有 target 都从 repo 根目录跑；.env 由 target 自己 source。

.PHONY: help test lint fmt bootstrap-pool promote-pool eval-all eval-p1 eval-p2 eval-p3 eval-bird eval-bird-p1 streamlit

REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
PY := python
LOG_DIR := $(REPO_ROOT)/logs

help:  ## 列出所有 target
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

test:  ## 跑全套 pytest
	$(PY) -m pytest tests/

lint:  ## ruff check
	ruff check src/ tests/ scripts/ streamlit_app/

fmt:  ## ruff format
	ruff format src/ tests/ scripts/ streamlit_app/

# ---------------------- Q-SQL 池管理 ----------------------

bootstrap-pool:  ## 首次灌注生产 pool（P1 eval + Langfuse 合并）
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) scripts/bootstrap_prod_pool.py --source both

promote-pool:  ## 夜间任务：只拉最近 1 天 Langfuse 里 👍 过的 (Q, SQL) 灌进池
	@mkdir -p $(LOG_DIR)
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) scripts/bootstrap_prod_pool.py --source langfuse --langfuse-days-back 1 \
		2>&1 | tee -a $(LOG_DIR)/nightly_promote_$$(date +%Y%m%d).log

# ---------------------- 评估 ----------------------

eval-all:  ## 三路径全跑 + markdown 报告
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) scripts/run_all_evals.py

eval-p1:  ## 只跑 P1 eval（--example-pool 可选）
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) -m chat_bi_agent.runners.run_p1_eval $(POOL_ARG)

eval-p2:  ## 只跑 P2 eval
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) -m chat_bi_agent.runners.run_p2_eval

eval-p3:  ## 只跑 P3 eval
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) -m chat_bi_agent.runners.run_p3_eval

eval-bird:  ## BIRD lean baseline 全量
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) scripts/run_bird_financial.py

eval-bird-p1:  ## BIRD P1 pipeline 全量（$(POOL_ARG) 加 --example-pool）
	set -a && [ -f .env ] && . ./.env; set +a; \
	$(PY) scripts/run_bird_financial_p1.py $(POOL_ARG)

# ---------------------- Streamlit ----------------------

streamlit:  ## 起本地 Streamlit（Postgres/Langfuse 需已 up）
	set -a && [ -f .env ] && . ./.env; set +a; \
	streamlit run streamlit_app/app.py

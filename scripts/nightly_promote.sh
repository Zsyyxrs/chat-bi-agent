#!/usr/bin/env bash
# Nightly cron entry: 把 Langfuse 里最近 1 天 👍 过的 (question, sql) 拉进
# data/example_pool_prod.jsonl。生产 P1 agent 会在启动时 hot-reload 这个文件。
#
# 部署示例（crontab -e）：
#   0 3 * * *  /path/to/chat-bi-agent/scripts/nightly_promote.sh
#
# 幂等：bootstrap_prod_pool.py 按 example_id (sha1(question||sql)[:12]) 去重，
# 重复运行不会重复灌样。

set -euo pipefail

# 找到 repo 根（脚本可能通过 symlink 调）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# 加载 .env（DASHSCOPE_API_KEY / LANGFUSE_* keys）
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

mkdir -p logs
LOG="logs/nightly_promote_$(date +%Y%m%d).log"

{
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) nightly_promote start ====="
    python scripts/bootstrap_prod_pool.py --source langfuse --langfuse-days-back 1
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) done ====="
} 2>&1 | tee -a "$LOG"

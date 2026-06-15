#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "🔥 [1/5] Drop & recreate schema..."
docker exec -i chatbi-pg psql -U chatbi -d chatbi -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker exec -i chatbi-pg psql -U chatbi -d chatbi < docker/postgres/init/01_schema.sql
docker exec -i chatbi-pg psql -U chatbi -d chatbi < docker/postgres/init/02_indexes.sql
docker exec -i chatbi-pg psql -U chatbi -d chatbi < docker/postgres/init/03_readonly_role.sql

echo "🌱 [2/5] Seed dimensions + facts with event propagation..."
python -m chat_bi_agent.data.seed \
    --port 5433 \
    --rows 100000 \
    --with-events \
    --truncate

echo "✅ [3/5] Verify events..."
python scripts/verify_events.py

echo "📊 [4/5] Run P3 RCA eval..."
python -m chat_bi_agent.runners.run_p3_eval

echo "🎉 [5/5] Reseed complete."

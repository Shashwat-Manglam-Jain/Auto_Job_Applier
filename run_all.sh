#!/usr/bin/env bash
# Launch all 7 pipelines in parallel, each scraping different sources.
# All share the same Neon PostgreSQL DB for dedup.

set -euo pipefail
cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
DATE=$(date +%Y-%m-%d_%H%M)

PIDS=()

for i in 1 2 3 4 5 6 7; do
    LOG_FILE="$LOG_DIR/pipeline_${i}_${DATE}.log"
    python run.py --pipeline "$i" > "$LOG_FILE" 2>&1 &
    PIDS+=($!)
    echo "[$(date +%H:%M:%S)] Pipeline $i launched (PID $!) → $LOG_FILE"
done

echo ""
echo "All 7 pipelines running. Waiting for completion..."
echo ""

FAILED=0
for idx in "${!PIDS[@]}"; do
    PID=${PIDS[$idx]}
    PIPELINE_NUM=$((idx + 1))
    if wait "$PID"; then
        echo "[$(date +%H:%M:%S)] Pipeline $PIPELINE_NUM (PID $PID) completed successfully"
    else
        echo "[$(date +%H:%M:%S)] Pipeline $PIPELINE_NUM (PID $PID) FAILED (exit $?)"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "All pipelines completed successfully."
else
    echo "$FAILED pipeline(s) failed. Check logs in $LOG_DIR/"
fi

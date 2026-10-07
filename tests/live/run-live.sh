#!/bin/sh
# Run each live OpenCode test separately; append one result line per test to $GP_LIVE_LOG (default /tmp/gp-live.log).
cd "$(dirname "$0")/../.." || exit 1
LOG=${GP_LIVE_LOG:-/tmp/gp-live.log}
for t in fixes_and_lands bad_green_is_caught worker_cannot_write_main two_card_batch server_exposes_no_mcp gp_ask_read_only; do
  s=$(date +%s)
  GP_LIVE=1 uv run --with pytest pytest -q tests/live -k "$t" > "/tmp/gp-live-$t.txt" 2>&1
  rc=$?
  echo "$(date '+%H:%M:%S') $t rc=$rc $(tail -1 /tmp/gp-live-$t.txt) ($(( $(date +%s)-s ))s)" >> "$LOG"
done
echo "done" >> "$LOG"

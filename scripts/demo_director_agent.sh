#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="$ROOT_DIR/src/agents/director_agent/agent.py"
MASTER="$ROOT_DIR/src/agents/director_agent/master_gap_list.json"
SIMILAR_PAPER="$ROOT_DIR/workspace/transformers/papers/arxiv_test_similarity_merge/paper.json"
SIMILAR_PAPER_2="$ROOT_DIR/workspace/transformers/papers/arxiv_test_similarity_merge_2/paper.json"
NEW_PAPER="$ROOT_DIR/workspace/transformers/papers/arxiv_test_new_gap/paper.json"

rm -f "$MASTER"

echo "=== Step 1: Create master_gap_list.json from scratch ==="
python3 "$AGENT" "$SIMILAR_PAPER"

echo
echo "=== Step 2: Merge a similar gap and extend evidence ==="
python3 "$AGENT" "$SIMILAR_PAPER_2"

echo
echo "=== Step 3: Append a non-similar gap ==="
python3 "$AGENT" "$NEW_PAPER"

echo
 echo "=== Resulting master_gap_list.json ==="
cat "$MASTER"

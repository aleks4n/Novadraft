#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="$ROOT_DIR/src/agents/director_agent/agent.py"
MASTER="$ROOT_DIR/src/agents/director_agent/master_gap_list.json"
SIMILAR_PAPER="$ROOT_DIR/workspace/transformers/papers/arxiv_test_similarity_merge/paper.json"
SIMILAR_PAPER_2="$ROOT_DIR/workspace/transformers/papers/arxiv_test_similarity_merge_2/paper.json"
NEW_PAPER="$ROOT_DIR/workspace/transformers/papers/arxiv_test_new_gap/paper.json"

rm -f "$MASTER"

echo "=== Processing multiple papers with gap consolidation ==="
python3 "$AGENT" "$SIMILAR_PAPER" "$SIMILAR_PAPER_2" "$NEW_PAPER"

echo
echo "=== Resulting master_gap_list.json ==="
cat "$MASTER"

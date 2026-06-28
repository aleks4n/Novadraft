from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from src.agents.director_agent.agent import main as run_director_agent


SIMILAR_PAPER = ROOT_DIR / "workspace/transformers/papers/arxiv_test_similarity_merge/paper.json"
SIMILAR_PAPER_2 = ROOT_DIR / "workspace/transformers/papers/arxiv_test_similarity_merge_2/paper.json"
NEW_PAPER = ROOT_DIR / "workspace/transformers/papers/arxiv_test_new_gap/paper.json"


def run_demo() -> None:
	print("=== Step 1: Create master_gap_list.json from scratch ===")
	run_director_agent(str(SIMILAR_PAPER))

	print()
	print("=== Step 2: Merge a similar gap and extend evidence ===")
	run_director_agent(str(SIMILAR_PAPER_2))

	print()
	print("=== Step 3: Append a non-similar gap ===")
	run_director_agent(str(NEW_PAPER))

	print()
	print("=== Resulting master_gap_list.json ===")
	print((ROOT_DIR / "src/agents/director_agent/master_gap_list.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
	run_demo()
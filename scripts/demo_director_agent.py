from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from src.agents.director_agent.agent import main as run_director_agent


SIMILAR_PAPER = ROOT_DIR / "/Users/aliihsangungoren/Desktop/novadraft/workspace/test/papers/doi_10_1038_nature14539/paper.json"
SIMILAR_PAPER_2 = ROOT_DIR / "/Users/aliihsangungoren/Desktop/novadraft/workspace/test/papers/arxiv_1706.03762/paper.json"
NEW_PAPER = ROOT_DIR / "/Users/aliihsangungoren/Desktop/novadraft/workspace/test/papers/arxiv_2010.11929/paper.json"


def run_demo() -> None:
	print("=== Processing multiple papers with gap consolidation ===")
	run_director_agent([
		str(SIMILAR_PAPER),
		str(SIMILAR_PAPER_2),
		str(NEW_PAPER)
	])

	print()
	print("=== Resulting master_gap_list.json ===")
	print((ROOT_DIR / "src/agents/director_agent/master_gap_list.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
	run_demo()
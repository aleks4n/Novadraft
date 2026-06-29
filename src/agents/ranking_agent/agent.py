"""Ranking agent — the subgraph wiring the ranking steps into one workflow.

Flow: score_openness -> score_impact -> score_feasibility -> combine. Each scoring node
also filters (a gap missing from its output dict is out), so later nodes only see
survivors. Embeds as a node in the main project; runnable directly for testing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.ranking_agent.state import RankingState
from src.agents.ranking_agent.steps.score_openness import score_openness
from src.agents.ranking_agent.steps.score_impact import score_impact
from src.agents.ranking_agent.subagents.score_feasibility.agent import score_feasibility
from src.agents.ranking_agent.steps.combine import combine


def build_ranking_graph():
    """Wire the ranking steps into a compiled graph."""
    g = StateGraph(RankingState)
    g.add_node("score_openness", score_openness)
    g.add_node("score_impact", score_impact)
    g.add_node("score_feasibility", score_feasibility)
    g.add_node("combine", combine)

    g.add_edge(START, "score_openness")
    g.add_edge("score_openness", "score_impact")
    g.add_edge("score_impact", "score_feasibility")
    g.add_edge("score_feasibility", "combine")
    g.add_edge("combine", END)
    return g.compile()


if __name__ == "__main__":
    import time
    from datetime import datetime, timezone

    from pydantic_core import to_jsonable_python

    from src.common.schemas.gap import Gap
    from src.common.schemas.verdict import Verdict
    from src.agents.ranking_agent.schemas import RankingConfig

    fixture_path = Path(__file__).parent / "test_data" / "ranking_input.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    gaps = [Gap.model_validate(g) for g in data["gaps"]]
    verdicts = {gid: Verdict.model_validate(v) for gid, v in data["verdicts"].items()}
    config = RankingConfig()

    graph = build_ranking_graph()
    print(f"INPUT: {len(gaps)} gaps\n")

    result: dict = {}  # accumulate each node's update so we keep the full final state
    last = time.perf_counter()
    for chunk in graph.stream(
        {"config": config, "gaps": gaps, "verdicts": verdicts}, stream_mode="updates"
    ):
        for node, update in chunk.items():
            now = time.perf_counter()
            result.update(update)
            print(f"[{node}]  {now - last:.1f}s")
            for key, value in update.items():
                if key == "ranked":
                    print("  === RANKED (top-X) ===")
                    for rank, r in enumerate(value, 1):
                        s = r.score
                        print(
                            f"   {rank}. {s.final_score:.3f}  {r.gap.title[:48]:48}  "
                            f"[o={s.openness:.2f} i={s.impact:.2f} f={s.feasibility:.2f}]"
                        )
                else:
                    print(f"  {key}: {len(value)} survivors -> {sorted(value)}")
            last = now

    # --- analysis dump: config/weights + every gap's per-dimension scores + where it dropped ---
    openness = result.get("openness", {})
    impact = result.get("impact", {})
    feasibility = result.get("feasibility", {})
    ranked = result.get("ranked", [])
    rank_of = {r.gap.gap_id: (i, r.score.final_score) for i, r in enumerate(ranked, 1)}

    rows = []
    for g in gaps:
        gid = g.gap_id
        feas = feasibility.get(gid)
        if gid in rank_of:
            outcome, rank, final = "ranked", rank_of[gid][0], rank_of[gid][1]
        elif gid in feasibility:
            outcome, rank, final = "passed_but_truncated", None, None  # cut by top_x
        elif gid in impact:
            outcome, rank, final = "filtered_at_feasibility", None, None
        elif gid in openness:
            outcome, rank, final = "filtered_at_impact", None, None
        else:
            outcome, rank, final = "filtered_at_openness", None, None
        v = verdicts.get(gid)
        bd = impact.get(gid)  # ImpactBreakdown or None
        rows.append({
            "gap_id": gid, "title": g.title, "outcome": outcome, "rank": rank, "final_score": final,
            "openness": {
                "score": openness.get(gid),
                "status": v.status if v else None,
                "confidence": v.confidence if v else None,
            },
            "impact": None if bd is None else {
                "score": bd.score, "quality": bd.quality, "venue_score": bd.venue_score,
                "venues": bd.venues, "citation": bd.citation,
                "recency": bd.recency, "evidence_bonus": bd.evidence_bonus,
            },
            "feasibility": None if feas is None else {"score": feas.score, "reasoning": feas.reasoning},
        })
    rows.sort(key=lambda r: (r["final_score"] is None, -(r["final_score"] or 0.0)))

    analysis = {
        "config": to_jsonable_python(config),
        "summary": {"input_gaps": len(gaps), "ranked": len(ranked), "filtered": len(gaps) - len(feasibility)},
        "gaps": rows,
    }
    runs_dir = Path(__file__).parent / "test_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_path = runs_dir / f"ranking-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nANALYSIS: {out_path}")

// Shared view-time ranking math, used by both the Gaps table and the sidebar
// tree so they order and score gaps identically. Weights feed the weighted
// geometric mean behind the "Overall" score; mins are filter floors.
import type { Gap, RankingConfig } from "./types";

// Fallback knobs, matching the agent's RankingConfig defaults (weights 1, no
// floors). Used until the backend's rankingConfig arrives in the bundle.
export const DEFAULT_RANKING_CONFIG: RankingConfig = {
  wOpenness: 1, wImpact: 1, wFeasibility: 1,
  minOpenness: 0, minImpact: 0, minFeasibility: 0,
};

// Combined overall score: weighted geometric mean of the three ranking
// dimensions, exp((Σ wᵢ·ln xᵢ) / Σ wᵢ), using the view-time weights. Returns
// null when the gap isn't ranked yet (so it sorts last / renders as —). A zero on
// any dimension pulls the mean to 0 — intended (a dead dimension kills the
// opportunity) — guarded so ln(0) isn't NaN.
export function overallScore(g: Gap, cfg: RankingConfig): number | null {
  const r = g.ranking;
  if (!r) return null;
  const dims = [
    [r.openness, cfg.wOpenness],
    [r.impact, cfg.wImpact],
    [r.feasibility, cfg.wFeasibility],
  ] as const;
  let wSum = 0;
  let lnSum = 0;
  for (const [x, w] of dims) {
    if (x === null) return null; // incomplete ranking → treat as unranked
    if (x <= 0) return 0;
    wSum += w;
    lnSum += w * Math.log(x);
  }
  return wSum === 0 ? null : Math.exp(lnSum / wSum);
}

// A ranked gap passes only if every dimension clears its min-floor. Unranked
// gaps carry no dimensions, so the floors don't apply to them.
export function passesFloors(g: Gap, cfg: RankingConfig): boolean {
  const r = g.ranking;
  if (!r) return true;
  return (r.openness ?? 0) >= cfg.minOpenness
    && (r.impact ?? 0) >= cfg.minImpact
    && (r.feasibility ?? 0) >= cfg.minFeasibility;
}

// Score → 2-decimal string, or "—" when unranked.
export function fmtScore(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

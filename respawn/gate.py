"""Gatekeeper: keep a change only if it beats the champion beyond noise on paired seeds."""
from __future__ import annotations

import random
import statistics


def paired_test(cand: list, champ: list, iters: int = 4000, seed: int = 0) -> dict:
    deltas = [a - b for a, b in zip(cand, champ)]
    n = len(deltas)
    mean = statistics.fmean(deltas) if deltas else 0.0
    rng = random.Random(seed)
    boots = sorted(statistics.fmean(rng.choice(deltas) for _ in range(n)) for _ in range(iters)) if n else [0.0]
    p_better = sum(b > 0 for b in boots) / len(boots)
    return {
        "n": n, "mean_delta": round(mean, 2), "wins": sum(d > 0 for d in deltas), "losses": sum(d < 0 for d in deltas),
        "ties": sum(d == 0 for d in deltas), "p_better": round(p_better, 3),
        "ci90": [round(boots[int(0.05 * len(boots))], 2), round(boots[int(0.95 * len(boots)) - 1], 2)],
        "deltas": deltas,
    }


def decide(stats: dict, min_delta: float = 1.0, min_p: float = 0.8) -> tuple:
    if stats["mean_delta"] < min_delta:
        return False, f"mean gain {stats['mean_delta']:+.1f} below the {min_delta:+.1f} bar"
    if stats["p_better"] < min_p:
        return False, f"not confident: bootstrap P(better)={stats['p_better']:.2f} < {min_p}"
    if stats["wins"] < stats["losses"]:
        return False, f"loses more seeds than it wins ({stats['wins']}W/{stats['losses']}L)"
    return True, f"mean {stats['mean_delta']:+.1f}, P(better)={stats['p_better']:.2f}, {stats['wins']}W/{stats['losses']}L/{stats['ties']}T"

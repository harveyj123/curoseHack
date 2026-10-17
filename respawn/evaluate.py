"""Held-out evaluation: the starting team vs the evolved champion on seeds neither ever trained on."""
from __future__ import annotations

import json
import os
import statistics

from respawn import gate
from respawn.genome import Genome
from respawn.harness import run_many


def heldout(llm, run_dir: str, seeds: list, player_models: list, parallel: int = 4, record_seed: int | None = None,
            include_all: bool = False, thinking: bool = False) -> dict:
    cfg = json.load(open(os.path.join(run_dir, "config.json")))
    env_name = cfg["env"]
    gdir = os.path.join(run_dir, "genomes")
    names = sorted(os.listdir(gdir))
    picks = names if include_all else [names[0], names[-1]]
    out = {"env": env_name, "seeds": seeds, "genomes": {}}
    for n in picks:
        g = Genome.load(os.path.join(gdir, n))
        eps = run_many(llm, env_name, seeds, g, player_models, parallel, record=False, thinking=thinking)
        out["genomes"][n] = {"fingerprint": g.fingerprint(), "change": g.meta.get("change"), "scores": {e.seed: e.score for e in eps},
                             "mean": round(statistics.fmean(e.score for e in eps), 2),
                             "outcomes": {e.seed: e.outcome for e in eps}}
        print(f"{n}: mean {out['genomes'][n]['mean']} scores {[e.score for e in eps]}", flush=True)
    first, last = picks[0], picks[-1]
    a = [out["genomes"][last]["scores"][s] for s in seeds]
    b = [out["genomes"][first]["scores"][s] for s in seeds]
    out["comparison"] = {"from": first, "to": last, **gate.paired_test(a, b)}
    if record_seed is not None:
        os.makedirs(os.path.join(run_dir, "replays"), exist_ok=True)
        for n in (first, last):
            g = Genome.load(os.path.join(gdir, n))
            ep = run_many(llm, env_name, [record_seed], g, player_models, 1, record=True, thinking=thinking)[0]
            json.dump(ep.to_json(with_replay=True), open(os.path.join(run_dir, "replays", f"{n}_seed{record_seed}.json"), "w"), default=str)
    json.dump(out, open(os.path.join(run_dir, "heldout.json"), "w"), indent=2, default=str)
    return out


def markdown(out: dict) -> str:
    rows = ["| genome | change | held-out mean | per-seed |", "|---|---|---|---|"]
    for n, g in out["genomes"].items():
        rows.append(f"| {n} | {(g['change'] or '')[:70]} | {g['mean']} | {', '.join(str(g['scores'][s]) for s in out['seeds'])} |")
    c = out["comparison"]
    rows.append("")
    rows.append(f"{c['to']} vs {c['from']} on {c['n']} unseen seeds: mean gain {c['mean_delta']:+}, "
                f"{c['wins']}W/{c['losses']}L/{c['ties']}T, 90% CI {c['ci90']}, bootstrap P(better) {c['p_better']}")
    return "\n".join(rows)

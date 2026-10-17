"""The Version N+1 loop: play a day, dream (coach proposes one change), gate it, repeat.

Everything is written to runs/<name>/ so a run can be resumed, replayed, and audited:
  lineage.jsonl          one line per dream: proposal, test result, stats, accepted/rejected
  genomes/gen_NNN/       every accepted champion (gen_000 is the starting team)
  rejected/gen_NNN/      every rejected candidate, for the demo's "what it refused to learn"
  episodes/*.jsonl       full trajectories (also usable later as an SFT dataset)
"""
from __future__ import annotations

import json
import os
import time

from respawn import coach, gate
from respawn.genome import Genome
from respawn.harness import run_many


def _w(path, obj, mode="a"):
    with open(path, mode) as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _save_eps(path, eps):
    with open(path, "w") as f:
        for e in eps:
            f.write(json.dumps(e.to_json(), default=str) + "\n")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def evolve(llm, env_name: str, run_dir: str, gens: int, train_seeds: list, player_models: list, coach_model: str,
           parallel: int = 4, start: Genome | None = None, min_delta: float = 1.0, min_p: float = 0.8,
           thinking: bool = False):
    os.makedirs(os.path.join(run_dir, "episodes"), exist_ok=True)
    state_path = os.path.join(run_dir, "state.json")
    lineage_path = os.path.join(run_dir, "lineage.jsonl")
    if os.path.exists(state_path):
        st = json.load(open(state_path))
        champion = Genome.load(st["champion_dir"])
        champ_scores = {int(k): v for k, v in st["champ_scores"].items()}
        from respawn.harness import Episode, Step  # reload champion episodes for the coach
        champ_eps = []
        for line in open(st["champ_episodes"]):
            d = json.loads(line)
            d["steps"] = [Step(**s) for s in d["steps"]]
            champ_eps.append(Episode(**d))
        first_gen = st["gen"] + 1
        log(f"resuming at gen {first_gen}, champion {champion.fingerprint()}")
    else:
        champion = (start or Genome()).copy()
        champion.meta = {"gen": 0, "parent": None, "change": "starting team"}
        champion.save(os.path.join(run_dir, "genomes", "gen_000"))
        json.dump({"env": env_name, "train_seeds": train_seeds, "player_models": player_models, "coach_model": coach_model,
                   "min_delta": min_delta, "min_p": min_p, "mock": llm.mock}, open(os.path.join(run_dir, "config.json"), "w"), indent=2)
        log(f"gen 0: evaluating starting team on {len(train_seeds)} train seeds")
        champ_eps = run_many(llm, env_name, train_seeds, champion, player_models, parallel, thinking=thinking)
        champ_scores = {e.seed: e.score for e in champ_eps}
        ep_path = os.path.join(run_dir, "episodes", "gen_000.jsonl")
        _save_eps(ep_path, champ_eps)
        _w(lineage_path, {"gen": 0, "accepted": True, "change": "starting team", "fingerprint": champion.fingerprint(),
                          "mean": _mean(champ_scores.values()), "scores": champ_scores})
        _state(state_path, 0, run_dir, "gen_000", champ_scores, ep_path)
        log(f"gen 0 mean {_mean(champ_scores.values()):.1f} scores {list(champ_scores.values())}")
        first_gen = 1

    history = [json.loads(line) for line in open(lineage_path)][1:]
    for g in range(first_gen, first_gen + gens):
        t0 = time.monotonic()
        log(f"night {g}: coach dreaming over {len(champ_eps)} episodes")
        try:
            cand, prop, err = coach.propose(llm, env_name, champion, champ_eps, history, g, model=coach_model)
        except Exception as e:  # coach API failure: skip this night
            cand, prop, err = None, {}, f"coach failed: {e}"
        entry = {"gen": g, "parent": champion.fingerprint(), "diagnosis": prop.get("diagnosis", ""),
                 "change": prop.get("change") or prop.get("diagnosis", ""), "proposal": prop}
        if cand is None:
            entry.update({"accepted": False, "error": err, "reason": "candidate failed validation (tool test or format)"})
            _w(lineage_path, entry)
            history.append(entry)
            log(f"night {g}: proposal rejected before play: {err[:200]}")
            continue
        cand.meta = {"gen": g, "parent": champion.fingerprint(), "change": entry["change"]}
        log(f"night {g}: testing candidate {cand.fingerprint()}: {entry['change'][:120]}")
        eps = run_many(llm, env_name, train_seeds, cand, player_models, parallel, thinking=thinking)
        ep_path = os.path.join(run_dir, "episodes", f"gen_{g:03d}_cand.jsonl")
        _save_eps(ep_path, eps)
        seeds = [e.seed for e in eps]
        stats = gate.paired_test([e.score for e in eps], [champ_scores[s] for s in seeds])
        ok, reason = gate.decide(stats, min_delta, min_p)
        entry.update({"accepted": ok, "reason": reason, "stats": stats, "fingerprint": cand.fingerprint(),
                      "mean": _mean(e.score for e in eps), "champion_mean": _mean(champ_scores[s] for s in seeds),
                      "delta": stats["mean_delta"], "seconds": round(time.monotonic() - t0, 1),
                      "tools": sorted(cand.tools), "scores": {e.seed: e.score for e in eps}})
        _w(lineage_path, entry)
        history.append(entry)
        if ok:
            champion = cand
            champ_scores = {e.seed: e.score for e in eps}
            champ_eps = eps
            d = f"gen_{g:03d}"
            champion.save(os.path.join(run_dir, "genomes", d))
            _state(state_path, g, run_dir, d, champ_scores, ep_path)
            log(f"night {g}: ACCEPTED ({reason}); new mean {entry['mean']:.1f}")
        else:
            cand.save(os.path.join(run_dir, "rejected", f"gen_{g:03d}"))
            st = json.load(open(state_path))
            st["gen"] = g
            json.dump(st, open(state_path, "w"), indent=2)
            log(f"night {g}: rejected ({reason})")
    log(f"done. champion {champion.fingerprint()} mean {_mean(champ_scores.values()):.1f}; usage {json.dumps(llm.stats.summary())}")
    return champion


def _mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def _state(path, gen, run_dir, gdir, scores, ep_path):
    json.dump({"gen": gen, "champion_dir": os.path.join(run_dir, "genomes", gdir), "champ_scores": scores,
               "champ_episodes": ep_path}, open(path, "w"), indent=2)

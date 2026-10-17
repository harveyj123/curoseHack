"""CLI: python -m respawn {evolve,eval,lineage,play} ..."""
from __future__ import annotations

import argparse
import json
import os
import sys

from respawn.llm import LIGHTNING, LLM, NANO, SUPER


def seeds(spec: str) -> list:
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="respawn")
    ap.add_argument("--mock", action="store_true", help="offline mode: scripted player/coach, no API calls")
    ap.add_argument("--players", default=f"{NANO},{LIGHTNING}", help="comma-separated player models (round-robin)")
    ap.add_argument("--coach", default=SUPER)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--thinking", action="store_true", help="let player models reason before acting")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evolve")
    e.add_argument("--env", default="dungeon")
    e.add_argument("--project", help="train on this Godot project folder (sets --env; used by the editor dock)")
    e.add_argument("--run", required=True)
    e.add_argument("--gens", type=int, default=5)
    e.add_argument("--train", default="0-7")
    e.add_argument("--min-delta", type=float, default=1.0)
    e.add_argument("--min-p", type=float, default=0.8)
    e.add_argument("--start", help="genome dir to start from")
    e.add_argument("--no-rerun", action="store_true", help="do not re-test the champion every night")
    e.add_argument("--patience", type=int, default=3,
                   help="stop after this many blocked nights in a row (the crew has plateaued); 0 = never")
    v = sub.add_parser("eval")
    v.add_argument("--run", required=True)
    v.add_argument("--seeds", default="1000-1011")
    v.add_argument("--record", type=int, help="also record replays of first/last genome on this seed")
    v.add_argument("--all", action="store_true", help="evaluate every accepted generation, not just first and last")
    v.add_argument("--env", help="evaluate on a different env (held-out game / bug set), e.g. godot_hunt_heldout")
    lg = sub.add_parser("lineage")
    lg.add_argument("--run", required=True)
    p = sub.add_parser("play")
    p.add_argument("--env", default="dungeon")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--genome")
    a = ap.parse_args(argv)
    llm = None if a.cmd == "lineage" else LLM(mock=a.mock or None)  # lineage only reads files: no key needed
    players = a.players.split(",")

    if a.cmd == "evolve":
        from respawn.evolve import evolve
        from respawn.genome import Genome
        if a.project:
            from respawn.qa import env_for_project
            a.env = env_for_project(a.project)
        evolve(llm, a.env, a.run, a.gens, seeds(a.train), players, a.coach, a.parallel,
               Genome.load(a.start) if a.start else None, a.min_delta, a.min_p, a.thinking, not a.no_rerun, a.patience)
    elif a.cmd == "eval":
        from respawn.evaluate import heldout, markdown
        out = heldout(llm, a.run, seeds(a.seeds), players, a.parallel, a.record, a.all, a.thinking, env=a.env)
        md = markdown(out)
        open(os.path.join(a.run, f"heldout_{out['env']}.md"), "w").write(md + "\n")
        print(md)
    elif a.cmd == "lineage":
        for line in open(os.path.join(a.run, "lineage.jsonl")):
            d = json.loads(line)
            mark = "KEPT" if d["accepted"] else "REJECTED"
            print(f"gen {d['gen']:>2} {mark:<8} {d.get('delta', 0):+7.1f}  {(d.get('change') or '')[:90]}")
            if d.get("reason") and d["gen"]:
                print(f"         {d['reason'][:120]}")
    elif a.cmd == "play":
        from respawn.genome import Genome
        from respawn.harness import run_episode
        g = Genome.load(a.genome) if a.genome else Genome()
        ep = run_episode(llm, a.env, a.seed, g, players)
        for s in ep.steps:
            print(f"{s.agent}#{s.i} {s.action}({json.dumps(s.args)}) -> {s.result[:160]}")
        print("score", ep.score, ep.outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())

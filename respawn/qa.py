"""QA Crew CLI: hunt bugs in a Godot game and write fix-ready bug reports.

  python -m respawn.qa hunt --project godot/games/platformer_train --genome runs/hunt1/genomes/gen_003 --seeds 0-3
  python -m respawn.qa check reports/<id>.json     # exit 0 = fixed (bug no longer reproduces), 1 = still broken

Every report is a structured JSON (schema below) plus a markdown page with a GIF of the failure.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time

from respawn.godot.client import GodotGame

REPORT_SCHEMA = {
    "id": "stable hash of class + project + minimal repro",
    "title": "one line",
    "bug_class": "crash | clip | infinite_jump | out_of_bounds | dupe | ghost_score",
    "game": "project path",
    "expected": "what should happen",
    "actual": "what happened (oracle evidence)",
    "repro": {"seed": "int", "plays": "list of plays; each play is a list of {hold:[actions], frames:int} segments sent as one bridge 'act' call"},
    "repro_tokens": "the same repro as play strings, e.g. ['R30 RJ20']",
    "frame": "physics frame of the failure within the repro",
    "state_at_failure": "player position, velocity, floor/wall flags, score",
    "suspects": "files/lines to look at first",
    "evidence": {"gif": "path", "png": "path", "strip": "sprite sheet of the GIF frames (for the editor dock)", "strip_frames": "int"},
    "check": "command that exits 0 once the bug is fixed",
}

EXPECTED = {
    "crash": "No script errors during play.",
    "clip": "The player body never passes through or overlaps solid level geometry.",
    "infinite_jump": "At most the allowed number of mid-air jumps (double jump = 1) before touching the floor again.",
    "out_of_bounds": "The player stays inside the level bounds.",
    "dupe": "Each collectible adds to the score exactly once.",
    "ghost_score": "The score does not change while the player is dead or hidden.",
}
LIKELY = {
    "clip": "movement code that sets position directly instead of using move_and_slide()",
    "infinite_jump": "jump / double-jump recharge conditions",
    "out_of_bounds": "position clamping and level limits",
    "dupe": "collectible pickup handler (disable the collision before the pickup animation)",
    "ghost_score": "score timer / game-over handling",
}


def tokens_of(segs, letters):
    inv = {v: k for k, v in letters.items()}
    out = []
    for s in segs:
        keys = "".join(inv.get(a, "?") for a in s.get("hold", [])) or "W"
        out.append(f"{keys}{s['frames']}")
    return " ".join(out)


class Reproducer:
    """Runs (seed, plays) in a game process and reports which bug classes fire. Reuses one process."""

    def __init__(self, project):
        from respawn.envs.godot_hunt import Oracle
        self.Oracle = Oracle
        self.project = project
        self.g = GodotGame(project)
        self.runs = 0

    def run(self, seed, plays):
        self.runs += 1
        st = self.g.call("reset", seed=seed)
        self.g.take_errors()
        o = self.Oracle(self.g.info.get("config") or {}, st)
        last = None
        for segs in plays:
            if not segs:
                continue
            res = self.g.call("act", inputs=segs)
            o.judge(res, self.g.take_errors())
            o.score_frames += [ev["f"] for ev in res["events"] if ev["type"] == "score"]
            o.judge_score(res["state"], res["frames"][-1]["f"] if res["frames"] else 0)
            last = res
        settle = self.g.call("act", inputs=[{"hold": [], "frames": 150}])
        o.judge_score(settle["state"], settle["frames"][-1]["f"] if settle["frames"] else 0)
        return o, last, settle

    def close(self):
        self.g.close()


def minimize(rep: Reproducer, seed: int, plays: list, cls: str, budget: int = 40) -> list:
    """Greedy delta debugging: drop whole plays, then segments, then shorten frame counts."""
    def ok(p):
        if rep.runs >= budget:
            return False
        o, _, _ = rep.run(seed, p)
        return cls in o.found
    plays = [list(p) for p in plays]
    i = 0
    while i < len(plays) and len(plays) > 1:
        cand = plays[:i] + plays[i + 1:]
        if ok(cand):
            plays = cand
        else:
            i += 1
    for pi in range(len(plays)):
        j = 0
        while j < len(plays[pi]) and len(plays[pi]) > 1:
            cand = copy.deepcopy(plays)
            del cand[pi][j]
            if ok(cand):
                plays = cand
            else:
                j += 1
    for pi in range(len(plays)):
        for j in range(len(plays[pi])):
            while plays[pi][j]["frames"] > 2:
                cand = copy.deepcopy(plays)
                cand[pi][j]["frames"] = max(1, plays[pi][j]["frames"] * 2 // 3)
                if ok(cand):
                    plays = cand
                else:
                    break
    return plays


def build_report(project: str, finding: dict, out_dir: str, evidence: bool = True, budget: int = 40) -> dict:
    rep = Reproducer(project)
    try:
        cfg = rep.g.info.get("config") or {}
        letters = cfg.get("letters") or {}
        plays = minimize(rep, finding["seed"], finding["inputs"], finding["class"], budget)
        o, last, _ = rep.run(finding["seed"], plays)
        reproduced = finding["class"] in o.found
        hit = o.found.get(finding["class"], {"frame": finding.get("frame"), "detail": finding.get("detail")})
        st = (last or {}).get("state", {})
        frames = (last or {}).get("frames", [])
        at = min(frames, key=lambda f: abs(f["f"] - (hit["frame"] or 0))) if frames else st.get("player", {})
    finally:
        rep.close()
    suspects = []
    if finding["class"] == "crash" and " at " in (hit.get("detail") or ""):
        suspects.append(hit["detail"].split(" at ", 1)[1])
    if (st.get("player") or {}).get("script"):
        suspects.append(f"{st['player']['script']}: {LIKELY.get(finding['class'], 'player logic')}")
    rid = hashlib.sha1(json.dumps([finding["class"], project, finding["seed"], plays]).encode()).hexdigest()[:8]
    report = {
        "id": f"{finding['class']}-{rid}",
        "title": f"{finding['class'].replace('_', ' ').capitalize()}: {hit.get('detail', '')[:90]}",
        "bug_class": finding["class"],
        "game": os.path.abspath(project),
        "expected": EXPECTED.get(finding["class"], ""),
        "actual": hit.get("detail", ""),
        "repro": {"seed": finding["seed"], "plays": plays},
        "repro_tokens": [tokens_of(p, letters) for p in plays],
        "original_plays": len(finding["inputs"]),
        "minimized_plays": len(plays),
        "reproduced": reproduced,
        "frame": hit.get("frame"),
        "state_at_failure": {k: at.get(k) for k in ("x", "y", "vx", "vy", "floor", "wall", "visible") if k in at} | {"score": st.get("score")},
        "suspects": suspects,
        "evidence": {},
        "check": f"python -m respawn.qa check {os.path.join(out_dir, '')}{finding['class']}-{rid}.json",
    }
    os.makedirs(out_dir, exist_ok=True)
    if evidence and reproduced:
        from respawn.godot.evidence import capture
        ev = capture(project, {"class": finding["class"], "seed": finding["seed"], "inputs": plays,
                               "act_index": len(plays) - 1, "frame": hit.get("frame")}, os.path.join(out_dir, "evidence"))
        rel = {k: ev.get(k) and os.path.relpath(ev[k], out_dir) for k in ("gif", "png", "strip")}
        report["evidence"] = rel | {"strip_frames": ev.get("strip_frames", 0)}
    path = os.path.join(out_dir, report["id"] + ".json")
    json.dump(report, open(path, "w"), indent=2)
    open(os.path.join(out_dir, report["id"] + ".md"), "w").write(markdown(report))
    return report


def markdown(r: dict) -> str:
    img = f"![failure]({r['evidence']['gif']})\n\n" if r.get("evidence", {}).get("gif") else ""
    return f"""# {r['title']}

**Class:** `{r['bug_class']}`  **Game:** `{r['game']}`  **Reproduces:** {'yes' if r['reproduced'] else 'NO'}

{img}**Expected:** {r['expected']}

**Actual:** {r['actual']}

**Minimal repro** (seed {r['repro']['seed']}, {r['minimized_plays']} play(s), shrunk from {r['original_plays']}):
```
{chr(10).join(r['repro_tokens'])}
```
**State at failure (frame {r['frame']}):** `{json.dumps(r['state_at_failure'])}`

**Look here first:** {'; '.join(r['suspects']) or 'n/a'}

**Check (exit 0 = fixed):** `{r['check']}`
"""


def _game_dir(game: str, report_path: str) -> str:
    """`game` is relative to wherever the hunt ran; also try the repo root and the project that holds qa_reports/."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for cand in (os.path.abspath(game), os.path.join(repo, game),
                 os.path.abspath(os.path.join(os.path.dirname(report_path), ".."))):
        if os.path.exists(os.path.join(cand, "project.godot")):
            return cand
    return os.path.abspath(game)


def check(path: str) -> int:
    r = json.load(open(path))
    rep = Reproducer(_game_dir(r["game"], path))
    try:
        o, _, _ = rep.run(r["repro"]["seed"], r["repro"]["plays"])
    finally:
        rep.close()
    still = r["bug_class"] in o.found
    print(f"{r['id']}: {'STILL BROKEN' if still else 'FIXED'}" + (f" ({o.found[r['bug_class']]['detail']})" if still else ""))
    return 1 if still else 0


def hunt(project: str, genome_dir: str | None, seeds: list, out_dir: str, players: list, evidence=True) -> list:
    from respawn import env as envmod
    from respawn.genome import Genome
    from respawn.harness import run_many
    from respawn.llm import LLM
    project = os.path.abspath(project)
    name = {"platformer_train": "godot_hunt", "platformer_heldout": "godot_hunt_heldout", "dodge_heldout": "godot_hunt_dodge"}.get(
        os.path.basename(os.path.normpath(project)))
    if name is None:  # any other Godot project with the bridge installed
        from respawn.envs.godot_hunt import _make
        name = "godot_hunt_" + os.path.basename(os.path.normpath(project))
        _make(os.path.abspath(project), name)
    envmod.get(name)
    os.makedirs(out_dir, exist_ok=True)
    g = Genome.load(genome_dir) if genome_dir else Genome()
    eps = run_many(LLM(), name, seeds, g, players, parallel=min(4, len(seeds)))
    seen, reports = set(), []
    for ep in eps:
        for f in ep.outcome.get("findings", []):
            if f["class"] in seen:
                continue
            seen.add(f["class"])
            r = build_report(os.path.abspath(project), f, out_dir, evidence)
            print(f"[{r['bug_class']}] {r['title']}  repro {r['repro_tokens']}  -> {r['id']}.md", flush=True)
            reports.append(r)
    json.dump({"project": project, "genome": genome_dir, "seeds": seeds, "reports": [r["id"] for r in reports],
               "time": time.strftime("%Y-%m-%d %H:%M:%S")}, open(os.path.join(out_dir, "index.json"), "w"), indent=2)
    return reports


def main(argv=None):
    from respawn.__main__ import seeds as parse_seeds
    from respawn.llm import LIGHTNING, NANO
    ap = argparse.ArgumentParser(prog="respawn.qa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hunt")
    h.add_argument("--project", required=True)
    h.add_argument("--genome")
    h.add_argument("--seeds", default="0-3")
    h.add_argument("--out", default="qa_reports")
    h.add_argument("--players", default=f"{NANO},{LIGHTNING}")
    h.add_argument("--no-evidence", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("report")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return check(a.report)
    hunt(a.project, a.genome, parse_seeds(a.seeds), a.out, a.players.split(","), not a.no_evidence)
    return 0


if __name__ == "__main__":
    sys.exit(main())

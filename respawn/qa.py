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
    "bug_class": "crash | clip | infinite_jump | out_of_bounds | dupe | ghost_score | rule:<id> (a house rule)",
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

    def __init__(self, project, extra_rules: list | None = None):
        from respawn import rules as house_rules
        from respawn.envs.godot_hunt import Oracle
        self.Oracle = Oracle
        self.project = project
        self.g = GodotGame(project)
        self.runs = 0
        self.rules = house_rules.load_enabled(self.g.project)  # [] without res://qa_rules.json
        for r in extra_rules or []:  # e.g. the rule stored in a report, even if it was since edited/disabled
            self.rules = [x for x in self.rules if x["id"] != r["id"]] + [r]

    def run(self, seed, plays):
        self.runs += 1
        st = self.g.call("reset", seed=seed)
        self.g.take_errors()
        o = self.Oracle(self.g.info.get("config") or {}, st, self.rules)
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


def safe_class(cls: str) -> str:
    """Bug class as used in file names: 'rule:no_walls' -> 'rule-no_walls'."""
    return cls.replace(":", "-")


def severity(cls: str) -> str:
    """One place for bug severity (the editor dock mirrors this table)."""
    if cls == "crash":
        return "high"
    if cls in ("clip", "out_of_bounds") or cls.startswith("rule:"):
        return "medium"
    return "low"


def build_report(project: str, finding: dict, out_dir: str, evidence: bool = True, budget: int = 40) -> dict:
    from respawn import rules as house_rules
    rule = house_rules.rule_of(project, finding["class"])
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
        likely = LIKELY.get(finding["class"]) or ("the game logic this house rule is about" if rule else "player logic")
        suspects.append(f"{st['player']['script']}: {likely}")
    rid = hashlib.sha1(json.dumps([finding["class"], project, finding["seed"], plays]).encode()).hexdigest()[:8]
    sc = safe_class(finding["class"])
    title = (f"House rule {finding['class'][5:]}: {hit.get('detail', '')[:80]}" if rule
             else f"{finding['class'].replace('_', ' ').capitalize()}: {hit.get('detail', '')[:90]}")
    report = {
        "id": f"{sc}-{rid}",
        "title": title,
        "bug_class": finding["class"],
        "severity": severity(finding["class"]),
        "game": os.path.abspath(project),
        "expected": EXPECTED.get(finding["class"], "") or (f"House rule: {rule['text']}" if rule else ""),
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
        "check": f"python -m respawn.qa check {os.path.join(out_dir, '')}{sc}-{rid}.json",
    }
    if rule:  # keep the exact check with the report, so `qa check` judges the same rule later
        report["rule"] = {"id": rule.get("id"), "text": rule.get("text"), "code": rule.get("code")}
    os.makedirs(out_dir, exist_ok=True)
    if evidence and reproduced:
        from respawn.godot.evidence import capture
        ev = capture(project, {"class": finding["class"], "seed": finding["seed"], "inputs": plays,
                               "act_index": len(plays) - 1, "frame": hit.get("frame")}, os.path.join(out_dir, "evidence"))
        rel = {k: ev.get(k) and os.path.relpath(ev[k], out_dir) for k in ("gif", "png", "strip")}
        report["evidence"] = rel | {"strip_frames": ev.get("strip_frames", 0)}
    report["plain"] = plain(report, project)
    path = os.path.join(out_dir, report["id"] + ".json")
    json.dump(report, open(path, "w"), indent=2)
    open(os.path.join(out_dir, report["id"] + ".md"), "w").write(markdown(report))
    return report


# ------------------------------------------------------------------ plain English (no LLM: fast, deterministic)
WHY = {
    "crash": "The game's code stops working mid-play, so players can lose their progress or get stuck.",
    "clip": "Players can get stuck inside the level or fall through it.",
    "infinite_jump": "Players can fly over obstacles and skip parts of the level.",
    "out_of_bounds": "Players can leave the playable area and get lost or stuck.",
    "dupe": "Players can farm points, so scores and rewards stop meaning anything.",
    "ghost_score": "Scores become unfair: they go up without anyone playing.",
}


def _action_name(a: str) -> str:
    for pre in ("move_", "toggle_", "ui_"):
        if a.startswith(pre):
            a = a[len(pre):]
    return {"start_game": "Start"}.get(a, a.replace("_", " ").title())


def _tick_rate(project_dir: str | None) -> int:
    try:
        txt = open(os.path.join(project_dir, "project.godot")).read()
        m = __import__("re").search(r"physics_ticks_per_second=(\d+)", txt)
        return int(m.group(1)) if m else 60
    except (OSError, TypeError):
        return 60


def _secs(frames: int, hz: int) -> str:
    t = frames / hz
    return f"{max(0.1, round(t, 1)):g} s"


def repro_words(plays: list, hz: int = 60) -> str:
    """[[{hold:[move_right], frames:24}, ...]] -> 'hold Right for 0.4 s, then tap Jump twice, then press Pause'."""
    steps = []  # [kind, keys, frames, count]
    for seg in [s for p in plays for s in p]:
        keys = " + ".join(_action_name(a) for a in seg.get("hold", []))
        n = int(seg.get("frames", 1))
        if not keys:
            if n / hz < 0.1:  # a short gap just releases the keys
                continue
            kind = "wait"
        else:
            kind = "tap" if n / hz < 0.15 else "hold"
        if steps and steps[-1][0] == kind and steps[-1][1] == keys and kind in ("tap", "wait"):
            steps[-1][2] += n
            steps[-1][3] += 1
            continue
        steps.append([kind, keys, n, 1])
    words = []
    for kind, keys, n, c in steps:
        if kind == "wait":
            words.append(f"wait {_secs(n, hz)}")
        elif kind == "hold":
            words.append(f"hold {keys} for {_secs(n, hz)}")
        else:
            times = {1: "", 2: " twice"}.get(c, f" {c} times")
            words.append(("press " if keys in ("Pause", "Start") else "tap ") + keys + times)
    return ", then ".join(words) or "just start the level"


def plain(report: dict, project_dir: str | None = None) -> dict:
    """Plain-English summary of a report, from per-class templates filled with the report's real values."""
    import re
    cls = report.get("bug_class", "")
    project_dir = project_dir or report.get("game")
    cfg = {}
    try:
        cfg = json.load(open(os.path.join(project_dir, "qa_crew.json")))
    except (OSError, TypeError, ValueError):
        pass
    hz = _tick_rate(project_dir)
    st = report.get("state_at_failure") or {}
    actual = str(report.get("actual", ""))
    at = f"({st.get('x', 0):.0f}, {st.get('y', 0):.0f})" if "x" in st else ""
    plays = (report.get("repro") or {}).get("plays") or []
    how = repro_words(plays, hz)
    segs = [s for p in plays for s in p if s.get("hold")]
    last = _action_name(segs[-1]["hold"][-1]) if segs else ""
    air = st.get("floor") is False
    if cls == "crash":
        when = (f" when you press {last}" if last in ("Pause", "Start") else (f" right after you {('tap ' + last)}" if last else ""))
        head = f"The game hit a script error{when}{' in mid-air' if air else ''}."
        msg = actual.split(" at ", 1)[0]
        where = actual.split(" at ", 1)[1] if " at " in actual else ""
        loc = re.search(r"res://([\w./-]+\.gd):(\d+)", where)
        what = (f"Godot stopped running the script at {loc.group(1)} line {loc.group(2)}, so the rest of that code did not run."
                if loc else "Godot stopped running a script, so the rest of that code did not run.") + \
            f" The error was: {msg.rstrip('.')}."
    elif cls == "clip":
        how_land = " after a hard landing" if not segs or all(not s.get("hold") or s["hold"] == ["jump"] for s in segs) else ""
        if "inside" in actual:
            head = f"The player ended up inside the ground{how_land} at {at}."
        else:
            head = f"The player went through solid ground{how_land} at {at}."
        what = "The player's body moved into solid level geometry instead of stopping on it."
    elif cls == "infinite_jump":
        m = re.search(r"#(\d+)", actual)
        n = int(m.group(1)) if m else 2
        allowed = int(cfg.get("max_air_jumps", 1))
        head = f"You can keep jumping in mid-air: {n + 1} jumps before landing, but only {allowed} extra {'is' if allowed == 1 else 'are'} allowed."
        what = f"The player jumped {n} times in the air without touching the ground."
    elif cls == "out_of_bounds":
        head = "The player can leave the level."
        what = f"The player reached {at}, outside the level's edges."
    elif cls == "dupe":
        head = "The score went up more than the coins collected."
        what = actual[:1].upper() + actual[1:] + "."
    elif cls == "ghost_score":
        head = "The score keeps going up after the player died."
        what = "Points were added while the player was dead or hidden."
    elif cls.startswith("rule:"):
        rule = (report.get("rule") or {}).get("text") or str(report.get("expected", "")).replace("House rule: ", "")
        head = f"Broke your rule: {rule}"
        what = f"The automatic check for this rule found: {actual}."
    else:
        head, what = report.get("title", cls), actual
    seed = (report.get("repro") or {}).get("seed", 0)
    spawn = f" from spawn point {seed % len(cfg['spawns']) + 1}" if cfg.get("spawns") else ""
    return {"headline": head,
            "what_happened": what,
            "how_to_see_it": f"Start the level{spawn}, then {how}. Or press Replay in game.",
            "why_it_matters": WHY.get(cls, "You said this must never happen in your game." if cls.startswith("rule:") else "")}


def markdown(r: dict) -> str:
    img = f"![failure]({r['evidence']['gif']})\n\n" if r.get("evidence", {}).get("gif") else ""
    pl = r.get("plain") or plain(r)
    return f"""# {pl['headline']}

{img}**What happened:** {pl['what_happened']}

**How to see it:** {pl['how_to_see_it']}

**Why it matters:** {pl['why_it_matters']}

## Details for AI agents

### {r['title']}

**Class:** `{r['bug_class']}`  **Game:** `{r['game']}`  **Reproduces:** {'yes' if r['reproduced'] else 'NO'}

**Expected:** {r['expected']}

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
    extra = []
    if r.get("rule", {}).get("code"):
        from respawn import rules as house_rules
        extra = [{"id": r["rule"]["id"], "text": r["rule"].get("text", ""),
                  "fn": house_rules.compile_check(r["rule"]["code"], r["rule"]["id"])}]
    rep = Reproducer(_game_dir(r["game"], path), extra)
    try:
        o, _, _ = rep.run(r["repro"]["seed"], r["repro"]["plays"])
    finally:
        rep.close()
    still = r["bug_class"] in o.found
    print(f"{r['id']}: {'STILL BROKEN' if still else 'FIXED'}" + (f" ({o.found[r['bug_class']]['detail']})" if still else ""))
    return 1 if still else 0


def env_for_project(project: str) -> str:
    """The registered env name for a Godot project folder (registering one for any other bridged project)."""
    project = os.path.abspath(project)
    name = {"platformer_train": "godot_hunt", "platformer_heldout": "godot_hunt_heldout", "dodge_heldout": "godot_hunt_dodge",
            "pixel_adventure": "godot_hunt_pixel", "pixel_clean": "godot_hunt_pixel_clean",
            "ev01_prefix": "godot_hunt_ev01_prefix", "ev01_fixed": "godot_hunt_ev01_fixed"}.get(
        os.path.basename(os.path.normpath(project)))
    if name is None:  # any other Godot project with the bridge installed
        from respawn.envs.godot_hunt import _make
        name = "godot_hunt_" + os.path.basename(os.path.normpath(project))
        _make(project, name)
    return name


def hunt(project: str, genome_dir: str | None, seeds: list, out_dir: str, players: list, evidence=True,
         watch=False, mock=False) -> list:
    from respawn import env as envmod
    from respawn.genome import Genome
    from respawn.harness import run_episode
    from respawn.llm import LIGHTNING, LLM, NANO, SUPER
    project = os.path.abspath(project)
    name = env_for_project(project)
    envmod.get(name)
    os.makedirs(out_dir, exist_ok=True)
    g = Genome.load(genome_dir) if genome_dir else Genome()
    llm = LLM(mock=mock or None)
    import threading
    done, lock = [0], threading.Lock()
    print(f"PROGRESS hunt 0/{len(seeds)}", flush=True)  # parsed by the editor dock

    def tick(ep):
        with lock:
            done[0] += 1
            print(f"PROGRESS hunt {done[0]}/{len(seeds)} bugs={','.join(ep.outcome.get('bugs', [])) or '-'}", flush=True)
        return ep
    if watch:  # the first seed plays in a visible real-time window with captions; the rest stay headless
        from concurrent.futures import ThreadPoolExecutor
        short = {NANO: "Nano", LIGHTNING: "Lightning", SUPER: "Super"}
        label = "Hunter " + short.get(players[seeds[0] % len(players)], players[seeds[0] % len(players)].split("/")[-1])
        kw = lambda s: {"watch": True, "watch_label": label, "house_rules": True} if s == seeds[0] else {"house_rules": True}
        with ThreadPoolExecutor(min(4, len(seeds))) as ex:
            eps = list(ex.map(lambda s: tick(run_episode(llm, name, s, g, players, env_kwargs=kw(s))), seeds))
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max(1, min(4, len(seeds)))) as ex:
            eps = list(ex.map(lambda s: tick(run_episode(llm, name, s, g, players, env_kwargs={"house_rules": True})), seeds))
    seen, reports, todo = set(), [], []
    for ep in eps:
        for f in ep.outcome.get("findings", []):
            if f["class"] not in seen:
                seen.add(f["class"])
                todo.append(f)
    print(f"PROGRESS verify 0/{len(todo)}", flush=True)
    for i, f in enumerate(todo):
        r = build_report(os.path.abspath(project), f, out_dir, evidence)
        print(f"[{r['bug_class']}] {r['plain']['headline']}  repro {r['repro_tokens']}  -> {r['id']}.md", flush=True)
        print(f"PROGRESS verify {i + 1}/{len(todo)}", flush=True)
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
    h.add_argument("--players", default=os.environ.get("QA_PLAYERS") or f"{NANO},{LIGHTNING}")  # QA_PLAYERS: the dock's default crew models
    h.add_argument("--no-evidence", action="store_true")
    h.add_argument("--watch", action="store_true", help="first session plays in a visible, captioned, real-time game window")
    h.add_argument("--mock", action="store_true", help="offline: scripted hunter, no API calls")
    c = sub.add_parser("check")
    c.add_argument("report")
    r = sub.add_parser("rules", help="house rules: compile plain-English rules into oracle checks")
    r.add_argument("action", choices=["compile", "list", "test", "suggest"])
    r.add_argument("--n", type=int, default=8, help="suggest: how many rules to propose")
    r.add_argument("--project", required=True)
    r.add_argument("--force", action="store_true", help="recompile rules that already have code")
    r.add_argument("--mock", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return check(a.report)
    if a.cmd == "rules":
        from respawn import rules as house_rules
        if a.action == "suggest":
            out = house_rules.suggest_project(a.project, n=a.n, mock=a.mock)
            return 0 if any(x.get("status") == "new" for x in out) else 1
        if a.action == "compile":
            out = house_rules.compile_project(a.project, mock=a.mock, force=a.force)
            return 0 if out and all(x.get("code") and not x.get("error") for x in out) else 1
        for x in house_rules.load_all(os.path.abspath(a.project)):
            prob = house_rules.test_rule(x["code"], x["id"], None) if a.action == "test" and x.get("code") else None
            print(f"{'on ' if x.get('enabled', True) else 'off'} {x.get('id', '?'):28} {'compiled' if x.get('code') else 'NOT COMPILED'}"
                  f"{'  ' + prob if prob else ''}  {x['text']}")
        return 0
    hunt(a.project, a.genome, parse_seeds(a.seeds), a.out, a.players.split(","), not a.no_evidence, a.watch, a.mock)
    return 0


if __name__ == "__main__":
    sys.exit(main())

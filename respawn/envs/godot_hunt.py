"""Bug hunting in a real Godot game: Nemotron plays through the QA Crew bridge and tries to break it.

The hunter never sees the game's source or the oracle. It sees what a human tester would:
the level layout, the player's position/velocity over time, the score, and the error console.
A bug only counts when the oracle confirms it from the recorded trace, and every finding is
stored as (seed, inputs) so `replay()` can reproduce it in a fresh game process.
"""
from __future__ import annotations

import json
import os
import re

from respawn import sandbox
from respawn.env import register
from respawn.godot.client import GodotGame

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "godot", "games"))
BUG_CLASSES = {
    "crash": "a script error (the game logged SCRIPT ERROR)",
    "clip": "the player passed through or ended up inside solid geometry",
    "infinite_jump": "more mid-air jumps than the game allows",
    "out_of_bounds": "the player left the level bounds",
    "dupe": "the score went higher than the number of collectibles taken",
    "ghost_score": "the score changed while the player was dead or hidden",
}
TOKEN = re.compile(r"([A-Za-z]+)(\d+)")
SETTLE_FRAMES = 150


def parse_inputs(text: str, controls: dict) -> list:
    """'R30 RJ12 J8 W10' -> bridge segments. Letters are held for the frame count; W = no keys."""
    segs = []
    for m in TOKEN.finditer(str(text)):
        keys, n = m.group(1).upper(), max(1, min(240, int(m.group(2))))
        hold = [controls[k] for k in keys if k in controls]
        segs.append({"hold": hold, "frames": n})
        if len(segs) >= 24:
            break
    return segs


class Oracle:
    """Judges bug classes from bridge traces. Knows the game's public rules (config), not its code."""

    def __init__(self, cfg: dict, start_state: dict):
        self.max_air_jumps = int(cfg.get("max_air_jumps", 1))
        self.start_collectibles = start_state.get("collectibles_left", -1)
        self.start_score = start_state.get("score") or 0  # e.g. a spawn point that lands on a coin
        self.found: dict = {}            # class -> {"frame", "detail"}
        self.air_jumps = 0
        self.prev = start_state.get("player") or {}
        self.score_frames: list = []     # frames of score increments (collect animations take ~60 frames)

    def _hit(self, cls, frame, detail):
        if cls not in self.found:
            self.found[cls] = {"frame": frame, "detail": detail}
            return [cls]
        return []

    def judge(self, res: dict, errors: list) -> list:
        new = []
        for e in errors:
            if e["kind"] == "SCRIPT ERROR":
                new += self._hit("crash", res["frames"][-1]["f"] if res["frames"] else 0, f"{e['message']} at {e['where']}")
        b = res["state"].get("bounds") or {}
        for f in res["frames"]:
            if f.get("tunnel") or f.get("inside"):
                new += self._hit("clip", f["f"], f"{'through' if f.get('tunnel') else 'inside'} geometry at ({f['x']:.0f},{f['y']:.0f})")
            if f.get("floor"):
                self.air_jumps = 0
            elif "vy" in f and "vy" in self.prev and f["vy"] < -400 and f["vy"] - self.prev["vy"] < -400 and not self.prev.get("floor"):
                self.air_jumps += 1
                if self.air_jumps > self.max_air_jumps:
                    new += self._hit("infinite_jump", f["f"], f"air jump #{self.air_jumps} at ({f['x']:.0f},{f['y']:.0f})")
            if b and (f["x"] < b["left"] or f["x"] > b["right"] or f["y"] < b["top"] or f["y"] > b["bottom"]):
                new += self._hit("out_of_bounds", f["f"], f"at ({f['x']:.0f},{f['y']:.0f}), bounds {b}")
            self.prev = f
        vis = {f["f"]: f.get("visible", True) for f in res["frames"]}
        for ev in res.get("events", []):
            if ev["type"] == "score" and ev.get("from") is not None and vis.get(ev["f"]) is False:
                new += self._hit("ghost_score", ev["f"], f"score {ev['from']} -> {ev['to']} while the player was hidden/dead")
        return new

    def judge_score(self, state: dict, frame: int) -> list:
        sc, left = state.get("score"), state.get("collectibles_left", -1)
        if sc is None or left < 0 or self.start_collectibles < 0:
            return []
        taken = self.start_collectibles - left
        pending = sum(1 for f in self.score_frames if f > frame - 150)  # pickups still animating (~2 s)
        if sc - self.start_score > taken + pending:
            return self._hit("dupe", frame, f"score {sc} but only {taken} collectibles were taken")
        return []


class GodotHuntEnv:
    agents = ["hunter"]
    max_decisions = 14
    tool_lang = "python"
    project = "platformer_train"
    default_action = ("wait", {})

    def __init__(self, seed: int, tools: dict | None = None, record: bool = False, watch: bool = False,
                 watch_label: str = "Hunter", **_kw):
        self.seed = seed
        self.watch, self.watch_label = watch, watch_label  # watch: visible real-time window with captions
        self.note = ""                   # the agent's reasoning for its next action (set by the harness)
        self.game = GodotGame(os.path.join(ROOT, self.project), watch=watch)
        self.cfg = self.game.info.get("config") or {}
        self.controls = {k: v for k, v in (self.cfg.get("letters") or {}).items()}
        if not self.controls:  # no qa_crew.json: guess from the game's InputMap
            acts = set(self.game.info.get("actions") or [])
            guess = {"R": ["move_right", "right", "ui_right"], "L": ["move_left", "left", "ui_left"],
                     "U": ["move_up", "up", "ui_up"], "D": ["move_down", "down", "ui_down"], "J": ["jump"],
                     "S": ["shoot", "fire", "attack"], "P": ["toggle_pause", "pause"], "E": ["start_game", "ui_accept"]}
            for k, names in guess.items():
                hit = next((n for n in names if n in acts), None)
                if hit:
                    self.controls[k] = hit
            self.cfg["letters"] = self.controls
        self.toolbox = sandbox.Toolbox(tools or {})
        self.state = self.game.call("reset", seed=seed)
        self.map = self.game.call("map", cell=32)
        self.oracle = Oracle(self.cfg, self.state)
        self.inputs: list = []           # full input history since the last reset (the repro)
        self.findings: list = []         # [{class, detail, seed, inputs}]
        self.plays = 0
        self.last = ""
        self.record = record
        self.trail: list = []
        self.done = False

    # ---------------------------------------------------------------- Env protocol
    @classmethod
    def rules_text(cls, cfg: dict) -> str:
        letters = ", ".join(f"{k}={v}" for k, v in (cfg.get("letters") or {}).items())
        return f"""You are a QA tester for a Godot game: {cfg.get('description', 'a 2D game')}.
Your job is to FIND BUGS, not to win. You score 10 points for each distinct bug class you trigger:
""" + "\n".join(f"- {k}: {v}" for k, v in BUG_CLASSES.items()) + f"""
Each found bug is verified automatically by replaying your exact inputs, so only real bugs count.

Controls: call play(inputs) with a string of tokens KEYS+FRAMES, e.g. "R30 RJ20 W5 J10".
Keys held during the token: {letters}. W means no keys. 60 frames = 1 second.
""" + (f"""A jump starts when J goes from released to pressed; holding J longer jumps higher.
Two consecutive tokens that both hold J do NOT re-press it; put a W1 between them.
The game allows {cfg.get('max_air_jumps', 1)} mid-air jump(s) (double jump), more is a bug.
""" if "J" in (cfg.get("letters") or {}) else "") + """reset() restarts the level from the spawn point (inputs restart too)."""

    def action_schemas(self, agent):
        return [
            {"name": "play", "description": "Send inputs to the game, e.g. 'R40 RJ20 W1 RJ10'. Returns what happened.",
             "parameters": {"type": "object", "properties": {"inputs": {"type": "string"}}, "required": ["inputs"]}},
            {"name": "reset", "description": "Restart the level from the spawn point.", "parameters": {"type": "object", "properties": {}}},
        ]

    def helper_schemas(self, agent):
        return self.toolbox.schemas()

    def helper(self, agent, name, args):
        return self.toolbox.call(name, self.observation(), args)

    @classmethod
    def validate_tool(cls, source: str) -> dict:
        sample = json.load(open(os.path.join(os.path.dirname(__file__), "godot_sample_obs.json")))
        try:
            return sandbox.validate(source, sample)
        except sandbox.ToolError as e:
            raise ValueError(str(e)) from e

    tool_guide = """A helper tool is one Python file (stdlib only: math, heapq, collections, itertools, functools, json, statistics).
It must define:
  TOOL = {"name": "snake_case_name", "description": "...", "parameters": {JSON schema of kwargs}}
  def run(obs, **kwargs): return a small JSON-serialisable result (e.g. a suggested inputs string)
  def test(sample_obs): assert the tool works on a real observation (raise on failure)
obs keys: map {cell, left, top, rows: list of strings, '#' solid '.' empty, row 0 = top}, player {x,y,vx,vy,floor,wall},
bounds {left,top,right,bottom}, enemies [[x,y]], score, collectibles_left, found (list of bug classes already found),
history (list of {inputs, summary}). World y grows downward. Map cell (c, r) covers x = left + c*cell, y = top + r*cell.
Helpers are free for the tester (they take no game time). No file, network or os access."""

    def observation(self, agent="hunter"):
        return {"map": self.map, "player": self.state.get("player"), "bounds": self.state.get("bounds"),
                "enemies": self.state.get("enemies"), "score": self.state.get("score"),
                "collectibles_left": self.state.get("collectibles_left"), "found": sorted(self.oracle.found),
                "history": self.trail[-6:]}

    def render(self, agent="hunter"):
        p = self.state.get("player") or {}
        m = self.map
        cell = m.get("cell", 32)
        rows = [list(r) for r in m.get("rows", [])]
        c, r = int((p.get("x", 0) - m["left"]) // cell), int((p.get("y", 0) - m["top"]) // cell)
        if 0 <= r < len(rows) and 0 <= c < len(rows[0]):
            rows[r][c] = "@"
        for ex, ey in self.state.get("enemies") or []:
            ec, er = int((ex - m["left"]) // cell), int((ey - m["top"]) // cell)
            if 0 <= er < len(rows) and 0 <= ec < len(rows[0]):
                rows[er][ec] = "e"
        grid = "\n".join("".join(rr) for rr in rows)
        found = ", ".join(sorted(self.oracle.found)) or "none yet"
        return (f"Level map ({cell}px cells, x from {m['left']}, y from {m['top']}; # solid, @ you, e enemy):\n{grid}\n\n"
                f"Player: x {p.get('x')}, y {p.get('y')}, vx {p.get('vx')}, vy {p.get('vy')}, on_floor {p.get('floor')}, on_wall {p.get('wall')}\n"
                f"Bounds: {self.state.get('bounds')}. Score {self.state.get('score')}, collectibles left {self.state.get('collectibles_left')}.\n"
                f"Plays left: {self.max_decisions - self.plays}. Bugs confirmed so far: {found}.\nLast result: {self.last}")

    def _live(self, what: str, extra: str = "") -> None:
        """Watch mode: caption the game window and stream the move to stdout (the editor dock shows LIVE lines)."""
        if not self.watch:
            return
        line = f"{self.watch_label} · {what} · bugs: {', '.join(sorted(self.oracle.found)) or 'none yet'}" + extra
        why = " ".join(self.note.split())
        why = why[:140] + ("…" if len(why) > 140 else "")
        print("LIVE " + line + (f"  ({why})" if why else ""), flush=True)
        try:
            self.game.call("say", text=line + (f"\n{why}" if why else ""))
        except Exception:
            pass

    def act(self, agent, name, args):
        self.plays += 1
        self._live(f"play {args.get('inputs', '')}" if name == "play" else name)
        if name == "reset":
            self.state = self.game.call("reset", seed=self.seed)
            self.oracle.prev = self.state.get("player") or {}
            self.oracle.air_jumps = 0
            self.oracle.start_collectibles = self.state.get("collectibles_left", -1)
            self.oracle.start_score = self.state.get("score") or 0
            self.oracle.score_frames = []
            self.inputs = []
            self.last = "level restarted"
            return self.last
        if name != "play":
            self.last = "waited"
            return self.last
        segs = parse_inputs(args.get("inputs", ""), self.controls)
        if not segs:
            self.last = "error: no valid tokens; use e.g. 'R30 RJ20 W5'"
            return self.last
        res = self.game.call("act", inputs=segs)
        errors = self.game.take_errors()
        self.inputs.append(segs)
        self.state = res["state"]
        new = self.oracle.judge(res, errors)
        for ev in res["events"]:
            if ev["type"] == "score":
                self.oracle.score_frames.append(ev["f"])
        new += self.oracle.judge_score(self.state, res["frames"][-1]["f"] if res["frames"] else 0)
        for cls in new:
            self.findings.append({"class": cls, "detail": self.oracle.found[cls]["detail"], "seed": self.seed,
                                  "frame": self.oracle.found[cls]["frame"], "act_index": len(self.inputs) - 1,
                                  "inputs": list(self.inputs), "project": self.project})
        fr = res["frames"]
        xs, ys = [f["x"] for f in fr], [f["y"] for f in fr]
        maxstep = max((abs(b["x"] - a["x"]) + abs(b["y"] - a["y"]) for a, b in zip(fr, fr[1:])), default=0)
        path = " ".join(f"({f['x']:.0f},{f['y']:.0f})" for f in fr[::max(1, len(fr) // 8)])
        ev = res["events"]
        scores = [e for e in ev if e["type"] == "score"]
        err_txt = "; ".join(f"{e['kind']}: {e['message']}" for e in errors)[:300]
        self.last = (f"played {len(fr)} frames. path {path}. x range {min(xs):.0f}..{max(xs):.0f}, y range {min(ys):.0f}..{max(ys):.0f}, "
                     f"biggest single-frame move {maxstep:.0f}px. score changes: {len(scores)}."
                     + (f" CONSOLE: {err_txt}." if err_txt else "")
                     + (f" NEW BUG CONFIRMED: {', '.join(new)}." if new else ""))
        self.trail.append({"inputs": args.get("inputs", ""), "summary": self.last[:300]})
        if new:
            self._live(f"play {args.get('inputs', '')}", f" · NEW BUG: {', '.join(new)}")
        return self.last

    @property
    def over(self):
        return self.plays >= self.max_decisions or len(self.oracle.found) == len(BUG_CLASSES)

    def _settle(self):
        if self.done:
            return
        self.done = True
        try:
            res = self.game.call("act", inputs=[{"hold": [], "frames": SETTLE_FRAMES}])
            self.inputs.append([{"hold": [], "frames": SETTLE_FRAMES}])
            new = self.oracle.judge_score(res["state"], res["frames"][-1]["f"] if res["frames"] else 0)
            for cls in new:
                self.findings.append({"class": cls, "detail": self.oracle.found[cls]["detail"], "seed": self.seed, "inputs": list(self.inputs)})
        except Exception:
            pass

    def score(self):
        self._settle()
        return 10 * len(self.oracle.found)

    def outcome(self):
        self._settle()
        return {"score": self.score(), "bugs": sorted(self.oracle.found), "details": {k: v["detail"][:120] for k, v in self.oracle.found.items()},
                "plays": self.plays, "findings": self.findings}

    def replay(self):
        return self.findings

    def close(self):
        self.game.close()

    # ---------------------------------------------------------------- offline mock
    def mock_policy(self, agent, playbook, call_tool):
        pb = playbook.lower()
        script = ["R60", "L40 LJ30 W1 LJ20", "R200"]
        if "wall" in pb:
            script.insert(0, "L120 LJ20 W1 LJ20 W1 LJ20 W1 LJ20 W1 LJ20 W1 LJ20")
        if "shoot" in pb:
            script.insert(0, "W40 J20 W1 J5 S5")
        self.note = f"(mock) scripted probe {self.plays % len(script) + 1}/{len(script)}: run, jump at walls, look for clips"
        return "play", {"inputs": script[self.plays % len(script)]}

    @staticmethod
    def mock_proposal(n, tools):
        props = [
            {"diagnosis": "Never tried shooting in the air.", "playbook_append": "- Try shooting right after a double jump: 'W40 J20 W1 J5 S5'."},
            {"diagnosis": "Walls are unexplored.", "playbook_append": "- Hug a wall and jump repeatedly (wall jumps) to test jump limits."},
        ]
        return props[n % len(props)]


def verify(project: str, finding: dict) -> bool:
    """Re-run a finding's (seed, inputs) in a fresh game process and confirm the same bug class."""
    g = GodotGame(os.path.join(ROOT, project))
    try:
        st = g.call("reset", seed=finding["seed"])
        o = Oracle(g.info.get("config") or {}, st)
        for segs in finding["inputs"]:  # one bridge call per original play, exactly as recorded
            res = g.call("act", inputs=segs)
            o.judge(res, g.take_errors())
            o.score_frames += [ev["f"] for ev in res["events"] if ev["type"] == "score"]
            o.judge_score(res["state"], res["frames"][-1]["f"] if res["frames"] else 0)
        return finding["class"] in o.found
    finally:
        g.close()


def _make(project_name: str, env_name: str):
    cls = type(f"Hunt_{env_name}", (GodotHuntEnv,), {"project": project_name, "name": env_name})
    cfg_path = os.path.join(ROOT, project_name, "qa_crew.json")
    cls.rules = GodotHuntEnv.rules_text(json.load(open(cfg_path))) if os.path.exists(cfg_path) else ""
    register(env_name)(cls)
    return cls


HuntTrain = _make("platformer_train", "godot_hunt")
HuntHeldout = _make("platformer_heldout", "godot_hunt_heldout")
HuntDodge = _make("dodge_heldout", "godot_hunt_dodge")

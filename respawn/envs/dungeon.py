"""Adapter: the seeded dungeon crawler as an Env (placeholder game used to test the loop)."""
from __future__ import annotations

import json

from respawn import sandbox
from respawn.env import register
from respawn.envs.dungeon_engine import GAME_ACTIONS, MAX_FLOORS, TURN_LIMIT, Game, manhattan

TOOL_GUIDE = """A helper tool is one Python file (stdlib only: math, heapq, collections, itertools, functools, json, statistics).
It must define:
  TOOL = {"name": "snake_case_name", "description": "...", "parameters": {JSON schema of kwargs}}
  def run(obs, **kwargs): return a small JSON-serialisable result
  def test(sample_obs): assert the tool works on a real observation (raise on failure)
obs keys: floor, turn, turn_limit, width, height, grid (list of strings: '#' wall, '.' floor, '+' locked door, ' ' void),
player {x,y,hp,max_hp,atk,potions,gold,has_key}, monsters [{id,kind,x,y,hp,atk,slow,awake}], items [{kind,x,y}],
stairs {x,y}, doors [{x,y}]. Helpers are free for the player (they take no game turn). No file, network or os access."""

RULES = f"""You control '@' in a grid dungeon with {MAX_FLOORS} floors. Reach '>' on each floor and call descend.
Legend: # wall, . floor, + locked door (walk into it holding the key), > stairs, k key, ! potion, $ gold, / sword (+2 atk),
r rat, g goblin, O ogre (very strong, slow: moves every other turn). Coordinates are (x,y), x = column, y = row, (0,0) top-left.
Combat is deterministic: you deal your atk per hit, each adjacent awake monster deals its atk to you every turn.
Monsters wake when within 6 tiles and chase you. Turn limit {TURN_LIMIT}.
Score = 1000 per floor cleared + 10 per gold - turns used - 250 if you die."""


@register("dungeon")
class DungeonEnv:
    name = "dungeon"
    rules = RULES
    agents = ["hero"]
    max_decisions = 45
    tool_lang = "python"
    tool_guide = TOOL_GUIDE

    def __init__(self, seed: int, tools: dict | None = None, record: bool = False, **_kw):
        self.seed = seed
        self.g = Game(seed, record=record)
        self.toolbox = sandbox.Toolbox(tools or {})
        if record:
            self.g._snap()

    @classmethod
    def validate_tool(cls, source: str) -> dict:
        try:
            return sandbox.validate(source, Game(424242).observation())
        except sandbox.ToolError as e:
            raise ValueError(str(e)) from e

    def action_schemas(self, agent):
        return GAME_ACTIONS

    def helper_schemas(self, agent):
        return self.toolbox.schemas()

    def helper(self, agent, name, args):
        return self.toolbox.call(name, self.g.observation(), args)

    def close(self):
        pass

    def observation(self, agent="hero") -> dict:
        return self.g.observation()

    def render(self, agent="hero") -> str:
        g = self.g
        mons = ", ".join(f"{m.kind}#{m.id} at ({m.x},{m.y}) hp{m.hp} atk{m.atk}{' slow' if m.speed > 1 else ''}"
                         for m in g.monsters) or "none"
        items = ", ".join(f"{i.kind} at ({i.x},{i.y})" for i in g.items) or "none"
        cols = "".join(str(x % 10) for x in range(len(g.grid[0])))
        rows = "\n".join(f"{y:2d} {line}" for y, line in enumerate(g.ascii().split("\n")))
        return (f"Floor {g.floor}/{MAX_FLOORS}  turn {g.turn}/{TURN_LIMIT}\n"
                f"You: ({g.px},{g.py}) hp {g.hp}/{g.max_hp} atk {g.atk} potions {g.potions} gold {g.gold} key {'yes' if g.has_key else 'no'}\n"
                f"Stairs: {g.stairs}\nMonsters: {mons}\nItems: {items}\n\n   {cols}\n{rows}")

    def act(self, agent, name, args):
        return self.g.act(name, args)

    @property
    def over(self):
        return self.g.over

    def score(self):
        return self.g.score()

    def outcome(self):
        g = self.g
        return {"score": g.score(), "won": g.won, "dead": g.dead, "floors_cleared": g.floors_cleared,
                "gold": g.gold, "turns": g.turn, "cause": g.death_cause or ("escaped" if g.won else "decision budget used up")}

    def replay(self):
        return self.g.frames

    # ---------------------------------------------------------------- offline mock policy
    def mock_policy(self, agent: str, playbook: str, call_tool) -> tuple:
        """Greedy heuristic whose behaviour is switched on by playbook phrases (offline testing only)."""
        o = self.g.observation()
        p = o["player"]
        pb = playbook.lower()
        here = (p["x"], p["y"])
        stairs = (o["stairs"]["x"], o["stairs"]["y"])
        if "potion" in pb and p["hp"] <= 5 and p["potions"] > 0:
            return "use_potion", {}
        if here == stairs:
            return "descend", {}
        if "ogre" in pb and "safe_route" in self.toolbox.names:
            res = call_tool("safe_route", {"x": stairs[0], "y": stairs[1]})
            if isinstance(res, dict) and res.get("next"):
                return "move_to", {"x": res["next"][0], "y": res["next"][1]}
        if "key" in pb and o["doors"] and not p["has_key"]:
            k = next((i for i in o["items"] if i["kind"] == "key"), None)
            if k:
                return "move_to", {"x": k["x"], "y": k["y"]}
        if "fight" in pb:
            weak = [m for m in o["monsters"] if m["kind"] != "ogre" and manhattan((m["x"], m["y"]), here) <= 3]
            if weak:
                return "fight", {"monster_id": weak[0]["id"]}
        return "move_to", {"x": stairs[0], "y": stairs[1]}

    @staticmethod
    def mock_proposal(n: int, champion_tools: dict) -> dict:
        """Canned coach proposals for offline runs (index n, cycles)."""
        p = dict(MOCK_PROPOSALS[n % len(MOCK_PROPOSALS)])
        if p.pop("tool", None):
            p["tool"] = {"filename": "safe_route.py", "source": SAFE_ROUTE_TOOL}
        return p


MOCK_PROPOSALS = [
    {"diagnosis": "Runs die at low hp while holding unused potions.", "playbook_append": "- Drink a potion (use_potion) whenever hp <= 5 and you have one."},
    {"diagnosis": "Bad idea on purpose: charge everything.", "playbook_append": "- Always fight every monster you see, including ogres."},
    {"diagnosis": "Runs stall at the locked door without the key.", "playbook_append": "- If a locked door exists and you have no key, fetch the key first."},
    {"diagnosis": "Ogres kill the player on the direct route.", "playbook_append": "- Never path next to an ogre; call safe_route to get a waypoint that keeps distance from ogres.",
     "tool": "safe_route"},
    {"diagnosis": "Goblins chip hp on the way; fight weak monsters that are close.", "playbook_append": "- fight rats and goblins within 3 tiles before moving on."},
]

SAFE_ROUTE_TOOL = '''
TOOL = {
    "name": "safe_route",
    "description": "Path to (x,y) that avoids tiles within 2 of an ogre. Returns the next waypoint (up to 6 steps ahead) and the path length.",
    "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]},
}

from collections import deque

def run(obs, x, y):
    grid = obs["grid"]
    p = obs["player"]
    start, goal = (p["x"], p["y"]), (int(x), int(y))
    danger = set()
    for m in obs["monsters"]:
        if m["kind"] == "ogre":
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if abs(dx) + abs(dy) <= 2:
                        danger.add((m["x"] + dx, m["y"] + dy))
    blocked = {(m["x"], m["y"]) for m in obs["monsters"]}
    def ok(c):
        t = grid[c[1]][c[0]] if 0 <= c[1] < len(grid) and 0 <= c[0] < len(grid[0]) else "#"
        return t == "." or (t == "+" and p["has_key"])
    for avoid in (danger, set()):
        prev = {start: None}
        q = deque([start])
        while q:
            c = q.popleft()
            if c == goal:
                break
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + d[0], c[1] + d[1])
                if n in prev or not ok(n) or (n in avoid and n != goal) or (n in blocked and n != goal):
                    continue
                prev[n] = c
                q.append(n)
        if goal in prev:
            path = []
            c = goal
            while c != start:
                path.append(c)
                c = prev[c]
            path.reverse()
            nxt = path[min(5, len(path) - 1)] if path else goal
            return {"next": list(nxt), "length": len(path), "avoided_ogres": bool(avoid)}
    return {"next": None, "length": -1, "avoided_ogres": False}

def test(sample_obs):
    p = sample_obs["player"]
    r = run(sample_obs, p["x"], p["y"])
    assert r["length"] in (0, -1)
    s = sample_obs["stairs"]
    r = run(sample_obs, s["x"], s["y"])
    assert r["next"] is None or len(r["next"]) == 2
'''

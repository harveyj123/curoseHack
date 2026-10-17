"""House rules: plain-English rules a developer types ("the robot should never pass through walls"),
compiled by Nemotron Super into a small Python check that the oracle runs on every physics frame.

Stored per game in res://qa_rules.json:
    [{"text": "...", "id": "slug", "code": "def check(prev, cur, ctx): ...", "enabled": true, "compiled_by": "model"}]

    python -m respawn.qa rules compile --project godot/games/platformer_heldout

A check is `def check(prev, cur, ctx)` returning None (fine) or a short violation string. It is validated
with a strict AST allowlist (no imports, no loops that can hang, no dunders), run with a tiny builtins set,
tested on a recorded clean trace (must not fire) and on the model's own violating example (must fire).
"""
from __future__ import annotations

import ast
import json
import os
import re

RULES_FILE = "qa_rules.json"
FIXTURE = os.path.join(os.path.dirname(__file__), "envs", "godot_rules_fixture.json")
SAFE_BUILTINS = {"abs": abs, "min": min, "max": max, "len": len, "round": round, "int": int, "float": float,
                 "bool": bool, "str": str, "sum": sum, "any": any, "all": all, "True": True, "False": False, "None": None}
BANNED_NODES = (ast.Import, ast.ImportFrom, ast.While, ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Yield,
                ast.YieldFrom, ast.Global, ast.Nonlocal, ast.With, ast.AsyncWith, ast.AsyncFor, ast.Raise, ast.Delete)
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                "delattr", "input", "breakpoint", "exit", "quit", "type", "object", "print", "range", "iter", "next"}

FIELDS = """prev, cur: one physics frame each (60 frames = 1 s), dicts with
  f (frame number), x, y (player position in px; y grows DOWNWARD, so going up = y decreasing),
  vx, vy (velocity px/s; vy < 0 = moving up), floor (bool, standing on floor), wall (bool, touching a wall),
  visible (bool, False while dead/hidden), tunnel (True only on a frame where the body passed THROUGH solid
  geometry since the previous frame), inside (True only when the body overlaps solid geometry).
  tunnel/inside are absent when false, so use cur.get("tunnel"). prev is {} on the first frame of a run.
  A jump (from the ground OR mid-air) shows as a sudden upward kick on one frame: cur["vy"] < -600 and
  cur["vy"] - prev.get("vy", 0) < -300 (gravity alone changes vy by +17.5 per frame). Walking off an edge is
  not a jump. ctx["max_air_jumps"] counts MID-AIR jumps only: the jump off the ground is not one of them
  (on its take-off frame prev["floor"] is True and cur["floor"] is already False).
ctx: dict with
  frame, score (current score, may be None), collectibles_left (pickups not yet taken, -1 if unknown),
  start_score, start_collectibles, taken (collectibles taken since the start),
  bounds {left, top, right, bottom}, start (the player dict at the start), max_air_jumps (allowed mid-air jumps),
  events (events on THIS frame: {"type": "score", "from", "to"} | {"type": "collectibles", "from", "to"} |
          {"type": "player_gone"} | {"type": "scene_change"}),
  mem (a dict that persists across frames for THIS rule; use it to count things, e.g. jumps since landing;
       it is cleared when the level restarts)."""

EXAMPLES = '''Example rule: "The player must never go above y=-200."
```python
def check(prev, cur, ctx):
    if cur["y"] < -200:
        return "player at y=%d, above the y=-200 ceiling" % cur["y"]
    return None
```
```json
{"frames": [{"f": 9, "x": 10, "y": -190, "vx": 0, "vy": -500, "floor": false, "wall": false, "visible": true},
            {"f": 10, "x": 10, "y": -205, "vx": 0, "vy": -480, "floor": false, "wall": false, "visible": true}],
 "ctx": {}}
```

Example rule: "Coins can't be collected twice."
```python
def check(prev, cur, ctx):
    for ev in ctx["events"]:
        if ev["type"] == "score" and ev.get("from") is not None and ev["to"] - ev["from"] > 1:
            return "score jumped %s -> %s from one pickup" % (ev["from"], ev["to"])
    if ctx["score"] is not None and ctx["start_collectibles"] >= 0:
        mem = ctx["mem"]
        if ctx["score"] - ctx["start_score"] > ctx["taken"] + 1:
            mem["over"] = mem.get("over", 0) + 1
            if mem["over"] > 120:  # pickups animate for ~2 s before they count as taken
                return "score %s but only %s coins taken" % (ctx["score"], ctx["taken"])
        else:
            mem["over"] = 0
    return None
```
```json
{"frames": [{"f": 99, "x": 0, "y": 0, "floor": true}, {"f": 100, "x": 0, "y": 0, "floor": true}],
 "ctx": {"events": [{"type": "score", "from": 3, "to": 5}], "score": 5}}
```'''

PROMPT = """You turn a game developer's plain-English house rule into an executable Python check for an automated
QA oracle. The oracle calls check(prev, cur, ctx) on EVERY physics frame of the player's run in this game:
{description}

{fields}

Write `def check(prev, cur, ctx):` that returns None when the frame is fine, or a SHORT string (under 100 chars,
with the numbers that show it) describing the violation. Rules:
- plain Python, no imports, no while loops, no ** power (use x*x), no print, no range, no names starting with _.
  Builtins available: abs, min, max, len, round, int, float, bool, str, sum, any, all.
- Use .get() for fields that may be missing. Be robust: normal play must NEVER trigger it (walking, jumping,
  the one allowed double jump, landing, collecting coins, shooting). Allow small tolerances.
- Keep it fast and simple (it runs 60 times per second).

{examples}

Now the developer's rule: "{rule}"

Reply with exactly two fenced blocks: first ```python with the check function, then ```json with ONE example
{{"frames": [frame, frame, ...], "ctx": {{...}}}} that VIOLATES the rule: the frames are run through check in order
(prev = the frame before, mem starts empty, so include every frame needed, e.g. take-off frames for jump counting);
ctx keys you leave out get normal defaults and ctx "events" apply to the last frame."""


class RuleError(Exception):
    pass


def slug(text: str, taken=()) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"the", "a", "an", "should", "must", "never", "be", "able", "to", "can", "not", "cant", "t", "is", "are", "of"}
    s = "_".join([w for w in words if w not in stop][:4]) or "rule"
    base, i = s[:32], 2
    s = base
    while s in taken:
        s, i = f"{base}_{i}", i + 1
    return s


def rules_path(project_dir: str) -> str:
    return os.path.join(project_dir, RULES_FILE)


def load_all(project_dir: str) -> list:
    p = rules_path(project_dir)
    if not os.path.exists(p):
        return []
    try:
        data = json.load(open(p))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("text")] if isinstance(data, list) else []


def save_all(project_dir: str, rules: list) -> None:
    json.dump(rules, open(rules_path(project_dir), "w"), indent=2)


def static_check(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise RuleError(f"syntax error: {e}") from e
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check"]
    if not fns:
        raise RuleError("must define def check(prev, cur, ctx)")
    if len(fns[0].args.args) != 3:
        raise RuleError("check must take exactly (prev, cur, ctx)")
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.Assign, ast.Expr)):
            raise RuleError(f"only functions and constants allowed at top level, got {type(n).__name__}")
    for node in ast.walk(tree):
        if isinstance(node, BANNED_NODES):
            raise RuleError(f"{type(node).__name__} is not allowed in a rule")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("_")):
            raise RuleError(f"name {node.id!r} is not allowed")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in ("format", "format_map")):
            raise RuleError(f"attribute {node.attr!r} is not allowed")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            raise RuleError("** is not allowed (use x*x)")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool) \
                and abs(node.value) > 1e9:
            raise RuleError("numeric constant too large")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 500:
            raise RuleError("string constant too long")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult) and any(
                isinstance(s, (ast.Constant, ast.List, ast.JoinedStr)) and isinstance(getattr(s, "value", ""), str)
                for s in (node.left, node.right)):
            raise RuleError("string repetition is not allowed")


def compile_check(source: str, name: str = "rule"):
    """AST check, then exec once with a tiny builtins set. Returns the check function."""
    static_check(source)
    ns = {"__builtins__": dict(SAFE_BUILTINS)}
    exec(compile(source, f"<rule {name}>", "exec"), ns)  # noqa: S102 (validated above, no builtins)
    fn = ns.get("check")
    if not callable(fn):
        raise RuleError("check is not a function")
    return fn


def load_enabled(project_dir: str) -> list:
    """Enabled, compiled rules of a game as [{id, text, fn}]. Rules whose code does not compile are skipped."""
    out = []
    for r in load_all(project_dir):
        if not r.get("enabled", True) or not r.get("code"):
            continue
        try:
            out.append({"id": r.get("id") or slug(r["text"]), "text": r["text"], "fn": compile_check(r["code"], r.get("id", ""))})
        except Exception:  # noqa: BLE001 (a broken rule is skipped, never a crash)
            continue
    return out


def rule_of(project_dir: str, cls: str) -> dict | None:
    if not cls.startswith("rule:"):
        return None
    rid = cls[5:]
    return next((r for r in load_all(project_dir) if r.get("id") == rid), None)


# ------------------------------------------------------------------ testing a compiled rule
def run_on_fixture(rule: dict, fixture_path: str = FIXTURE):
    """Run one rule over the recorded clean trace. Returns (violation or None, error or None)."""
    from respawn.envs.godot_hunt import Oracle
    fx = json.load(open(fixture_path))
    o = Oracle(fx.get("config") or {}, fx["start"], rules=[rule])
    for a in fx["acts"]:
        o.judge({"frames": a["frames"], "events": a["events"], "state": a["state"]}, [])
    cls = "rule:" + rule["id"]
    if cls not in o.found:
        return None, o.rule_errors.get(rule["id"])
    at = o.found[cls]["frame"]
    near = [f for a in fx["acts"] for f in a["frames"] if at - 12 <= f["f"] <= at]
    keep = ("f", "x", "y", "vx", "vy", "floor", "wall")
    trace = "; ".join(json.dumps({k: f[k] for k in keep if k in f}) for f in near)
    return f"{o.found[cls]['detail']!r} at frame {at}. The frames up to it were: {trace}", o.rule_errors.get(rule["id"])


def default_ctx(fixture_path: str = FIXTURE) -> dict:
    fx = json.load(open(fixture_path))
    st = fx["start"]
    return {"frame": 100, "score": st.get("score"), "collectibles_left": st.get("collectibles_left"),
            "start_score": st.get("score") or 0, "start_collectibles": st.get("collectibles_left"), "taken": 0,
            "bounds": st.get("bounds") or {}, "start": st.get("player") or {}, "max_air_jumps": 1, "events": [], "mem": {}}


def test_rule(code: str, rid: str, example: dict | None) -> str | None:
    """None when the code is good; otherwise the problem (fed back to the model)."""
    try:
        fn = compile_check(code, rid)
    except Exception as e:  # noqa: BLE001
        return f"the code was rejected: {e}"
    v, err = run_on_fixture({"id": rid, "text": "", "fn": fn})
    if err:
        return f"the check raised an exception on a normal trace: {err}"
    if v:
        return f"the check fired on NORMAL, bug-free play (walking, coins, a hop, a legal double jump): {v}. Make it stricter about what counts as a violation."
    if example:
        base = default_ctx() | {k: v for k, v in dict(example.get("ctx") or {}).items() if k != "mem"}
        frames = example.get("frames") or [example.get("prev") or {}, example.get("cur") or {}]
        mem, got, prev = {}, None, {}
        try:
            for i, cur in enumerate(frames):
                ctx = dict(base, mem=mem, frame=cur.get("f", i))
                if i < len(frames) - 1:
                    ctx["events"] = []  # the example's events happen on its last frame
                got = got or fn(dict(prev), dict(cur), ctx)
                prev = cur
        except Exception as e:  # noqa: BLE001
            return f"the check raised on your own violating example: {type(e).__name__}: {e}"
        if not got:
            return ("the check returned None on your own violating example (frames run in order, mem starts empty); "
                    "fix the check or the example")
    return None


# ------------------------------------------------------------------ compiling with Nemotron
def _blocks(text: str):
    code = example = None
    for m in re.finditer(r"```(\w*)\s*\n(.*?)```", text or "", re.S):
        lang, body = m.group(1).lower(), m.group(2)
        if code is None and (lang in ("python", "py") or (not lang and "def check" in body)):
            code = body.strip() + "\n"
        elif example is None and (lang == "json" or body.lstrip().startswith("{")):
            try:
                example = json.loads(body)
            except json.JSONDecodeError:
                pass
    if code is None and text and "def check" in text:
        code = text[text.index("def check"):].strip() + "\n"
    return code, example


def compile_rule(llm, text: str, rid: str, description: str, model: str | None = None, log=print) -> dict:
    """Nemotron writes the check; we validate + test it, retrying with the error up to 2 times."""
    from respawn.llm import SUPER
    model = model or SUPER
    msgs = [{"role": "user", "content": PROMPT.format(description=description or "a 2D game", fields=FIELDS,
                                                      examples=EXAMPLES, rule=text)}]
    problem = "no attempt"
    for attempt in range(3):
        reply = llm.chat(model, msgs, max_tokens=1500, temperature=0.2,
                         extra={"chat_template_kwargs": {"enable_thinking": False}})
        content = reply.get("content") or ""
        code, example = _blocks(content)
        problem = "no ```python block with def check(prev, cur, ctx)" if not code else test_rule(code, rid, example)
        if problem is None:
            log(f"  [{rid}] ok (attempt {attempt + 1})")
            return {"code": code, "compiled_by": model.split("/")[-1], "error": None}
        log(f"  [{rid}] attempt {attempt + 1}: {problem[:200]}")
        msgs += [{"role": "assistant", "content": content},
                 {"role": "user", "content": f"That does not work: {problem}\nReply again with the two fenced blocks."}]
    return {"code": code if code else "", "compiled_by": model.split("/")[-1], "error": problem}


def _mock_rule(model, messages, tools):
    return {"content": "```python\ndef check(prev, cur, ctx):\n    if cur.get('tunnel') or cur.get('inside'):\n"
                       "        return 'inside geometry at (%d,%d)' % (cur['x'], cur['y'])\n    return None\n```\n"
                       "```json\n{\"prev\": {}, \"cur\": {\"x\": 1, \"y\": 2, \"inside\": true}, \"ctx\": {}}\n```"}


def compile_project(project_dir: str, mock: bool = False, force: bool = False, log=print) -> list:
    """Compile every rule of a game that has no code yet (or all with force). Writes qa_rules.json back."""
    from respawn.llm import LLM
    project_dir = os.path.abspath(project_dir)
    rules = load_all(project_dir)
    if not rules:
        log(f"no rules in {rules_path(project_dir)}: add some in the dock's Rules tab first")
        return []
    cfg_p = os.path.join(project_dir, "qa_crew.json")
    desc = (json.load(open(cfg_p)).get("description") if os.path.exists(cfg_p) else "") or "a 2D game"
    llm = LLM(mock=mock or None)
    if llm.mock:
        llm.mock_responder = _mock_rule
    taken = set()
    for r in rules:
        r["id"] = r.get("id") or slug(r["text"], taken)
        taken.add(r["id"])
        r.setdefault("enabled", True)
    todo = [r for r in rules if force or not r.get("code") or r.get("error")]
    log(f"compiling {len(todo)} of {len(rules)} rule(s) for {os.path.basename(project_dir)}")
    for i, r in enumerate(todo):
        log(f"RULE {i + 1}/{len(todo)} {r['id']}: {r['text']}")
        try:
            out = compile_rule(llm, r["text"], r["id"], desc, log=log)
        except Exception as e:  # noqa: BLE001 (network etc.: keep the others)
            out = {"code": r.get("code", ""), "compiled_by": r.get("compiled_by", ""), "error": f"{type(e).__name__}: {e}"[:300]}
        r.update(out)
        if out["error"]:
            log(f"  [{r['id']}] FAILED: {out['error'][:200]}")
        save_all(project_dir, rules)
    log(f"done: {sum(1 for r in rules if r.get('code') and not r.get('error'))}/{len(rules)} rule(s) compiled")
    return rules


# ------------------------------------------------------------------ suggesting rules (game-specific bug hypotheses)
SUGGEST_FILE = "qa_rule_suggestions.json"
SEVERITIES = ("high", "med", "low")

SUGGEST_PROMPT = """You are a senior game QA lead. Read this Godot game's code, scene and controls, then propose about {n}
GAME-SPECIFIC house rules: things that must always hold in THIS game and that you suspect the code may break.
Prefer rules tied to this game's own mechanics, numbers, states and edge cases (what happens when two systems meet:
pausing, dying, landing, scoring, timers, restarts, screen edges, combined inputs). Avoid generic physics truisms
(e.g. "never overlap solid geometry") unless the code gives a specific reason to suspect them.

Each rule must be checkable by an automated oracle that only sees, per physics frame:
{fields}
plus "a script error was logged" (crash). Phrase each rule in plain English a developer would type, with concrete
numbers from the code where useful.

# Game: {name}
{description}

# Controls (input actions): {actions}
# qa_crew.json
{cfg}

# Main scene nodes
{scene}

# Scripts (comments may be trimmed)
{scripts}

# A short recorded playthrough (state samples)
{trace}
{history}
Reply with ONLY a JSON object:
{{"suggestions": [{{"text": "the rule", "rationale": "one line: why this game might break it (cite the code)",
  "severity": "high|med|low", "likelihood": 0.0-1.0}}]}}"""


def suggestions_path(project_dir: str) -> str:
    return os.path.join(project_dir, SUGGEST_FILE)


def load_suggestions(project_dir: str) -> list:
    p = suggestions_path(project_dir)
    try:
        data = json.load(open(p))
    except (OSError, json.JSONDecodeError):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("text")] if isinstance(data, list) else []


def _scripts(project_dir: str, budget: int = 14000) -> str:
    """The game's own .gd files (addons excluded), blank lines and planted-bug markers removed."""
    out, used = [], 0
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = sorted(d for d in dirs if d not in ("addons", "qa_reports", ".godot"))
        for f in sorted(files):
            if not f.endswith(".gd"):
                continue
            rel = os.path.relpath(os.path.join(root, f), project_dir)
            lines = []
            for ln in open(os.path.join(root, f), errors="replace").read().splitlines():
                if not ln.strip() or "PLANTED" in ln.upper() or ln.strip().startswith("##"):
                    continue  # the markers we use to document our planted bugs must not leak to the model
                lines.append(ln.rstrip())
            body = "\n".join(lines)
            room = budget - used
            if room < 200:
                out.append(f"## res://{rel} (omitted: context budget)")
                continue
            if len(body) > room:
                body = body[:room] + "\n# ... (truncated)"
            out.append(f"## res://{rel}\n{body}")
            used += len(body)
    return "\n\n".join(out)


def _scene(project_dir: str, limit: int = 60) -> str:
    proj = open(os.path.join(project_dir, "project.godot"), errors="replace").read()
    m = re.search(r'run/main_scene="res://([^"]+)"', proj)
    if not m or not os.path.exists(os.path.join(project_dir, m.group(1))):
        return "(unknown)"
    text = open(os.path.join(project_dir, m.group(1)), errors="replace").read()
    ext = dict(re.findall(r'\[ext_resource[^\]]*path="([^"]+)"[^\]]*id="([^"]+)"', text))
    ext = {v: k for k, v in ext.items()}
    rows = []
    for nm in re.finditer(r'\[node name="([^"]+)"([^\]]*)\]', text):
        attrs = nm.group(2)
        typ = re.search(r'type="([^"]+)"', attrs)
        inst = re.search(r'instance=ExtResource\("([^"]+)"\)', attrs)
        par = re.search(r'parent="([^"]+)"', attrs)
        what = typ.group(1) if typ else (ext.get(inst.group(1), "?") if inst else "?")
        rows.append(f"- {(par.group(1) + '/') if par and par.group(1) != '.' else ''}{nm.group(1)}: {what}")
    return f"res://{m.group(1)}\n" + "\n".join(rows[:limit])


def _actions(project_dir: str) -> str:
    proj = open(os.path.join(project_dir, "project.godot"), errors="replace").read()
    sec = proj.split("[input]", 1)[1].split("\n[", 1)[0] if "[input]" in proj else ""
    return ", ".join(re.findall(r"^(\w+)=\{", sec, re.M)) or "(default ui_* actions)"


def _playthrough(project_dir: str, cfg: dict) -> str:
    """A few seconds of play through the bridge: each control held in turn, sampled every 15 frames."""
    try:
        from respawn.envs.godot_hunt import parse_inputs
        from respawn.godot.client import GodotGame
        g = GodotGame(project_dir)
    except Exception as e:  # noqa: BLE001
        return f"(no playthrough: {e})"[:200]
    try:
        letters = (g.info.get("config") or {}).get("letters") or cfg.get("letters") or {}
        st = g.call("reset", seed=0)
        lines = [f"start: player {json.dumps({k: v for k, v in (st.get('player') or {}).items() if k != 'script'})}, "
                 f"score {st.get('score')}, collectibles_left {st.get('collectibles_left')}, bounds {st.get('bounds')}"]
        for tok in ["W30"] + [f"{k}40" for k in letters if k not in ("P", "E")][:5] + ["W30"]:
            res = g.call("act", inputs=parse_inputs(tok, letters))
            errs = g.take_errors()
            fr = res["frames"][::15]
            samp = " ".join(f"f{f['f']}({f['x']:.0f},{f['y']:.0f} v{f.get('vx', 0):.0f},{f.get('vy', 0):.0f}"
                            f"{' floor' if f.get('floor') else ''}{' hidden' if f.get('visible') is False else ''})" for f in fr)
            evs = [e for e in res["events"] if e["type"] in ("score", "scene_change", "player_gone")][:4]
            lines.append(f"{tok}: {samp}" + (f" events {evs}" if evs else "") + (f" ERRORS {errs[:1]}" if errs else ""))
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"(playthrough failed: {e})"[:200]
    finally:
        g.close()


def suggest_context(project_dir: str) -> dict:
    cfg_p = os.path.join(project_dir, "qa_crew.json")
    cfg = json.load(open(cfg_p)) if os.path.exists(cfg_p) else {}
    name = re.search(r'config/name="([^"]+)"', open(os.path.join(project_dir, "project.godot"), errors="replace").read())
    return {"name": name.group(1) if name else os.path.basename(project_dir), "description": cfg.get("description", ""),
            "actions": _actions(project_dir), "cfg": json.dumps({k: v for k, v in cfg.items() if k != "spawns"}),
            "scene": _scene(project_dir), "scripts": _scripts(project_dir), "trace": _playthrough(project_dir, cfg)}


def _clean_suggestions(obj) -> list:
    items = obj.get("suggestions") if isinstance(obj, dict) else obj
    out = []
    for s in items or []:
        if not isinstance(s, dict) or not str(s.get("text", "")).strip():
            continue
        sev = str(s.get("severity", "med")).lower()[:4]
        sev = {"medi": "med", "mediu": "med"}.get(sev, sev)
        try:
            lik = max(0.0, min(1.0, float(s.get("likelihood", 0.5))))
        except (TypeError, ValueError):
            lik = 0.5
        out.append({"text": " ".join(str(s["text"]).split())[:240], "rationale": " ".join(str(s.get("rationale", "")).split())[:300],
                    "severity": sev if sev in SEVERITIES else "med", "likelihood": round(lik, 2)})
    return out


def _mock_suggest(model, messages, tools):
    return {"content": json.dumps({"suggestions": [
        {"text": "The score never changes while the player is hidden.", "rationale": "(mock) score timer keeps running", "severity": "med", "likelihood": 0.6},
        {"text": "The player never leaves the screen bounds.", "rationale": "(mock) clamp may be skipped", "severity": "med", "likelihood": 0.5}]})}


def suggest_project(project_dir: str, n: int = 8, mock: bool = False, log=print) -> list:
    """Nemotron Super proposes game-specific rules. At most 2 calls (one + one repair). Keeps the history of
    accepted/rejected suggestions and shows it to the model so later suggestions explore different kinds."""
    import time as _time

    from respawn.llm import LLM, SUPER, extract_json
    project_dir = os.path.abspath(project_dir)
    old = load_suggestions(project_dir)
    log(f"reading {os.path.basename(project_dir)}: scripts, main scene, controls, a short playthrough")
    ctx = suggest_context(project_dir)
    acc = [s["text"] for s in old if s.get("status") == "accepted"]
    rej = [s["text"] for s in old if s.get("status") == "rejected"]
    typed = [r["text"] for r in load_all(project_dir)]
    hist = ""
    if acc or typed:
        hist += "\n# The developer already has these rules (do not repeat them):\n" + "\n".join(f"- {t}" for t in dict.fromkeys(acc + typed))
    if rej:
        hist += "\n# The developer REJECTED these suggestions before; propose different kinds of rules:\n" + "\n".join(f"- {t}" for t in rej)
    prompt = SUGGEST_PROMPT.format(n=n, fields=FIELDS, history=hist + "\n", **ctx)
    llm = LLM(mock=mock or None)
    if llm.mock:
        llm.mock_responder = _mock_suggest
    msgs = [{"role": "user", "content": prompt}]
    sugg, problem = [], ""
    for attempt in range(2):
        log(f"asking Nemotron Super ({len(prompt) // 4} tokens of context)" if attempt == 0 else f"repairing: {problem}")
        reply = llm.chat(SUPER, msgs, max_tokens=3000, temperature=0.5, extra={"chat_template_kwargs": {"enable_thinking": False}})
        content = reply.get("content") or ""
        try:
            sugg = _clean_suggestions(extract_json(content))
        except ValueError as e:
            sugg, problem = [], f"no JSON object ({e})"
        if len(sugg) >= 3:
            break
        problem = problem or f"only {len(sugg)} usable suggestions"
        msgs += [{"role": "assistant", "content": content},
                 {"role": "user", "content": f"That did not parse ({problem}). Reply with ONLY the JSON object with about {n} suggestions."}]
    if not sugg:
        log(f"no suggestions: {problem}")
        return old
    known = {s["text"].lower() for s in old if s.get("status") in ("accepted", "rejected")} | {t.lower() for t in typed}
    keep = [s for s in old if s.get("status") in ("accepted", "rejected")]
    taken = {s.get("id") for s in keep}
    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    for s in sorted(sugg, key=lambda s: -s["likelihood"]):
        if s["text"].lower() in known:
            continue
        sid = slug(s["text"], taken)
        taken.add(sid)
        keep.append(s | {"id": sid, "status": "new", "model": SUPER.split("/")[-1], "created": now})
    json.dump(keep, open(suggestions_path(project_dir), "w"), indent=2)
    for s in [x for x in keep if x["status"] == "new"]:
        log(f"SUGGEST [{s['severity']}] {s['likelihood']:.2f} {s['text']}  ({s['rationale']})")
    log(f"done: {sum(1 for x in keep if x['status'] == 'new')} new suggestion(s) in {SUGGEST_FILE}")
    return keep

"""Runs one episode: every agent in the env gets its own Nemotron decision loop.

Per decision the agent sees: rules + playbook + its role (stable prefix, so Crusoe's prefix
cache hits), then the current rendered state and its last few results. It may call free
helper tools a few times, then must commit to exactly one game action.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from respawn import env as envmod
from respawn.genome import Genome
from respawn.llm import LLM

MAX_HELPER_ROUNDS = 3


@dataclass
class Step:
    agent: str
    i: int
    helpers: list
    action: str
    args: dict
    result: str
    note: str = ""
    seconds: float = 0.0


@dataclass
class Episode:
    env: str
    seed: int
    genome: str
    score: float
    outcome: dict
    steps: list = field(default_factory=list)
    replay: list = field(default_factory=list)
    models: dict = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""

    def to_json(self, with_replay=False) -> dict:
        d = asdict(self)
        if not with_replay:
            d.pop("replay")
        return d


def system_prompt(env, genome: Genome, agent: str) -> str:
    role = genome.roles.get(agent)
    team = ", ".join(env.agents)
    parts = [
        f"You are {agent}, an agent playing a game. Team: {team}." if len(env.agents) > 1 else "You are an agent playing a game.",
        "## Rules\n" + env.rules,
        "## Team playbook (follow it; it was written from experience)\n" + genome.playbook.strip(),
    ]
    if role:
        parts.append(f"## Your role\n{role}")
    parts.append("## How to act\nEach turn call exactly ONE game action tool. Helper tools (marked [helper]) are free and "
                 "do not use game time; call them first if useful. Be decisive. Never reply with only prose.")
    return "\n\n".join(parts)


def _parse_text_action(text: str, names: set):
    """Fallback when a model writes the call in prose: name(...) or {"name":..,"arguments":..}."""
    if not text:
        return None
    try:
        from respawn.llm import extract_json
        obj = extract_json(text)
        n = obj.get("name") or obj.get("action") or obj.get("tool")
        if n in names:
            a = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
            return n, a if isinstance(a, dict) else {}
    except Exception:
        pass
    for m in re.finditer(r"\b([a-z_]+)\s*\(([^)]*)\)", text):
        if m.group(1) in names:
            args = {}
            for kv in re.finditer(r"(\w+)\s*[=:]\s*(\"[^\"]*\"|'[^']*'|-?\d+)", m.group(2)):
                v = kv.group(2).strip("'\"")
                args[kv.group(1)] = int(v) if re.fullmatch(r"-?\d+", v) else v
            return m.group(1), args
    return None


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        a = json.loads(raw or "{}")
        return a if isinstance(a, dict) else {}
    except json.JSONDecodeError:
        return {}


def agent_loop(llm: LLM, env, genome: Genome, agent: str, model: str, steps: list, lock: threading.Lock,
               thinking: bool = False):
    sysmsg = system_prompt(env, genome, agent)
    recent: list[str] = []
    mock = getattr(llm, "mock", False)
    for i in range(env.max_decisions):
        if env.over:
            break
        t0 = time.monotonic()
        actions = env.action_schemas(agent)
        helpers = env.helper_schemas(agent)
        action_names = {a["name"] for a in actions}
        helper_names = {h["name"] for h in helpers}
        used_helpers = []
        name, args, note = None, {}, ""
        if mock:
            def call_tool(n, a):
                r = env.helper(agent, n, a)
                used_helpers.append({"name": n, "args": a, "result": r})
                return r
            name, args = env.mock_policy(agent, genome.playbook, call_tool)
        else:
            user = env.render(agent)
            if recent:
                user += "\n\nYour last results:\n" + "\n".join(recent[-5:])
            user += "\n\nChoose your next action."
            messages = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]
            extra = {"chat_template_kwargs": {"enable_thinking": thinking}}
            for _round in range(MAX_HELPER_ROUNDS + 1):
                tools = actions + (helpers if _round < MAX_HELPER_ROUNDS else [])
                try:
                    msg = llm.chat(model, messages, tools=tools, max_tokens=1500 if thinking else 600,
                                   temperature=0.4, extra=extra)
                except Exception as e:  # network/API failure: fall back to the env default
                    note = f"llm error: {str(e)[:200]}"
                    break
                calls = msg.get("tool_calls") or []
                content = msg.get("content") or ""
                note = content.strip()[:300]
                if not calls:
                    parsed = _parse_text_action(content, action_names | helper_names)
                    if parsed:
                        calls = [{"id": "text", "function": {"name": parsed[0], "arguments": json.dumps(parsed[1])}}]
                game_call = next((c for c in calls if c["function"]["name"] in action_names), None)
                if game_call:
                    name, args = game_call["function"]["name"], _args(game_call["function"].get("arguments"))
                    break
                helper_calls = [c for c in calls if c["function"]["name"] in helper_names]
                if not helper_calls:
                    messages.append({"role": "assistant", "content": content or "(no tool call)"})
                    messages.append({"role": "user", "content": "You must call one of the game action tools now."})
                    continue
                messages.append({"role": "assistant", "content": content or None, "tool_calls": [
                    {"id": c.get("id") or f"c{k}", "type": "function", "function": c["function"]} for k, c in enumerate(helper_calls)]})
                for k, c in enumerate(helper_calls):
                    a = _args(c["function"].get("arguments"))
                    r = env.helper(agent, c["function"]["name"], a)
                    used_helpers.append({"name": c["function"]["name"], "args": a, "result": r})
                    messages.append({"role": "tool", "tool_call_id": c.get("id") or f"c{k}", "content": json.dumps(r)[:2000]})
        if name is None:
            name, args = getattr(env, "default_action", ("wait", {}))
            note = (note + " | no valid action; default used").strip(" |")
        if hasattr(env, "note") and not mock:  # envs that show the agent's reasoning (e.g. a watched Godot hunt)
            env.note = note
        result = env.act(agent, name, args)
        recent.append(f"{i}: {name}({json.dumps(args)}) -> {result[:240]}")
        with lock:
            steps.append(Step(agent, i, used_helpers, name, args, result[:600], note, round(time.monotonic() - t0, 2)))


def run_episode(llm: LLM, env_name: str, seed: int, genome: Genome, models: list, record: bool = False,
                thinking: bool = False, env_kwargs: dict | None = None) -> Episode:
    t0 = time.monotonic()
    env = envmod.make(env_name, seed, tools=genome.tools, record=record, roles=genome.roles, **(env_kwargs or {}))
    steps: list[Step] = []
    lock = threading.Lock()
    assign = {a: models[(seed + k) % len(models)] for k, a in enumerate(env.agents)}
    err = ""
    try:
        if len(env.agents) == 1:
            agent_loop(llm, env, genome, env.agents[0], assign[env.agents[0]], steps, lock, thinking)
        else:
            with ThreadPoolExecutor(len(env.agents)) as ex:
                futs = [ex.submit(agent_loop, llm, env, genome, a, assign[a], steps, lock, thinking) for a in env.agents]
                for f in futs:
                    f.result()
        out = env.outcome()
        sc = env.score()
        rep = env.replay() if record else []
    except Exception as e:  # keep the generation going; a crashed episode scores as recorded so far
        err = f"{type(e).__name__}: {e}"
        try:
            out, sc, rep = env.outcome(), env.score(), []
        except Exception:
            out, sc, rep = {"cause": err}, 0, []
    finally:
        env.close()
    print(f"  episode {env_name} seed {seed} genome {genome.fingerprint()}: score {sc} in {time.monotonic() - t0:.0f}s"
          + (f" ERROR {err[:120]}" if err else ""), flush=True)
    return Episode(env_name, seed, genome.fingerprint(), sc, out, steps, rep, assign, round(time.monotonic() - t0, 1), err)


def run_many(llm: LLM, env_name: str, seeds: list, genome: Genome, models: list, parallel: int = 4,
             record: bool = False, thinking: bool = False) -> list:
    with ThreadPoolExecutor(max(1, parallel)) as ex:
        return list(ex.map(lambda s: run_episode(llm, env_name, s, genome, models, record, thinking), seeds))

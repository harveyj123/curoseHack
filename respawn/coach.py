"""The dream coach: reads the team's worst episodes and proposes ONE change to the genome.

A change can rewrite the playbook, add/replace/remove an agent-written tool (which must
pass its own test before the gatekeeper ever sees it), or reassign agent roles.
"""
from __future__ import annotations

import json

from respawn import env as envmod
from respawn.genome import Genome
from respawn.llm import LLM, SUPER, extract_json

SCHEMA = """Reply with ONE JSON object and nothing else:
{
  "diagnosis": "what went wrong in these episodes, citing concrete steps",
  "change": "one sentence: the single change you are making and why it should raise the score",
  "playbook": "the FULL new playbook in markdown (omit to keep the current one)",
  "tool": {"filename": "name.EXT", "source": "full source"},   // optional: add or replace one tool
  "remove_tools": ["filename"],                               // optional
  "roles": {"agent_name": "role description"}                 // optional: reassign team roles
}
Make exactly one focused change (a playbook edit OR one tool OR roles, or a playbook edit that teaches a new tool).
Keep the playbook short, concrete and imperative (under 25 bullet points). Prefer rules that fix the most common failure."""


def digest_episode(ep, max_steps: int = 18) -> str:
    lines = [f"seed {ep.seed}: score {ep.score} | outcome {json.dumps(ep.outcome)}" + (f" | crash {ep.error}" if ep.error else "")]
    steps = ep.steps if len(ep.steps) <= max_steps else ep.steps[:4] + ep.steps[-(max_steps - 4):]
    for s in steps:
        h = ""
        if s.helpers:
            h = " [helpers: " + "; ".join(f"{x['name']}({json.dumps(x['args'])})->{json.dumps(x['result'])[:80]}" for x in s.helpers) + "]"
        lines.append(f"  {s.agent}#{s.i}: {s.action}({json.dumps(s.args)}){h} -> {s.result[:200].replace(chr(10), ' / ')}")
    return "\n".join(lines)


def build_prompt(env_cls, champion: Genome, episodes: list, history: list) -> list:
    worst = sorted(episodes, key=lambda e: e.score)[:3]
    best = max(episodes, key=lambda e: e.score)
    tools_txt = "\n\n".join(f"--- tools/{k}\n{v}" for k, v in champion.tools.items()) or "(no tools yet)"
    hist = "\n".join(f"- gen {h['gen']}: {'ACCEPTED' if h['accepted'] else 'REJECTED'} ({h.get('delta', 0):+.1f}): {h.get('change', '')[:160]}"
                     + (f" [error: {h['error'][:100]}]" if h.get('error') else "") for h in history[-8:]) or "(none yet)"
    user = f"""You coach a team of small Nemotron agents. Overnight you study today's episodes and improve the team.

## Game rules (what the players see)
{env_cls.rules}

## Team: {', '.join(env_cls.agents)}
Current roles: {json.dumps(champion.roles) if champion.roles else '(none)'}

## Current playbook
{champion.playbook}

## Current tools
{tools_txt}

## How to write a tool ({env_cls.tool_lang})
{env_cls.tool_guide}

## Earlier dreams (do not repeat rejected ideas)
{hist}

## Today's scores: {[e.score for e in episodes]} (mean {sum(e.score for e in episodes) / max(1, len(episodes)):.1f})

## Worst episodes
{chr(10).join(digest_episode(e) for e in worst)}

## Best episode
{digest_episode(best, 10)}

{SCHEMA}"""
    return [{"role": "system", "content": "You are a precise, pragmatic game coach and software engineer. Output strict JSON."},
            {"role": "user", "content": user}]


def apply_proposal(champion: Genome, prop: dict, env_cls) -> Genome:
    cand = champion.copy()
    if prop.get("playbook") and isinstance(prop["playbook"], str) and len(prop["playbook"].strip()) > 10:
        cand.playbook = prop["playbook"].strip() + "\n"
    if prop.get("playbook_append"):
        cand.playbook = cand.playbook.rstrip() + "\n" + prop["playbook_append"].strip() + "\n"
    for f in prop.get("remove_tools") or []:
        cand.tools.pop(f, None)
    tool = prop.get("tool")
    if isinstance(tool, dict) and tool.get("source"):
        src = tool["source"]
        if "\n" not in src and "\\n" in src:  # model double-escaped the code into one line
            tool["source"] = src.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        spec = env_cls.validate_tool(tool["source"])  # raises ValueError
        ext = {"python": ".py", "js": ".js"}.get(env_cls.tool_lang, ".txt")
        fname = tool.get("filename") or spec["name"] + ext
        if not fname.endswith(ext):
            fname = spec["name"] + ext
        cand.tools[fname] = tool["source"]
    if isinstance(prop.get("roles"), dict):
        cand.roles.update({k: str(v) for k, v in prop["roles"].items() if k in env_cls.agents})
    return cand


def propose(llm: LLM, env_name: str, champion: Genome, episodes: list, history: list, gen: int,
            model: str = SUPER, repair_rounds: int = 2):
    """Returns (candidate genome or None, proposal dict, error string)."""
    env_cls = envmod.get(env_name)
    if llm.mock:
        prop = env_cls.mock_proposal(gen - 1, champion.tools)
        try:
            return apply_proposal(champion, prop, env_cls), prop, ""
        except ValueError as e:
            return None, prop, str(e)
    messages = build_prompt(env_cls, champion, episodes, history)
    prop, err = {}, ""
    for _attempt in range(repair_rounds + 1):
        msg = llm.chat(model, messages, max_tokens=8000, temperature=0.7,
                       extra={"chat_template_kwargs": {"enable_thinking": True}})
        content = msg.get("content") or ""
        try:
            prop = extract_json(content)
            cand = apply_proposal(champion, prop, env_cls)
            if cand.fingerprint() == champion.fingerprint():
                raise ValueError("proposal does not change anything")
            return cand, prop, ""
        except ValueError as e:
            err = str(e)
            messages += [{"role": "assistant", "content": content[:6000]},
                         {"role": "user", "content": f"That proposal was rejected before testing: {err}\nFix it and reply with the corrected JSON only."}]
    return None, prop, err

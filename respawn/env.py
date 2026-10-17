"""Game-agnostic environment interface.

An Env is one seeded episode of some game, played by one or more agents. The harness,
coach, gatekeeper and evaluator only talk to this interface, so swapping the game means
writing one adapter module under respawn/envs/.
"""
from __future__ import annotations

from typing import Callable, Protocol


class Env(Protocol):
    name: str
    rules: str                 # game rules for the player's system prompt
    agents: list               # agent names, e.g. ["hero"] or ["ash", "birch"]
    max_decisions: int         # model-call budget per agent per episode
    tool_lang: str             # language of agent-written tools ("python", "js")
    tool_guide: str            # how to write a tool, shown to the coach

    @classmethod
    def validate_tool(cls, source: str) -> dict: ...   # raise ValueError, else return {name, description, parameters}

    def action_schemas(self, agent: str) -> list: ...  # game actions (consume game time)
    def helper_schemas(self, agent: str) -> list: ...  # free helpers (agent-written analysis tools)
    def render(self, agent: str) -> str: ...
    def act(self, agent: str, name: str, args: dict) -> str: ...
    def helper(self, agent: str, name: str, args: dict): ...
    @property
    def over(self) -> bool: ...
    def score(self) -> float: ...
    def outcome(self) -> dict: ...
    def replay(self) -> list: ...
    def close(self) -> None: ...


REGISTRY: dict[str, Callable[..., Env]] = {}


def register(name: str):
    def deco(factory):
        REGISTRY[name] = factory
        return factory
    return deco


def get(name: str):
    if name not in REGISTRY:
        import importlib
        importlib.import_module(f"respawn.envs.{'godot_hunt' if name.startswith('godot_hunt') else name}")
    return REGISTRY[name]


def make(name: str, seed: int, tools: dict | None = None, record: bool = False, **kw) -> Env:
    return get(name)(seed=seed, tools=tools or {}, record=record, **kw)

"""Sandbox for agent-written Python tools.

A tool is one Python file that defines:
    TOOL = {"name": ..., "description": ..., "parameters": <JSON schema>}
    def run(obs, **kwargs) -> JSON-serialisable
    def test(sample_obs) -> None   # must pass before the tool can ever be used

Defence in depth: an AST allowlist (no os/sys/subprocess/network/file IO/eval),
then execution in a separate isolated interpreter with CPU, memory and wall-clock limits.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

ALLOWED_IMPORTS = {"math", "heapq", "collections", "itertools", "functools", "json", "random", "statistics", "dataclasses", "typing"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                "delattr", "input", "breakpoint", "exit", "quit", "memoryview"}
RUNNER = os.path.join(os.path.dirname(__file__), "_tool_runner.py")


class ToolError(Exception):
    pass


def static_check(source: str) -> dict:
    """Validate source and return its TOOL spec. Raises ToolError."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ToolError(f"syntax error: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m.split(".")[0] not in ALLOWED_IMPORTS:
                    raise ToolError(f"import of {m!r} is not allowed (allowed: {sorted(ALLOWED_IMPORTS)})")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise ToolError(f"use of {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ToolError(f"dunder attribute access {node.attr!r} is not allowed")
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for need in ("run", "test"):
        if need not in names:
            raise ToolError(f"tool must define {need}()")
    spec = None
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL" for t in n.targets):
            try:
                spec = ast.literal_eval(n.value)
            except ValueError as e:
                raise ToolError("TOOL must be a literal dict") from e
    if not isinstance(spec, dict) or not {"name", "description", "parameters"} <= spec.keys():
        raise ToolError("TOOL must be a dict with name, description, parameters")
    if not str(spec["name"]).isidentifier():
        raise ToolError("TOOL name must be an identifier")
    return spec


def _limits():  # runs in the child before exec
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 2**20, 512 * 2**20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0)) if hasattr(resource, "RLIMIT_NPROC") else None


def execute(source: str, mode: str, obs: dict, args: dict | None = None, timeout: float = 5.0):
    payload = json.dumps({"source": source, "mode": mode, "obs": obs, "args": args or {}})
    try:
        p = subprocess.run([sys.executable, "-I", "-S", RUNNER], input=payload, capture_output=True, text=True,
                           timeout=timeout, preexec_fn=_limits if os.name == "posix" else None, cwd="/")
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"timed out after {timeout}s") from e
    if p.returncode != 0 or not p.stdout.strip():
        raise ToolError((p.stderr or "no output").strip().splitlines()[-1][:300] if p.stderr else "crashed")
    out = json.loads(p.stdout.strip().splitlines()[-1])
    if not out.get("ok"):
        raise ToolError(out.get("error", "unknown error")[:500])
    return out.get("result")


def validate(source: str, sample_obs: dict) -> dict:
    """Static check + the tool's own test. Returns TOOL spec or raises ToolError."""
    spec = static_check(source)
    execute(source, "test", sample_obs)
    return spec


class Toolbox:
    """The validated tools of one genome, callable by the player."""

    def __init__(self, sources: dict[str, str]):
        self.sources = {}
        self.specs = {}
        for _fname, src in sources.items():
            spec = static_check(src)
            self.sources[spec["name"]] = src
            self.specs[spec["name"]] = spec

    @property
    def names(self):
        return tuple(self.specs)

    def schemas(self):
        return [{"name": s["name"], "description": "[helper tool, free: takes no game turn] " + s["description"],
                 "parameters": s["parameters"]} for s in self.specs.values()]

    def call(self, name, obs, args):
        if name not in self.sources:
            return {"error": f"no tool {name}"}
        try:
            return execute(self.sources[name], "run", obs, args)
        except ToolError as e:
            return {"error": str(e)}

"""A genome is everything the team can rewrite about itself: playbook, tools, roles.

On disk: <dir>/playbook.md, <dir>/roles.json (agent -> role text), <dir>/tools/<file>, <dir>/meta.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field

DEFAULT_PLAYBOOK = "# Playbook\n\n- Play the game and try to score points.\n"


@dataclass
class Genome:
    playbook: str = DEFAULT_PLAYBOOK
    tools: dict = field(default_factory=dict)      # filename -> source
    roles: dict = field(default_factory=dict)      # agent name -> role description
    meta: dict = field(default_factory=dict)       # gen, parent, change summary

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.playbook.encode())
        for k in sorted(self.tools):
            h.update(k.encode() + b"\0" + self.tools[k].encode())
        h.update(json.dumps(self.roles, sort_keys=True).encode())
        return h.hexdigest()[:10]

    def copy(self) -> "Genome":
        return Genome(self.playbook, dict(self.tools), dict(self.roles), dict(self.meta))

    def save(self, path: str) -> None:
        if os.path.exists(path):
            shutil.rmtree(path)
        os.makedirs(os.path.join(path, "tools"))
        open(os.path.join(path, "playbook.md"), "w").write(self.playbook)
        json.dump(self.roles, open(os.path.join(path, "roles.json"), "w"), indent=2)
        json.dump({**self.meta, "fingerprint": self.fingerprint()}, open(os.path.join(path, "meta.json"), "w"), indent=2)
        for fname, src in self.tools.items():
            open(os.path.join(path, "tools", fname), "w").write(src)

    @classmethod
    def load(cls, path: str) -> "Genome":
        tools = {}
        tdir = os.path.join(path, "tools")
        if os.path.isdir(tdir):
            for f in sorted(os.listdir(tdir)):
                tools[f] = open(os.path.join(tdir, f)).read()
        roles = json.load(open(os.path.join(path, "roles.json"))) if os.path.exists(os.path.join(path, "roles.json")) else {}
        meta = json.load(open(os.path.join(path, "meta.json"))) if os.path.exists(os.path.join(path, "meta.json")) else {}
        return cls(open(os.path.join(path, "playbook.md")).read(), tools, roles, meta)

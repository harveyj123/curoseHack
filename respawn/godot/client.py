"""Python side of the QA Crew bridge: launches a Godot game headless and drives it over TCP."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

GODOT = os.environ.get("GODOT_BIN", "/opt/godot/Godot_v4.3-stable_linux.x86_64")
ERR_RE = re.compile(r"(SCRIPT ERROR|ERROR): (.*)")
_port_lock = threading.Lock()
_next_port = [int(os.environ.get("QA_PORT_BASE", 0)) or 27000 + os.getpid() % 1000 * 3]  # QA_PORT_BASE keeps concurrent runs apart
# QA_VISIBLE=1 runs every game in a real window on $DISPLAY (e.g. an Xvfb screen being recorded), tiled in a
# grid of QA_TILE (default 640x360) cells so several parallel hunters can be watched at once.
VISIBLE = os.environ.get("QA_VISIBLE") == "1"
_slots: set = set()


def free_port() -> int:
    with _port_lock:
        while True:
            _next_port[0] += 1
            p = _next_port[0]
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue


class GodotGame:
    """One running game process. Script errors printed by Godot are collected as crash evidence."""

    def __init__(self, project: str, headless: bool = True, timeout: float = 60.0):
        self.project = os.path.abspath(project)
        self.port = free_port()
        self.slot = None
        if VISIBLE:
            headless = False
            with _port_lock:
                self.slot = next(i for i in range(64) if i not in _slots)
                _slots.add(self.slot)
        args = [GODOT, "--path", self.project, "--fixed-fps", "60", "--audio-driver", "Dummy"]
        if headless:
            args.insert(1, "--headless")
        else:  # rendered (for screenshots / GIF evidence) on a virtual display
            args += ["--rendering-driver", "opengl3"]
            if self.slot is not None:
                tw, th = (int(v) for v in os.environ.get("QA_TILE", "640x360").split("x"))
                cols = int(os.environ.get("QA_COLS", "3"))
                args += ["--resolution", f"{tw}x{th}", "--position", f"{self.slot % cols * tw},{self.slot // cols * th}"]
            if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
                args = ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24"] + args
        args += ["--", f"--qa-port={self.port}"]
        env = dict(os.environ)
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, bufsize=1)
        self.errors: list[dict] = []
        self.log: list[str] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()
        if not self._ready.wait(timeout):
            self.close()
            raise RuntimeError("godot did not start: " + "\n".join(self.log[-20:]))
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.rfile = self.sock.makefile("r")
        self._id = 0
        self.info = self.call("hello")

    def _pump(self):
        pending = None
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            with self._lock:
                self.log.append(line)
                if len(self.log) > 2000:
                    self.log = self.log[-1000:]
                if "QA_BRIDGE listening" in line:
                    self._ready.set()
                m = ERR_RE.search(line)
                if m and "Failed loading resource" not in line and "music" not in line:
                    pending = {"kind": m.group(1), "message": m.group(2)[:300], "where": ""}
                    self.errors.append(pending)
                elif pending and line.strip().startswith("at:"):
                    pending["where"] = line.strip()[3:].strip()[:200]
                    pending = None

    def call(self, cmd: str, **kw):
        self._id += 1
        self.sock.sendall((json.dumps({"id": self._id, "cmd": cmd, **kw}) + "\n").encode())
        line = self.rfile.readline()
        if not line:
            raise RuntimeError("bridge closed connection; recent log: " + " | ".join(self.log[-8:]))
        resp = json.loads(line)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error"))
        return resp["result"]

    def take_errors(self) -> list:
        time.sleep(0.05)  # let stdout catch up
        with self._lock:
            errs, self.errors = self.errors, []
        return errs

    def close(self):
        if self.slot is not None:
            with _port_lock:
                _slots.discard(self.slot)
            self.slot = None
        try:
            if getattr(self, "sock", None):
                self.sock.sendall(b'{"cmd":"quit"}\n')
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()

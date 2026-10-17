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
_next_port = [27000 + os.getpid() % 1000 * 3]


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


_import_lock = threading.Lock()


def ensure_imported(project: str, timeout: float = 120.0) -> None:
    """A fresh clone has no <project>/.godot/ (import cache, class_name registry): without it the game fails to
    resolve its own classes ("Could not find type ...") and a hunt files a false crash. Import it once
    (`--import` waits for every resource to be imported, then quits; Godot 4.3+)."""
    with _import_lock:  # parallel sessions of one hunt share the project
        if os.path.isdir(os.path.join(project, ".godot")):
            return
        print(f"[qa] first run of {os.path.basename(project)}: importing the project (once)...", file=sys.stderr, flush=True)
        try:
            subprocess.run([GODOT, "--headless", "--import", "--path", project], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[qa] import did not finish: {e}", file=sys.stderr, flush=True)


class GodotGame:
    """One running game process. Script errors printed by Godot are collected as crash evidence."""

    def __init__(self, project: str, headless: bool = True, timeout: float = 60.0, watch: bool = False):
        """watch=True: a visible window that plays at real time and shows `say` captions (judges watching)."""
        self.project = os.path.abspath(project)
        ensure_imported(self.project)
        self.port = free_port()
        args = [GODOT, "--path", self.project, "--fixed-fps", "60", "--audio-driver", "Dummy"]
        headless = headless and not watch
        if headless:
            args.insert(1, "--headless")
        else:  # rendered (for screenshots / GIF evidence) on a virtual display
            args += ["--rendering-driver", "opengl3"]
            if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
                args = ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24"] + args
        args += ["--", f"--qa-port={self.port}"] + (["--qa-watch"] if watch else [])
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
        try:
            if getattr(self, "sock", None):
                self.sock.sendall(b'{"cmd":"quit"}\n')
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()

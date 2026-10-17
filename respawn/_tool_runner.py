# Child process for sandbox.execute: reads {"source","mode","obs","args"} on stdin, prints one JSON line.
import json
import sys
import traceback

req = json.loads(sys.stdin.read())
ns = {"__name__": "agent_tool"}
try:
    exec(compile(req["source"], "agent_tool.py", "exec"), ns)
    if req["mode"] == "test":
        ns["test"](req["obs"])
        res = None
    else:
        res = ns["run"](req["obs"], **req["args"])
    print(json.dumps({"ok": True, "result": res}, default=str))
except BaseException as e:  # noqa: BLE001 - report everything back to the coach
    tb = traceback.format_exc().strip().splitlines()
    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e} | " + " / ".join(tb[-3:])}))

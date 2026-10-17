"""Visual evidence for a verified bug: replay it on a rendered (Xvfb) Godot and save a GIF + snapshot."""
from __future__ import annotations

import glob
import json
import os
import shutil
import tempfile

from PIL import Image, ImageDraw

from respawn.godot.client import GodotGame


def strip(ims: list, path: str, width: int = 320, max_frames: int = 40) -> int:
    """Same frames as the GIF, side by side in one PNG: the Godot editor dock cannot play GIFs, so it
    animates this sprite sheet instead. Kept under 16384 px wide (the max texture size)."""
    step = max(1, -(-len(ims) // max_frames))
    picked = ims[::step]
    if ims[-1] is not picked[-1]:
        picked.append(ims[-1])
    h = round(ims[0].height * width / ims[0].width)
    sheet = Image.new("RGB", (width * len(picked), h))
    for i, im in enumerate(picked):
        sheet.paste(im.resize((width, h)), (i * width, 0))
    sheet.save(path)
    return len(picked)


def capture(project_dir: str, finding: dict, out_dir: str, every: int = 2, window: int = 60) -> dict:
    """Replays finding['inputs'] act by act; captures frames of the act that triggered the bug."""
    from respawn.envs.godot_hunt import Oracle
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="qa_shots_")
    g = GodotGame(project_dir, headless=False)
    try:
        st = g.call("reset", seed=finding["seed"])
        o = Oracle(g.info.get("config") or {}, st)
        acts = finding["inputs"]
        hit_act = finding.get("act_index", len(acts) - 1)
        for i, segs in enumerate(acts):
            kw = {"capture_every": every, "capture_dir": tmp} if i == hit_act else {}
            res = g.call("act", inputs=segs, **kw)
            o.judge(res, g.take_errors())
            o.score_frames += [ev["f"] for ev in res["events"] if ev["type"] == "score"]
            o.judge_score(res["state"], res["frames"][-1]["f"] if res["frames"] else 0)
            if i == hit_act:
                break
        reproduced = finding["class"] in o.found
        bug_frame = o.found.get(finding["class"], {}).get("frame", finding.get("frame", 0))
    finally:
        g.close()
    shots = sorted(glob.glob(os.path.join(tmp, "f*.png")))
    sel = [s for s in shots if abs(int(os.path.basename(s)[1:6]) - bug_frame) <= window] or shots[-30:]
    frames = []
    for s in sel:
        im = Image.open(s).convert("RGB")
        im.thumbnail((640, 400))
        f = int(os.path.basename(s)[1:6])
        d = ImageDraw.Draw(im)
        tag = f"{finding['class'].upper()}  frame {f}" + ("  <== bug" if abs(f - bug_frame) <= every else "")
        d.rectangle([0, 0, im.width, 18], fill=(0, 0, 0))
        d.text((6, 3), tag, fill=(255, 80, 80) if abs(f - bug_frame) <= every else (255, 255, 255))
        frames.append((f, im))
    base = os.path.join(out_dir, f"{finding['class']}_seed{finding['seed']}")
    out = {"reproduced": reproduced, "bug_frame": bug_frame, "gif": None, "png": None, "strip": None, "strip_frames": 0}
    if frames:
        ims = [im for _, im in frames]
        ims[0].save(base + ".gif", save_all=True, append_images=ims[1:], duration=50, loop=0)
        key = min(frames, key=lambda t: abs(t[0] - bug_frame))[1]
        key.save(base + ".png")
        n = strip(ims, base + "_strip.png")
        out.update({"gif": base + ".gif", "png": base + ".png", "strip": base + "_strip.png", "strip_frames": n})
    json.dump({**finding, **out}, open(base + ".json", "w"), indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    return out

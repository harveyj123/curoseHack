# Crash: Invalid call. Nonexistent function 'show_air_hint (via call)' in base 'Control (PauseMenu)

**Class:** `crash`  **Game:** `/home/user/curosehack/godot/games/platformer_heldout`  **Reproduces:** yes

![failure](evidence/crash_seed1.gif)

**Expected:** No script errors during play.

**Actual:** Invalid call. Nonexistent function 'show_air_hint (via call)' in base 'Control (PauseMenu)'. at Game._unhandled_input (res://game.gd:22)

**Minimal repro** (seed 1, 1 play(s), shrunk from 1):
```
R20 J2 P2
```
**State at failure (frame 24):** `{"x": 332.02, "y": 624.38, "vx": 225, "vy": -396.5, "floor": false, "wall": false, "visible": true, "score": 1}`

**Look here first:** Game._unhandled_input (res://game.gd:22); res://player/player.gd: player logic

**Check (exit 0 = fixed):** `python -m respawn.qa check godot/games/platformer_heldout/qa_reports/crash-8a2994bb.json`

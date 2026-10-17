# QA Crew bridge: lets an external Nemotron test crew play any Godot 4 game.
#
# Add as an autoload (Project Settings > Autoload, name "QABridge"). It does nothing unless the
# game is launched with `-- --qa-port=PORT`. Then it opens a local TCP server speaking JSON lines:
#   {"cmd":"hello"}                                  -> game info + config
#   {"cmd":"state"}                                  -> current state snapshot
#   {"cmd":"act","inputs":[{"hold":["move_right"],"tap":["jump"],"frames":20}, ...]}
#        -> per-frame trace (position, velocity, floor, tunnelling, overlap), events, final state
#   {"cmd":"reset","seed":3}                         -> reload the main scene, apply a spawn for the seed
#   {"cmd":"screenshot","path":"/tmp/x.png"}         -> save the viewport (needs a display, e.g. xvfb)
#   {"cmd":"say","text":"Hunter · play R30"}          -> show a caption bar at the top of the window
#   {"cmd":"quit"}
# Run with `--fixed-fps 60` so every physics frame is exactly 1/60 s: a run is then fully
# described by (seed, inputs) and replays identically.
#
# Optional per-game config at res://qa_crew.json (see README): which node is the player, where the
# score lives, which nodes are collectibles, level bounds, spawn points, and allowed air jumps.
#
# `-- --qa-watch` (with a window) is for people watching a hunt live: `act` runs at real time and the
# window keeps redrawing while the engine is frozen between commands. Physics stepping is unchanged,
# so traces are identical to a headless run.
extends Node

var _server := TCPServer.new()
var _peer: StreamPeerTCP = null
var _buf := ""
var _busy := false
var _cfg := {}
var _frame := 0
var _seed := 0
var _pending_spawn = null
var _hud: Label = null
var _watch := false
var _caption: Label = null

# Written by the editor dock's "Replay in game" button; played once at real-time speed, then deleted.
const REPLAY_FILE := "res://qa_reports/.replay.json"

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var port := 0
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--qa-port="):
			port = int(a.split("=")[1])
		elif a == "--qa-watch":
			_watch = DisplayServer.get_name() != "headless"
	if port == 0:
		set_process(false)
		if FileAccess.file_exists(REPLAY_FILE) and DisplayServer.get_name() != "headless":
			_load_cfg()
			_replay.call_deferred()
		return
	_load_cfg()
	if _watch:
		Engine.max_fps = Engine.physics_ticks_per_second  # one physics frame per drawn frame: real time
	var err := _server.listen(port, "127.0.0.1")
	print("QA_BRIDGE listening on %d (err %d)" % [port, err])
	get_tree().node_added.connect(_on_node_added)

func _load_cfg() -> void:
	if FileAccess.file_exists("res://qa_crew.json"):
		_cfg = JSON.parse_string(FileAccess.get_file_as_string("res://qa_crew.json"))
		if _cfg == null:
			_cfg = {}

# ------------------------------------------------------------------ replay (no TCP client, no freeze)
func _replay() -> void:
	var r = JSON.parse_string(FileAccess.get_file_as_string(REPLAY_FILE))
	DirAccess.remove_absolute(ProjectSettings.globalize_path(REPLAY_FILE))
	if typeof(r) != TYPE_DICTIONARY:
		return
	var layer := CanvasLayer.new()
	layer.layer = 128
	add_child(layer)
	_hud = Label.new()
	_hud.position = Vector2(8, 6)
	_hud.add_theme_color_override("font_color", Color(1, 0.35, 0.3))
	_hud.add_theme_color_override("font_outline_color", Color.BLACK)
	_hud.add_theme_constant_override("outline_size", 4)
	layer.add_child(_hud)
	var plays: Array = r.get("plays", [])
	var label := "QA replay %s (seed %d)" % [String(r.get("id", "")), int(r.get("seed", 0))]
	_hud.text = label + "  starting..."
	await get_tree().create_timer(1.0).timeout  # let the window come up so the whole repro is seen
	await _reset(int(r.get("seed", 0)))
	for i in plays.size():
		_hud.text = "%s  play %d/%d" % [label, i + 1, plays.size()]
		await _act(plays[i])
	_hud.text = "%s  done at frame %d" % [label, _frame]

func _process(_delta: float) -> void:
	if _peer == null and _server.is_connection_available():
		_peer = _server.take_connection()
		_peer.set_no_delay(true)
	if _peer == null:
		return
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		_peer = null
		return
	var n := _peer.get_available_bytes()
	if n == 0 and not _busy and _buf.find("\n") == -1:
		# Freeze the whole engine while the crew is thinking: no frames advance between commands,
		# so a run is exactly reproducible from its command sequence.
		var t0 := Time.get_ticks_msec()
		var drawn := t0
		while n == 0 and Time.get_ticks_msec() - t0 < 20000:
			OS.delay_msec(1)
			if _watch and Time.get_ticks_msec() - drawn >= 33:
				# Watched window: keep it responsive and redrawn (~30 Hz) without advancing any frame.
				drawn = Time.get_ticks_msec()
				DisplayServer.process_events()
				RenderingServer.force_draw()
			_peer.poll()
			if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
				_peer = null
				return
			n = _peer.get_available_bytes()
	if n > 0:
		_buf += _peer.get_utf8_string(n)
	while not _busy and _buf.find("\n") != -1:
		var i := _buf.find("\n")
		var line := _buf.substr(0, i)
		_buf = _buf.substr(i + 1)
		if line.strip_edges() != "":
			_handle(line)

func _send(obj) -> void:
	if _peer:
		_peer.put_data((JSON.stringify(obj) + "\n").to_utf8_buffer())

func _handle(line: String) -> void:
	var msg = JSON.parse_string(line)
	if typeof(msg) != TYPE_DICTIONARY:
		_send({"ok": false, "error": "bad json"})
		return
	_busy = true
	var res = await _dispatch(msg)
	_busy = false
	_send({"id": msg.get("id"), "ok": true, "result": res})

func _dispatch(msg: Dictionary):
	match String(msg.get("cmd", "")):
		"hello":
			return {"name": ProjectSettings.get_setting("application/config/name"), "config": _cfg,
				"actions": InputMap.get_actions().filter(func(a): return not String(a).begins_with("ui_")),
				"physics_fps": Engine.physics_ticks_per_second}
		"state":
			return _state()
		"reset":
			return await _reset(int(msg.get("seed", 0)))
		"act":
			return await _act(msg.get("inputs", []), int(msg.get("capture_every", 0)), String(msg.get("capture_dir", "")))
		"map":
			return _map(int(msg.get("cell", 32)))
		"screenshot":
			await RenderingServer.frame_post_draw
			var img := get_viewport().get_texture().get_image()
			var p := String(msg.get("path", "user://qa_shot.png"))
			img.save_png(p)
			return {"path": p, "size": [img.get_width(), img.get_height()]}
		"say":
			_say(String(msg.get("text", "")))
			return "ok"
		"quit":
			get_tree().quit()
			return "bye"
	return {"error": "unknown cmd"}

func _say(text: String) -> void:
	# Caption bar for watched runs. Lives under the bridge (survives scene reloads), ignores the mouse.
	if _caption == null:
		var layer := CanvasLayer.new()
		layer.layer = 128
		add_child(layer)
		var bar := PanelContainer.new()
		var sb := StyleBoxFlat.new()
		sb.bg_color = Color(0, 0, 0, 0.65)
		sb.content_margin_left = 10
		sb.content_margin_right = 10
		sb.content_margin_top = 6
		sb.content_margin_bottom = 6
		bar.add_theme_stylebox_override("panel", sb)
		bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
		bar.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
		layer.add_child(bar)
		_caption = Label.new()
		_caption.add_theme_color_override("font_color", Color.WHITE)
		_caption.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		_caption.mouse_filter = Control.MOUSE_FILTER_IGNORE
		bar.add_child(_caption)
	_caption.text = text
	_caption.get_parent().visible = text != ""

# ------------------------------------------------------------------ discovery helpers
func _player() -> Node:
	var g := get_tree().get_nodes_in_group("qa_player")
	if g.size() > 0:
		return g[0]
	var want := String(_cfg.get("player_class", "Player"))
	return _find(get_tree().root, func(n): return _class_of(n) == want or n.name == want)

func _class_of(n: Node) -> String:
	var s = n.get_script()
	if s and s.get_global_name() != &"":
		return String(s.get_global_name())
	return n.get_class()

func _find(n: Node, pred: Callable) -> Node:
	if pred.call(n):
		return n
	for c in n.get_children():
		var r := _find(c, pred)
		if r:
			return r
	return null

func _find_all(n: Node, pred: Callable, out: Array) -> Array:
	if pred.call(n):
		out.append(n)
	for c in n.get_children():
		_find_all(c, pred, out)
	return out

func _score():
	var sc = _cfg.get("score")
	if sc == null:
		return null
	var node: Node = null
	if sc.has("node"):
		node = _find(get_tree().root, func(n): return n.name == String(sc["node"]))
	else:
		node = _find(get_tree().root, func(n): return _class_of(n) == String(sc.get("class", "")))
	if node == null:
		return null
	return node.get(String(sc.get("property", "score")))

func _collectibles() -> int:
	var cls := String(_cfg.get("collectible_class", ""))
	if cls == "":
		return -1
	# A pickup that has switched its Area2D monitoring off is already taken, even while its pickup
	# animation still runs (or is frozen because the game is paused).
	var arr := _find_all(get_tree().root, func(n): return _class_of(n) == cls and n.is_inside_tree() and not n.is_queued_for_deletion() and not (n is Area2D and not n.monitoring), [])
	return arr.size()

func _bounds() -> Dictionary:
	var b = _cfg.get("bounds")
	if b != null:
		return b
	var p := _player()
	if p and p.has_node("Camera"):
		var c := p.get_node("Camera") as Camera2D
		return {"left": c.limit_left, "top": c.limit_top, "right": c.limit_right, "bottom": c.limit_bottom}
	var r := get_viewport().get_visible_rect()
	return {"left": r.position.x, "top": r.position.y, "right": r.end.x, "bottom": r.end.y}

func _on_node_added(n: Node) -> void:
	if _pending_spawn != null and n == _player():
		pass

# ------------------------------------------------------------------ state + stepping
func _state() -> Dictionary:
	var p := _player()
	var st := {"frame": _frame, "seed": _seed, "score": _score(), "collectibles_left": _collectibles(),
		"bounds": _bounds(), "paused": get_tree().paused,
		"scene": get_tree().current_scene.scene_file_path if get_tree().current_scene else ""}
	if p:
		st["player"] = _body_state(p)
	if get_tree().current_scene and get_tree().current_scene.has_method("qa_state"):
		st["game"] = get_tree().current_scene.qa_state()
	var hostiles := []
	var hcls := String(_cfg.get("enemy_class", ""))
	if hcls != "":
		for e in _find_all(get_tree().root, func(n): return _class_of(n) == hcls, []):
			if e is Node2D:
				hostiles.append([round((e as Node2D).global_position.x), round((e as Node2D).global_position.y)])
	st["enemies"] = hostiles
	return st

func _body_state(p: Node) -> Dictionary:
	var d := {"x": 0.0, "y": 0.0}
	if p is Node2D:
		d["x"] = snappedf((p as Node2D).global_position.x, 0.01)
		d["y"] = snappedf((p as Node2D).global_position.y, 0.01)
	if "velocity" in p:
		d["vx"] = snappedf(p.velocity.x, 0.01)
		d["vy"] = snappedf(p.velocity.y, 0.01)
	if p.has_method("is_on_floor"):
		d["floor"] = p.is_on_floor()
		d["wall"] = p.is_on_wall()
	if "visible" in p:
		d["visible"] = p.visible
	if p.get_script():
		d["script"] = p.get_script().resource_path
	return d

func _reset(seed: int) -> Dictionary:
	_seed = seed
	get_tree().paused = false
	for a in InputMap.get_actions():
		Input.action_release(a)
	seed(seed)  # global RNG, so games that use randf()/randi() are reproducible too
	get_tree().reload_current_scene()
	await get_tree().process_frame
	await get_tree().physics_frame
	var spawns = _cfg.get("spawns", [])
	var p := _player()
	if p and spawns.size() > 0:
		var s = spawns[seed % spawns.size()]
		(p as Node2D).global_position = Vector2(s[0], s[1])
	var hook = _cfg.get("on_reset_call")  # e.g. press "Start" for games that open on a title screen
	if hook != null:
		var hn := _find(get_tree().root, func(n): return n.name == String(hook["node"]))
		if hn and hn.has_method(String(hook["method"])):
			hn.call(String(hook["method"]))
	_frame = 0
	for i in 3:
		await get_tree().physics_frame
	return _state()

func _map(cell: int) -> Dictionary:
	# Coarse collision map of the level (# = solid), sampled at cell centres inside the bounds.
	var b := _bounds()
	var p := _player()
	if p == null:
		return {}
	var space := (p as Node2D).get_world_2d().direct_space_state
	var rows := []
	var y := float(b["top"])
	while y < float(b["bottom"]):
		var row := ""
		var x := float(b["left"])
		while x < float(b["right"]):
			var q := PhysicsPointQueryParameters2D.new()
			q.position = Vector2(x + cell / 2.0, y + cell / 2.0)
			q.collision_mask = (p as CollisionObject2D).collision_mask if p is CollisionObject2D else 1
			var solid := false
			for h in space.intersect_point(q, 4):
				if h.collider is StaticBody2D or h.collider is TileMap or h.collider is AnimatableBody2D:
					solid = true
			row += "#" if solid else "."
			x += cell
		rows.append(row)
		y += cell
	return {"cell": cell, "left": b["left"], "top": b["top"], "rows": rows}

func _is_solid(h: Dictionary) -> bool:
	# Solid level geometry only. One-way platforms are excluded: the player legally jumps up through them
	# and can overlap them, so counting them as walls reports clips in an unmodified game.
	var c = h.get("collider")
	if not (c is StaticBody2D or c is TileMap or c is AnimatableBody2D):
		return false
	if c is CollisionObject2D:
		var owner_id: int = c.shape_find_owner(int(h.get("shape", 0)))
		return not c.is_shape_owner_one_way_collision_enabled(owner_id)
	if c.has_method("get_coords_for_body_rid"):
		var coords: Vector2i = c.get_coords_for_body_rid(h["rid"])
		for layer in c.get_layers_count():
			var td: TileData = c.get_cell_tile_data(layer, coords)
			if td:
				for pl in c.tile_set.get_physics_layers_count():
					for i in td.get_collision_polygons_count(pl):
						if td.is_collision_polygon_one_way(pl, i):
							return false
	return true

func _tunnel_check(p: Node2D, a: Vector2, b: Vector2) -> Dictionary:
	# A character body should never pass THROUGH static geometry between two physics frames.
	if a.distance_to(b) < 0.5 or not (p is CollisionObject2D):
		return {}
	var space := p.get_world_2d().direct_space_state
	var out := {}
	var from := a
	for _i in 4:  # step past one-way platforms the ray hits first
		var q := PhysicsRayQueryParameters2D.create(from, b, (p as CollisionObject2D).collision_mask, [(p as CollisionObject2D).get_rid()])
		q.collide_with_areas = false
		var hit := space.intersect_ray(q)
		if hit.is_empty() or hit.collider is CharacterBody2D or hit.collider is RigidBody2D:
			break
		if _is_solid(hit):
			out["tunnel"] = true
			break
		from = hit.position + (b - a).normalized() * 0.5
		if from.distance_to(a) >= a.distance_to(b):
			break
	var pq := PhysicsPointQueryParameters2D.new()
	pq.position = b
	pq.collision_mask = (p as CollisionObject2D).collision_mask
	pq.exclude = [(p as CollisionObject2D).get_rid()]
	for h in space.intersect_point(pq, 4):
		if _is_solid(h):
			out["inside"] = true
	return out

func _press(a: String, pressed: bool) -> void:
	# Held state goes through Input.action_press/release, and a real InputEventAction is pushed to the
	# viewport so _input/_unhandled_input handlers and button shortcuts see it like a keyboard press.
	# (Input.parse_input_event is avoided: in Godot 4.3 each parsed InputEventAction releases every
	# other held action, so "move right + jump" would drop the move.)
	if pressed:
		Input.action_press(a)
	else:
		Input.action_release(a)
	var ev := InputEventAction.new()
	ev.action = a
	ev.pressed = pressed
	get_viewport().push_input(ev)

func _act(inputs: Array, capture_every: int = 0, capture_dir: String = "") -> Dictionary:
	var frames := []
	var events := []
	var p := _player()
	var prev_score = _score()
	var prev_coll := _collectibles()
	var prev_pos := (p as Node2D).global_position if p is Node2D else Vector2.ZERO
	var scene0 := get_tree().current_scene
	for seg in inputs:
		var hold: Array = seg.get("hold", [])
		var tap: Array = seg.get("tap", [])
		var n := clampi(int(seg.get("frames", 1)), 1, 600)
		for a in hold:
			if InputMap.has_action(a):
				_press(a, true)
		for k in n:
			if k == 0:
				for a in tap:
					if InputMap.has_action(a):
						_press(a, true)
			await get_tree().physics_frame
			_frame += 1
			if k == 0:
				for a in tap:
					if InputMap.has_action(a) and not hold.has(a):
						_press(a, false)
			p = _player()
			if p == null or not is_instance_valid(p):
				events.append({"f": _frame, "type": "player_gone"})
				break
			var bs := _body_state(p)
			bs["f"] = _frame
			if p is Node2D:
				var cur := (p as Node2D).global_position
				bs.merge(_tunnel_check(p, prev_pos, cur))
				prev_pos = cur
			frames.append(bs)
			if capture_every > 0 and _frame % capture_every == 0 and DisplayServer.get_name() != "headless":
				await RenderingServer.frame_post_draw
				get_viewport().get_texture().get_image().save_png("%s/f%05d.png" % [capture_dir, _frame])
			var sc = _score()
			if sc != prev_score:
				events.append({"f": _frame, "type": "score", "from": prev_score, "to": sc})
				prev_score = sc
			var cl := _collectibles()
			if cl != prev_coll:
				events.append({"f": _frame, "type": "collectibles", "from": prev_coll, "to": cl})
				prev_coll = cl
			if get_tree().current_scene != scene0:
				events.append({"f": _frame, "type": "scene_change"})
				scene0 = get_tree().current_scene
		for a in hold:
			if InputMap.has_action(a):
				_press(a, false)
	return {"frames": frames, "events": events, "state": _state()}

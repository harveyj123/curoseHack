@tool
extends VBoxContainer
## Editor dock: run the QA crew, read its bug reports, replay and check them, and see how the crew learned.
## Tabs: Bugs (reports in res://qa_reports), Rules (house rules in res://qa_rules.json), Crew (lineage of a run),
## Run (settings + live log).
## The key is stored in EditorSettings (per machine), never in the project.

const KEY_SETTING := "qa_crew/crusoe_api_key"
const REPO_SETTING := "qa_crew/crew_path"
const GENOME_SETTING := "qa_crew/crew_genome"
const RUNDIR_SETTING := "qa_crew/run_folder"
const PYTHON_SETTING := "qa_crew/python"
const WATCH_SETTING := "qa_crew/watch_live"
const REPORTS := "res://qa_reports"
const RULES_FILE := "res://qa_rules.json"
const REPLAY_FILE := "res://qa_reports/.replay.json"
const GD_REF := "res://[\\w./-]+\\.gd(?::(\\d+))?"
const HELDOUT_NAMES := {"godot_hunt_heldout": "Platformer, bug set B", "godot_hunt_dodge": "Dodge, bug set C"}

var tabs := TabContainer.new()
# Run tab
var key_edit := LineEdit.new()
var repo_edit := LineEdit.new()
var genome_edit := LineEdit.new()
var python_edit := LineEdit.new()
var seeds_edit := LineEdit.new()
var watch_check := CheckBox.new()
var run_button := Button.new()
var output := RichTextLabel.new()
# Bugs tab
var bug_count := Label.new()
var bug_list := VBoxContainer.new()
var _rows: Array = []
var _row_group := ButtonGroup.new()
var detail := VBoxContainer.new()
var d_title := Label.new()
var d_meta := RichTextLabel.new()
var d_anim := TextureRect.new()
var d_info := RichTextLabel.new()
var open_button := Button.new()
var replay_button := Button.new()
var check_button := Button.new()
var check_label := RichTextLabel.new()
var empty_hint := Label.new()
# Crew tab
var run_edit := LineEdit.new()
var crew_summary := RichTextLabel.new()
var chart := Control.new()
var nights_text := RichTextLabel.new()
var heldout_text := RichTextLabel.new()
# Rules tab
var rule_input := TextEdit.new()
var rules_list := VBoxContainer.new()
var rules_log := RichTextLabel.new()
var rules_count := Label.new()
var compile_button := Button.new()
var _rules: Array = []
var _rule_open := {}
var _compile_job = null
var _compile_t0 := 0
# How-it-works stepper + run status
const STEPS := [
	["Hunt", "Nemotron hunters play the game through the bridge and try to break it"],
	["Verify", "The oracle replays each finding, shrinks the repro and records a GIF: only real bugs count"],
	["Coach", "Every night Nemotron Super reads the play logs and rewrites the playbook or writes a tool"],
	["Gate", "A change is kept only if the crew scores higher on the same sessions (P(better))"],
	["Unseen", "Version 0 vs the latest crew on games it never trained on"],
]
var _step_buttons: Array = []
var step_caption := Label.new()
var _active_step := -1
var run_status := Label.new()
var run_progress := ProgressBar.new()
# Bugs filter / sort
var class_filter := OptionButton.new()
var sort_mode := OptionButton.new()
var _all_reports: Array = []
# Crew tab extras
var crew_scroll: ScrollContainer
var score_card := PanelContainer.new()
var card_box := VBoxContainer.new()
var coach_title: Label
var coach_text := RichTextLabel.new()
var nights_title: Label

var _reports: Array = []
var _sel := -1
var _anim_timer := Timer.new()
var _atlas: AtlasTexture
var _anim_n := 0
var _anim_i := 0
var _anim_hold := 0
var _nights: Array = []
var _jobs: Array = []
var _run_job = null
var _gd_ref := RegEx.create_from_string(GD_REF)

func _ready() -> void:
	var s := EditorInterface.get_editor_scale()
	custom_minimum_size = Vector2(380, 0) * s
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_build_steps())
	add_child(tabs)
	tabs.add_child(_build_bugs())
	tabs.add_child(_build_rules())
	tabs.add_child(_build_crew())
	tabs.add_child(_build_run())
	_anim_timer.wait_time = 0.08
	_anim_timer.timeout.connect(_anim_tick)
	add_child(_anim_timer)
	_refresh_bugs()
	_load_rules()
	_load_crew()
	# For docs screenshots: QA_DOCK_TAB=Rules opens that tab (and the first rule's check).
	var want := OS.get_environment("QA_DOCK_TAB")
	if want != "":
		for i in tabs.get_tab_count():
			if tabs.get_tab_control(i).name == want:
				tabs.current_tab = i
		if want == "Rules" and _rules.size() > 0:
			_rule_open[str(_rules[0].get("id", 0))] = true
			_render_rules()

# ------------------------------------------------------------------ severity (mirrors respawn.qa.severity)
func _severity(cls: String) -> int:
	# 2 = high, 1 = medium, 0 = low. The one place the dock maps bug classes to severity.
	if cls == "crash":
		return 2
	if cls == "clip" or cls == "out_of_bounds" or cls.begins_with("rule:"):
		return 1
	return 0

const SEV_NAMES := ["LOW", "MED", "HIGH"]
const SEV_COLORS := ["#6b7385", "#c98a2b", "#c9423b"]

func _class_label(cls: String) -> String:
	return ("RULE · " + cls.substr(5)) if cls.begins_with("rule:") else cls.to_upper()

# ------------------------------------------------------------------ how-it-works stepper
func _build_steps() -> Control:
	var s := EditorInterface.get_editor_scale()
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", int(2 * s))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", int(3 * s))
	for i in STEPS.size():
		var b := Button.new()
		b.text = "%d %s" % [i + 1, STEPS[i][0]]
		b.tooltip_text = STEPS[i][1]
		b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		b.clip_text = true
		b.add_theme_font_size_override("font_size", int(12 * s))
		b.focus_mode = Control.FOCUS_NONE
		b.pressed.connect(_on_step.bind(i))
		b.mouse_entered.connect(func(): step_caption.text = "%d. %s" % [i + 1, STEPS[i][1]])
		b.mouse_exited.connect(_caption_default)
		row.add_child(b)
		_step_buttons.append(b)
	box.add_child(row)
	step_caption.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	step_caption.add_theme_font_size_override("font_size", int(11 * s))
	step_caption.add_theme_color_override("font_color", _col("font_disabled_color"))
	box.add_child(step_caption)
	_paint_steps()
	_caption_default()
	return box

func _caption_default() -> void:
	if _active_step >= 0:
		step_caption.text = "Now: %d. %s" % [_active_step + 1, STEPS[_active_step][1]]
	else:
		step_caption.text = "How it works: hunt → verify → coach → gate → unseen games. Click a step to see its output."

func _pill(bg: Color, border: Color) -> StyleBoxFlat:
	var s := EditorInterface.get_editor_scale()
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.border_color = border
	sb.set_border_width_all(int(1 * s))
	sb.set_corner_radius_all(int(11 * s))
	sb.content_margin_left = 6 * s
	sb.content_margin_right = 6 * s
	sb.content_margin_top = 2 * s
	sb.content_margin_bottom = 2 * s
	return sb

func _paint_steps() -> void:
	var acc := _col("accent_color")
	for i in _step_buttons.size():
		var b: Button = _step_buttons[i]
		var on := i == _active_step
		var normal := _pill(Color(acc, 0.85) if on else Color(0, 0, 0, 0.22), acc if on else Color(1, 1, 1, 0.12))
		var hover := _pill(Color(acc, 0.95) if on else Color(acc, 0.25), acc)
		b.add_theme_stylebox_override("normal", normal)
		b.add_theme_stylebox_override("hover", hover)
		b.add_theme_stylebox_override("pressed", hover)
		b.add_theme_color_override("font_color", Color.WHITE if on else _col("font_color"))
		b.add_theme_color_override("font_hover_color", Color.WHITE)

func _set_step(i: int) -> void:
	_active_step = i
	_paint_steps()
	_caption_default()

func _tab_index(tab_name: String) -> int:
	for i in tabs.get_tab_count():
		if tabs.get_tab_control(i).name == tab_name:
			return i
	return 0

func _on_step(i: int) -> void:
	step_caption.text = "%d. %s" % [i + 1, STEPS[i][1]]
	if i <= 1:
		tabs.current_tab = _tab_index("Bugs")
		return
	tabs.current_tab = _tab_index("Crew")
	var target: Control = score_card
	if i == 2:
		target = coach_title
	elif i == 3:
		target = nights_title
	_scroll_crew_to.call_deferred(target)

func _scroll_crew_to(c: Control) -> void:
	if crew_scroll and c:
		crew_scroll.scroll_vertical = int(c.position.y)
		var tw := create_tween()
		c.modulate = Color(1.6, 1.6, 1.0)
		tw.tween_property(c, "modulate", Color.WHITE, 1.2)

# ------------------------------------------------------------------ layout
func _section(text: String) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_color_override("font_color", _col("accent_color"))
	return l

func _col(name: String) -> Color:
	return EditorInterface.get_editor_theme().get_color(name, "Editor")

func _rich(r: RichTextLabel) -> RichTextLabel:
	r.bbcode_enabled = true
	r.fit_content = true
	r.scroll_active = false
	r.selection_enabled = true
	r.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return r

func _build_bugs() -> Control:
	var s := EditorInterface.get_editor_scale()
	var root := VBoxContainer.new()
	root.name = "Bugs"
	var bar := HBoxContainer.new()
	bug_count.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bug_count.clip_text = true
	bar.add_child(bug_count)
	var refresh := Button.new()
	refresh.text = "Refresh"
	refresh.pressed.connect(_refresh_bugs)
	bar.add_child(refresh)
	root.add_child(bar)
	var bar2 := HBoxContainer.new()
	class_filter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	class_filter.clip_text = true
	class_filter.tooltip_text = "Show one bug class"
	class_filter.item_selected.connect(func(_i): _apply_bugs())
	bar2.add_child(class_filter)
	sort_mode.add_item("Newest first")
	sort_mode.add_item("Severity first")
	sort_mode.selected = 1
	sort_mode.item_selected.connect(func(_i): _apply_bugs())
	bar2.add_child(sort_mode)
	root.add_child(bar2)
	var split := VSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(split)
	var list_scroll := ScrollContainer.new()
	list_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	list_scroll.custom_minimum_size = Vector2(0, 236) * s
	bug_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	list_scroll.add_child(bug_list)
	split.add_child(list_scroll)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.custom_minimum_size = Vector2(0, 240) * s
	split.add_child(scroll)
	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(box)
	empty_hint.text = "No bugs yet: press Run QA crew in the Run tab (or Refresh after a hunt)."
	empty_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(empty_hint)
	detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(detail)
	d_title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	d_title.add_theme_font_size_override("font_size", int(16 * s))
	detail.add_child(d_title)
	detail.add_child(_rich(d_meta))
	d_anim.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	d_anim.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	d_anim.custom_minimum_size = Vector2(0, 200) * s
	d_anim.texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR
	detail.add_child(d_anim)
	var buttons := HFlowContainer.new()
	for b in [[open_button, "Open code", _open_code], [replay_button, "Replay in game", _replay], [check_button, "Check fix", _check]]:
		b[0].text = b[1]
		b[0].pressed.connect(b[2])
		buttons.add_child(b[0])
	open_button.tooltip_text = "Open the suspect script at the line to look at first"
	replay_button.tooltip_text = "Run the game and play the exact minimal repro at normal speed"
	check_button.tooltip_text = "Re-run the repro headless: green once the bug no longer reproduces"
	detail.add_child(buttons)
	detail.add_child(_rich(check_label))
	detail.add_child(_rich(d_info))
	d_info.meta_clicked.connect(func(meta): _open_ref(str(meta)))
	detail.visible = false
	return root

func _build_crew() -> Control:
	var s := EditorInterface.get_editor_scale()
	var scroll := ScrollContainer.new()
	scroll.name = "Crew"
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	crew_scroll = scroll
	var root := VBoxContainer.new()
	root.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(root)
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(_col("accent_color"), 0.12)
	sb.border_color = Color(_col("accent_color"), 0.6)
	sb.set_border_width_all(int(1 * s))
	sb.set_corner_radius_all(int(6 * s))
	sb.set_content_margin_all(10 * s)
	score_card.add_theme_stylebox_override("panel", sb)
	score_card.add_child(card_box)
	root.add_child(score_card)
	var bar := HBoxContainer.new()
	var l := Label.new()
	l.text = "Run folder"
	bar.add_child(l)
	run_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var es := EditorInterface.get_editor_settings()
	run_edit.text = es.get_setting(RUNDIR_SETTING) if es.has_setting(RUNDIR_SETTING) else "runs/hunt1"
	run_edit.text_submitted.connect(func(_t): _load_crew())
	bar.add_child(run_edit)
	var load_button := Button.new()
	load_button.text = "Load"
	load_button.pressed.connect(_load_crew)
	bar.add_child(load_button)
	root.add_child(bar)
	root.add_child(_rich(crew_summary))
	root.add_child(_section("Score per night"))
	chart.custom_minimum_size = Vector2(0, 180) * s
	chart.draw.connect(_draw_chart)
	chart.resized.connect(chart.queue_redraw)
	root.add_child(chart)
	root.add_child(_section("Unseen games: version 0 vs latest"))
	root.add_child(_rich(heldout_text))
	coach_title = _section("Coach: latest change to the playbook")
	root.add_child(coach_title)
	root.add_child(_rich(coach_text))
	nights_title = _section("Gate: every night's verdict")
	root.add_child(nights_title)
	root.add_child(_rich(nights_text))
	return scroll

func _build_run() -> Control:
	var root := VBoxContainer.new()
	root.name = "Run"
	var es := EditorInterface.get_editor_settings()
	_add_row(root, "Crusoe API key", key_edit)
	key_edit.secret = true
	key_edit.placeholder_text = "Intelligence API key"
	key_edit.text = es.get_setting(KEY_SETTING) if es.has_setting(KEY_SETTING) else ""
	_add_row(root, "Crew folder", repo_edit)
	repo_edit.placeholder_text = "path to the curosehack repo"
	repo_edit.text = es.get_setting(REPO_SETTING) if es.has_setting(REPO_SETTING) else _guess_repo()
	_add_row(root, "Python", python_edit)
	python_edit.placeholder_text = "python3 with Pillow (e.g. <crew folder>/.venv/bin/python3)"
	python_edit.text = es.get_setting(PYTHON_SETTING) if es.has_setting(PYTHON_SETTING) else _guess_python()
	_add_row(root, "Crew genome", genome_edit)
	genome_edit.placeholder_text = "empty = newest trained crew in demo/genomes; \"none\" = untrained"
	genome_edit.text = es.get_setting(GENOME_SETTING) if es.has_setting(GENOME_SETTING) else ""
	_add_row(root, "Sessions (seeds)", seeds_edit)
	seeds_edit.text = "0-3"
	watch_check.text = "Watch live (first session plays in a game window)"
	watch_check.button_pressed = es.get_setting(WATCH_SETTING) if es.has_setting(WATCH_SETTING) else false
	root.add_child(watch_check)
	run_button.text = "Run QA crew"
	run_button.pressed.connect(_on_run)
	root.add_child(run_button)
	run_status.text = "Idle. Press Run QA crew to start a hunt."
	run_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(run_status)
	run_progress.min_value = 0
	run_progress.max_value = 1
	run_progress.value = 0
	run_progress.show_percentage = false
	run_progress.custom_minimum_size = Vector2(0, 8) * EditorInterface.get_editor_scale()
	root.add_child(run_progress)
	output.bbcode_enabled = true
	output.scroll_following = true
	output.selection_enabled = true
	output.custom_minimum_size = Vector2(0, 320)
	output.size_flags_vertical = Control.SIZE_EXPAND_FILL
	output.meta_clicked.connect(func(meta): OS.shell_open(str(meta)))
	root.add_child(output)
	return root

func _add_row(root: Control, label: String, field: Control) -> void:
	var l := Label.new()
	l.text = label
	root.add_child(l)
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root.add_child(field)

func _guess_repo() -> String:
	# The demo games live inside the crew repo: walk up until respawn/qa.py shows up.
	var d := ProjectSettings.globalize_path("res://").trim_suffix("/")
	for i in 6:
		if FileAccess.file_exists(d.path_join("respawn/qa.py")):
			return d
		d = d.get_base_dir()
	return ""

func _guess_python() -> String:
	# A Godot started from the macOS Finder only sees the system python3 (no Pillow): prefer the crew's venv.
	for v in [".venv/bin/python3", "venv/bin/python3"]:
		var p := repo_edit.text.path_join(v)
		if repo_edit.text != "" and FileAccess.file_exists(p):
			return p
	return "python3"

func _python() -> String:
	return python_edit.text.strip_edges() if python_edit.text.strip_edges() != "" else "python3"

func _esc(t) -> String:
	return str(t).replace("[", "[lb]")

# ------------------------------------------------------------------ subprocesses (read on a thread: pipes block)
func _spawn(argv: PackedStringArray, on_line: Callable, on_exit: Callable):
	# GODOT_BIN: run the games with this editor's own binary (the crew's default path is Linux-only).
	var env := PackedStringArray(["PYTHONPATH=" + repo_edit.text, "PYTHONUNBUFFERED=1", "GODOT_BIN=" + OS.get_executable_path()])
	var res := OS.execute_with_pipe("sh", PackedStringArray(["-c", "exec \"$@\" 2>&1", "sh", "env"]) + env + argv)
	if res.is_empty():
		return null
	var job := {"pid": res["pid"], "pipe": res["stdio"], "lines": [], "mutex": Mutex.new(), "thread": Thread.new(),
		"on_line": on_line, "on_exit": on_exit, "out": ""}
	job["thread"].start(_read_pipe.bind(job))
	_jobs.append(job)
	return job

func _read_pipe(job: Dictionary) -> void:
	var pipe: FileAccess = job["pipe"]
	while true:
		var line := pipe.get_line()
		# Godot 4.3 pipes never flag EOF: an empty read after the process exited ends the stream.
		if line == "" and (pipe.eof_reached() or pipe.get_error() != OK or not OS.is_process_running(job["pid"])):
			break
		job["mutex"].lock()
		job["lines"].append(line)
		job["mutex"].unlock()

func _process(_delta: float) -> void:
	if _compile_job != null:  # a Super call can take a minute under load: show it is alive
		var secs := (Time.get_ticks_msec() - _compile_t0) / 1000
		compile_button.text = "%s Compiling with Nemotron... %ds" % [["|", "/", "-", "\\"][(Time.get_ticks_msec() / 150) % 4], secs]
	for job in _jobs.duplicate():
		job["mutex"].lock()
		var lines: Array = job["lines"].duplicate()
		job["lines"].clear()
		job["mutex"].unlock()
		for line in lines:
			job["out"] += line + "\n"
			job["on_line"].call(line)
		if not job["thread"].is_alive() and not OS.is_process_running(job["pid"]):
			job["thread"].wait_to_finish()
			_jobs.erase(job)
			var code := OS.get_process_exit_code(job["pid"])
			if code > 255:  # Godot 4.3 returns the raw wait status (exit code << 8)
				code = code >> 8
			job["on_exit"].call(code, job["out"])

func _exit_tree() -> void:
	for job in _jobs:
		OS.kill(job["pid"])
		job["thread"].wait_to_finish()
	_jobs.clear()

# ------------------------------------------------------------------ Run tab
func _newest_demo_genome() -> String:
	# The trained crew shipped with the repo (demo/genomes/gen_NNN), so a fresh checkout hunts with it by default.
	var names := Array(DirAccess.get_directories_at(repo_edit.text.path_join("demo/genomes")))
	names.sort()
	return "demo/genomes/" + names[-1] if names.size() > 0 else ""

func _on_run() -> void:
	var es := EditorInterface.get_editor_settings()
	es.set_setting(KEY_SETTING, key_edit.text)
	es.set_setting(REPO_SETTING, repo_edit.text)
	es.set_setting(GENOME_SETTING, genome_edit.text)
	es.set_setting(PYTHON_SETTING, python_edit.text)
	es.set_setting(WATCH_SETTING, watch_check.button_pressed)
	if key_edit.text.strip_edges() == "" or repo_edit.text.strip_edges() == "":
		output.append_text("[color=orange]Set the Crusoe API key and the crew folder first.[/color]\n")
		return
	var project := ProjectSettings.globalize_path("res://")
	var out_dir := ProjectSettings.globalize_path(REPORTS)
	var args := PackedStringArray(["CRUSOE_API_KEY=" + key_edit.text,
		_python(), "-m", "respawn.qa", "hunt", "--project", project, "--seeds", seeds_edit.text, "--out", out_dir])
	var g := genome_edit.text.strip_edges()
	if g == "":
		g = _newest_demo_genome()
	if g != "" and g != "none":
		if not g.is_absolute_path():
			g = repo_edit.text.path_join(g)
		output.append_text("Crew genome: %s\n" % _esc(g))
		args.append_array(["--genome", g])
	if watch_check.button_pressed:
		args.append("--watch")
	_run_job = _spawn(args, _show, _run_done)
	if _run_job == null:
		output.append_text("[color=red]Could not start %s.[/color]\n" % _esc(_python()))
		return
	run_button.disabled = true
	_bugs_this_run = 0
	_set_status("Starting the crew...", 0, 1)
	_set_step(0)
	output.append_text("[b]QA crew started[/b] on %s\n" % project)

var _bugs_this_run := 0

func _set_status(t: String, done: int, total: int) -> void:
	run_status.text = t
	run_progress.max_value = maxi(1, total)
	run_progress.value = done

func _run_done(code: int, _out: String) -> void:
	_run_job = null
	run_button.disabled = false
	_set_step(-1)
	if code == 0:
		_set_status("Done: %d verified bug report(s). See the Bugs tab." % _bugs_this_run, 1, 1)
	else:
		_set_status("Stopped with exit code %d: see the log below." % code, 0, 1)
	output.append_text("[b]Done[/b] (exit %d). Reports are in res://qa_reports/\n" % code)
	EditorInterface.get_resource_filesystem().scan()
	_refresh_bugs()
	tabs.current_tab = 0

func _show(line: String) -> void:
	if line.begins_with("PROGRESS "):  # PROGRESS hunt 2/4 bugs=... | PROGRESS verify 1/3 (respawn.qa hunt)
		var parts := line.split(" ")
		var kd := parts[2].split("/") if parts.size() > 2 else PackedStringArray(["0", "1"])
		var k := int(kd[0])
		var n := int(kd[1]) if kd.size() > 1 else 1
		if parts[1] == "hunt":
			_set_step(0)
			_set_status("Hunting: %d of %d session(s) played" % [k, n], k, n)
		else:
			_set_step(1)
			_set_status("Verifying: %d of %d finding(s) replayed, minimized and recorded" % [k, n] if n > 0 else "No new findings to verify", k, maxi(n, 1))
		return
	if line.begins_with("["):  # a bug report line from respawn.qa
		_bugs_this_run += 1
		var id := line.get_slice("-> ", 1).strip_edges()
		var path := ProjectSettings.globalize_path("res://qa_reports/" + id)
		output.append_text("[color=tomato]BUG[/color] %s  [url=%s]report[/url]\n" % [_esc(line.get_slice("  repro", 0)), path])
	elif line.begins_with("LIVE "):  # a watched hunter's move (respawn.qa hunt --watch)
		output.append_text("[color=#5ec8e5]%s[/color]\n" % _esc(line.substr(5)))
	else:
		output.append_text(_esc(line) + "\n")

# ------------------------------------------------------------------ Bugs tab
func _refresh_bugs() -> void:
	_all_reports.clear()
	if DirAccess.dir_exists_absolute(REPORTS):
		for f in DirAccess.get_files_at(REPORTS):
			if not f.ends_with(".json") or f.begins_with(".") or f == "index.json":
				continue
			var path := REPORTS.path_join(f)
			var r = JSON.parse_string(FileAccess.get_file_as_string(path))
			if typeof(r) == TYPE_DICTIONARY and r.has("bug_class"):
				r["_path"] = path
				r["_mtime"] = FileAccess.get_modified_time(path)
				_all_reports.append(r)
	# class filter: keep the current choice when it still exists
	var cur := class_filter.get_item_text(class_filter.selected) if class_filter.selected >= 0 else ""
	var classes := []
	for r in _all_reports:
		if not classes.has(r["bug_class"]):
			classes.append(r["bug_class"])
	classes.sort()
	class_filter.clear()
	class_filter.add_item("All classes")
	for c in classes:
		class_filter.add_item(c)
		if c == cur:
			class_filter.selected = class_filter.item_count - 1
	_apply_bugs()

func _apply_bugs() -> void:
	var keep := ""
	if _sel >= 0 and _sel < _reports.size():
		keep = _reports[_sel].get("id", "")
	_reports.clear()
	for row in _rows:
		row.queue_free()
	_rows.clear()
	var want := class_filter.get_item_text(class_filter.selected) if class_filter.selected > 0 else ""
	for r in _all_reports:
		if want == "" or r["bug_class"] == want:
			_reports.append(r)
	if sort_mode.selected == 1:
		_reports.sort_custom(func(a, b):
			var sa := _severity(a["bug_class"])
			var sb := _severity(b["bug_class"])
			return sa > sb if sa != sb else int(a["_mtime"]) > int(b["_mtime"]))
	else:
		_reports.sort_custom(func(a, b): return int(a["_mtime"]) > int(b["_mtime"]) if a["_mtime"] != b["_mtime"] else str(a.get("id", "")) < str(b.get("id", "")))
	var n_rep := 0
	var n_high := 0
	for r in _all_reports:
		n_rep += 1 if r.get("reproduced", false) else 0
		n_high += 1 if _severity(r["bug_class"]) == 2 else 0
	for r in _reports:
		var row := _bug_row(r, _rows.size())
		bug_list.add_child(row)
		_rows.append(row)
	bug_count.text = "%d bug(s): %d reproduce, %d high severity" % [_all_reports.size(), n_rep, n_high] if _all_reports.size() > 0 else "No bug reports yet"
	class_filter.visible = _all_reports.size() > 0
	sort_mode.visible = _all_reports.size() > 0
	empty_hint.text = "No bugs yet: press Run QA crew in the Run tab (or Refresh after a hunt)." if _all_reports.is_empty() else "No %s bugs. Pick All classes." % want
	empty_hint.visible = _reports.is_empty()
	_sel = -1
	detail.visible = false
	_anim_timer.stop()
	for i in _reports.size():
		if _reports[i].get("id", "") == keep or (keep == "" and i == 0):
			_rows[i].button_pressed = true
			_select(i)
			break

func _bug_row(r: Dictionary, i: int) -> Button:
	# One selectable row: thumbnail, bug class + "reproduces" mark, title (two lines at most).
	var s := EditorInterface.get_editor_scale()
	var ok: bool = r.get("reproduced", false)
	var b := Button.new()
	b.toggle_mode = true
	b.button_group = _row_group
	b.custom_minimum_size = Vector2(0, 68) * s
	b.tooltip_text = r.get("title", "")
	b.pressed.connect(_select.bind(i))
	var hb := HBoxContainer.new()
	hb.set_anchors_preset(Control.PRESET_FULL_RECT)
	hb.offset_left = 4 * s
	hb.offset_top = 4 * s
	hb.offset_right = -6 * s
	hb.offset_bottom = -4 * s
	hb.mouse_filter = Control.MOUSE_FILTER_IGNORE
	b.add_child(hb)
	var thumb := TextureRect.new()
	thumb.texture = _thumb(r)
	thumb.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	thumb.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	thumb.custom_minimum_size = Vector2(96, 60) * s
	hb.add_child(thumb)
	var vb := VBoxContainer.new()
	vb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	vb.alignment = BoxContainer.ALIGNMENT_CENTER
	vb.add_theme_constant_override("separation", 0)
	hb.add_child(vb)
	var head := HBoxContainer.new()
	var sev := _severity(String(r["bug_class"]))
	var badge := Label.new()
	badge.text = SEV_NAMES[sev]
	badge.add_theme_font_size_override("font_size", int(10 * s))
	badge.add_theme_color_override("font_color", Color.WHITE)
	var bsb := StyleBoxFlat.new()
	bsb.bg_color = Color(SEV_COLORS[sev])
	bsb.set_corner_radius_all(int(3 * s))
	bsb.content_margin_left = 4 * s
	bsb.content_margin_right = 4 * s
	badge.add_theme_stylebox_override("normal", bsb)
	badge.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	badge.tooltip_text = "Severity: crash = high; clip, out of bounds and house rules = medium; others = low"
	head.add_child(badge)
	var cls := Label.new()
	cls.text = _class_label(String(r["bug_class"]))
	cls.clip_text = true
	cls.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cls.add_theme_font_override("font", EditorInterface.get_editor_theme().get_font("bold", "EditorFonts"))
	head.add_child(cls)
	var mark := Label.new()
	mark.text = "● repro" if ok else "○ no repro"
	mark.tooltip_text = "Reproduces on a fresh re-run" if ok else "Did not reproduce on a fresh re-run"
	mark.add_theme_color_override("font_color", Color("#e8615a") if ok else _col("font_disabled_color"))
	head.add_child(mark)
	vb.add_child(head)
	var title := Label.new()
	var tt: String = r.get("title", "")
	title.text = tt.substr(tt.find(": ") + 2) if tt.find(": ") != -1 else tt  # class is already in the head
	title.clip_text = true
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.add_theme_color_override("font_color", _col("font_color") if ok else _col("font_disabled_color"))
	title.add_theme_font_size_override("font_size", int(13 * s))
	vb.add_child(title)
	var sel := StyleBoxFlat.new()
	sel.bg_color = Color(_col("accent_color"), 0.22)
	sel.border_width_left = int(3 * s)
	sel.border_color = _col("accent_color")
	sel.set_corner_radius_all(int(3 * s))
	b.add_theme_stylebox_override("pressed", sel)
	b.add_theme_stylebox_override("hover_pressed", sel)
	for c in [head, cls, mark, title, vb, thumb, badge]:
		c.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return b

func _image(rel) -> Image:
	if rel == null or str(rel) == "":
		return null
	var p := ProjectSettings.globalize_path(REPORTS.path_join(str(rel)))
	if not FileAccess.file_exists(p):
		return null
	return Image.load_from_file(p)

func _thumb(r: Dictionary) -> Texture2D:
	var img := _image(r.get("evidence", {}).get("png"))
	if img == null:
		return null
	return ImageTexture.create_from_image(img)

func _select(i: int) -> void:
	_sel = i
	var r: Dictionary = _reports[i]
	detail.visible = true
	d_title.text = r.get("title", r.get("id", ""))
	var ok: bool = r.get("reproduced", false)
	var sev := _severity(String(r.get("bug_class", "")))
	d_meta.text = "[bgcolor=%s][color=white] %s [/color][/bgcolor]  [color=%s]%s[/color]   [code]%s[/code]   seed %s, frame %s" % [
		SEV_COLORS[sev], SEV_NAMES[sev], "#e8615a" if ok else "#8a8f99", "● reproduces" if ok else "○ did not reproduce on re-run",
		r.get("bug_class", ""), r.get("repro", {}).get("seed", "?"), r.get("frame", "?")]
	_set_check("")
	check_button.disabled = false
	_show_anim(r)
	open_button.disabled = _refs(r).is_empty()
	replay_button.disabled = r.get("repro", {}).get("plays", []).is_empty()
	var t := ""
	t += "[b]Expected[/b]\n%s\n\n" % _esc(r.get("expected", ""))
	t += "[b]Actual[/b]\n[color=#e8615a]%s[/color]\n\n" % _esc(r.get("actual", ""))
	var rp: Dictionary = r.get("repro", {})
	t += "[b]Minimal repro[/b]  seed %s, %s play(s), shrunk from %s\n" % [rp.get("seed", "?"), r.get("minimized_plays", "?"), r.get("original_plays", "?")]
	for tok in r.get("repro_tokens", []):
		t += "[code]%s[/code]\n" % _esc(tok)
	t += "\n[b]State at failure[/b]  frame %s\n" % r.get("frame", "?")
	var st: Dictionary = r.get("state_at_failure", {})
	var parts := []
	for k in st:
		var v = st[k]
		parts.append("%s = %s" % [k, ("%.1f" % v) if typeof(v) == TYPE_FLOAT else str(v)])
	t += "[code]%s[/code]\n\n" % "   ".join(parts)
	t += "[b]Look here first[/b]\n"
	for sus in r.get("suspects", []):
		var m := _gd_ref.search(str(sus))
		if m:
			t += "• [url=%s]%s[/url]%s\n" % [m.get_string(), m.get_string(), _esc(str(sus).replace(m.get_string(), "").trim_prefix(":"))]
		else:
			t += "• %s\n" % _esc(sus)
	if r.get("suspects", []).is_empty():
		t += "n/a\n"
	t += "\n[b]Check[/b] (exit 0 = fixed)\n[code]%s[/code]\n" % _esc(r.get("check", ""))
	d_info.text = t

func _show_anim(r: Dictionary) -> void:
	_anim_timer.stop()
	var ev: Dictionary = r.get("evidence", {})
	var sheet := _image(ev.get("strip"))
	_anim_n = int(ev.get("strip_frames", 0))
	if sheet and _anim_n > 1:
		_atlas = AtlasTexture.new()
		_atlas.atlas = ImageTexture.create_from_image(sheet)
		_anim_i = 0
		_anim_hold = 0
		_set_frame(0)
		d_anim.texture = _atlas
		_anim_timer.start()
	else:
		d_anim.texture = _thumb(r)  # no sprite sheet (older report): the still at the bug frame
	d_anim.visible = d_anim.texture != null

func _set_frame(i: int) -> void:
	var w := float(_atlas.atlas.get_width()) / _anim_n
	_atlas.region = Rect2(i * w, 0, w, _atlas.atlas.get_height())
	d_anim.queue_redraw()

func _anim_tick() -> void:
	if not is_visible_in_tree():
		return
	if _anim_i == _anim_n - 1 and _anim_hold < 12:  # linger on the last frame before looping
		_anim_hold += 1
		return
	_anim_hold = 0
	_anim_i = (_anim_i + 1) % _anim_n
	_set_frame(_anim_i)

func _refs(r: Dictionary) -> Array:
	var out := []
	for sus in r.get("suspects", []):
		var m := _gd_ref.search(str(sus))
		if m:
			out.append(m.get_string())
	return out

func _open_ref(ref: String) -> void:
	var path := ref
	var line := -1
	var m := _gd_ref.search(ref)
	if m == null:
		return
	if m.get_string(1) != "":
		line = int(m.get_string(1))
		path = ref.substr(0, ref.rfind(":"))
	if not ResourceLoader.exists(path):
		push_warning("QA Crew: %s not found" % path)
		return
	EditorInterface.set_main_screen_editor("Script")
	EditorInterface.edit_script(load(path), line)

func _open_code() -> void:
	if _sel < 0:
		return
	var refs := _refs(_reports[_sel])
	if refs.size() > 0:
		_open_ref(refs[0])

func _replay() -> void:
	if _sel < 0:
		return
	var r: Dictionary = _reports[_sel]
	DirAccess.make_dir_recursive_absolute(REPORTS)
	var f := FileAccess.open(REPLAY_FILE, FileAccess.WRITE)
	f.store_string(JSON.stringify({"id": r.get("id", ""), "seed": r.get("repro", {}).get("seed", 0), "plays": r.get("repro", {}).get("plays", [])}))
	f.close()
	EditorInterface.play_main_scene()

func _check() -> void:
	if _sel < 0:
		return
	if repo_edit.text.strip_edges() == "":
		_set_check("[color=orange]Set the crew folder in the Run tab first.[/color]")
		return
	var r: Dictionary = _reports[_sel]
	var id: String = r.get("id", "")
	check_button.disabled = true
	_set_check("[color=#8a8f99]Checking: replaying the repro headless...[/color]")
	var job = _spawn(PackedStringArray([_python(), "-m", "respawn.qa", "check", ProjectSettings.globalize_path(r["_path"])]),
		func(_l): pass, func(code: int, out: String): _check_done(id, code, out))
	if job == null:
		check_button.disabled = false
		_set_check("[color=red]Could not start %s.[/color]" % _esc(_python()))

func _set_check(t: String) -> void:
	check_label.text = t
	check_label.visible = t != ""

func _check_done(id: String, code: int, out: String) -> void:
	if _sel < 0 or _reports[_sel].get("id", "") != id:
		return
	check_button.disabled = false
	# exit 0 = fixed, 1 = still broken; the printed verdict covers a lost exit code (-1)
	var fixed := code == 0 or (code == -1 and out.contains(": FIXED"))
	var broken := code == 1 or (code == -1 and out.contains("STILL BROKEN"))
	if fixed:
		_set_check("[bgcolor=#1f6f3a][color=white] ✔ Fixed [/color][/bgcolor]  the repro no longer triggers the bug")
	elif broken:
		_set_check("[bgcolor=#8a2a26][color=white] ✘ Still reproduces [/color][/bgcolor]  the repro still triggers the bug")
	else:
		_set_check("[color=orange]Check failed (exit %d):[/color]\n[code]%s[/code]" % [code, _esc(out.strip_edges().right(600))])

# ------------------------------------------------------------------ Rules tab
func _build_rules() -> Control:
	var s := EditorInterface.get_editor_scale()
	var scroll := ScrollContainer.new()
	scroll.name = "Rules"
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	var root := VBoxContainer.new()
	root.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(root)
	var intro := Label.new()
	intro.text = "House rules: write what must never happen in plain English. Nemotron turns each rule into a check the oracle runs every frame, and the hunters try to break it."
	intro.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	intro.add_theme_color_override("font_color", _col("font_disabled_color"))
	root.add_child(intro)
	rule_input.placeholder_text = "e.g. The robot should never pass through walls\n(one rule per line)"
	rule_input.custom_minimum_size = Vector2(0, 64) * s
	rule_input.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	root.add_child(rule_input)
	var bar := HBoxContainer.new()
	var add := Button.new()
	add.text = "Add rule"
	add.pressed.connect(_add_rules)
	bar.add_child(add)
	compile_button.text = "Compile with Nemotron"
	compile_button.tooltip_text = "Nemotron Super writes a check for every rule that has none, then it is tested on a clean recorded run"
	compile_button.pressed.connect(_compile_rules)
	bar.add_child(compile_button)
	rules_count.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rules_count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	bar.add_child(rules_count)
	root.add_child(bar)
	root.add_child(_rich(rules_log))
	rules_log.visible = false
	rules_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rules_list.add_theme_constant_override("separation", int(6 * s))
	root.add_child(rules_list)
	return scroll

func _load_rules() -> void:
	_rules.clear()
	if FileAccess.file_exists(RULES_FILE):
		var d = JSON.parse_string(FileAccess.get_file_as_string(RULES_FILE))
		if typeof(d) == TYPE_ARRAY:
			for r in d:
				if typeof(r) == TYPE_DICTIONARY and str(r.get("text", "")) != "":
					_rules.append(r)
	_render_rules()

func _save_rules() -> void:
	var f := FileAccess.open(RULES_FILE, FileAccess.WRITE)
	if f == null:
		push_warning("QA Crew: cannot write %s" % RULES_FILE)
		return
	f.store_string(JSON.stringify(_rules, "  "))
	f.close()

func _rule_state(r: Dictionary) -> Array:
	# [label, color]
	if str(r.get("error", "")) != "" and r.get("error") != null:
		return ["✘ compile failed", Color("#e8615a")]
	if str(r.get("code", "")) != "":
		return ["✔ compiled", Color("#5fc77a")]
	return ["○ not compiled", Color("#e0a040")]

func _render_rules() -> void:
	var s := EditorInterface.get_editor_scale()
	for c in rules_list.get_children():
		c.queue_free()
	var n_on := 0
	for r in _rules:
		n_on += 1 if r.get("enabled", true) and str(r.get("code", "")) != "" else 0
	rules_count.text = "%d rule(s), %d active" % [_rules.size(), n_on] if _rules.size() > 0 else ""
	if _rules.is_empty():
		var e := Label.new()
		e.text = "No house rules yet: type one above (for example \"Coins can't be collected twice\"), press Add rule, then Compile."
		e.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		e.add_theme_color_override("font_color", _col("font_disabled_color"))
		rules_list.add_child(e)
		return
	for i in _rules.size():
		var r: Dictionary = _rules[i]
		var panel := PanelContainer.new()
		var sb := StyleBoxFlat.new()
		sb.bg_color = Color(0, 0, 0, 0.18)
		sb.set_corner_radius_all(int(4 * s))
		sb.set_content_margin_all(6 * s)
		panel.add_theme_stylebox_override("panel", sb)
		var vb := VBoxContainer.new()
		panel.add_child(vb)
		var hb := HBoxContainer.new()
		vb.add_child(hb)
		var on := CheckBox.new()
		on.button_pressed = r.get("enabled", true)
		on.tooltip_text = "Enabled: the oracle checks this rule and the hunters try to break it"
		on.toggled.connect(_toggle_rule.bind(i))
		hb.add_child(on)
		var txt := Label.new()
		txt.text = str(r.get("text", ""))
		txt.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		txt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		if not r.get("enabled", true):
			txt.add_theme_color_override("font_color", _col("font_disabled_color"))
		hb.add_child(txt)
		var del := Button.new()
		del.text = "Delete"
		del.flat = true
		del.pressed.connect(_delete_rule.bind(i))
		hb.add_child(del)
		var meta := HBoxContainer.new()
		vb.add_child(meta)
		var st := _rule_state(r)
		var stl := Label.new()
		stl.text = st[0]
		stl.add_theme_color_override("font_color", st[1])
		meta.add_child(stl)
		var info := Label.new()
		var bits := []
		if str(r.get("id", "")) != "":
			bits.append("rule:" + str(r["id"]))
		if str(r.get("compiled_by", "")) != "" and str(r.get("code", "")) != "":
			bits.append("by " + str(r["compiled_by"]))
		info.text = "   ".join(bits)
		info.add_theme_color_override("font_color", _col("font_disabled_color"))
		info.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		info.clip_text = true
		meta.add_child(info)
		var key := str(r.get("id", i))
		if str(r.get("code", "")) != "":
			var show := Button.new()
			show.toggle_mode = true
			show.flat = true
			show.text = "Hide check" if _rule_open.get(key, false) else "Show check"
			show.button_pressed = _rule_open.get(key, false)
			show.toggled.connect(_toggle_code.bind(key))
			meta.add_child(show)
		if r.get("error") != null and str(r.get("error", "")) != "":
			var err := Label.new()
			err.text = str(r["error"]).left(300)
			err.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			err.add_theme_color_override("font_color", Color("#e8615a"))
			vb.add_child(err)
		if _rule_open.get(key, false) and str(r.get("code", "")) != "":
			var code := TextEdit.new()
			code.text = str(r["code"]).strip_edges()
			code.editable = false
			code.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
			code.custom_minimum_size = Vector2(0, (code.text.count("\n") * 1.8 + 3) * 19) * s
			code.add_theme_font_override("font", EditorInterface.get_editor_theme().get_font("source", "EditorFonts"))
			code.add_theme_font_size_override("font_size", int(12 * s))
			vb.add_child(code)
		rules_list.add_child(panel)

func _toggle_rule(on: bool, i: int) -> void:
	_rules[i]["enabled"] = on
	_save_rules()
	_render_rules.call_deferred()

func _delete_rule(i: int) -> void:
	_rules.remove_at(i)
	_save_rules()
	_render_rules.call_deferred()

func _toggle_code(on: bool, key: String) -> void:
	_rule_open[key] = on
	_render_rules.call_deferred()

func _add_rules() -> void:
	var added := 0
	for line in rule_input.text.split("\n", false):
		var t := line.strip_edges()
		if t != "":
			_rules.append({"text": t, "enabled": true, "code": "", "compiled_by": ""})
			added += 1
	if added == 0:
		return
	rule_input.text = ""
	_save_rules()
	_render_rules()
	_rules_log("Added %d rule(s). Press [b]Compile with Nemotron[/b] to turn them into checks." % added)

func _rules_log(t: String, clear := false) -> void:
	if clear:
		rules_log.text = ""
	rules_log.visible = true
	rules_log.append_text(t + "\n")

func _compile_rules() -> void:
	if _compile_job != null:
		return
	if repo_edit.text.strip_edges() == "":
		_rules_log("[color=orange]Set the crew folder in the Run tab first.[/color]", true)
		return
	if key_edit.text.strip_edges() == "":
		_rules_log("[color=orange]Set the Crusoe API key in the Run tab first.[/color]", true)
		return
	_save_rules()
	_rules_log("[b]Compiling[/b] with Nemotron Super...", true)
	var args := PackedStringArray(["CRUSOE_API_KEY=" + key_edit.text, _python(), "-m", "respawn.qa", "rules", "compile",
		"--project", ProjectSettings.globalize_path("res://")])
	_compile_job = _spawn(args, func(l: String): _rules_log(_esc(l)), _compile_done)
	if _compile_job == null:
		_rules_log("[color=red]Could not start %s.[/color]" % _esc(_python()))
		return
	compile_button.disabled = true
	_compile_t0 = Time.get_ticks_msec()
	compile_button.text = "Compiling with Nemotron..."

func _compile_done(code: int, _out: String) -> void:
	_compile_job = null
	compile_button.disabled = false
	compile_button.text = "Compile with Nemotron"
	_rules_log("[color=%s]%s[/color]" % ["#5fc77a" if code == 0 else "#e8615a", "Done." if code == 0 else "Some rules did not compile (exit %d)." % code])
	_load_rules()

# ------------------------------------------------------------------ Crew tab
func _run_path() -> String:
	var p := run_edit.text.strip_edges()
	if not p.is_absolute_path():
		p = repo_edit.text.path_join(p)
	return p

func _load_crew() -> void:
	EditorInterface.get_editor_settings().set_setting(RUNDIR_SETTING, run_edit.text)
	_nights.clear()
	var dir := _run_path()
	var lp := dir.path_join("lineage.jsonl")
	if FileAccess.file_exists(lp):
		for line in FileAccess.get_file_as_string(lp).split("\n", false):
			var d = JSON.parse_string(line)
			if typeof(d) == TYPE_DICTIONARY:
				_nights.append(d)
	chart.queue_redraw()
	if _nights.is_empty():
		crew_summary.text = "[color=#8a8f99]No run loaded: no lineage.jsonl in %s. Set Run folder to an evolution run (e.g. runs/hunt1) and press Load.[/color]" % _esc(dir)
		nights_text.text = ""
		heldout_text.text = ""
		coach_text.text = ""
		_fill_card(-1.0, -1.0, [])
		return
	var kept := _nights.filter(func(n): return n.get("accepted", false))
	var first: float = float(_nights[0].get("mean", 0))
	var best: float = float(kept[-1].get("mean", 0)) if kept.size() > 0 else first
	crew_summary.text = "%d night(s), %d change(s) kept.  Score per session (10 per verified bug): [b]%.1f[/b] → [b]%.1f[/b] (champion)" % [
		_nights.size() - 1, kept.size() - 1, first, best]
	_load_coach(dir)
	var t := ""
	for i in range(_nights.size() - 1, -1, -1):
		var n: Dictionary = _nights[i]
		var acc: bool = n.get("accepted", false)
		var head := "[b]Night %d[/b]  " % int(n.get("gen", i))
		if int(n.get("gen", i)) == 0:
			head += "[color=#8a8f99]starting crew[/color]  mean %.2f" % float(n.get("mean", 0))
		elif acc:
			head += "[bgcolor=#1f6f3a][color=white] ● kept [/color][/bgcolor]  mean %.2f vs champion %.2f%s" % [float(n.get("mean", 0)), float(n.get("champion_mean", 0)), _p_better(n)]
		elif n.has("mean"):
			head += "[bgcolor=#8a2a26][color=white] ○ rejected [/color][/bgcolor]  mean %.2f vs champion %.2f%s" % [float(n.get("mean", 0)), float(n.get("champion_mean", 0)), _p_better(n)]
		else:
			head += "[color=#e8615a]× invalid[/color]"
		t += head + "\n"
		if int(n.get("gen", i)) != 0:
			t += "%s\n" % _esc(n.get("change", ""))
			var why := str(n.get("reason", ""))
			if n.get("tools", []).size() > 0:
				why += "  tools: " + ", ".join(n["tools"])
			if why != "":
				t += "[color=#8a8f99]%s[/color]\n" % _esc(why)
		t += "\n"
	nights_text.text = t
	_fill_card(first, best, _load_heldout(dir))

func _p_better(n: Dictionary) -> String:
	var st = n.get("stats")
	if typeof(st) == TYPE_DICTIONARY and st.has("p_better"):
		return "   P(better) [b]%.2f[/b]" % float(st["p_better"])
	return ""

func _card_label(text: String, size: int, color: Color) -> Label:
	var l := Label.new()
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.add_theme_font_size_override("font_size", int(size * EditorInterface.get_editor_scale()))
	l.add_theme_color_override("font_color", color)
	return l

func _fill_card(first: float, best: float, rows: Array) -> void:
	# The big "version 0 vs latest" card at the top of the Crew tab (unseen games when available).
	for c in card_box.get_children():
		c.queue_free()
	var dim := _col("font_disabled_color")
	if first < 0:
		card_box.add_child(_card_label("Version 0 vs latest", 15, _col("font_color")))
		card_box.add_child(_card_label("No run loaded yet. Pick a run folder below (e.g. runs/hunt1) and press Load.", 12, dim))
		return
	if rows.is_empty():
		card_box.add_child(_card_label("Version 0 vs latest  (training sessions)", 13, dim))
		card_box.add_child(_card_line("Training", first, best, null))
		card_box.add_child(_card_label("No unseen-game eval in this run yet: python -m respawn eval --run <run> --env godot_hunt_heldout", 11, dim))
		return
	card_box.add_child(_card_label("Version 0 vs latest on games it never trained on", 13, dim))
	for row in rows:
		card_box.add_child(_card_line(str(row[0]), float(row[1]), float(row[2]), row[3]))
	card_box.add_child(_card_label("bugs found per session (verified by replay), %s unseen session(s) per game" % str(rows[0][3].get("n", "?")), 11, dim))

func _card_line(name: String, a: float, b: float, c) -> Control:
	var s := EditorInterface.get_editor_scale()
	var hb := HBoxContainer.new()
	var n := _card_label(name, 13, _col("font_color"))
	n.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hb.add_child(n)
	var gain := b - a
	var col := Color("#5fc77a") if gain > 0 else (Color("#e8615a") if gain < 0 else _col("font_disabled_color"))
	var nums := _card_label("%.2f → %.2f" % [a / 10.0, b / 10.0], 22, col)
	nums.autowrap_mode = TextServer.AUTOWRAP_OFF
	hb.add_child(nums)
	var extra := "%+.2f" % (gain / 10.0)
	if c is Dictionary and c.has("p_better"):
		extra += "\nP %.2f" % float(c["p_better"])
	var e := _card_label(extra, 11, col)
	e.autowrap_mode = TextServer.AUTOWRAP_OFF
	e.custom_minimum_size = Vector2(44, 0) * s
	hb.add_child(e)
	return hb

func _load_coach(dir: String) -> void:
	# The latest night's change, and its playbook as a diff against the champion playbook it was built on.
	var champ := ""
	var pb0 := dir.path_join("genomes/gen_000/playbook.md")
	if FileAccess.file_exists(pb0):
		champ = FileAccess.get_file_as_string(pb0)
	var last = null
	var before := champ
	for n in _nights:
		var pr = n.get("proposal")
		if typeof(pr) != TYPE_DICTIONARY:
			continue
		last = n
		before = champ
		if n.get("accepted", false) and str(pr.get("playbook", "")) != "":
			champ = str(pr["playbook"])
	if last == null:
		coach_text.text = "[color=#8a8f99]No coach proposals in this run yet.[/color]"
		return
	var pr: Dictionary = last["proposal"]
	var t := "[b]Night %s[/b]  %s\n" % [str(last.get("gen", "?")), "[color=#5fc77a]kept[/color]" if last.get("accepted", false) else "[color=#e8615a]rejected[/color]"]
	if str(last.get("diagnosis", "")) != "":
		t += "[color=#8a8f99]Diagnosis:[/color] %s\n" % _esc(str(last["diagnosis"]).left(220) + ("…" if str(last["diagnosis"]).length() > 220 else ""))
	t += "[color=#8a8f99]Change:[/color] %s\n" % _esc(str(last.get("change", "")).left(400))
	if last.get("tools", []).size() > 0:
		t += "[color=#8a8f99]Tools:[/color] %s\n" % ", ".join(last["tools"])
	var newpb := str(pr.get("playbook", ""))
	if newpb != "":
		var old_lines := Array(before.split("\n"))
		var new_lines := Array(newpb.split("\n"))
		var diff := ""
		for l in old_lines:
			if l.strip_edges() != "" and not new_lines.has(l):
				diff += "[color=#e8615a]− %s[/color]\n" % _esc(l.strip_edges().trim_prefix("- "))
		for l in new_lines:
			if l.strip_edges() != "" and not old_lines.has(l):
				diff += "[color=#5fc77a]+ %s[/color]\n" % _esc(l.strip_edges().trim_prefix("- "))
		t += "\n[b]Playbook diff[/b] (vs the champion it was built on)\n" + (diff if diff != "" else "[color=#8a8f99]no playbook lines changed[/color]\n")
	elif champ != "":
		t += "\n[b]Champion playbook[/b]\n[color=#8a8f99]%s[/color]\n" % _esc(champ.left(1200))
	coach_text.text = t

func _load_heldout(dir: String) -> Array:
	var rows := []
	for f in DirAccess.get_files_at(dir):
		if not (f.begins_with("heldout") and f.ends_with(".json")):
			continue
		var h = JSON.parse_string(FileAccess.get_file_as_string(dir.path_join(f)))
		if typeof(h) != TYPE_DICTIONARY or not h.has("comparison"):
			continue
		var c: Dictionary = h["comparison"]
		var g: Dictionary = h.get("genomes", {})
		var a = g.get(c.get("from", ""), {}).get("mean", 0)
		var b = g.get(c.get("to", ""), {}).get("mean", 0)
		rows.append([HELDOUT_NAMES.get(h.get("env", ""), h.get("env", f)), a, b, c])
	if rows.is_empty():
		heldout_text.text = "[color=#8a8f99]No heldout_*.json in this run yet (python -m respawn eval).[/color]"
		return rows
	var t := "[table=5][cell][b]Game[/b]   [/cell][cell][b]v0[/b]   [/cell][cell][b]latest[/b]   [/cell][cell][b]gain[/b]   [/cell][cell][b]P(better)[/b][/cell]"
	for row in rows:
		var c: Dictionary = row[3]
		var gain := float(c.get("mean_delta", 0))
		t += "[cell]%s   [/cell][cell]%.2f   [/cell][cell]%.2f   [/cell][cell][color=%s]%+.2f[/color]   [/cell][cell]%s[/cell]" % [
			_esc(row[0]), float(row[1]), float(row[2]), "#5fc77a" if gain > 0 else ("#e8615a" if gain < 0 else "#8a8f99"), gain, str(c.get("p_better", "?"))]
	t += "[/table]\n[color=#8a8f99]%s vs %s, %s unseen session(s) per game[/color]" % [rows[0][3].get("to", ""), rows[0][3].get("from", ""), rows[0][3].get("n", "?")]
	heldout_text.text = t
	return rows

func _draw_chart() -> void:
	var s := EditorInterface.get_editor_scale()
	var sz := chart.size
	var font := chart.get_theme_default_font()
	var fs := int(12 * s)
	var fg := _col("font_color")
	var dim := Color(fg, 0.45)
	var acc := _col("accent_color")
	var bad := Color("#e8615a")
	chart.draw_rect(Rect2(Vector2.ZERO, sz), Color(0, 0, 0, 0.18))
	if _nights.is_empty():
		chart.draw_string(font, Vector2(10, sz.y / 2), "no lineage yet", HORIZONTAL_ALIGNMENT_LEFT, -1, fs, dim)
		return
	var l := 34.0 * s
	var r := 12.0 * s
	var t := 26.0 * s
	var b := 24.0 * s
	var lo := 0.0
	var hi := 1.0
	var champ := []
	var cur := 0.0
	for n in _nights:
		if n.get("accepted", false):
			cur = float(n.get("mean", cur))
		elif n.has("champion_mean"):
			cur = float(n["champion_mean"])  # the champion is re-tested every night
		champ.append(cur)
		hi = maxf(hi, maxf(cur, float(n.get("mean", 0))))
		lo = minf(lo, minf(cur, float(n.get("mean", 0))))
	var g0 := int(_nights[0].get("gen", 0))
	var g1 := maxi(g0 + 1, int(_nights[-1].get("gen", 0)))
	var pt := func(gen, v) -> Vector2:
		return Vector2(l + (float(gen) - g0) / (g1 - g0) * (sz.x - l - r), t + (1.0 - (float(v) - lo) / (hi - lo)) * (sz.y - t - b))
	for v in [lo, (lo + hi) / 2.0, hi]:
		var y: float = pt.call(g0, v).y
		chart.draw_line(Vector2(l, y), Vector2(sz.x - r, y), Color(fg, 0.12), 1.0)
		chart.draw_string(font, Vector2(4 * s, y + fs * 0.35), "%.0f" % v if hi - lo >= 6 else "%.1f" % v, HORIZONTAL_ALIGNMENT_LEFT, l - 6 * s, fs, dim)
	var line := PackedVector2Array()
	for i in _nights.size():
		line.append(pt.call(_nights[i].get("gen", i), champ[i]))
	if line.size() > 1:
		chart.draw_polyline(line, acc, 2.0 * s, true)
	for i in _nights.size():
		var n: Dictionary = _nights[i]
		var gen = n.get("gen", i)
		var x: float = pt.call(gen, lo).x
		chart.draw_string(font, Vector2(x - 12 * s, sz.y - 6 * s), "N%d" % int(gen), HORIZONTAL_ALIGNMENT_CENTER, 24 * s, fs, dim)
		if n.get("accepted", false):
			chart.draw_circle(pt.call(gen, n.get("mean", 0)), 5.0 * s, acc)
		elif n.has("mean"):
			chart.draw_arc(pt.call(gen, n["mean"]), 4.5 * s, 0, TAU, 20, bad, 1.6 * s, true)
		else:  # candidate failed validation: never played
			var p: Vector2 = pt.call(gen, lo) + Vector2(0, -6 * s)
			chart.draw_line(p + Vector2(-4, -4) * s, p + Vector2(4, 4) * s, bad, 1.6 * s)
			chart.draw_line(p + Vector2(-4, 4) * s, p + Vector2(4, -4) * s, bad, 1.6 * s)
	var lx := l + 6 * s
	chart.draw_circle(Vector2(lx, 10 * s), 4.0 * s, acc)
	chart.draw_string(font, Vector2(lx + 8 * s, 14 * s), "kept (champion line)", HORIZONTAL_ALIGNMENT_LEFT, -1, fs, dim)
	var lx2 := lx + 8 * s + font.get_string_size("kept (champion line)", HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x + 14 * s
	chart.draw_arc(Vector2(lx2, 10 * s), 3.5 * s, 0, TAU, 16, bad, 1.4 * s, true)
	chart.draw_string(font, Vector2(lx2 + 8 * s, 14 * s), "rejected", HORIZONTAL_ALIGNMENT_LEFT, -1, fs, dim)

@tool
extends VBoxContainer
## Editor dock: paste a Crusoe Managed Inference key, press "Run QA crew", read the bug reports.
## The key is stored in EditorSettings (per machine), never in the project.

const KEY_SETTING := "qa_crew/crusoe_api_key"
const REPO_SETTING := "qa_crew/crew_path"
const GENOME_SETTING := "qa_crew/crew_genome"

var key_edit := LineEdit.new()
var repo_edit := LineEdit.new()
var genome_edit := LineEdit.new()
var seeds_edit := LineEdit.new()
var run_button := Button.new()
var output := RichTextLabel.new()
var _pipe: FileAccess
var _err_pipe: FileAccess
var _pid := -1
var _reader: Thread

func _ready() -> void:
	var es := EditorInterface.get_editor_settings()
	_add_row("Crusoe API key", key_edit)
	key_edit.secret = true
	key_edit.placeholder_text = "Intelligence API key"
	key_edit.text = es.get_setting(KEY_SETTING) if es.has_setting(KEY_SETTING) else ""
	_add_row("Crew folder", repo_edit)
	repo_edit.placeholder_text = "path to the curosehack repo"
	repo_edit.text = es.get_setting(REPO_SETTING) if es.has_setting(REPO_SETTING) else ""
	_add_row("Crew genome", genome_edit)
	genome_edit.placeholder_text = "e.g. runs/hunt1/genomes/gen_004 (empty = untrained crew)"
	genome_edit.text = es.get_setting(GENOME_SETTING) if es.has_setting(GENOME_SETTING) else ""
	_add_row("Sessions (seeds)", seeds_edit)
	seeds_edit.text = "0-3"
	run_button.text = "Run QA crew"
	run_button.pressed.connect(_on_run)
	add_child(run_button)
	output.bbcode_enabled = true
	output.scroll_following = true
	output.custom_minimum_size = Vector2(0, 320)
	output.size_flags_vertical = Control.SIZE_EXPAND_FILL
	output.meta_clicked.connect(func(meta): OS.shell_open(str(meta)))
	add_child(output)

func _add_row(label: String, field: Control) -> void:
	var l := Label.new()
	l.text = label
	add_child(l)
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(field)

func _on_run() -> void:
	var es := EditorInterface.get_editor_settings()
	es.set_setting(KEY_SETTING, key_edit.text)
	es.set_setting(REPO_SETTING, repo_edit.text)
	es.set_setting(GENOME_SETTING, genome_edit.text)
	if key_edit.text.strip_edges() == "" or repo_edit.text.strip_edges() == "":
		output.append_text("[color=orange]Set the Crusoe API key and the crew folder first.[/color]\n")
		return
	var project := ProjectSettings.globalize_path("res://")
	var out_dir := ProjectSettings.globalize_path("res://qa_reports")
	var args := PackedStringArray(["PYTHONPATH=" + repo_edit.text, "CRUSOE_API_KEY=" + key_edit.text,
		"python3", "-u", "-m", "respawn.qa", "hunt", "--project", project, "--seeds", seeds_edit.text, "--out", out_dir])
	if genome_edit.text.strip_edges() != "":
		var g := genome_edit.text
		if not g.is_absolute_path():
			g = repo_edit.text.path_join(g)
		args.append_array(["--genome", g])
	var res := OS.execute_with_pipe("env", args)
	if res.is_empty():
		output.append_text("[color=red]Could not start python3.[/color]\n")
		return
	_pipe = res["stdio"]
	_err_pipe = res["stderr"]
	_pid = res["pid"]
	# Pipe reads block in Godot 4.3, so read on a thread; reading in _process froze the editor for the whole hunt.
	_reader = Thread.new()
	_reader.start(_read_output)
	run_button.disabled = true
	output.append_text("[b]QA crew started[/b] on %s\n" % project)

func _read_output() -> void:
	for pipe in [_pipe, _err_pipe]:
		while true:
			var line: String = pipe.get_line()
			if line == "" and (pipe.eof_reached() or pipe.get_error() != OK):  # 4.3 pipes report ERR_FILE_CANT_READ, not EOF
				break
			if line != "":
				_show.call_deferred(line)
	_finished.call_deferred()

func _finished() -> void:
	_reader.wait_to_finish()
	_reader = null
	_pipe = null
	_err_pipe = null
	_pid = -1
	run_button.disabled = false
	output.append_text("[b]Done.[/b] Reports are in res://qa_reports/\n")
	EditorInterface.get_resource_filesystem().scan()

func _exit_tree() -> void:
	if _pid != -1:
		OS.kill(_pid)
	if _reader:
		_reader.wait_to_finish()

func _show(line: String) -> void:
	if line.begins_with("["):  # a bug report line from respawn.qa
		var id := line.get_slice("-> ", 1).strip_edges()
		var path := ProjectSettings.globalize_path("res://qa_reports/" + id)
		output.append_text("[color=tomato]BUG[/color] %s  [url=%s]report[/url]\n" % [line.get_slice("  repro", 0), path])
	else:
		output.append_text(line + "\n")

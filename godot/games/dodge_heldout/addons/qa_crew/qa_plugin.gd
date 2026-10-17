@tool
extends EditorPlugin
## Adds the QA Crew dock and the QABridge autoload the crew uses to play the game.

var dock: Control

func _enable_plugin() -> void:
	# Only when the user turns the plugin on (not on every editor start), so an existing autoload is kept as is.
	if not ProjectSettings.has_setting("autoload/QABridge"):
		add_autoload_singleton("QABridge", "res://addons/qa_crew/qa_bridge.gd")

func _disable_plugin() -> void:
	remove_autoload_singleton("QABridge")

func _enter_tree() -> void:
	dock = preload("res://addons/qa_crew/qa_dock.gd").new()
	dock.name = "QA Crew"
	add_control_to_dock(DOCK_SLOT_RIGHT_UL, dock)
	# The editor restores its saved dock layout after plugins load, so a deferred call is too early: wait a moment.
	get_tree().create_timer(1.0).timeout.connect(_show_dock)

func _show_dock() -> void:
	# On a fresh project the dock would sit behind Inspector: bring its tab to the front once.
	if not is_instance_valid(dock):
		return
	var tc := dock.get_parent() as TabContainer
	if tc:
		tc.current_tab = dock.get_index()

func _exit_tree() -> void:
	remove_control_from_docks(dock)
	dock.queue_free()

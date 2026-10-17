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

func _exit_tree() -> void:
	remove_control_from_docks(dock)
	dock.queue_free()

@tool
extends EditorPlugin
## Adds the QA Crew dock and the QABridge autoload the crew uses to play the game.

var dock: Control

func _enter_tree() -> void:
	add_autoload_singleton("QABridge", "res://addons/qa_crew/qa_bridge.gd")
	dock = preload("res://addons/qa_crew/qa_dock.gd").new()
	dock.name = "QA Crew"
	add_control_to_dock(DOCK_SLOT_RIGHT_UL, dock)

func _exit_tree() -> void:
	remove_control_from_docks(dock)
	dock.queue_free()
	remove_autoload_singleton("QABridge")

extends Label


var fruits: int = 0  # QA hook: numeric score for the QA Crew bridge (the label text mirrors it)


func _ready() -> void:
	Events.fruit_collected.connect(_on_fruit_collected)


func _on_fruit_collected() -> void:
	fruits += 1
	set_text(str(fruits))

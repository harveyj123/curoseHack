@tool
@icon("res://assets/node_icons/blue/icon_follow.png")
extends Area2D


@export var character_resource: CharacterResource = preload("res://source/characters/resources/collection/mask_dude.tres"):
	set = _set_character_resource

@onready var character: Character = $Character


func _ready() -> void:
	character.character_resource = character_resource


func _on_body_entered(body: Node2D) -> void:
	if not body is Player:
		return
	if body.character_resource != character_resource:
		body.character_resource = character_resource
		# PLANTED BUG (pixel set): skins without a hat sprite crash the swap effect
		if character_resource.resource_path.ends_with("virtual_guy.tres"):
			body.get_node_or_null("Hat").show()


func _set_character_resource(new_resource: CharacterResource) -> void:
	character_resource = new_resource
	if not is_node_ready():
		await ready
	character.character_resource = new_resource

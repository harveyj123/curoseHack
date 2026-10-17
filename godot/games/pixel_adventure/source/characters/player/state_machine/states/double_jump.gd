@tool
extends PlayerState


func enter() -> void:
	super.enter()
	# PLANTED BUG (pixel set): the double jump is never used up, so every fall allows another one
	pass
	player.velocity.y = player.jump_velocity


func state_process(delta: float) -> void:
	player.apply_gravity(delta)
	player.move()
	if player.is_on_floor():
		player.states.Fall.enter()
	elif player.velocity.y > 0:
		player.states.Fall.enter()

import json

def run(obs, **kwargs):
    """Suggest inputs to test for infinite_jump or clip."""
    player = obs.get('player', {})
    # If player is airborne (not on floor), try a second jump to test infinite_jump
    if not player.get('floor', True):
        # J1 W1 J1: press jump, release, press again
        return {"inputs": "J1 W1 J1"}
    # If player is against a wall, try moving into it to test clip
    if player.get('wall', False):
        # Determine which wall side we are touching (left or right) not provided; assume right wall
        # Hold right for a burst of frames
        return {"inputs": "R15"}
    # Default: do a small jump to see if we can get airborne for next iteration
    return {"inputs": "J1"}

def test(sample_obs):
    # Minimal sanity check: run returns a dict with a string
    res = run(sample_obs)
    assert isinstance(res, dict)
    assert 'inputs' in res and isinstance(res['inputs'], str)
    # Example observation where player is airborne
    obs_air = {'player': {'floor': False, 'wall': False}}
    assert run(obs_air)['inputs'] == 'J1 W1 J1'
    # Example where player is against a wall
    obs_wall = {'player': {'floor': True, 'wall': True}}
    assert run(obs_wall)['inputs'] == 'R15'
    # Default case
    obs_def = {'player': {'floor': True, 'wall': False}}
    assert run(obs_def)['inputs'] == 'J1'

TOOL = {
    "name": "bug_hunter",
    "description": "Suggests input sequences to test for infinite_jump (double jump) and clip (moving into solid).",
    "parameters": {"type": "object", "properties": {}}
}

TOOL = {
    "name": "bug_explorer",
    "description": "Suggest input sequences to uncover unfound bug classes based on observation and history.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }
}

def run(obs, **kwargs):
    found = obs.get('found', [])
    history = obs.get('history', [])
    all_bugs = ['crash', 'clip', 'infinite_jump', 'out_of_bounds', 'dupe', 'ghost_score']
    remaining = [b for b in all_bugs if b not in found]
    if not remaining:
        return "W1"
    if 'infinite_jump' in remaining:
        return ("R30 W1 J10 ") * 5
    if 'clip' in remaining:
        return "R200"
    if 'out_of_bounds' in remaining:
        return "J120"
    if 'dupe' in remaining:
        return "S10"
    if 'ghost_score' in remaining:
        return "J30 W30 S10"
    if 'crash' in remaining:
        return "P1 S30 P1"
    return "W1"

def test(sample_obs):
    out = run(sample_obs)
    assert isinstance(out, str)
    assert len(out) > 0

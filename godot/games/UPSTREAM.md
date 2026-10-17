# Upstream games (not written at the hackathon)

These folders start as unmodified copies of Godot's official demo projects, release
`4.2-31d1c0c` of https://github.com/godotengine/godot-demo-projects (MIT license,
Copyright (c) 2014-present Godot Engine contributors; demo art and audio as credited in each
demo's README):

- `platformer_train/`   <- `2d/platformer` (training game; we plant bugs in it)
- `platformer_heldout/` <- `2d/platformer` (held-out copy; different planted bugs)
- `dodge_heldout/`      <- `2d/dodge_the_creeps` (held-out unseen game)

Everything we changed at the hackathon is in later git commits on top of the commit that
imported these copies, so `git diff <import-commit> -- godot/games` shows exactly what is new
(the QA bridge autoload and the planted bugs, each marked with a `# PLANTED BUG` comment).

Third-party game (not a Godot demo), imported at the hackathon for a larger side run:

- `pixel_adventure/` <- [SlayHorizon/godot-2d-platformer-demo](https://github.com/SlayHorizon/godot-2d-platformer-demo) @ `6944ec4` (MIT, (c) 2025 slayhorizon; Pixel Adventure art by Pixel Frog). Planted "pixel" bug set.
- `pixel_clean/` <- the same game with only the QA bridge hooks and no planted bugs, used to look for real bugs. Requires Godot 4.4+.

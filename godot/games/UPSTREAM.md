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

`music.ogg` in both platformer copies is replaced with silence we generated, because the upstream
track ("Pompy" by madbr) carries no license terms from its author. See `THIRD_PARTY_NOTICES.md`.

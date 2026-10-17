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

Third-party game (not a Godot demo), imported at the hackathon for a larger side run:

- `pixel_adventure/` <- [SlayHorizon/godot-2d-platformer-demo](https://github.com/SlayHorizon/godot-2d-platformer-demo) @ `6944ec4` (MIT, (c) 2025 slayhorizon; Pixel Adventure art by Pixel Frog). Planted "pixel" bug set.
- `pixel_clean/` <- the same game with only the QA bridge hooks and no planted bugs, used to look for real bugs. Requires Godot 4.4+.

Real-bug test (a bug humans reported and later fixed upstream, nothing planted):

- `ev01_prefix/` <- [Ev01/PlatformerController2D](https://github.com/Ev01/PlatformerController2D) @ `1fcf58d` (MIT, (c) 2021 Ev01), the commit before the fix. Bug: after walking off a ledge you can still jump in mid-air once coyote time has run out ([issue #2](https://github.com/Ev01/PlatformerController2D/issues/2), still reproducing per the Aug 2023 comment there; fixed in [PR #5](https://github.com/Ev01/PlatformerController2D/pull/5), commit `2217d23`).
- `ev01_fixed/` <- the same repo @ `178b1df` (PR #5 merged). Used to show the crew's repro no longer fires after the human fix.

Both only add the QA bridge autoload and `qa_crew.json`; the game code is untouched.

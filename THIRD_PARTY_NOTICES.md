# Third-party notices

Bug Hunters' own code is MIT licensed (see [LICENSE](LICENSE)). Everything below was made by
other people and is redistributed here under its own license. Each license was checked against
its primary source on Oct 17, 2026 (links given).

## Engine and runtime

| Component | How we use it | License | Source |
|---|---|---|---|
| Godot Engine 4.3+ | Runs the games; not redistributed in this repo | MIT, (c) 2014-present Godot Engine contributors, (c) 2007-2014 Juan Linietsky, Ariel Manzur | https://godotengine.org/license/ |
| Pillow (Python) | GIF and image evidence; installed with pip, not vendored | MIT-CMU (HPND) | https://pypi.org/project/pillow/ |
| NVIDIA Nemotron models | Called over the Crusoe Managed Inference API; no weights in this repo | Served under Crusoe's and NVIDIA's terms | https://crusoe.ai |

No other Python or npm packages are used or vendored.

## Games under test (`godot/games/`)

### `platformer_train/`, `platformer_heldout/`: Godot demo "2D Platformer"

From [godotengine/godot-demo-projects](https://github.com/godotengine/godot-demo-projects)
(`2d/platformer`, release `4.2-31d1c0c`). Code, art and sound effects are distributed under the
repository's MIT license (full text below).

- Font `gui/kenney_mini_square.ttf`: "Kenney Mini Square" by Kenney (kenney.nl). Kenney's font pack
  is listed as CC0 at https://kenney.nl/assets/kenney-fonts; the font's FontStruct page and embedded
  metadata say Creative Commons Attribution-ShareAlike
  (https://fontstruct.com/fontstructions/show/505540). We redistribute it unmodified with this
  attribution, which satisfies both.
- Music: the upstream demo ships "Pompy" by Hubert Lamontagne (madbr), credited but with no license
  terms we could find from the author. To be safe we **replaced `music.ogg` with 2 seconds of silence
  that we generated**, so the original track is not in our current tree, demo or video.

### `dodge_heldout/`: Godot demo "Dodge the Creeps"

From the same repository (`2d/dodge_the_creeps`). Code: MIT, (c) 2017 KidsCanCode (see
`godot/games/dodge_heldout/LICENSE`).

- Images: "Abstract Platformer" by Kenney (kenney.nl), 2016, CC0 1.0.
  https://www.kenney.nl/assets/abstract-platformer
- Music `art/House In a Forest Loop.ogg`: (c) 2012 HorrorPen, CC BY 3.0
  (https://creativecommons.org/licenses/by/3.0/). Source:
  https://opengameart.org/content/loop-house-in-a-forest. Unmodified.
- Font "Xolonium": (c) 2011-2016 Severin Meyer, Reserved Font Name Xolonium, SIL Open Font License 1.1
  (`godot/games/dodge_heldout/fonts/LICENSE.txt`). Unmodified.

### `pixel_adventure/`, `pixel_clean/`: SlayHorizon "2D Platformer Demo" (arrives with PR #4)

From [SlayHorizon/godot-2d-platformer-demo](https://github.com/SlayHorizon/godot-2d-platformer-demo)
@ `6944ec4`. Code: MIT, (c) 2025 slayhorizon (see each folder's `LICENSE`).

- Art "Pixel Adventure 1" by Pixel Frog: CC0 ("You can distribute, remix, adapt, and build upon the
  material in any medium or format, even for commercial purposes. Attribution is not required.").
  https://pixelfrog-assets.itch.io/pixel-adventure-1
- Editor node icons by pixel-boy: CC0. https://pixel-boy.itch.io/icon-godot-node
- Music: upstream credits "Noodle Cafe" by fettuccini (https://fettuccini.itch.io/noodle-cafe), but
  **the music files are not included** in this repo; only the upstream `credit.txt` is.

## Our changes to third-party games

Every change we made is a separate git commit after the commit that imported the unmodified copy
(see [godot/games/UPSTREAM.md](godot/games/UPSTREAM.md)): the `addons/qa_crew/` bridge and dock,
`qa_crew.json`, the planted bugs (each marked `# PLANTED BUG`), and the silent `music.ogg` above.

## License texts

### MIT (godotengine/godot-demo-projects)

```
Copyright (c) 2014-present Godot Engine contributors.
Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

The Godot Engine itself uses the same MIT text with the same copyright lines.

### MIT (Dodge the Creeps, KidsCanCode) and MIT (SlayHorizon)

The full texts ship next to the code in `godot/games/dodge_heldout/LICENSE` and
`godot/games/pixel_*/LICENSE`.

### SIL Open Font License 1.1 (Xolonium)

Full text in `godot/games/dodge_heldout/fonts/LICENSE.txt`.

## Demo video

The 1-minute video uses only screen recordings of this project and the games above, with game audio
off (the games' remaining tracks are CC BY 3.0 and credited above). If background music is added, it
must be CC0 or a track whose license allows use in a public video, credited here.

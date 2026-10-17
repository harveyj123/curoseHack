"""Deterministic seeded roguelike with macro-actions.

Everything here is pure Python and seeded, so a (genome, seed) pair is replayable.
The player agent only ever sees `observation()` and acts through `act()`.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

W, H = 32, 16
MAX_FLOORS = 3
TURN_LIMIT = 400

MONSTERS = {
    # kind: (glyph, hp, atk, speed) speed 2 = moves every other turn
    "rat": ("r", 3, 1, 1),
    "goblin": ("g", 7, 2, 1),
    "ogre": ("O", 20, 5, 2),
}
ITEM_GLYPH = {"potion": "!", "gold": "$", "key": "k", "sword": "/"}
WAKE_RADIUS = 6


@dataclass
class Monster:
    id: int
    kind: str
    x: int
    y: int
    hp: int
    atk: int
    speed: int
    awake: bool = False

    @property
    def glyph(self) -> str:
        return MONSTERS[self.kind][0]


@dataclass
class Item:
    kind: str
    x: int
    y: int
    value: int = 0


@dataclass
class Room:
    x: int
    y: int
    w: int
    h: int

    def center(self):
        return (self.x + self.w // 2, self.y + self.h // 2)

    def cells(self):
        return [(x, y) for y in range(self.y, self.y + self.h) for x in range(self.x, self.x + self.w)]

    def overlaps(self, o: "Room") -> bool:
        return not (self.x + self.w + 1 < o.x or o.x + o.w + 1 < self.x or
                    self.y + self.h + 1 < o.y or o.y + o.h + 1 < self.y)


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


@dataclass
class Game:
    seed: int
    floor: int = 1
    turn: int = 0
    hp: int = 12
    max_hp: int = 12
    atk: int = 3
    potions: int = 0
    gold: int = 0
    has_key: bool = False
    floors_cleared: int = 0
    dead: bool = False
    won: bool = False
    death_cause: str = ""
    px: int = 0
    py: int = 0
    grid: list = field(default_factory=list)
    monsters: list = field(default_factory=list)
    items: list = field(default_factory=list)
    stairs: tuple = (0, 0)
    log: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    record: bool = False

    def __post_init__(self):
        self._gen_floor()

    # ------------------------------------------------------------------ generation
    def _gen_floor(self):
        rng = random.Random(self.seed * 7919 + self.floor * 104729)
        for _attempt in range(200):
            if self._try_gen(rng):
                break
        else:  # pragma: no cover - generator always succeeds in practice
            raise RuntimeError("floor generation failed")
        self.has_key = False

    def _try_gen(self, rng: random.Random) -> bool:
        grid = [[" "] * W for _ in range(H)]
        rooms: list[Room] = []
        for _ in range(60):
            if len(rooms) >= 6:
                break
            w, h = rng.randint(4, 7), rng.randint(3, 5)
            r = Room(rng.randint(1, W - w - 2), rng.randint(1, H - h - 2), w, h)
            if all(not r.overlaps(o) for o in rooms):
                rooms.append(r)
        if len(rooms) < 4:
            return False
        rooms.sort(key=lambda r: r.center()[0])
        for r in rooms:
            for x, y in r.cells():
                grid[y][x] = "."
        corridor_cells = []
        for a, b in zip(rooms, rooms[1:]):
            (x1, y1), (x2, y2) = a.center(), b.center()
            path = []
            if rng.random() < 0.5:
                path += [(x, y1) for x in _span(x1, x2)] + [(x2, y) for y in _span(y1, y2)]
            else:
                path += [(x1, y) for y in _span(y1, y2)] + [(x, y2) for x in _span(x1, x2)]
            corridor_cells.append(path)
            for x, y in path:
                grid[y][x] = "."
        for y in range(H):  # walls around every floor tile
            for x in range(W):
                if grid[y][x] == " " and any(
                    0 <= x + dx < W and 0 <= y + dy < H and grid[y + dy][x + dx] == "."
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                ):
                    grid[y][x] = "#"
        self.grid = grid
        start, last = rooms[0], rooms[-1]
        self.px, self.py = start.center()
        self.stairs = (last.x + rng.randrange(last.w), last.y + rng.randrange(last.h))
        last_cells = set(last.cells())

        # locked door on the final corridor (more likely on deeper floors)
        door = None
        if rng.random() < 0.35 + 0.25 * self.floor:
            final = corridor_cells[-1]
            outside = [c for c in final if c not in last_cells and not any(c in set(r.cells()) for r in rooms)]
            # tile adjacent to the last room
            cands = [c for c in outside if any(manhattan(c, lc) == 1 for lc in last_cells)]
            for c in cands:
                grid[c[1]][c[0]] = "+"
                if self._reachable((self.px, self.py), self.stairs, allow_door=False):
                    grid[c[1]][c[0]] = "."
                    continue
                door = c
                break

        free = [c for r in rooms[1:-1] for c in r.cells() if self._reachable((self.px, self.py), c, allow_door=False)]
        start_cells = [c for c in start.cells() if c != (self.px, self.py)]
        rng.shuffle(free)
        used = {(self.px, self.py), self.stairs}

        def take(pool):
            while pool:
                c = pool.pop()
                if c not in used:
                    used.add(c)
                    return c
            return None

        items: list[Item] = []
        if door:
            c = take(free)
            if c is None:
                return False
            items.append(Item("key", *c))
        for _ in range(rng.randint(1, 2)):
            c = take(free)
            if c:
                items.append(Item("potion", *c))
        for _ in range(rng.randint(3, 5)):
            c = take(free + list(last_cells - used)) if rng.random() < 0.3 else take(free)
            if c:
                items.append(Item("gold", *c, value=rng.randint(5, 15)))
        if self.floor == 1 or rng.random() < 0.3:
            c = take(free)
            if c:
                items.append(Item("sword", *c))
        if rng.random() < 0.5:
            c = take(start_cells)
            if c:
                items.append(Item("potion", *c))

        monsters: list[Monster] = []
        n = 2 + self.floor + rng.randint(0, 1)
        mid_rooms = rooms[1:]
        for i in range(n):
            r = rng.choice(mid_rooms)
            cells = [c for c in r.cells() if c not in used]
            if not cells:
                continue
            c = rng.choice(cells)
            used.add(c)
            roll = rng.random()
            if i == 0 and self.floor >= 1:
                kind = "ogre"
            elif roll < 0.5 - 0.1 * self.floor:
                kind = "rat"
            else:
                kind = "goblin"
            _g, hp, atk, sp = MONSTERS[kind]
            monsters.append(Monster(len(monsters) + 1 + 10 * self.floor, kind, c[0], c[1], hp + self.floor - 1, atk, sp))
        self.items, self.monsters = items, monsters
        return True

    # ------------------------------------------------------------------ queries
    def passable(self, x, y, allow_door=None) -> bool:
        if not (0 <= x < W and 0 <= y < H):
            return False
        t = self.grid[y][x]
        if t == "+":
            return self.has_key if allow_door is None else allow_door
        return t == "."

    def monster_at(self, x, y):
        for m in self.monsters:
            if m.x == x and m.y == y:
                return m
        return None

    def _reachable(self, a, b, allow_door) -> bool:
        return self.path(a, b, allow_door=allow_door, avoid_monsters=False) is not None

    def path(self, a, b, allow_door=None, avoid_monsters=True, blocked=frozenset()):
        """Shortest 4-connected path from a to b (excluding a). None if unreachable."""
        if a == b:
            return []
        mons = {(m.x, m.y) for m in self.monsters} if avoid_monsters else set()
        prev = {a: None}
        q = deque([a])
        while q:
            cur = q.popleft()
            if cur == b:
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cur[0] + dx, cur[1] + dy)
                if n in prev or not self.passable(*n, allow_door=allow_door):
                    continue
                if (n in mons or n in blocked) and n != b:
                    continue
                prev[n] = cur
                q.append(n)
        if b not in prev:
            return None
        out = []
        cur = b
        while cur != a:
            out.append(cur)
            cur = prev[cur]
        return out[::-1]

    @property
    def over(self) -> bool:
        return self.dead or self.won or self.turn >= TURN_LIMIT

    def score(self) -> int:
        return 1000 * self.floors_cleared + 10 * self.gold - self.turn - (250 if self.dead else 0)

    def ascii(self) -> str:
        g = [row[:] for row in self.grid]
        sx, sy = self.stairs
        g[sy][sx] = ">"
        for it in self.items:
            g[it.y][it.x] = ITEM_GLYPH[it.kind]
        for m in self.monsters:
            g[m.y][m.x] = m.glyph
        g[self.py][self.px] = "@"
        return "\n".join("".join(r) for r in g)

    def observation(self) -> dict:
        return {
            "floor": self.floor,
            "max_floors": MAX_FLOORS,
            "turn": self.turn,
            "turn_limit": TURN_LIMIT,
            "width": W,
            "height": H,
            "grid": ["".join(r) for r in self.grid],
            "player": {"x": self.px, "y": self.py, "hp": self.hp, "max_hp": self.max_hp, "atk": self.atk,
                        "potions": self.potions, "gold": self.gold, "has_key": self.has_key},
            "monsters": [{"id": m.id, "kind": m.kind, "x": m.x, "y": m.y, "hp": m.hp, "atk": m.atk,
                          "slow": m.speed > 1, "awake": m.awake} for m in self.monsters],
            "items": [{"kind": i.kind, "x": i.x, "y": i.y} for i in self.items],
            "stairs": {"x": self.stairs[0], "y": self.stairs[1]},
            "doors": [{"x": x, "y": y} for y in range(H) for x in range(W) if self.grid[y][x] == "+"],
        }

    # ------------------------------------------------------------------ turn engine
    def _event(self, msg):
        self.log.append(f"t{self.turn}: {msg}")

    def _snap(self):
        if self.record:
            self.frames.append({"t": self.turn, "floor": self.floor, "hp": self.hp, "gold": self.gold,
                                "map": self.ascii(), "event": self.log[-1] if self.log else ""})

    def _end_turn(self) -> list:
        """Monsters act. Returns list of monsters that hit the player."""
        hits = []
        self.turn += 1
        for m in self.monsters:
            d = manhattan((m.x, m.y), (self.px, self.py))
            if d <= WAKE_RADIUS:
                m.awake = True
            if not m.awake:
                continue
            if d == 1:
                self.hp -= m.atk
                hits.append(m)
                self._event(f"{m.kind}#{m.id} hits you for {m.atk} (hp {self.hp})")
                if self.hp <= 0:
                    self.dead = True
                    self.death_cause = f"killed by {m.kind} on floor {self.floor}"
                    self._event(f"you die ({self.death_cause})")
                    break
            elif m.speed == 1 or self.turn % m.speed == 0:
                occupied = {(o.x, o.y) for o in self.monsters if o is not m}
                p = self.path((m.x, m.y), (self.px, self.py), allow_door=False, avoid_monsters=False, blocked=frozenset(occupied))
                if p and len(p) > 1:
                    m.x, m.y = p[0]
        if not self.dead and self.turn >= TURN_LIMIT:
            self.death_cause = "ran out of turns"
            self._event("turn limit reached")
        self._snap()
        return hits

    def _step_to(self, x, y):
        if self.grid[y][x] == "+":
            self.grid[y][x] = "."
            self._event("you unlock the door")
        self.px, self.py = x, y
        for it in list(self.items):
            if (it.x, it.y) == (x, y):
                self.items.remove(it)
                if it.kind == "gold":
                    self.gold += it.value
                elif it.kind == "potion":
                    self.potions += 1
                elif it.kind == "key":
                    self.has_key = True
                elif it.kind == "sword":
                    self.atk += 2
                self._event(f"picked up {it.kind}")

    def _attack(self, m: Monster):
        m.hp -= self.atk
        m.awake = True
        if m.hp <= 0:
            self.monsters.remove(m)
            self._event(f"you kill {m.kind}#{m.id}")
        else:
            self._event(f"you hit {m.kind}#{m.id} (hp {m.hp})")

    # ------------------------------------------------------------------ macro actions
    def act(self, name: str, args: dict | None = None) -> str:
        """Execute one macro-action. Returns a short text result for the agent."""
        args = args or {}
        if self.over:
            return "game over"
        start_log = len(self.log)
        try:
            fn = getattr(self, f"_a_{name}")
        except AttributeError:
            return f"error: unknown action {name!r}"
        try:
            res = fn(**args)
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}"
        tail = self.log[start_log:][-6:]
        return res + ("\nevents: " + " | ".join(tail) if tail else "")

    def _a_move_to(self, x, y, max_steps=40):
        x, y = int(x), int(y)
        target = (x, y)
        if not self.passable(x, y, allow_door=True):
            return f"error: ({x},{y}) is not walkable"
        if self.grid[y][x] == "+" and not self.has_key:
            return "error: door is locked and you have no key"
        steps = 0
        while (self.px, self.py) != target and steps < int(max_steps) and not self.over:
            p = self.path((self.px, self.py), target)
            if p is None:
                return f"stopped at ({self.px},{self.py}): no path to ({x},{y}) (monster or locked door in the way)"
            nxt = p[0]
            if self.monster_at(*nxt):
                return f"stopped at ({self.px},{self.py}): monster blocks the way"
            self._step_to(*nxt)
            steps += 1
            hits = self._end_turn()
            if hits and (self.px, self.py) != target:
                return f"interrupted at ({self.px},{self.py}) after {steps} steps: attacked by " + \
                    ", ".join(f"{m.kind}#{m.id}" for m in hits)
        if self.dead:
            return "you died"
        return f"arrived at ({self.px},{self.py})" if (self.px, self.py) == target else f"stopped at ({self.px},{self.py})"

    def _a_fight(self, monster_id, retreat_hp=0):
        retreat_hp = int(retreat_hp)
        m = next((m for m in self.monsters if m.id == int(monster_id)), None)
        if m is None:
            return f"error: no monster with id {monster_id}"
        for _ in range(60):
            if self.over or m not in self.monsters:
                break
            if 0 < retreat_hp and self.hp <= retreat_hp:
                return f"retreat threshold reached (hp {self.hp}); {m.kind}#{m.id} has hp {m.hp}"
            if manhattan((m.x, m.y), (self.px, self.py)) == 1:
                self._attack(m)
            else:
                p = self.path((self.px, self.py), (m.x, m.y))
                if not p:
                    return f"cannot reach {m.kind}#{m.id}"
                self._step_to(*p[0])
            self._end_turn()
        if self.dead:
            return "you died"
        if m not in self.monsters:
            return f"killed {m.kind}#{m.id}; hp {self.hp}"
        return f"fight stopped; {m.kind}#{m.id} hp {m.hp}, your hp {self.hp}"

    def _a_use_potion(self):
        if self.potions <= 0:
            return "error: no potions"
        self.potions -= 1
        healed = min(6, self.max_hp - self.hp)
        self.hp += healed
        self._event(f"drank potion (+{healed}, hp {self.hp})")
        self._end_turn()
        return f"hp now {self.hp}"

    def _a_descend(self):
        if (self.px, self.py) != self.stairs:
            return f"error: not on stairs; stairs are at {self.stairs}"
        self.floors_cleared += 1
        self._event(f"descended from floor {self.floor}")
        if self.floor >= MAX_FLOORS:
            self.won = True
            self._snap()
            return "you escaped the dungeon. YOU WIN"
        self.floor += 1
        self._gen_floor()
        self._snap()
        return f"now on floor {self.floor}"

    def _a_wait(self):
        self._end_turn()
        return "waited"


def _span(a, b):
    step = 1 if b >= a else -1
    return range(a, b + step, step)


GAME_ACTIONS = [
    {"name": "move_to", "description": "Walk the shortest path to tile (x,y). Picks up items on the way, unlocks doors if you hold the key. Stops early if a monster attacks you or blocks the path.",
     "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}},
    {"name": "fight", "description": "Walk to the monster and melee it until it dies. Optional retreat_hp stops the fight when your hp drops to that value or below.",
     "parameters": {"type": "object", "properties": {"monster_id": {"type": "integer"}, "retreat_hp": {"type": "integer"}}, "required": ["monster_id"]}},
    {"name": "use_potion", "description": "Drink a potion: heal 6 hp (up to max). Takes one turn.", "parameters": {"type": "object", "properties": {}}},
    {"name": "descend", "description": "Take the stairs. You must be standing on '>'. Descending the last floor wins.", "parameters": {"type": "object", "properties": {}}},
    {"name": "wait", "description": "Do nothing for one turn.", "parameters": {"type": "object", "properties": {}}},
]

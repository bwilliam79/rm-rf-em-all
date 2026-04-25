#!/usr/bin/env python3
"""RM -RF 'EM ALL -- terminal pixel-art side-scroller.

Renders 8-bit pixel art into your terminal using upper-half-block characters
plus 24-bit ANSI truecolor (each cell holds two stacked pixels: fg = top,
bg = bottom). The player is a nerd with glasses and a slingshot; red ghouls
shamble in from the right and you pelt them. macOS only -- uses `afplay`
for sound and a stdlib `wave` chiptune for theme music.
"""

import array
import os
import random
import select
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import wave

# ===================================================================
# Tuning
# ===================================================================
TARGET_FPS         = 30
FRAME_DT           = 1.0 / TARGET_FPS
PLAYER_MIN_X       = 6
PLAYER_MAX_FRACTION = 0.45     # player can walk left half-ish of screen
PLAYER_SPEED       = 1.4       # px / tick
PELLET_SPEED       = 3.4       # px / tick (straight shot, no gravity)
PELLET_COOLDOWN    = 0.18      # seconds between shots
ENEMY_SPEED_MIN    = 0.30
ENEMY_SPEED_MAX    = 0.65
ENEMY_SPAWN_MIN    = 0.9
ENEMY_SPAWN_MAX    = 2.0
TOTAL_ENEMIES      = 8
PLAYER_LIVES       = 3
HIT_COOLDOWN       = 0.8

# ===================================================================
# Flavor text (some carried over)
# ===================================================================
KILL_TAUNTS = [
    "Core dumped.",
    "Uninstalled.",
    "Segfault sent.",
    "404: Enemy not found.",
    "kill -9'd ya.",
    "sudo rm -rf /them",
    "Access denied... to oxygen.",
    "Process terminated.",
    "Exit code: ouch.",
    "EOF, buddy.",
]

WIN_QUOTES = [
    "All enemies uninstalled. Ship it.",
    "Exit code 0. You are the root user now.",
    "Clean build, no warnings. Go get a beer.",
]

DEATH_QUOTES = [
    "You got rm -rf'd by an NPC. Embarrassing.",
    "Segmentation fault (core dumped).",
    "kernel panic -- not syncing: attempted to kill you",
]

SOUNDS = {
    "shoot": "/System/Library/Sounds/Pop.aiff",
    "kill":  "/System/Library/Sounds/Glass.aiff",
    "miss":  "/System/Library/Sounds/Tink.aiff",
    "win":   "/System/Library/Sounds/Hero.aiff",
    "lose":  "/System/Library/Sounds/Basso.aiff",
    "hit":   "/System/Library/Sounds/Bottle.aiff",
}

# ===================================================================
# ANSI helpers
# ===================================================================
def fg_ansi(r, g, b): return f"\x1b[38;2;{r};{g};{b}m"
def bg_ansi(r, g, b): return f"\x1b[48;2;{r};{g};{b}m"

RESET   = "\x1b[0m"
HIDE    = "\x1b[?25l"
SHOW    = "\x1b[?25h"
CLEAR   = "\x1b[2J"
HOME    = "\x1b[H"
NORM_CK = "\x1b[?1l"  # normal (not application) cursor-key mode
HALF    = "\u2580"    # upper half block

# ===================================================================
# Palette - single-char-per-pixel keys for sprite strings
# ===================================================================
PAL = {
    '.': None,                  # transparent
    ' ': None,                  # transparent (alias)

    # sky / atmosphere
    'k': (10, 14, 26),          # deep night sky
    'K': (28, 34, 60),          # mid sky
    'B': (52, 62, 100),         # high sky / dawn
    '*': (220, 220, 240),       # star

    # ground
    'g': (104, 80, 50),         # ground top (lit)
    'G': (72, 54, 32),          # ground mid
    'h': (44, 32, 18),          # ground deep
    'q': (180, 140, 80),        # grass/grain highlight

    # back wall / arena
    'j': (60, 36, 24),          # back wall mid
    'J': (78, 50, 32),           # back wall light
    'u': (36, 22, 14),           # back wall shadow

    # nerd
    'H': (60, 35, 22),          # hair
    's': (232, 185, 136),        # skin
    'x': (180, 130, 90),         # skin shadow
    'F': (32, 32, 48),           # glasses frame
    'L': (180, 230, 255),        # glasses lens
    'E': (20, 20, 24),           # pupil
    'M': (160, 70, 70),          # mouth
    'P': (110, 90, 220),         # shirt purple (nerd)
    'p': (78, 60, 170),          # shirt shadow
    'T': (60, 60, 88),           # pants
    't': (40, 40, 64),           # pants shadow
    'b': (16, 16, 24),           # boots
    'W': (140, 95, 55),          # slingshot wood
    'w': (90, 60, 32),           # wood shadow
    'R': (200, 70, 70),          # rubber band

    # pellet
    'O': (230, 230, 240),        # pellet
    'o': (255, 250, 200),        # pellet glow

    # enemy (red ghoul)
    'r': (130, 30, 30),
    'd': (200, 65, 65),
    'D': (255, 100, 100),
    'V': (255, 220, 60),         # eye yellow
    'm': (50, 10, 10),           # mouth interior
    'i': (90, 16, 16),           # enemy shadow / boot

    # decoration
    'c': (160, 110, 50),         # crate light
    'C': (110, 80, 40),           # crate mid
    'X': (60, 40, 18),            # crate dark
    'f': (255, 200, 60),         # fire
    'Y': (255, 240, 130),         # fire bright
    'z': (180, 60, 30),           # fire base
}

# ===================================================================
# Sprites - rectangular grids, one char per pixel.
# '.' or ' ' = transparent.  Multi-char keys NOT used here; one char each.
# ===================================================================

# 18 wide x 20 tall nerd protagonist with glasses, holding a Y-shaped slingshot
# extending out to the right hand. ".\" / " " = transparent. Pixel keys map via PAL.
NERD = [
    "....HHHHHH........",
    "..HHHHHHHHHH......",
    "..HsssssssH.......",
    "..HsssssssH.......",
    ".HFFLLFFLLFFH.....",
    ".HFLELFFLELFH.....",
    "..HsssssssH.......",
    "...sssMMss........",
    "....sssss.........",
    "...PPPPPPP........",
    "..PPpPPPPPp..W.W..",
    "..pPPPPPPPpsWRRRW.",
    "..PPPPPPPPPsWWoWW.",
    "..PPPPPPPPPs.WRW..",
    "..pPPPPPPPp..WWW..",
    "...PPPPPP....WW...",
    "...TTTTT.....WW...",
    "..tTTTTt..........",
    "..tT..Tt..........",
    "..bb..bb..........",
]

# 12 wide x 16 tall red ghoul, two animation frames (legs swap)
ENEMY_A = [
    "...rrrrrr...",
    "..drrrrrrr..",
    "..drVdddVdr.",
    "..drdddddrr.",
    "..drdmmmmdr.",
    "..rdmmmmmdr.",
    "..rrdddddrr.",
    "..rrrrrrrrr.",
    "rrr.rdrr.rrr",
    "rr...rr...rr",
    "..rr.rr.rr..",
    "..rr.rr.rr..",
    "..rr.rr.rr..",
    "..rd.rd.dr..",
    "..rr.rr.rr..",
    "..ii.ii.ii..",
]

ENEMY_B = [
    "...rrrrrr...",
    "..drrrrrrr..",
    "..drVdddVdr.",
    "..drdddddrr.",
    "..drdmmmmdr.",
    "..rdmmmmmdr.",
    "..rrdddddrr.",
    "..rrrrrrrrr.",
    "..rrrrrrrr..",
    "...rd..dr...",
    "..rr....rr..",
    ".rr......rr.",
    "rr........rr",
    "rd........dr",
    "rr........rr",
    "ii........ii",
]

# 10x9 crate decoration
CRATE = [
    "cccccccccc",
    "cCCCCCCCCc",
    "cCXCCCCXCc",
    "cCCCCCCCCc",
    "cCXCCCCXCc",
    "cCCCCCCCCc",
    "cCXCCCCXCc",
    "cCCCCCCCCc",
    "XXXXXXXXXX",
]

# 4x6 torch (wall sconce)
TORCH = [
    ".YY.",
    "YfYf",
    "fYfY",
    ".zz.",
    ".WW.",
    ".WW.",
]

# 5x7 pixel font for HUD, taunts.
def _g(*rows):
    assert len(rows) == 7 and all(len(r) == 5 for r in rows)
    return rows

FONT = {
    "A": _g(".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": _g("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": _g(".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": _g("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": _g("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": _g("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": _g(".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": _g("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": _g(".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": _g("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": _g("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": _g("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": _g("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": _g("#...#", "##..#", "#.#.#", "#.#.#", "#..##", "#...#", "#...#"),
    "O": _g(".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": _g("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": _g(".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": _g("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": _g(".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": _g("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": _g("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": _g("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": _g("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": _g("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": _g("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": _g("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": _g(".###.", "#..##", "#.#.#", "#.#.#", "##..#", "#...#", ".###."),
    "1": _g("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": _g(".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": _g("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "4": _g("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": _g("#####", "#....", "#....", "####.", "....#", "....#", "####."),
    "6": _g(".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": _g("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": _g(".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": _g(".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    " ": _g(".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "'": _g("..#..", "..#..", "..#..", ".....", ".....", ".....", "....."),
    ".": _g(".....", ".....", ".....", ".....", ".....", ".....", "..#.."),
    ",": _g(".....", ".....", ".....", ".....", ".....", "..#..", ".#..."),
    "-": _g(".....", ".....", ".....", ".###.", ".....", ".....", "....."),
    "/": _g("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "!": _g("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    ":": _g(".....", ".....", "..#..", ".....", "..#..", ".....", "....."),
    "?": _g(".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "+": _g(".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "=": _g(".....", ".....", "#####", ".....", "#####", ".....", "....."),
}


# ===================================================================
# Framebuffer + half-block renderer
# ===================================================================
class Framebuffer:
    """RGB framebuffer rendered with upper-half-block + truecolor.

    Each terminal cell shows two stacked pixels: fg = top pixel, bg = bottom.
    Writing pixel (x, y) at width W produces a character at column x, row y//2.
    """
    __slots__ = ("w", "h", "px")

    def __init__(self, w, h):
        if h % 2:  # round up to even so half-block pairs align
            h += 1
        self.w = w
        self.h = h
        self.px = [(0, 0, 0)] * (w * h)

    def clear(self, color):
        self.px = [color] * (self.w * self.h)

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h and color is not None:
            self.px[y * self.w + x] = color

    def fill_rect(self, x, y, w, h, color):
        if color is None:
            return
        for yy in range(max(0, y), min(self.h, y + h)):
            base = yy * self.w
            for xx in range(max(0, x), min(self.w, x + w)):
                self.px[base + xx] = color

    def blit_sprite(self, sprite_rows, x0, y0, palette=PAL, flip=False):
        for dy, row in enumerate(sprite_rows):
            if flip:
                row = row[::-1]
            for dx, ch in enumerate(row):
                color = palette.get(ch)
                if color is not None:
                    self.set(x0 + dx, y0 + dy, color)

    def blit_text(self, text, x0, y0, color, spacing=1):
        """Render uppercase text using FONT (5x7 glyphs)."""
        cx = x0
        upper = text.upper()
        for ch in upper:
            glyph = FONT.get(ch, FONT[" "])
            for ry, row in enumerate(glyph):
                for rx, c in enumerate(row):
                    if c == "#":
                        self.set(cx + rx, y0 + ry, color)
            cx += 5 + spacing

    def render(self):
        """Return the ANSI string that draws the framebuffer."""
        out = [HOME]
        prev_fg = prev_bg = None
        w = self.w
        for cy in range(0, self.h, 2):
            row_top = cy * w
            row_bot = (cy + 1) * w
            line = []
            for x in range(w):
                top = self.px[row_top + x]
                bot = self.px[row_bot + x]
                if top != prev_fg:
                    line.append(fg_ansi(*top))
                    prev_fg = top
                if bot != prev_bg:
                    line.append(bg_ansi(*bot))
                    prev_bg = bot
                line.append(HALF)
            line.append(RESET)
            line.append("\r\n")
            out.append("".join(line))
            prev_fg = prev_bg = None  # reset across lines (terminal may not preserve)
        return "".join(out)


# ===================================================================
# Background (computed once per resolution)
# ===================================================================
def draw_background(fb):
    """Sky -> mountains -> back wall -> ground floor with some texture."""
    w, h = fb.w, fb.h

    # ground horizon line: ground takes bottom 22% of pixels
    ground_y = int(h * 0.78)
    wall_top = int(h * 0.18)
    wall_bot = ground_y - 1

    # Sky gradient: 3 bands top to bottom
    for y in range(0, wall_top):
        t = y / max(1, wall_top - 1)
        # interpolate from k -> K -> B
        if t < 0.5:
            tt = t * 2
            c = lerp_rgb(PAL['k'], PAL['K'], tt)
        else:
            tt = (t - 0.5) * 2
            c = lerp_rgb(PAL['K'], PAL['B'], tt)
        for x in range(w):
            fb.set(x, y, c)

    # Stars
    rng = random.Random(42)
    for _ in range(max(8, w // 8)):
        sx = rng.randrange(w)
        sy = rng.randrange(0, max(1, wall_top - 2))
        fb.set(sx, sy, PAL['*'])

    # Back wall (brick)
    fb.fill_rect(0, wall_top, w, wall_bot - wall_top + 1, PAL['j'])
    # brick courses every 4 px, alternating offsets
    for r in range(wall_top, wall_bot + 1, 4):
        # mortar horizontal line
        for x in range(w):
            fb.set(x, r, PAL['u'])
        offset = 0 if ((r - wall_top) // 4) % 2 == 0 else 5
        for x in range(-offset, w, 10):
            for dy in range(0, 4):
                if r + dy <= wall_bot:
                    fb.set(x % w, r + dy, PAL['u'])
    # subtle wall highlight band
    for x in range(w):
        fb.set(x, wall_top, PAL['J'])

    # Wall-floor seam shadow
    fb.fill_rect(0, ground_y - 1, w, 1, (20, 12, 6))

    # Ground (3 bands: lit -> mid -> deep)
    for y in range(ground_y, h):
        t = (y - ground_y) / max(1, h - ground_y - 1)
        if t < 0.25:
            c = PAL['g']
        elif t < 0.6:
            c = PAL['G']
        else:
            c = PAL['h']
        for x in range(w):
            fb.set(x, y, c)
    # tile seams
    for x in range(0, w, 12):
        for y in range(ground_y, ground_y + 2):
            fb.set(x, y, PAL['h'])
    # grain highlights every other pixel on the top ground row
    for x in range(0, w, 3):
        fb.set(x, ground_y, PAL['q'])

    return ground_y, wall_top, wall_bot


def lerp_rgb(a, b, t):
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


# ===================================================================
# World
# ===================================================================
class Pellet:
    __slots__ = ("x", "y", "vx", "alive")
    def __init__(self, x, y, vx):
        self.x = x
        self.y = y
        self.vx = vx
        self.alive = True


class Enemy:
    __slots__ = ("x", "y", "speed", "hp", "alive", "anim_t")
    def __init__(self, x, y, speed):
        self.x = x
        self.y = y
        self.speed = speed
        self.hp = 1
        self.alive = True
        self.anim_t = 0.0


class World:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        # static-set in update_layout when bg drawn
        self.ground_y = int(h * 0.78)
        self.player_x = PLAYER_MIN_X + 4
        self.player_y = self.ground_y - len(NERD)
        self.player_face_right = True
        self.pellets = []
        self.enemies = []
        self.kills = 0
        self.lives = PLAYER_LIVES
        self.spawned = 0
        self.next_spawn = time.time() + random.uniform(ENEMY_SPAWN_MIN, ENEMY_SPAWN_MAX)
        self.last_shot = 0.0
        self.last_hit = 0.0
        self.state = "playing"  # "win" / "lose"
        self.message = random.choice(KILL_TAUNTS)
        self.message_until = 0.0
        self.end_message = ""

    def player_max_x(self):
        return int(self.w * PLAYER_MAX_FRACTION)

    def feet_y(self):
        return self.ground_y

    def update_layout(self, w, h, ground_y):
        self.w, self.h, self.ground_y = w, h, ground_y
        # clamp player
        self.player_x = max(PLAYER_MIN_X, min(self.player_x, self.player_max_x()))
        self.player_y = self.ground_y - len(NERD)

    def shoot(self):
        now = time.time()
        if now - self.last_shot < PELLET_COOLDOWN:
            return
        self.last_shot = now
        # pellet emerges from slingshot tip ~ right side of nerd, mid-height
        sling_x = self.player_x + (12 if self.player_face_right else 1)
        sling_y = self.player_y + 12
        vx = PELLET_SPEED if self.player_face_right else -PELLET_SPEED
        self.pellets.append(Pellet(sling_x, sling_y, vx))
        play("shoot")

    def tick(self, dt, keys):
        if self.state != "playing":
            return

        # input
        for k in keys:
            if k == "LEFT":
                self.player_x = max(PLAYER_MIN_X, self.player_x - PLAYER_SPEED)
                self.player_face_right = False
            elif k == "RIGHT":
                self.player_x = min(self.player_max_x(), self.player_x + PLAYER_SPEED)
                self.player_face_right = True
            elif k == " ":
                self.shoot()

        # spawn enemies
        now = time.time()
        if (self.spawned < TOTAL_ENEMIES
                and now >= self.next_spawn
                and len([e for e in self.enemies if e.alive]) < 4):
            speed = random.uniform(ENEMY_SPEED_MIN, ENEMY_SPEED_MAX)
            ex = self.w + 2
            ey = self.ground_y - len(ENEMY_A)
            self.enemies.append(Enemy(ex, ey, speed))
            self.spawned += 1
            self.next_spawn = now + random.uniform(ENEMY_SPAWN_MIN, ENEMY_SPAWN_MAX)

        # pellets
        for p in self.pellets:
            if not p.alive:
                continue
            p.x += p.vx
            if p.x < -2 or p.x > self.w + 2:
                p.alive = False
        self.pellets = [p for p in self.pellets if p.alive]

        # enemies
        for e in self.enemies:
            if not e.alive:
                continue
            e.x -= e.speed
            e.anim_t += dt
            # collide with pellets (bounding box: enemy ~ 12x16 at e.x..e.x+11)
            for p in self.pellets:
                if not p.alive:
                    continue
                if (e.x - 1 <= p.x <= e.x + 12
                        and e.y - 1 <= p.y <= e.y + 16):
                    e.alive = False
                    p.alive = False
                    self.kills += 1
                    self.message = random.choice(KILL_TAUNTS)
                    self.message_until = time.time() + 1.4
                    play("kill")
                    break
            # touch player
            if e.alive:
                # enemy bbox (12 wide) vs player bbox (nerd is ~12 wide centered)
                pleft = self.player_x + 2
                pright = self.player_x + 12
                if e.x < pright and e.x + 12 > pleft:
                    if time.time() - self.last_hit > HIT_COOLDOWN:
                        self.lives -= 1
                        self.last_hit = time.time()
                        play("hit")
                        e.alive = False
                        self.message = "You took a hit!"
                        self.message_until = time.time() + 1.0
        self.enemies = [e for e in self.enemies if e.alive or e.x > -16]

        # win/lose
        if self.lives <= 0:
            self.state = "lose"
            self.end_message = random.choice(DEATH_QUOTES)
            play("lose")
        elif self.kills >= TOTAL_ENEMIES:
            self.state = "win"
            self.end_message = random.choice(WIN_QUOTES)
            play("win")


# ===================================================================
# Render
# ===================================================================
def render_world(fb, world):
    fb.clear(PAL['k'])
    ground_y, wall_top, wall_bot = draw_background(fb)
    if ground_y != world.ground_y:
        world.update_layout(fb.w, fb.h, ground_y)

    # decoration: a torch on the back wall and a crate on the floor
    crate_x = max(20, fb.w // 4)
    crate_y = ground_y - len(CRATE)
    fb.blit_sprite(CRATE, crate_x, crate_y)
    torch_x = max(40, fb.w * 3 // 5)
    torch_y = wall_top + 4
    # torch glow (soft halo)
    for d in range(5):
        rr = 6 - d
        for dx in range(-rr, rr + 1):
            fb.set(torch_x + 2 + dx, torch_y + d - 2, PAL['j'])
    fb.blit_sprite(TORCH, torch_x, torch_y)

    # enemies (back to front by x)
    for e in sorted(world.enemies, key=lambda e: -e.x):
        if not e.alive:
            continue
        frame = ENEMY_A if int(e.anim_t * 6) % 2 == 0 else ENEMY_B
        # enemies face left toward player
        fb.blit_sprite(frame, int(e.x), int(e.y), flip=True)

    # player
    fb.blit_sprite(NERD, int(world.player_x), int(world.player_y),
                   flip=not world.player_face_right)

    # pellets: 2x2 ball + glow + trailing streak
    for p in world.pellets:
        if not p.alive:
            continue
        px, py = int(p.x), int(p.y)
        # streak (4 px tail) opposite to direction
        sign = -1 if p.vx > 0 else 1
        for d in range(1, 5):
            fb.set(px + sign * d, py, PAL['o'])
        # glow halo
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            fb.set(px + dx, py + dy, PAL['o'])
        # core ball (2x2)
        fb.fill_rect(px, py, 2, 2, PAL['O'])

    # HUD bar (top): score + lives only (title is on splash)
    hud_h = 9
    fb.fill_rect(0, 0, fb.w, hud_h, (10, 16, 12))
    if fb.w >= 110:
        stat = f"KILLS {world.kills}/{TOTAL_ENEMIES}    LIVES {world.lives}"
    else:
        stat = f"K {world.kills}/{TOTAL_ENEMIES}  HP {world.lives}"
    stat_w = len(stat) * 6 - 1
    fb.blit_text(stat, max(2, (fb.w - stat_w) // 2), 1, PAL['C'])

    # taunt centered if active
    if time.time() < world.message_until:
        msg = world.message.upper()
        msg_w = len(msg) * 6 - 1
        fb.blit_text(msg, max(2, (fb.w - msg_w) // 2), fb.h - 9, PAL['Y'])

    # end-state overlay
    if world.state in ("win", "lose"):
        text = "YOU WIN!" if world.state == "win" else "YOU LOSE."
        col = PAL['C'] if world.state == "win" else PAL['D']
        bigw = len(text) * 12 - 1  # scale=2 (each glyph 10 wide + 2 spacing)
        # render scaled font manually
        x0 = max(2, (fb.w - bigw) // 2)
        y0 = (fb.h // 2) - 8
        # draw a dark plate behind it
        fb.fill_rect(x0 - 4, y0 - 2, bigw + 8, 18, (0, 0, 0))
        cx = x0
        for ch in text.upper():
            glyph = FONT.get(ch, FONT[" "])
            for ry, row in enumerate(glyph):
                for rx, c in enumerate(row):
                    if c == "#":
                        fb.fill_rect(cx + rx * 2, y0 + ry * 2, 2, 2, col)
            cx += 5 * 2 + 2
        sub = world.end_message.upper()
        sub_w = len(sub) * 6 - 1
        fb.blit_text(sub, max(2, (fb.w - sub_w) // 2), y0 + 18, PAL['Y'])
        prompt = "Q TO QUIT"
        pw = len(prompt) * 6 - 1
        fb.blit_text(prompt, max(2, (fb.w - pw) // 2), y0 + 28, PAL['C'])


# ===================================================================
# Audio (kept from prior version)
# ===================================================================
def play(name):
    path = SOUNDS.get(name)
    if not path or not os.path.exists(path):
        return
    try:
        subprocess.Popen(
            ["afplay", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        pass


def generate_theme(path):
    """Death-metal-adjacent palm-muted square-wave riff. (Carried over.)"""
    SR = 22050
    NOTE_DUR = 0.085
    VOL = 5800
    FIFTH_VOL = 3100
    NOTES = {
        "E": 82.41, "F": 87.31, "G": 98.00, "A": 110.00,
        "b": 116.54, "B": 123.47, "C": 130.81, "D": 146.83,
        "e": 164.81, "f": 174.61, "g": 196.00, "-": 0.0,
    }
    SONG = (
        "EEEFEEEE" "EEEFEEbE"
        "EEEGEEEE" "EEEbEEbE"
        "EFGFEDEF" "bAGFEFGA"
        "EEEEEEEE" "EbEbEEEE"
        "eEeEeEeE" "EFEFEFEF"
        "bGEbGEbG" "EDEFGFED"
        "EEEEEEEE" "EEbEFGEE"
        "EFGFEDEF" "EEEEEEEE"
    )
    samples = array.array("h")
    for ch in SONG:
        freq = NOTES.get(ch, 0.0)
        n = int(SR * NOTE_DUR)
        if freq <= 0:
            samples.extend([0] * n)
            continue
        root_p = max(2, int(round(SR / freq)))
        root_half = root_p // 2
        root_one = [VOL] * root_half + [-VOL] * (root_p - root_half)
        root = root_one * (n // root_p) + root_one[:n - (n // root_p) * root_p]
        fifth_freq = freq * 1.5
        fifth_p = max(2, int(round(SR / fifth_freq)))
        fifth_half = fifth_p // 2
        fifth_one = [FIFTH_VOL] * fifth_half + [-FIFTH_VOL] * (fifth_p - fifth_half)
        fifth = fifth_one * (n // fifth_p) + fifth_one[:n - (n // fifth_p) * fifth_p]
        attack_n = max(1, int(n * 0.015))
        decay_floor = 0.22
        note = array.array("h", [0] * n)
        inv_tail = 1.0 / max(1, n - attack_n)
        for i in range(n):
            if i < attack_n:
                env = i / attack_n
            else:
                env = 1.0 - ((i - attack_n) * inv_tail) * (1.0 - decay_floor)
            s = int((root[i] + fifth[i]) * env)
            if s > 32767: s = 32767
            elif s < -32767: s = -32767
            note[i] = s
        samples.extend(note)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


_music_stop = threading.Event()
_music_proc = [None]


def start_music(wav_path):
    def runner():
        while not _music_stop.is_set():
            try:
                p = subprocess.Popen(
                    ["afplay", wav_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                _music_proc[0] = p
            except (FileNotFoundError, OSError):
                return
            while p.poll() is None:
                if _music_stop.is_set():
                    try:
                        p.terminate()
                    except Exception:
                        pass
                    return
                time.sleep(0.1)
    threading.Thread(target=runner, daemon=True).start()


def stop_music():
    _music_stop.set()
    p = _music_proc[0]
    if p:
        try:
            p.terminate()
        except Exception:
            pass


# ===================================================================
# Terminal helpers
# ===================================================================
def get_screen_size():
    try:
        sz = os.get_terminal_size()
        cols = max(60, min(160, sz.columns))
        rows = max(18, min(48, sz.lines - 1))
        return cols, rows
    except OSError:
        return 80, 24


def get_keys():
    """Drain every pending keypress. Returns a list (possibly empty)."""
    keys = []
    fd = sys.stdin.fileno()
    data = b""
    while True:
        r, _, _ = select.select([fd], [], [], 0)
        if not r:
            break
        try:
            chunk = os.read(fd, 64)
        except (BlockingIOError, OSError):
            break
        if not chunk:
            break
        data += chunk

    if data.endswith(b"\x1b"):
        r, _, _ = select.select([fd], [], [], 0.03)
        if r:
            try:
                data += os.read(fd, 8)
            except (BlockingIOError, OSError):
                pass
    if len(data) >= 2 and data[-2] == 0x1b and data[-1] in (0x5b, 0x4f):
        r, _, _ = select.select([fd], [], [], 0.03)
        if r:
            try:
                data += os.read(fd, 8)
            except (BlockingIOError, OSError):
                pass

    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x1b:
            if i + 1 < len(data) and data[i + 1] in (0x5b, 0x4f):
                if i + 2 < len(data):
                    c = data[i + 2]
                    if c == 0x41: keys.append("UP")
                    elif c == 0x42: keys.append("DOWN")
                    elif c == 0x43: keys.append("RIGHT")
                    elif c == 0x44: keys.append("LEFT")
                    i += 3
                    continue
                i += 2
                continue
            i += 1
        elif b < 128:
            keys.append(chr(b))
            i += 1
        else:
            i += 1
    return keys


# ===================================================================
# Splash screen (text-based, kept simple)
# ===================================================================
BANNER = [
    " ____  __  __      ____  _____ ",
    "|  _ \\|  \\/  |    |  _ \\|  ___|",
    "| |_) | |\\/| |    | |_) | |_  ",
    "|  _ <| |  | |    |  _ <|  _| ",
    "|_| \\_\\_|  |_|    |_| \\_\\_|   ",
    "",
    " ___ __  __       _    _     _ ",
    "| __|  \\/  |     / \\  | |   | |",
    "| _|| |\\/| |    / _ \\ | |   | |",
    "|___|_|  |_|   /_/ \\_\\|___|_|_|",
]
BANNER_COLORS = [
    fg_ansi(255, 60, 60),
    fg_ansi(255, 100, 60),
    fg_ansi(255, 150, 60),
    fg_ansi(255, 200, 60),
    fg_ansi(255, 240, 60),
    fg_ansi(255, 240, 60),
    fg_ansi(240, 255, 60),
    fg_ansi(200, 255, 80),
    fg_ansi(150, 255, 120),
    fg_ansi(100, 255, 160),
]
TAGLINE = "A terminal pixel-art slingshot for the terminally online."
SUBLINE = "Built in Python. Runs in your shell. Smells like a burned CPU."
PROMPT  = "[ press ENTER to rm -rf em all   //   press Q to chicken out ]"


def splash(W, H):
    blink_on = True
    last_blink = time.time()
    BLACK = bg_ansi(0, 0, 0)
    while True:
        now = time.time()
        if now - last_blink > 0.45:
            blink_on = not blink_on
            last_blink = now

        out = [HOME, RESET, BLACK]
        for r in range(H):
            out.append(f"\x1b[{r + 1};1H")
            out.append(BLACK + " " * W)

        banner_top = max(1, (H - len(BANNER) - 6) // 2)
        for i, line in enumerate(BANNER):
            row = banner_top + i
            if row >= H - 3:
                break
            col = max(0, (W - len(line)) // 2)
            out.append(f"\x1b[{row + 1};{col + 1}H")
            out.append(BANNER_COLORS[min(i, len(BANNER_COLORS) - 1)])
            out.append(line)

        tag_row = banner_top + len(BANNER) + 1
        if tag_row < H - 2:
            col = max(0, (W - len(TAGLINE)) // 2)
            out.append(f"\x1b[{tag_row + 1};{col + 1}H")
            out.append(fg_ansi(220, 220, 220) + TAGLINE)
        sub_row = tag_row + 1
        if sub_row < H - 2:
            col = max(0, (W - len(SUBLINE)) // 2)
            out.append(f"\x1b[{sub_row + 1};{col + 1}H")
            out.append(fg_ansi(140, 140, 140) + SUBLINE)
        prompt_row = sub_row + 2
        if prompt_row >= H:
            prompt_row = H - 1
        col = max(0, (W - len(PROMPT)) // 2)
        out.append(f"\x1b[{prompt_row + 1};{col + 1}H")
        if blink_on:
            out.append(fg_ansi(255, 230, 80) + PROMPT)
        else:
            out.append(" " * len(PROMPT))
        out.append(RESET)
        sys.stdout.write("".join(out))
        sys.stdout.flush()

        for k in get_keys():
            if k in ("\r", "\n"):
                return True
            if k in ("q", "Q"):
                return False
        time.sleep(0.05)


# ===================================================================
# Main
# ===================================================================
def main():
    if not sys.stdin.isatty():
        print("RM -RF 'EM ALL needs a real terminal. Run it directly.", file=sys.stderr)
        return 1

    theme_path = os.path.join(tempfile.gettempdir(), "rm_rf_em_all_theme_v2.wav")
    try:
        if not os.path.exists(theme_path):
            generate_theme(theme_path)
        start_music(theme_path)
    except Exception:
        pass

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write(CLEAR + HIDE + NORM_CK)
        sys.stdout.flush()

        if not splash(*get_screen_size()):
            stop_music()
            return 0
        stop_music()
        sys.stdout.write(RESET + CLEAR)
        sys.stdout.flush()

        cols, rows = get_screen_size()
        fb_w = cols
        fb_h = rows * 2
        fb = Framebuffer(fb_w, fb_h)
        world = World(fb_w, fb_h)
        last = time.time()
        while True:
            now = time.time()
            dt = now - last
            last = now

            ncols, nrows = get_screen_size()
            if (ncols, nrows) != (cols, rows):
                cols, rows = ncols, nrows
                fb = Framebuffer(cols, rows * 2)
                world.update_layout(fb.w, fb.h, world.ground_y)
                sys.stdout.write(CLEAR)

            keys = get_keys()
            quit_flag = any(k in ("q", "Q") for k in keys)
            if quit_flag:
                break
            if world.state == "playing":
                world.tick(dt, keys)

            render_world(fb, world)
            sys.stdout.write(fb.render())
            sys.stdout.flush()

            sleep_for = FRAME_DT - (time.time() - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        stop_music()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(RESET + CLEAR + HOME + SHOW)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

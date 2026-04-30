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
TARGET_FPS         = 60        # bumped from 30 -- doubles input poll rate, halves
FRAME_DT           = 1.0 / TARGET_FPS  # input latency, smooths motion.
PLAYER_MIN_X       = 6
# All physics are now expressed in real-time (px/sec, px/sec^2). Per-tick
# values get derived in tick() via dt so physics is identical at any FPS.
PLAYER_PX_PER_SEC      = 70.0   # walk speed (px/sec)
MOVE_HOLD_S            = 0.40   # extends a single keypress this long so we walk
                                # through the OS auto-repeat pre-delay (250-500ms).
WORLD_SCREENS          = 3
CAMERA_DEAD_LO         = 0.35
CAMERA_DEAD_HI         = 0.55
PELLET_PX_PER_SEC      = 160.0  # ~50% snappier than before.
PELLET_COOLDOWN        = 0.18   # seconds between shots
JUMP_V0_PX_PER_SEC     = 150.0  # initial jump velocity. With GRAVITY below,
GRAVITY_PX_PER_SEC2    = 460.0  # peak jump = v0^2 / (2g) ~ 24 px (clears a
                                # 2-stack crate, 18 px, with comfortable margin).
ENEMY_PX_PER_SEC_MIN   = 14.0   # ghoul shamble speed. 14 = leisurely, 28 = brisk.
ENEMY_PX_PER_SEC_MAX   = 28.0
ENEMY_SPAWN_MIN        = 0.9
ENEMY_SPAWN_MAX        = 2.0
TOTAL_ENEMIES          = 8
PLAYER_LIVES           = 3
HIT_COOLDOWN           = 0.8

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
    'P': (238, 240, 246),        # lab coat white
    'p': (188, 192, 205),        # lab coat shadow / fold
    'N': ( 50,  50,  82),        # collar V + button placket (dark navy)
    'n': (110,  90, 200),        # bit of purple shirt under the V
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

    # floppy disk pickup
    '7': (50, 50, 75),           # floppy shell (dark plastic)
    '8': (225, 225, 240),        # floppy label (white)
    '9': (170, 170, 190),        # floppy slider (silver)

    # rapid-fire pickup glow
    'Z': (255, 180,  60),        # outer glow
    'y': (255, 230, 100),        # mid glow
    'l': (255, 255, 230),        # bright core

    # gap / abyss
    'a': (  6,   8,  12),        # near-black void
    'A': ( 16,  18,  24),        # void mid
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
    "...PPPnPPP........",   # closed neck, tiny purple shirt peek
    "..PPpNnnnNp..W.W..",   # V-collar opens: lapels (N) frame undershirt (n)
    "..pPPPNPPPpsWRRRW.",   # V tip
    "..PPPPPPPPPsWWoWW.",
    "..PPPPPPPPPs.WRW..",
    "..pPPPNPPPp..WWW..",   # button
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

# 5x6 floppy disk pickup
FLOPPY = [
    "77997",
    "77777",
    "78887",
    "78887",
    "78887",
    "77777",
]

# 5x5 RAPID powerup -- glowing orb
POWERUP_RAPID = [
    ".ZZZ.",
    "ZyyyZ",
    "ZylyZ",
    "ZyyyZ",
    ".ZZZ.",
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

    def blit_text_scaled(self, text, x0, y0, scale, color, spacing=1, row_colors=None):
        """Render uppercase text scaled. row_colors (list of 7) overrides color
        per glyph row (for fire/rainbow gradients). spacing is pre-scale."""
        cx = x0
        for ch in text.upper():
            glyph = FONT.get(ch, FONT[" "])
            for ry, row in enumerate(glyph):
                rc = row_colors[ry] if row_colors else color
                for rx, c in enumerate(row):
                    if c == "#":
                        self.fill_rect(cx + rx * scale, y0 + ry * scale,
                                       scale, scale, rc)
            cx += (5 + spacing) * scale

    @staticmethod
    def text_width(text, scale=1, spacing=1):
        n = len(text)
        return n * 5 * scale + max(0, n - 1) * spacing * scale

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
def draw_background(fb, camera_x=0.0):
    """Sky -> back wall -> ground. Wall + ground textures scroll with the
    camera; sky and stars stay fixed (parallax at infinity)."""
    w, h = fb.w, fb.h
    cx = int(camera_x)

    # ground horizon line: ground takes bottom 22% of pixels
    ground_y = int(h * 0.78)
    wall_top = int(h * 0.18)
    wall_bot = ground_y - 1

    # Sky gradient: 3 bands top to bottom (no scroll)
    for y in range(0, wall_top):
        t = y / max(1, wall_top - 1)
        if t < 0.5:
            c = lerp_rgb(PAL['k'], PAL['K'], t * 2)
        else:
            c = lerp_rgb(PAL['K'], PAL['B'], (t - 0.5) * 2)
        for x in range(w):
            fb.set(x, y, c)

    # Stars (fixed in screen space — they're "at infinity")
    rng = random.Random(42)
    for _ in range(max(8, w // 8)):
        sx = rng.randrange(w)
        sy = rng.randrange(0, max(1, wall_top - 2))
        fb.set(sx, sy, PAL['*'])

    # Back wall fill
    fb.fill_rect(0, wall_top, w, wall_bot - wall_top + 1, PAL['j'])

    # Horizontal mortar courses every 4 px (full width — same regardless of cx)
    for r in range(wall_top, wall_bot + 1, 4):
        for x in range(w):
            fb.set(x, r, PAL['u'])

    # Vertical mortar boundaries — scroll with camera, alternate offset per
    # course so bricks look staggered.
    for r in range(wall_top, wall_bot + 1, 4):
        course_idx = (r - wall_top) // 4
        course_offset = 5 if (course_idx % 2) else 0
        # World-x boundaries are at multiples of 10 + course_offset.
        # Pick the smallest screen_x in [-10, 0) that maps to one.
        first = (course_offset - cx) % 10
        if first > 0:
            first -= 10
        for sx in range(first, w, 10):
            for dy in range(0, 4):
                yy = r + dy
                if yy <= wall_bot:
                    fb.set(sx, yy, PAL['u'])

    # Wall highlight band
    for x in range(w):
        fb.set(x, wall_top, PAL['J'])

    # Wall-floor seam shadow
    fb.fill_rect(0, ground_y - 1, w, 1, (20, 12, 6))

    # Ground bands (3 colors top→bottom)
    for y in range(ground_y, h):
        t = (y - ground_y) / max(1, h - ground_y - 1)
        if t < 0.25:   c = PAL['g']
        elif t < 0.6:  c = PAL['G']
        else:          c = PAL['h']
        for x in range(w):
            fb.set(x, y, c)

    # Tile seams every 12 px in world coords (scroll with camera)
    seam0 = -(cx % 12)
    for sx in range(seam0, w, 12):
        for yy in range(ground_y, ground_y + 2):
            fb.set(sx, yy, PAL['h'])

    # Grain highlights every 3 px in world coords
    grain0 = -(cx % 3)
    for sx in range(grain0, w, 3):
        fb.set(sx, ground_y, PAL['q'])

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
    """A red ghoul. Moves at vx px/sec (signed -> direction). Reverses
    direction when it would step into a crate or fall into a gap."""
    __slots__ = ("x", "y", "vx", "hp", "alive", "anim_t")
    def __init__(self, x, y, vx):
        self.x = x
        self.y = y
        self.vx = vx        # px/sec; negative = walking left, positive = right
        self.hp = 1
        self.alive = True
        self.anim_t = 0.0


class World:
    # NERD sprite is 18x20 but the visible body is roughly cols 2-14, rows 4-19.
    # Use a tighter bbox for collisions so hair etc. doesn't trigger them.
    PB_X, PB_Y, PB_W, PB_H = 2, 4, 12, 16

    def __init__(self, w, h):
        # fb_w/fb_h = current visible framebuffer dims. world_w = total level width.
        self.fb_w = w
        self.fb_h = h
        self.w = w           # alias kept for legacy callers
        self.h = h
        self.world_w = max(w * WORLD_SCREENS, w + 200)
        self.camera_x = 0.0
        self.ground_y = int(h * 0.78)
        self.player_x = PLAYER_MIN_X + 4
        self.player_y = self.ground_y - len(NERD)
        self.player_vy = 0.0
        self.player_grounded = True
        self.player_face_right = True
        # held-key bridge: extend the effect of each LEFT/RIGHT key event for
        # MOVE_HOLD_S so we keep walking through the OS auto-repeat pre-delay.
        self.hold_left_until = 0.0
        self.hold_right_until = 0.0
        # Last-press timestamps -- used at jump time to capture the player's
        # intended direction even if the hold window has already expired
        # during the OS auto-repeat pre-delay.
        self.last_press_left = 0.0
        self.last_press_right = 0.0
        # Direction to drift while airborne: -1 left, 0 none, +1 right.
        # Set at jump time, cleared when grounded.
        self.air_dir = 0
        self.pellets = []
        self.enemies = []
        # obstacles (crate stacks) in world coords: list of (x, y, w, h)
        self.obstacles = []
        # gaps in the floor: list of (x_start, x_end). Inside one of these the
        # ground is missing -- player falls through.
        self.gaps = []
        # collectibles. Each is [x, y, collected].
        self.floppies = []
        # power-ups. Each is [kind, x, y, collected].
        self.powerups = []
        # last x where player was safely grounded (NOT above a gap). Respawn
        # point after falling into a pit.
        self.last_safe_x = float(PLAYER_MIN_X + 4)
        # rapid-fire timer: fire-rate is doubled while time.time() < rapid_until
        self.rapid_until = 0.0
        # disks collected count (total floppies set by _gen_level)
        self.disks = 0
        self.disks_total = 0
        self.kills = 0
        self.lives = PLAYER_LIVES
        self.spawned = 0
        self.next_spawn = time.time() + random.uniform(ENEMY_SPAWN_MIN, ENEMY_SPAWN_MAX)
        self.last_shot = 0.0
        self.last_hit = 0.0
        self.state = "playing"
        self.message = random.choice(KILL_TAUNTS)
        self.message_until = 0.0
        self.end_message = ""
        self._gen_level()

    def _gen_level(self):
        """Place crate stacks, gaps in the floor, floppy pickups, a single
        RAPID powerup, and wall torches across the world. Deterministic per
        (world_w, ground_y)."""
        self.obstacles = []
        self.torches = []
        self.gaps = []
        self.floppies = []
        self.powerups = []
        rng = random.Random(0xC0FFEE ^ self.world_w ^ self.ground_y)
        crate_w, crate_h = len(CRATE[0]), len(CRATE)  # 10 x 9
        # Walk left-to-right placing one feature per step. Maintain a buffer of
        # "safe ground" before and after each gap so the player has takeoff +
        # landing room. Floppies sit either on the ground or on top of crates.
        x = self.fb_w + rng.randint(20, 50)
        while x < self.world_w - 60:
            kind = rng.choices(
                ["crate", "gap", "floppy_run", "ground"],
                weights=[4, 3, 3, 1],
                k=1,
            )[0]
            if kind == "crate":
                stack = rng.choice([1, 1, 1, 1, 2, 2])
                ow, oh = crate_w, stack * crate_h
                self.obstacles.append((x, self.ground_y - oh, ow, oh))
                # 50% chance: floppy on top of the crate
                if rng.random() < 0.5:
                    fy = self.ground_y - oh - 7  # 6-px-tall floppy + 1 lift
                    self.floppies.append([x + ow // 2 - 2, fy, False])
                x += ow + rng.randint(20, 44)
            elif kind == "gap":
                # 14-22 px gap. Max jump arc covers ~56 horizontal so this is
                # always crossable.
                gw = rng.randint(14, 22)
                self.gaps.append((x, x + gw))
                # Sometimes float a floppy mid-gap as a reward for jumping.
                if rng.random() < 0.55:
                    self.floppies.append([x + gw // 2 - 2, self.ground_y - 14, False])
                x += gw + rng.randint(20, 36)
            elif kind == "floppy_run":
                # Two or three floppies on the ground in a short row.
                n = rng.choice([2, 3])
                for i in range(n):
                    self.floppies.append([x + i * 9, self.ground_y - 8, False])
                x += n * 9 + rng.randint(16, 30)
            else:  # plain ground
                x += rng.randint(20, 40)
        # If RNG produced zero gaps (unlucky on a small world), force one near
        # the middle so the player sees the pit-jumping mechanic at all.
        if not self.gaps:
            mid = self.world_w // 2
            self.gaps.append((mid - 8, mid + 8))
        # One RAPID powerup at the level's midpoint, lifted off the ground a
        # bit so it visually reads as a pickup instead of debris.
        rx = self.world_w // 2
        # Don't place on top of an obstacle or in a gap -- shift if needed.
        rx = self._nearest_safe_x(rx)
        self.powerups.append(["RAPID", rx, self.ground_y - 12, False])
        # Torches every ~70-110 px on the back wall.
        tx = 30
        while tx < self.world_w - 10:
            self.torches.append(tx)
            tx += rng.randint(60, 110)
        self.disks_total = len(self.floppies)

    def _nearest_safe_x(self, x):
        """Nudge x sideways until it's not over a gap or inside an obstacle."""
        for _ in range(40):
            if any(gx <= x <= gx2 for gx, gx2 in self.gaps):
                x += 8
                continue
            if any(ox <= x < ox + ow for ox, _, ow, _ in self.obstacles):
                x += 12
                continue
            return x
        return x

    def _in_gap(self, x):
        for gx, gx2 in self.gaps:
            if gx <= x <= gx2:
                return True
        return False

    def player_max_x(self):
        # Player can walk to the right edge of the WORLD, not the screen.
        return self.world_w - 18  # 18 = NERD sprite width

    def grounded_y(self):
        # y position of player_y when standing on the floor at the current x.
        return self._floor_top_at(self.player_x) - len(NERD)

    def _player_bbox(self, x=None, y=None):
        if x is None: x = self.player_x
        if y is None: y = self.player_y
        return (x + self.PB_X, y + self.PB_Y, self.PB_W, self.PB_H)

    def _aabb_overlap(self, ax, ay, aw, ah, bx, by, bw, bh):
        return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by

    def _hits_any_obstacle(self, x, y):
        ax, ay, aw, ah = self._player_bbox(x, y)
        for ox, oy, ow, oh in self.obstacles:
            if self._aabb_overlap(ax, ay, aw, ah, ox, oy, ow, oh):
                return (ox, oy, ow, oh)
        return None

    def _floor_top_at(self, x):
        """y of the highest surface (smallest y) directly under the player at
        x. Returns a value below the screen if the player's center of mass
        is over a gap with no obstacle to stand on."""
        ax = x + self.PB_X
        aw = self.PB_W
        left, right = ax, ax + aw
        center = ax + aw // 2
        # Obstacles always provide a surface (they sit in/above gaps too)
        floor = None
        for ox, oy, ow, oh in self.obstacles:
            if left < ox + ow and right > ox:
                if floor is None or oy < floor:
                    floor = oy
        if floor is not None:
            return floor
        # No obstacle under us. Check whether the player's center is over a
        # gap; if so, gravity wins. (Center-based, not whole-bbox, so the
        # player drops after running off the lip rather than skating along it.)
        for gx, gx2 in self.gaps:
            if gx <= center <= gx2:
                return self.fb_h + 999
        return self.ground_y

    def update_layout(self, w, h, ground_y):
        old_ground = self.ground_y
        self.fb_w, self.fb_h, self.ground_y = w, h, ground_y
        self.w, self.h = w, h
        # If the terminal resized, rebuild the world width and re-place obstacles
        # so they sit on the new ground_y.
        new_world_w = max(w * WORLD_SCREENS, w + 200)
        if new_world_w != self.world_w or ground_y != old_ground:
            self.world_w = new_world_w
            self._gen_level()
        self.player_x = max(PLAYER_MIN_X, min(self.player_x, self.player_max_x()))
        if self.player_grounded:
            self.player_y = self.grounded_y()
        # clamp camera to world
        self.camera_x = max(0.0, min(float(self.world_w - self.fb_w), self.camera_x))

    def shoot(self):
        now = time.time()
        cooldown = PELLET_COOLDOWN * 0.5 if now < self.rapid_until else PELLET_COOLDOWN
        if now - self.last_shot < cooldown:
            return
        self.last_shot = now
        # pellet emerges from slingshot tip ~ right side of nerd, mid-height
        sling_x = self.player_x + (12 if self.player_face_right else 1)
        sling_y = self.player_y + 12
        # vx is in px/sec; pellet update integrates with dt
        vx = PELLET_PX_PER_SEC if self.player_face_right else -PELLET_PX_PER_SEC
        self.pellets.append(Pellet(sling_x, sling_y, vx))
        play("shoot")

    def _respawn(self):
        """Send the player back to the last safe ground tile, take a life."""
        self.lives -= 1
        if self.lives <= 0:
            self.state = "lose"
            self.end_message = random.choice(DEATH_QUOTES)
            play("lose")
            return
        self.player_x = self.last_safe_x
        self.player_y = self._floor_top_at(self.last_safe_x) - len(NERD)
        self.player_vy = 0.0
        self.player_grounded = True
        self.air_dir = 0
        self.last_hit = time.time()       # i-frames after respawn
        self.message = "You fell in. Try harder."
        self.message_until = time.time() + 1.4
        play("hit")

    def _check_pickups(self):
        """Floppy disks + powerups: claim any whose bbox intersects the
        player. Floppies are 5x6, powerups 5x5 (sprite shapes); use simple
        rectangle overlap with the player's collision bbox."""
        ax, ay, aw, ah = self._player_bbox()
        for f in self.floppies:
            if f[2]:
                continue
            fx, fy = f[0], f[1]
            if (fx < ax + aw and fx + 5 > ax and fy < ay + ah and fy + 6 > ay):
                f[2] = True
                self.disks += 1
                play("kill")  # reuse the bling sound; no new asset
        for p in self.powerups:
            if p[3]:
                continue
            px, py = p[1], p[2]
            if (px < ax + aw and px + 5 > ax and py < ay + ah and py + 5 > ay):
                p[3] = True
                if p[0] == "RAPID":
                    self.rapid_until = time.time() + 8.0
                    self.message = "*RAPID FIRE*  ROOT++"
                    self.message_until = time.time() + 1.6
                    play("kill")

    def tick(self, dt, keys):
        if self.state != "playing":
            return

        # input. LEFT/RIGHT also extend a "hold window" so movement is
        # continuous while held, even during the OS auto-repeat pre-delay.
        now_t = time.time()

        def _carry_walk_through_action():
            """In cbreak mode macOS only auto-repeats the most recently
            held key. While the player mashes X to shoot or holds SPACE
            to jump, the LEFT/RIGHT auto-repeat is suspended and the hold
            window would expire. Keep the walk going as long as the
            player IS currently walking (hold not yet expired) -- and
            also bump last_press_* so the next action key in the chain
            still finds 'currently walking' true."""
            if now_t < self.hold_right_until:
                self.hold_right_until = now_t + MOVE_HOLD_S
                self.last_press_right = now_t
            elif now_t < self.hold_left_until:
                self.hold_left_until = now_t + MOVE_HOLD_S
                self.last_press_left = now_t

        for k in keys:
            if k == "LEFT":
                self.hold_left_until = now_t + MOVE_HOLD_S
                self.last_press_left = now_t
                self.player_face_right = False
            elif k == "RIGHT":
                self.hold_right_until = now_t + MOVE_HOLD_S
                self.last_press_right = now_t
                self.player_face_right = True
            elif k == " ":
                # SPACE = jump (NES/SNES platformer convention).
                # UP is reserved for future ladder/vertical movement.
                if self.player_grounded:
                    self.player_vy = -JUMP_V0_PX_PER_SEC  # px/sec, integrated below
                    self.player_grounded = False
                    # Capture jump direction from recent press history. The OS
                    # auto-repeat pre-delay (~250-500ms) can leave a gap where
                    # hold_*_until is expired even though the user is still
                    # holding the key. Looking at last_press_* lets us preserve
                    # the player's intent through that gap.
                    JUMP_INTENT_WINDOW = 0.6
                    left_recent  = (now_t - self.last_press_left)  < JUMP_INTENT_WINDOW
                    right_recent = (now_t - self.last_press_right) < JUMP_INTENT_WINDOW
                    if left_recent and not right_recent:
                        self.air_dir = -1
                    elif right_recent and not left_recent:
                        self.air_dir = +1
                    else:
                        self.air_dir = 0
                _carry_walk_through_action()
            elif k in ("x", "X"):
                # X = shoot (Mega Man / NES B-button convention).
                self.shoot()
                _carry_walk_through_action()

        # ---- horizontal motion with obstacle collision ----
        step = PLAYER_PX_PER_SEC * dt
        dx = 0.0
        if now_t < self.hold_left_until:
            dx -= step
        if now_t < self.hold_right_until:
            dx += step
        # In-air persistence: if no direction is currently within the hold
        # window but we captured air_dir at jump time, keep drifting that way
        # (this is what lets you actually move sideways while jumping when
        # the OS auto-repeat hasn't caught up yet).
        if dx == 0.0 and not self.player_grounded and self.air_dir != 0:
            dx = self.air_dir * step
        if dx != 0.0:
            new_x = max(PLAYER_MIN_X, min(self.player_max_x(), self.player_x + dx))
            hit = self._hits_any_obstacle(new_x, self.player_y)
            if hit:
                ox, oy, ow, oh = hit
                # Snap to the side we approached from.
                if dx > 0:
                    new_x = ox - self.PB_X - self.PB_W  # left edge of obstacle
                else:
                    new_x = ox + ow - self.PB_X         # right edge of obstacle
                new_x = max(PLAYER_MIN_X, min(self.player_max_x(), new_x))
            self.player_x = new_x

        # ---- vertical motion (jump / fall / land on platforms) ----
        if not self.player_grounded:
            # vy is in px/sec; gravity in px/sec^2. Both integrated with dt
            # so physics is identical at any FPS.
            self.player_vy += GRAVITY_PX_PER_SEC2 * dt
            new_y = self.player_y + self.player_vy * dt
            if self.player_vy > 0:  # falling: try to land on the highest surface
                old_feet = self.player_y + len(NERD)
                new_feet = new_y + len(NERD)
                floor = self._floor_top_at(self.player_x)
                # Allow crossing several pixels of floor in a single frame so
                # we still catch the surface when vy*dt is large.
                if new_feet >= floor and old_feet <= floor + 4:
                    self.player_y = floor - len(NERD)
                    self.player_vy = 0.0
                    self.player_grounded = True
                    self.air_dir = 0
                else:
                    self.player_y = new_y
            else:
                self.player_y = new_y
        else:
            # Walked off an edge? If so, switch to falling.
            if self.player_y < self._floor_top_at(self.player_x) - len(NERD):
                self.player_grounded = False

        # ---- safe-ground tracking + fall death ----
        # last_safe_x is the respawn anchor: player is grounded AND their
        # center is not over a gap. We won't respawn onto a crate (we land
        # on the natural ground at that x).
        if self.player_grounded:
            center_x = self.player_x + self.PB_X + self.PB_W // 2
            if not self._in_gap(center_x):
                self.last_safe_x = self.player_x
        # Fell off the world?
        if self.player_y > self.fb_h + 10:
            self._respawn()
            if self.state != "playing":
                return  # game over

        # ---- pickups (floppies + powerups) ----
        self._check_pickups()

        # ---- camera follow (deadzone) ----
        screen_x = self.player_x - self.camera_x
        if screen_x > self.fb_w * CAMERA_DEAD_HI:
            self.camera_x = self.player_x - self.fb_w * CAMERA_DEAD_HI
        elif screen_x < self.fb_w * CAMERA_DEAD_LO:
            self.camera_x = self.player_x - self.fb_w * CAMERA_DEAD_LO
        self.camera_x = max(0.0, min(float(self.world_w - self.fb_w), self.camera_x))

        # ---- spawn enemies, half from in front (right of camera), half from
        # behind (left of camera). Each enemy walks toward the player. ----
        now = time.time()
        if (self.spawned < TOTAL_ENEMIES
                and now >= self.next_spawn
                and len([e for e in self.enemies if e.alive]) < 4):
            speed_mag = random.uniform(ENEMY_PX_PER_SEC_MIN, ENEMY_PX_PER_SEC_MAX)
            from_behind = random.random() < 0.5
            if from_behind and self.camera_x > 24:
                # spawn left of the camera, walking RIGHT toward the player
                ex = self.camera_x - 16
                vx = speed_mag
            else:
                # spawn right of the camera, walking LEFT toward the player
                ex = self.camera_x + self.fb_w + 4
                vx = -speed_mag
            ey = self.ground_y - len(ENEMY_A)
            self.enemies.append(Enemy(ex, ey, vx))
            self.spawned += 1
            self.next_spawn = now + random.uniform(ENEMY_SPAWN_MIN, ENEMY_SPAWN_MAX)

        # ---- pellets (despawn when off-camera or absorbed by an obstacle) ----
        for p in self.pellets:
            if not p.alive:
                continue
            p.x += p.vx * dt
            # crate absorption
            for ox, oy, ow, oh in self.obstacles:
                if ox <= p.x <= ox + ow and oy <= p.y <= oy + oh:
                    p.alive = False
                    break
            if p.alive and (p.x < self.camera_x - 8
                            or p.x > self.camera_x + self.fb_w + 8):
                p.alive = False
        self.pellets = [p for p in self.pellets if p.alive]

        # ---- enemies. They block on crates and pits; they reverse direction
        # when they would step into one. ----
        ENEMY_W = len(ENEMY_A[0])  # 12
        ENEMY_H = len(ENEMY_A)     # 16
        for e in self.enemies:
            if not e.alive:
                continue
            new_x = e.x + e.vx * dt
            # Would the next position put the ghoul inside an obstacle?
            blocked = False
            for ox, oy, ow, oh in self.obstacles:
                # Use a tight bbox around the ghoul for the collision check
                if (new_x + 1 < ox + ow and new_x + ENEMY_W - 1 > ox
                        and e.y + 1 < oy + oh and e.y + ENEMY_H > oy):
                    blocked = True
                    break
            # About to walk off into a gap? Sample the ground at the leading
            # foot's x position. Also reverse at world boundaries so they
            # don't escape sideways.
            if not blocked:
                lead_x = new_x + (ENEMY_W if e.vx > 0 else 0)
                if self._in_gap(int(lead_x)):
                    blocked = True
                elif lead_x <= -8 or lead_x >= self.world_w + 8:
                    blocked = True
            if blocked:
                e.vx = -e.vx       # turn around, keep shambling
            else:
                e.x = new_x
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
            # touch player (full bbox so jumping clears them)
            if e.alive:
                pleft   = self.player_x + 2
                pright  = self.player_x + 12
                ptop    = self.player_y + 4    # head/torso, not whole sprite
                pbot    = self.player_y + len(NERD)
                etop    = e.y + 1
                ebot    = e.y + len(ENEMY_A)
                if (e.x < pright and e.x + 12 > pleft
                        and etop < pbot and ebot > ptop):
                    if time.time() - self.last_hit > HIT_COOLDOWN:
                        self.lives -= 1
                        self.last_hit = time.time()
                        play("hit")
                        e.alive = False
                        self.message = "You took a hit!"
                        self.message_until = time.time() + 1.0
        # despawn enemies that walked far off the left side of the camera
        cull_x = self.camera_x - 32
        self.enemies = [e for e in self.enemies if e.alive or e.x > cull_x]

        # win/lose. To win you must kill all the ghouls AND walk to the very
        # right edge of the level -- the certificate is at the end.
        if self.lives <= 0:
            self.state = "lose"
            self.end_message = random.choice(DEATH_QUOTES)
            play("lose")
        elif self.kills >= TOTAL_ENEMIES and self.player_x >= self.world_w - 30:
            self.state = "win"
            self.end_message = random.choice(WIN_QUOTES)
            play("win")


# ===================================================================
# Render
# ===================================================================
def render_world(fb, world):
    fb.clear(PAL['k'])
    ground_y, wall_top, wall_bot = draw_background(fb, world.camera_x)
    if ground_y != world.ground_y:
        world.update_layout(fb.w, fb.h, ground_y)

    cx = int(world.camera_x)

    # Carve gaps: replace the ground bands with a void column (slight gradient
    # so it reads as depth rather than a flat black box).
    for gx, gx2 in world.gaps:
        sx0 = max(0, gx - cx)
        sx1 = min(fb.w, gx2 - cx)
        if sx1 <= sx0:
            continue
        for yy in range(ground_y, fb.h):
            t = (yy - ground_y) / max(1, fb.h - ground_y - 1)
            # Top of pit slightly lighter than the deep
            r = int(PAL['A'][0] + (PAL['a'][0] - PAL['A'][0]) * t)
            g = int(PAL['A'][1] + (PAL['a'][1] - PAL['A'][1]) * t)
            b = int(PAL['A'][2] + (PAL['a'][2] - PAL['A'][2]) * t)
            for x in range(sx0, sx1):
                fb.set(x, yy, (r, g, b))
        # Soft lip on each side of the pit (1-px brick-edge highlight)
        if sx0 - 1 >= 0:
            fb.set(sx0 - 1, ground_y, PAL['h'])
        if sx1 < fb.w:
            fb.set(sx1, ground_y, PAL['h'])

    # Torches sprinkled along the back wall, in world coords.
    torch_h = len(TORCH)
    torch_w = len(TORCH[0])
    for tx_w in world.torches:
        sx = tx_w - cx
        if -torch_w <= sx < fb.w + torch_w:
            ty = wall_top + 4
            # glow halo (drawn before torch so it sits behind)
            for d in range(5):
                rr = 6 - d
                for dx in range(-rr, rr + 1):
                    fb.set(sx + 2 + dx, ty + d - 2, PAL['j'])
            fb.blit_sprite(TORCH, sx, ty)

    # Obstacles: stack of CRATE sprites
    crate_h = len(CRATE)
    crate_w = len(CRATE[0])
    for ox, oy, ow, oh in world.obstacles:
        sx = ox - cx
        if -ow <= sx < fb.w + ow:
            n = oh // crate_h
            for i in range(n):
                fb.blit_sprite(CRATE, sx, oy + i * crate_h)

    # Floppy disks (collectibles)
    for f in world.floppies:
        if f[2]:
            continue
        sx = f[0] - cx
        if -6 <= sx < fb.w + 6:
            fb.blit_sprite(FLOPPY, sx, f[1])

    # Powerups (with a slight pulse so they catch the eye)
    pulse = (time.time() * 4) % 2
    for p in world.powerups:
        if p[3]:
            continue
        sx = p[1] - cx
        if -6 <= sx < fb.w + 6:
            # bob up/down 1 px
            bob = 0 if pulse < 1 else 1
            fb.blit_sprite(POWERUP_RAPID, sx, p[2] - bob)

    # Enemies (back to front by world x). Cull off-screen.
    for e in sorted(world.enemies, key=lambda e: -e.x):
        if not e.alive:
            continue
        sx = int(e.x) - cx
        if -16 <= sx < fb.w + 16:
            frame = ENEMY_A if int(e.anim_t * 6) % 2 == 0 else ENEMY_B
            # Sprites are drawn facing right by default; flip when walking left
            fb.blit_sprite(frame, sx, int(e.y), flip=(e.vx < 0))

    # Player
    psx = int(world.player_x) - cx
    fb.blit_sprite(NERD, psx, int(world.player_y),
                   flip=not world.player_face_right)

    # Pellets: 2x2 ball + glow + trailing streak
    for p in world.pellets:
        if not p.alive:
            continue
        spx = int(p.x) - cx
        py = int(p.y)
        # streak (4 px tail) opposite to direction
        sign = -1 if p.vx > 0 else 1
        for d in range(1, 5):
            fb.set(spx + sign * d, py, PAL['o'])
        # glow halo
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            fb.set(spx + dx, py + dy, PAL['o'])
        # core ball (2x2)
        fb.fill_rect(spx, py, 2, 2, PAL['O'])

    # HUD bar (top). Stat is left-aligned, RAPID timer right-aligned, so the
    # two don't fight over the center of the bar.
    hud_h = 9
    fb.fill_rect(0, 0, fb.w, hud_h, (10, 16, 12))
    now = time.time()
    rapid_left = max(0.0, world.rapid_until - now)
    rapid_text = f"RAPID {rapid_left:.0f}S" if rapid_left > 0 else ""
    rapid_w = (len(rapid_text) * 6 - 1) if rapid_text else 0
    # Reserve room on the right for RAPID + 4 px gap.
    budget = fb.w - 4 - (rapid_w + 4 if rapid_text else 0)

    full = (f"KILLS {world.kills}/{TOTAL_ENEMIES}  "
            f"DISKS {world.disks}/{world.disks_total}  "
            f"LIVES {world.lives}")
    med  = (f"K {world.kills}/{TOTAL_ENEMIES}  "
            f"D {world.disks}/{world.disks_total}  "
            f"L {world.lives}")
    short = (f"K{world.kills}/{TOTAL_ENEMIES} "
             f"D{world.disks}/{world.disks_total} "
             f"L{world.lives}")
    if budget >= len(full) * 6 - 1:
        stat = full
    elif budget >= len(med) * 6 - 1:
        stat = med
    else:
        stat = short
    fb.blit_text(stat, 2, 1, PAL['C'])
    if rapid_text:
        fb.blit_text(rapid_text, fb.w - rapid_w - 2, 1, PAL['Y'])

    # taunt centered if active
    if now < world.message_until:
        msg = world.message.upper()
        msg_w = len(msg) * 6 - 1
        fb.blit_text(msg, max(2, (fb.w - msg_w) // 2), fb.h - 9, PAL['Y'])

    # end-state overlay
    if world.state in ("win", "lose"):
        if world.state == "win":
            _draw_certificate(fb, world)
        else:
            _draw_lose(fb, world)


def _draw_lose(fb, world):
    text = "YOU LOSE."
    col = PAL['D']
    bigw = len(text) * 12 - 1
    x0 = max(2, (fb.w - bigw) // 2)
    y0 = (fb.h // 2) - 8
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


def _draw_certificate(fb, world):
    """The goofy nerd-prize end screen: a fake terminal certificate.
    Uses only FONT-supported chars (A-Z, 0-9, ! ' + , - . / : = ?).
    Three size tiers, each verified to fit in its target framebuffer."""
    border = (90, 200, 110)        # phosphor green
    text_col = (210, 220, 230)
    accent = PAL['Y']
    k, t = world.kills, TOTAL_ENEMIES
    d, dt = world.disks, world.disks_total
    L = world.lives

    if fb.w >= 160 and fb.h >= 100:
        # Full: 7 lines, max 137 px wide, fits 160x100+
        lines = [
            ("= SUDO RM -RF 'EM ALL =",       border),
            ("STATUS:    VANQUISHED",         text_col),
            (f"GHOULS:    {k} / {t}",         text_col),
            (f"DISKS:     {d} / {dt}",        text_col),
            (f"LIVES:     {L}",               text_col),
            ("LICENSE:   WTFPL",              text_col),
            (".ANXIETY DELETED.",             accent),
        ]
    elif fb.w >= 110 and fb.h >= 72:
        # Compact: 5 lines, max 101 px wide, fits 110x72+
        lines = [
            ("ROOT GRANTED!",                  border),
            (f"GHOULS:  {k}/{t}",              text_col),
            (f"DISKS:   {d}/{dt}",             text_col),
            (f"LIVES:   {L}",                  text_col),
            (".ANXIETY DELETED.",              accent),
        ]
    elif fb.h >= 60:
        # Tiny: 3 lines, fits 80x60+
        lines = [
            ("ROOT WIN!",                              border),
            (f"K {k}/{t} D {d}/{dt}",                  text_col),
            (".ANXIETY DEAD.",                         accent),
        ]
    else:
        # Sub-tiny: 2 lines, no plate — for fb.h < 60
        lines = [
            ("ROOT WIN!",                              border),
            (f"K {k}/{t} D {d}/{dt}",                  accent),
        ]

    line_h = 9
    pad = 4
    text_w = max(len(l) for l, _ in lines) * 6 - 1
    text_h = len(lines) * line_h

    # If we don't have room for a plate + prompt, render plate-less so the
    # text doesn't collide with the Q TO QUIT line.
    use_plate = fb.h >= text_h + pad * 2 + 12 + 11

    if use_plate:
        plate_w = text_w + pad * 2
        plate_h = text_h + pad * 2
        plate_x = max(2, (fb.w - plate_w) // 2)
        plate_y = max(11, (9 + (fb.h - 9 - plate_h - 12)) // 2)
        plate_w = min(plate_w, fb.w - plate_x - 2)
        plate_h = min(plate_h, fb.h - plate_y - 12)

        fb.fill_rect(plate_x, plate_y, plate_w, plate_h, (8, 10, 16))
        for x in range(plate_x, plate_x + plate_w):
            fb.set(x, plate_y, border)
            fb.set(x, plate_y + plate_h - 1, border)
        for y in range(plate_y, plate_y + plate_h):
            fb.set(plate_x, y, border)
            fb.set(plate_x + plate_w - 1, y, border)
        x0 = plate_x + pad
        y0 = plate_y + pad
        for i, (line, color) in enumerate(lines):
            fb.blit_text(line, x0, y0 + i * line_h, color)
        prompt_y = plate_y + plate_h + 3
    else:
        # No plate: just stack text vertically, leaving room for prompt.
        y0 = max(11, (fb.h - text_h - 11) // 2)
        for i, (line, color) in enumerate(lines):
            x0 = max(2, (fb.w - len(line) * 6 + 1) // 2)
            fb.blit_text(line, x0, y0 + i * line_h, color)
        prompt_y = y0 + text_h + 2

    prompt = "Q TO QUIT"
    pw = len(prompt) * 6 - 1
    fb.blit_text(prompt, max(2, (fb.w - pw) // 2),
                 min(fb.h - 9, prompt_y), border)


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

    # If the read ended mid-escape-sequence, give the OS a tiny window to
    # deliver the rest. 5 ms is plenty -- the entire CSI is normally one
    # write -- and keeps input latency low at 60 fps.
    if data.endswith(b"\x1b"):
        r, _, _ = select.select([fd], [], [], 0.005)
        if r:
            try:
                data += os.read(fd, 8)
            except (BlockingIOError, OSError):
                pass
    if len(data) >= 2 and data[-2] == 0x1b and data[-1] in (0x5b, 0x4f):
        r, _, _ = select.select([fd], [], [], 0.005)
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
# Splash screen (pixel-art via framebuffer)
# ===================================================================

# 14x10 pixel skull (used as splash icon).  Reuses palette: r=red dark,
# d=red mid, D=red light, V=yellow eye, m=mouth interior, F=glasses frame.
SKULL = [
    "....DDDDDD....",
    "..DddddddddD..",
    ".DddddddddddD.",
    ".dEEdddddEEdd.",
    ".dEEdddddEEdd.",
    ".ddmmdmmddddd.",
    ".dDDDmmmmDDDd.",
    "..DddmmmmddD..",
    "...DDDDDDDD...",
    "....DDDDDD....",
]

# 7-row fire gradients (red->yellow and yellow->green) for the title halves.
FIRE_TOP = [
    (255,  60,  60),
    (255, 100,  60),
    (255, 150,  60),
    (255, 200,  60),
    (255, 240,  80),
    (255, 240, 100),
    (240, 255, 100),
]
FIRE_BOT = [
    (240, 255, 100),
    (200, 255, 100),
    (170, 255, 130),
    (140, 255, 160),
    (110, 255, 180),
    ( 90, 255, 200),
    ( 80, 255, 220),
]


def render_splash(fb, blink_on):
    """Draw the 8-bit splash into the framebuffer."""
    # background gradient (deep navy at top, slightly lighter at bottom)
    for y in range(fb.h):
        t = y / max(1, fb.h - 1)
        c = lerp_rgb((10, 12, 22), (24, 26, 50), t)
        for x in range(fb.w):
            fb.set(x, y, c)

    # sparse stars
    rng = random.Random(7)
    for _ in range(max(10, fb.w // 6)):
        sx = rng.randrange(fb.w)
        sy = rng.randrange(fb.h)
        # avoid the title band so we don't flicker stars inside letters
        fb.set(sx, sy, (190, 190, 220))

    # ---- Title ----
    # If room, render at scale 2; otherwise scale 1.
    scale = 2 if fb.w >= 100 else 1

    line1, line2 = "RM -RF", "EM ALL"
    w1 = Framebuffer.text_width(line1, scale=scale, spacing=1)
    w2 = Framebuffer.text_width(line2, scale=scale, spacing=1)

    # pick tagline that fits
    for cand in ("PIXEL SLINGSHOT ARCADE", "NERD WITH A SLINGSHOT",
                 "8-BIT NERD MODE", "NERD MODE", ""):
        tw = Framebuffer.text_width(cand, scale=1, spacing=1) if cand else 0
        if cand == "" or tw <= fb.w - 4:
            tag = cand
            break

    # pick prompt that fits
    for cand in ("PRESS ENTER  -  Q TO QUIT", "ENTER OR Q"):
        pw = Framebuffer.text_width(cand, scale=1, spacing=1)
        if pw <= fb.w - 4:
            prompt = cand
            break
    else:
        prompt, pw = "ENTER OR Q", Framebuffer.text_width("ENTER OR Q", 1, 1)

    # vertical layout pieces
    line_h = 7 * scale
    tag_h = 7 if tag else 0
    prompt_h = 7
    gap = 3 if scale == 2 else 2

    # skull only if scale 2 AND there's vertical room for it
    base_h = line_h + gap + line_h + gap + tag_h + (gap if tag else 0) + prompt_h
    show_skull = scale == 2 and fb.h - base_h >= 10 + gap + 2
    skull_h = 10 if show_skull else 0

    total_h = (skull_h + gap if skull_h else 0) + base_h
    top = max(1, (fb.h - total_h) // 2)

    y = top
    if show_skull:
        sx = (fb.w - 14) // 2
        fb.blit_sprite(SKULL, sx, y)
        y += skull_h + gap

    # title line 1 with fire-top gradient
    fb.blit_text_scaled(line1, (fb.w - w1) // 2, y, scale, None,
                        spacing=1, row_colors=FIRE_TOP)
    y += line_h + gap

    # title line 2 with fire-bottom gradient
    fb.blit_text_scaled(line2, (fb.w - w2) // 2, y, scale, None,
                        spacing=1, row_colors=FIRE_BOT)
    y += line_h + gap

    # tagline (single short line) — may be empty if nothing fits
    if tag:
        tw = Framebuffer.text_width(tag, scale=1, spacing=1)
        fb.blit_text(tag, max(2, (fb.w - tw) // 2), y, (220, 220, 230))
        y += tag_h + gap

    # blinking prompt
    if blink_on:
        fb.blit_text(prompt, max(2, (fb.w - pw) // 2), y, (255, 230, 80))

    # CRT scanlines: dim every other row
    for sy in range(1, fb.h, 2):
        for x in range(fb.w):
            r, g, b = fb.px[sy * fb.w + x]
            fb.px[sy * fb.w + x] = (r * 4 // 5, g * 4 // 5, b * 4 // 5)

    # bezel border (1 pixel)
    border = (60, 60, 100)
    for x in range(fb.w):
        fb.set(x, 0, border)
        fb.set(x, fb.h - 1, border)
    for yy in range(fb.h):
        fb.set(0, yy, border)
        fb.set(fb.w - 1, yy, border)


def splash(cols, rows):
    """Run the pixel-art splash loop. Returns True for ENTER, False for Q."""
    fb_w, fb_h = cols, rows * 2
    fb = Framebuffer(fb_w, fb_h)
    blink_on = True
    last_blink = time.time()
    while True:
        now = time.time()
        if now - last_blink > 0.45:
            blink_on = not blink_on
            last_blink = now

        # re-create fb if terminal resized
        ncols, nrows = get_screen_size()
        if (ncols, nrows) != (cols, rows):
            cols, rows = ncols, nrows
            fb = Framebuffer(cols, rows * 2)
            sys.stdout.write(CLEAR)

        render_splash(fb, blink_on)
        sys.stdout.write(fb.render())
        sys.stdout.flush()

        for k in get_keys():
            if k in ("\r", "\n"):
                return True
            if k in ("q", "Q"):
                return False
        time.sleep(0.06)


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

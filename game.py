#!/usr/bin/env python3
"""RM -RF 'EM ALL -- 8-bit pixel-art side-scroller (pygame edition).

The original ran in a terminal with half-block ANSI truecolor pixels. This
version renders to a real pygame window so we get true keyboard state
(key-up + simultaneous keys), making the controls trivially correct on
macOS, Linux, and Windows. The pixel-art look is preserved: we render to
a small internal surface (160x80) and scale up with nearest-neighbor.
"""

import array
import math
import os
import random
import sys
import tempfile
import time
import wave

try:
    import pygame
except ImportError:
    sys.stderr.write(
        "rm-rf-em-all needs pygame. Install with:\n"
        "    pip3 install pygame\n"
    )
    sys.exit(1)

# ===================================================================
# Display tuning -- internal resolution preserved from the terminal
# version so all the existing sprite/level code Just Works. Scaled up
# nearest-neighbor in the window for crisp pixels.
# ===================================================================
INTERNAL_W         = 160
INTERNAL_H         = 80
WINDOW_SCALE       = 6
WINDOW_W           = INTERNAL_W * WINDOW_SCALE   # 960
WINDOW_H           = INTERNAL_H * WINDOW_SCALE   # 480
WINDOW_TITLE       = "RM -RF 'EM ALL"

# ===================================================================
# Gameplay tuning. All physics in real-time units (px/sec, px/sec^2)
# and integrated against dt, so frame rate doesn't change behavior.
# ===================================================================
TARGET_FPS             = 60
PLAYER_MIN_X           = 6
PLAYER_PX_PER_SEC      = 70.0
WORLD_SCREENS          = 3
CAMERA_DEAD_LO         = 0.35
CAMERA_DEAD_HI         = 0.55
PELLET_PX_PER_SEC      = 160.0
PELLET_COOLDOWN        = 0.18
JUMP_V0_PX_PER_SEC     = 150.0
GRAVITY_PX_PER_SEC2    = 460.0  # peak jump ~ 24 px, clears 2-stack crate (18 px)
ENEMY_PX_PER_SEC_MIN   = 14.0
ENEMY_PX_PER_SEC_MAX   = 28.0
ENEMY_SPAWN_MIN        = 0.9
ENEMY_SPAWN_MAX        = 2.0
TOTAL_ENEMIES          = 8
PLAYER_LIVES           = 3
HIT_COOLDOWN           = 0.8
FINAL_LEVEL            = 3   # win the game by clearing this level

# ===================================================================
# Level themes. Each entry overrides PAL keys to recolor the world
# without redrawing every sprite. Sprites that read 'r/d/D' (ghouls)
# get a tint applied at render time so they match the level palette.
# ===================================================================
LEVEL_THEMES = {
    1: {
        "name": "BRICK CORRIDOR",
        "pal": {},   # default palette
        "ghoul_tint": None,
        "boss_tint": (160, 90, 220, 255),  # purple boss in level 1
    },
    2: {
        "name": "SERVER ROOM",
        "pal": {
            'k': ( 5,  10,  18),
            'K': (10,  20,  35),
            'B': (20,  45,  72),
            'j': (28,  46,  64),     # wall mid (cool blue-grey)
            'J': (50,  78, 100),
            'u': (12,  24,  38),
            'g': (60,  78,  96),     # floor lit
            'G': (40,  60,  78),
            'h': (20,  35,  50),
            'q': (110, 160, 200),
            # 404-error ghouls: cyan
            'r': ( 25,  70, 110),
            'd': ( 60, 150, 220),
            'D': (110, 210, 255),
            'V': (255, 240, 100),
            'm': ( 10,  30,  50),
            'i': ( 16,  50,  80),
            # Server-rack obstacle: cool grey
            'c': (105, 115, 130),
            'C': ( 65,  75,  90),
            'X': ( 25,  35,  50),
        },
        "boss_tint": (90, 160, 255, 255),
    },
    3: {
        "name": "CUBICLE FARM",
        "pal": {
            # 'sky' band -- ceiling: pale grey tile
            'k': (110, 115, 120),
            'K': (130, 135, 140),
            'B': (150, 155, 160),
            # cubicle wall: warm beige fabric
            'j': (180, 165, 130),
            'J': (210, 195, 160),
            'u': (140, 125,  95),
            # carpet floor
            'g': (115, 110, 105),
            'G': ( 85,  80,  78),
            'h': ( 60,  55,  52),
            'q': (170, 160, 140),
            # KAREN palette (level 3): jeans + pink sweater + blonde hair.
            'r': ( 60,  75, 110),    # jeans navy
            'd': (210, 110, 165),    # sweater pink mid
            'D': (245, 175, 215),    # sweater pink light
            'V': (250, 220, 130),    # blonde hair
            'm': (180,  55,  80),    # red lipstick / frown
            'i': ( 50,  30,  60),    # dark shoes
            # Office chair: brighter mesh-red so it actually pops against
            # the beige cubicle walls.
            'c': (220,  80,  80),    # mesh fabric (bright red)
            'C': (160,  40,  45),    # mid red
            'X': ( 70,  15,  20),    # frame / wheels (deep maroon)
        },
        "boss_tint": (220, 220, 230, 255),     # boss is mostly grey/silver
    },
}
# Snapshot of the level-1 (i.e. defaults) PAL values so we can restore
# them when transitioning back to level 1 or applying a partial override.
_PAL_DEFAULTS = None

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

# Pancakes mode taunts. The ghouls are gone; what remains are little
# black-and-white memorial Frenchies you say hello to. In honor of
# Pancake Waffles -- a very good boy.
PANCAKES_TAUNTS = [
    "Pancakes loves you.",
    "A wag for the road.",
    "Pancake Waffles approves.",
    "Boop! Right on the snoot.",
    "He says hi, friend.",
    "Forever a good boy.",
    "All the head pats.",
    "Snuggle delivered.",
    "Bork: 'I love you.'",
    "He's so proud of you.",
    "Tail wags from beyond.",
    "You earned a slobbery kiss.",
    "Treat received with love.",
    "Belly rub achievement.",
    "Best buddy, always.",
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

# ===================================================================
# Audio: SFX are tiny procedural square-wave bleeps generated to WAV
# files in temp at first launch and loaded as pygame.mixer.Sound objects.
# Theme music uses generate_theme() (preserved from the terminal version).
# ===================================================================
SFX_SPECS = {
    # name -> (frequencies in Hz, duration_s, mode)
    "shoot": ((900.0, 1400.0), 0.05, "down"),
    "kill":  ((220.0, 80.0),   0.18, "noise"),
    "hit":   ((140.0, 70.0),   0.18, "down"),
    "win":   ((392.0, 523.0),  0.50, "arp"),
    "lose":  ((196.0, 110.0),  0.55, "down"),
    "miss":  ((1200.0, 1700.0), 0.04, "down"),
    # Looping drone propeller buzz: square-wave carrier with a gentle
    # 25 Hz amplitude modulation that reads as 'rotor blades passing'.
    "drone": ((230.0, 230.0),  0.50, "buzz"),
    # Pancakes: a happy two-yip bark instead of the regular kill blip.
    "bark":  ((700.0, 320.0),  0.32, "bark"),
}
_SFX_CACHE = {}        # name -> pygame.mixer.Sound
_MUSIC_PATH = None     # path to the theme WAV (set in init_audio)
_MIXER_OK = False      # True only after a successful pygame.mixer.init().
                       # On systems where pygame was built without SDL2_mixer
                       # (e.g. Linux + Python 3.14 source-built wheel), the
                       # whole pygame.mixer module is missing and any access
                       # raises NotImplementedError. All audio code paths
                       # check this flag first and silently no-op when False.

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

    # delivery drone
    '$': (180, 185, 200),        # drone body silver
    '!': ( 80,  90, 110),        # drone body shadow / window
    'Q': (235, 240, 250),        # rotor blade highlight

    # boss (overlord) -- only the HP-bar accent is needed since the
    # sprite itself is a runtime-tinted ENEMY surface (see _get_overlord_surface).
    'I': (220,  90, 200),        # phosphor pink (boss HP bar)

    # SSL cert (golden padlock that drops when the boss dies)
    'e': (255, 215,  60),        # gold body
    'v': (200, 165,  20),        # gold shadow
    'S': (255, 240, 160),        # bright gold highlight
    'U': (110,  80,  10),        # dark gold outline

    # Boss-specific colors. These keys aren't used by anything that gets
    # retinted by level themes, so each boss keeps its own look across
    # all three palettes.
    # JOBBA blob (level 1 boss): green-yellow slug
    '+': (190, 215, 100),        # jobba light
    '-': (140, 165,  70),        # jobba shadow
    '?': (230, 240, 150),        # jobba highlight
    # CABLE BUNDLE (level 2 boss): black wires with bristling RAM sticks
    '0': ( 25,  25,  30),        # cable black
    '1': ( 60,  60,  70),        # cable mid
    '2': ( 90, 220,  90),        # RAM stick green
    '3': (255, 110, 110),        # RAM stick red
    # OFFICE MANAGER (level 3 boss): dark suit, white shirt, red tie
    '4': ( 30,  30,  45),        # suit dark
    '5': (235, 235, 240),        # white shirt
    '6': (180,  40,  40),        # red tie
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

# Level-2 enemy: rogue CPU. Square chip body with a metallic brand
# stripe + pin pads. Same r/d/D/m/V palette so the level-2 cyan tint
# applies automatically.
CPU_A = [
    "............",
    "...rrrrrr...",
    "..rrrrrrrr..",
    "..rdddddddr.",
    "..rdDDDDDdr.",
    "..rdmmmmmdr.",
    "..rdDDDDDdr.",
    "..rddddddDr.",
    "..rrrrrrrrr.",
    "...........r",
    ".r.r.r.r.r..",
    "r..r..r..r..",
    ".rr..rr..rr.",
    ".rr..rr..rr.",
    ".rr..rr..rr.",
    ".ii..ii..ii.",
]
CPU_B = [
    "............",
    "...rrrrrr...",
    "..rrrrrrrr..",
    "..rdddddddr.",
    "..rdDDDDDdr.",
    "..rdmmmmmdr.",
    "..rdDDDDDdr.",
    "..rddddddDr.",
    "..rrrrrrrrr.",
    "r...........",
    "..r.r.r.r.r.",
    ".r..r..r..r.",
    "rr..rr..rr..",
    "rr..rr..rr..",
    "rr..rr..rr..",
    "ii..ii..ii..",
]

# Level-3 enemy: KAREN. Blonde bob, frowning face, sweater, jeans,
# loafers. Hair uses 'V' (eye-yellow palette key, gets overridden to a
# warm cream in the level-3 theme), face uses 's' (skin), body uses
# the r/d/D/m/i ghoul palette like the others.
KAREN_A = [
    "............",
    "...VVVVVV...",
    "..VVVVVVVV..",
    ".VVVssssVVV.",
    ".VsmsssmssV.",
    ".VsssssssV..",
    ".VssMMMMssV.",
    "..VsssssV...",
    "..ddddddd...",
    "..dDDDDDd...",
    "..dDDDDDd...",
    "..dDDDDDd...",
    "..rr..rr....",
    "..rr..rr....",
    "..rr..rr....",
    "..ii..ii....",
]
KAREN_B = [
    "............",
    "...VVVVVV...",
    "..VVVVVVVV..",
    ".VVVssssVVV.",
    ".VsmsssmssV.",
    ".VsssssssV..",
    ".VssMMMMssV.",
    "..VsssssV...",
    "..ddddddd...",
    "..dDDDDDd...",
    "..dDDDDDd...",
    "..dDDDDDd...",
    "...rrrr.....",
    "..rr..rr....",
    "..rr..rr....",
    "..ii..ii....",
]

# Per-level enemy frames. Spawn logic, collision, AI all stay the same;
# only the rendered sprite changes.
LEVEL_ENEMIES = {
    1: (ENEMY_A, ENEMY_B),
    2: (CPU_A, CPU_B),
    3: (KAREN_A, KAREN_B),
}

# ---- PANCAKES: black-and-white French bulldogs replace enemies, hearts
# replace pellets. Activate with `python3 game.py --pancakes`. Sprite uses
# 'b' (boots near-black) for spots and '8' (floppy white) for the white
# coat -- neither is overridden by any level theme so dogs stay
# black-and-white in all 3 worlds. 'M' is the existing mouth red,
# perfect for tongue-out.
# Side-profile black Frenchie (faces right by default; existing flip
# logic mirrors it when walking left). Tail nub on the left, head +
# bat ears on the right, long flat back between them, white chest
# patch under the throat, two visible legs.
# Refined side-profile Frenchie: distinct head with a hint of smushed
# snout, single bright eye, a small white chest patch under the throat,
# barrel body, four short legs, and the iconic upright bat ears.
FRENCHIE_A = [
    "........b.b.",   # 0:  ear tips (small triangular points)
    ".......bbbbb",   # 1:  ears widening
    "......bbbbb.",   # 2:  ear bases tapering down to head
    ".....bbbbbbb",   # 3:  head meets ears, snout starts (cols 5-11)
    "....bbbb8bbb",   # 4:  face with bright eye (8 = white highlight)
    "....bbbbbbb.",   # 5:  jaw / mouth area (slightly tucked under)
    "...bbbbbbbb.",   # 6:  neck curving into the shoulders
    "bbbbbbbbbbbb",   # 7:  shoulders + long back (full row)
    "bbbbbbbbbbbb",   # 8:  body
    "bbbbbb8888bb",   # 9:  WHITE CHEST PATCH (front of belly)
    "bbbbbbbbbbbb",   # 10: body bottom
    "............",   # 11: leg gap
    ".bb...bbb...",   # 12: rear leg (slim) + front leg (thicker, closer)
    ".bb...bbb...",   # 13: legs continued
    ".bb...bbb...",   # 14: legs
    ".ii...iii...",   # 15: paws
]
FRENCHIE_B = [
    "........b.b.",
    ".......bbbbb",
    "......bbbbb.",
    ".....bbbbbbb",
    "....bbbb8bbb",
    "....bbbbbbb.",
    "...bbbbbbbb.",
    "bbbbbbbbbbbb",
    "bbbbbbbbbbbb",
    "bbbbbb8888bb",
    "bbbbbbbbbbbb",
    "............",
    ".bbb...bb...",   # weight shifted (rear thicker, front slimmer)
    ".bbb...bb...",
    ".bbb...bb...",
    ".iii...ii...",
]

# 5x5 heart pellet for Pancakes. 'R' is rubber-band red (always defined,
# never retinted), 'l' is the bright powerup core for a soft highlight.
HEART = [
    ".R.R.",
    "RlRRR",
    "RRRRR",
    ".RRR.",
    "..R..",
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

# 10x9 server-rack chunk -- the level-2 obstacle. Same footprint as a CRATE
# so the existing collision math works unchanged. Status LEDs alternate.
# 'h' (ground deep) is dark grey, 'X' is darker, 'C' is mid-tone.
SERVER_RACK = [
    "XXXXXXXXXX",
    "XCCCCCCCCX",
    "XCXCCCCXCX",
    "XCCCCCCCCX",
    "XCXCCCCXCX",
    "XCCCCCCCCX",
    "XCXCCCCXCX",
    "XCCCCCCCCX",
    "XXXXXXXXXX",
]

# 10x9 office chair -- the level-3 obstacle. Front view: tall padded
# backrest, wide cushioned seat, post + 5-wheel spider base.
# Designed for visibility at game scale: the silhouette pops against
# the beige cubicle wall.
OFFICE_CHAIR = [
    "...XXXX...",   # top of the headrest
    "..XCCCCX..",   # headrest narrowing into the back
    ".XCccccCX.",   # backrest top (mesh fabric highlight)
    ".XCccccCX.",
    ".XCccccCX.",
    ".XCccccCX.",
    "XCCCCCCCCX",   # shoulders / lumbar
    "XccccccccX",   # seat cushion (collision top)
    "XccccccccX",   # under-seat
    "....XX....",   # gas-lift post
    "X..XXXX..X",   # 5-spoke wheel base
    "..........",   # 1-px gap to the carpet
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

# 12x6 delivery quadcopter. Top + bottom rotors with horizontal arms;
# the body in the middle has two "camera" eyes. Two animation frames
# alternate the rotor blade orientation so they look like they're
# spinning.
DRONE_A = [
    "QQ........QQ",
    ".QQ......QQ.",
    "..$$$$$$$$..",
    "..$$U..U$$..",
    ".QQ......QQ.",
    "QQ........QQ",
]
DRONE_B = [
    ".QQ......QQ.",
    "QQ........QQ",
    "..$$$$$$$$..",
    "..$$U..U$$..",
    "QQ........QQ",
    ".QQ......QQ.",
]

# Three 7x6 weapon crates: each visually hints at the weapon's effect.
# RAPID = stacked motion lines (bullet hose). SPREAD = three radiating
# rays from a central source. PIERCE = horizontal arrow/lance.
WEAPON_CRATE_RAPID = [    # yellow box, lightning-style stripes
    "cccccccc"[:7],
    "cYYYYYYc"[:7],
    "cZZZZZZc"[:7],
    "cYYYYYYc"[:7],
    "cZZZZZZc"[:7],
    "cccccccc"[:7],
]
WEAPON_CRATE_SPREAD = [   # cyan-ish box, three rays converging from left
    "ccccccc",
    "cd...lc",
    "c.dl..c",
    "c.lDd.c",
    "cd..dlc",
    "ccccccc",
]
WEAPON_CRATE_PIERCE = [   # red box, horizontal arrow inside
    "ccccccc",
    "cXXXXXc",
    "c.D...c",
    "cDDDDDc",
    "c.D...c",
    "cccccXc"[:7],
]
# Backwards-compat: old name still referenced by some code paths
WEAPON_CRATE = WEAPON_CRATE_RAPID

# 7x9 SSL cert: a golden padlock with a tiny "SSL" plate. Drops when
# the boss dies and ends the level when the player walks over it.
SSL_CERT = [
    ".UUUUU.",   # padlock shackle top
    ".U...U.",
    ".U...U.",
    "UUeeeUU",   # body top
    "UeeSeeU",
    "UeeSeeU",
    "UeevveU",
    "UeevveU",
    "UUUUUUU",
]

# Bosses are 24x32 hand-built sprites, one per level. Footprint and HP
# are shared across the three; the visuals (and per-level theme tint)
# vary. _build_boss_* helpers below construct each one programmatically
# at module load -- avoids the dimension-counting fiasco of hand-typed
# 24x32 grids.
BOSS_W = 24
BOSS_H = 32
BOSS_HP = 8
BOSS_PX_PER_SEC = 18.0
BOSS_TINT = (255, 255, 255, 255)   # default no-op tint; level themes set the real one


def _ellipse(rows, cx, cy, rx, ry, ch):
    """Fill an ellipse on a 2D char grid (mutates rows in place)."""
    H, W = len(rows), len(rows[0])
    for y in range(H):
        for x in range(W):
            d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            if d <= 1.0:
                rows[y][x] = ch


def _rect(rows, x0, y0, w, h, ch):
    H, W = len(rows), len(rows[0])
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            if 0 <= x < W and 0 <= y < H:
                rows[y][x] = ch


def _build_jobba():
    """Level 1 boss: bulbous green-yellow slug with a tiny head."""
    rows = [['.'] * BOSS_W for _ in range(BOSS_H)]
    # Big oval body taking up the bottom 2/3
    _ellipse(rows, 12, 22, 11, 9, '+')
    # Smaller head perched on top
    _ellipse(rows, 12, 8, 6, 6, '+')
    # Connector neck (so head joins body smoothly)
    _rect(rows, 9, 11, 6, 5, '+')
    # Shadow speckles on the lower-right of the body
    for y in range(18, 31):
        for x in range(0, BOSS_W):
            if rows[y][x] == '+' and ((x * 3 + y) % 11 == 0 or (x + y * 2) % 13 == 0):
                rows[y][x] = '-'
    # Highlight streak on the upper-left of the body
    for y in range(13, 22):
        for x in range(2, 12):
            if rows[y][x] == '+' and (x + y) % 7 == 0:
                rows[y][x] = '?'
    # Eyes
    for x, y in [(8, 6), (9, 6), (15, 6), (16, 6),
                 (8, 7), (9, 7), (15, 7), (16, 7)]:
        if 0 <= x < BOSS_W and 0 <= y < BOSS_H:
            rows[y][x] = 'V'
    # Wide grinning mouth
    for x in range(8, 17):
        if 0 <= x < BOSS_W:
            rows[10][x] = 'm'
    for x in range(9, 16):
        if 0 <= x < BOSS_W:
            rows[11][x] = 'm'
    return [''.join(r) for r in rows]


def _build_cable_bundle():
    """Level 2 boss: a black mass of networking cables with bristling
    memory sticks and red eyes peeking through."""
    rows = [['.'] * BOSS_W for _ in range(BOSS_H)]
    # Main body: irregular roundish shape -- ellipse with bumps
    _ellipse(rows, 12, 17, 11, 12, '0')
    # Top "bun" of cables
    _ellipse(rows, 12, 6, 8, 5, '0')
    _rect(rows, 4, 6, 16, 12, '0')      # connector slab
    # Cable highlight rows: alternate horizontal bands of '1' (cable mid)
    for y in range(BOSS_H):
        for x in range(BOSS_W):
            if rows[y][x] == '0' and y % 2 == 0:
                if (x + y // 2) % 4 == 0:
                    rows[y][x] = '1'
    # Memory sticks bristling from the sides. Each stick is a 1x4
    # horizontal '2' (green PCB) with a '1' (gold contact) cap.
    sticks = [
        (0, 9, 5, '2'),     # left stick
        (19, 11, 5, '3'),   # right stick (red PCB)
        (0, 21, 5, '3'),
        (19, 19, 5, '2'),
        (0, 14, 4, '2'),
        (20, 24, 4, '3'),
    ]
    for sx, sy, slen, color in sticks:
        for k in range(slen):
            xx = sx + k
            if 0 <= xx < BOSS_W and 0 <= sy < BOSS_H:
                rows[sy][xx] = color
        # gold contact pin at the connector end (whichever end is in body)
        contact_x = sx + slen - 1 if sx == 0 else sx
        if 0 <= contact_x < BOSS_W:
            rows[sy][contact_x] = '1'
    # Two glowing red eyes peeking out of the cable mass
    for x, y in [(9, 12), (10, 12), (14, 12), (15, 12)]:
        if 0 <= x < BOSS_W and 0 <= y < BOSS_H:
            rows[y][x] = '3'
    return [''.join(r) for r in rows]


def _build_office_manager():
    """Level 3 boss: pudgy office manager in a suit with red tie + glasses."""
    rows = [['.'] * BOSS_W for _ in range(BOSS_H)]
    # Round head (skin)
    _ellipse(rows, 12, 6, 5, 4, 's')
    # Hair on top
    _rect(rows, 8, 2, 9, 2, 'H')
    _rect(rows, 7, 3, 11, 1, 'H')
    # Glasses (frames + lenses)
    for x in [9, 10, 13, 14]:
        rows[6][x] = 'F'    # frame
        rows[7][x] = 'L'    # lens
    rows[6][11] = 'F'
    rows[6][12] = 'F'       # bridge
    # Tiny nose / mouth
    rows[8][11] = 'x'
    rows[8][12] = 'x'
    rows[9][10] = 'M'
    rows[9][11] = 'M'
    rows[9][12] = 'M'
    # Wide pudgy torso (suit jacket)
    _ellipse(rows, 12, 18, 10, 7, '4')
    # Belly bulge -- white shirt visible through unbuttoned middle
    _rect(rows, 10, 14, 5, 8, '5')
    # Tie down the middle of the shirt
    for y in range(14, 22):
        rows[y][12] = '6'
    rows[14][11] = '6'
    rows[14][13] = '6'
    rows[15][12] = '6'
    # Suit lapels framing the shirt
    for y in range(14, 19):
        rows[y][9] = '4'
        rows[y][15] = '4'
    # Arms hanging at sides (suit color)
    for y in range(15, 25):
        if 0 <= y < BOSS_H:
            rows[y][2] = '4'
            rows[y][3] = '4'
            rows[y][20] = '4'
            rows[y][21] = '4'
    # Legs (suit pants) stamped at bottom
    for y in range(25, 32):
        rows[y][9] = '4'
        rows[y][10] = '4'
        rows[y][13] = '4'
        rows[y][14] = '4'
    # Shoes
    for x in [8, 9, 10, 11]:
        rows[31][x] = 'X'
    for x in [12, 13, 14, 15]:
        rows[31][x] = 'X'
    return [''.join(r) for r in rows]


JOBBA_BOSS         = _build_jobba()
CABLE_BUNDLE_BOSS  = _build_cable_bundle()
OFFICE_MANAGER_BOSS = _build_office_manager()
BOSS_SPRITES = {1: JOBBA_BOSS, 2: CABLE_BUNDLE_BOSS, 3: OFFICE_MANAGER_BOSS}

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
    """Thin wrapper over a pygame Surface that exposes the same drawing
    API the rest of the game already calls (set / fill_rect / blit_sprite
    / blit_text / blit_text_scaled). Drawing happens at the internal
    resolution; main() scales the surface to the window once per frame.
    """
    __slots__ = ("w", "h", "surface", "_sprite_cache", "_cache_version")

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.surface = pygame.Surface((w, h))
        self._sprite_cache = {}  # id(sprite_rows) -> (right_facing, left_facing)
        self._cache_version = _SPRITE_CACHE_VERSION

    def clear(self, color):
        self.surface.fill(color)

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h and color is not None:
            self.surface.set_at((int(x), int(y)), color)

    def fill_rect(self, x, y, w, h, color):
        if color is None:
            return
        # pygame clips automatically.
        self.surface.fill(color, (int(x), int(y), int(w), int(h)))

    def blit_sprite(self, sprite_rows, x0, y0, palette=PAL, flip=False):
        # Invalidate the cache when the global palette changes (level transition)
        if self._cache_version != _SPRITE_CACHE_VERSION:
            self._sprite_cache.clear()
            self._cache_version = _SPRITE_CACHE_VERSION
        key = id(sprite_rows)
        cached = self._sprite_cache.get(key)
        if cached is None:
            cached = (_make_sprite_surface(sprite_rows, palette),
                      _make_sprite_surface(sprite_rows, palette, flip=True))
            self._sprite_cache[key] = cached
        surf = cached[1] if flip else cached[0]
        self.surface.blit(surf, (int(x0), int(y0)))

    def blit_text(self, text, x0, y0, color, spacing=1):
        """Render uppercase text using FONT (5x7 glyphs)."""
        cx = x0
        for ch in text.upper():
            glyph = FONT.get(ch, FONT[" "])
            for ry, row in enumerate(glyph):
                for rx, c in enumerate(row):
                    if c == "#":
                        self.set(cx + rx, y0 + ry, color)
            cx += 5 + spacing

    def blit_text_scaled(self, text, x0, y0, scale, color, spacing=1, row_colors=None):
        """Render uppercase text scaled. row_colors (list of 7) overrides
        color per glyph row (for fire/rainbow gradients)."""
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


def _make_sprite_surface(sprite_rows, palette=PAL, flip=False):
    """Convert a char-grid sprite to a pygame Surface with per-pixel alpha
    (any palette key whose color is None becomes transparent)."""
    h = len(sprite_rows)
    w = len(sprite_rows[0])
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y, row in enumerate(sprite_rows):
        if flip:
            row = row[::-1]
        for x, ch in enumerate(row):
            color = palette.get(ch)
            if color is not None:
                surf.set_at((x, y), color)
    return surf


# Bumped on level transition so cached sprite surfaces (which capture the
# palette at build time) get invalidated and rebuilt with the new colors.
_SPRITE_CACHE_VERSION = 0


def _apply_level_palette(level):
    """Mutate PAL in-place to apply the level's theme overrides. The first
    time this is called we snapshot the defaults so we can restore them."""
    global _PAL_DEFAULTS
    if _PAL_DEFAULTS is None:
        _PAL_DEFAULTS = dict(PAL)
    # Reset to defaults, then overlay the level's overrides.
    PAL.clear()
    PAL.update(_PAL_DEFAULTS)
    overrides = LEVEL_THEMES.get(level, LEVEL_THEMES[1])["pal"]
    PAL.update(overrides)
    # Boss tint comes from the theme too; updated globally so render uses it.
    global BOSS_TINT
    BOSS_TINT = LEVEL_THEMES.get(level, LEVEL_THEMES[1])["boss_tint"]
    _OVERLORD_CACHE.clear()


_OVERLORD_CACHE = {}   # (level, flip) -> pygame.Surface


def _get_overlord_surface(level, flip):
    """Return the boss sprite for the current level, flipped if needed.
    Cached so the per-pixel surface build only runs once per (level, flip)."""
    key = (level, bool(flip))
    cached = _OVERLORD_CACHE.get(key)
    if cached is not None:
        return cached
    sprite = BOSS_SPRITES.get(level, BOSS_SPRITES[1])
    surf = _make_sprite_surface(sprite, flip=flip)
    _OVERLORD_CACHE[key] = surf
    return surf


# ===================================================================
# Background (computed once per resolution)
# ===================================================================
def draw_background(fb, camera_x=0.0, level=1):
    """Sky -> back wall -> ground. Wall texture differs per level
    (bricks for L1, server racks for L2, desks/monitors for L3). Wall +
    ground textures scroll with the camera; sky and stars stay fixed."""
    w, h = fb.w, fb.h
    cx = int(camera_x)

    ground_y = int(h * 0.78)
    wall_top = int(h * 0.18)
    wall_bot = ground_y - 1

    # Sky gradient (no scroll, 'parallax at infinity')
    for y in range(0, wall_top):
        t = y / max(1, wall_top - 1)
        if t < 0.5:
            c = lerp_rgb(PAL['k'], PAL['K'], t * 2)
        else:
            c = lerp_rgb(PAL['K'], PAL['B'], (t - 0.5) * 2)
        for x in range(w):
            fb.set(x, y, c)

    # Stars (only for level 1; the indoor levels don't have a sky)
    if level == 1:
        rng = random.Random(42)
        for _ in range(max(8, w // 8)):
            sx = rng.randrange(w)
            sy = rng.randrange(0, max(1, wall_top - 2))
            fb.set(sx, sy, PAL['*'])

    # Back wall fill
    fb.fill_rect(0, wall_top, w, wall_bot - wall_top + 1, PAL['j'])

    # Per-level wall texture
    if level == 2:
        _draw_wall_servers(fb, cx, wall_top, wall_bot)
    elif level == 3:
        _draw_wall_desks(fb, cx, wall_top, wall_bot, ground_y)
    else:
        _draw_wall_bricks(fb, cx, wall_top, wall_bot)

    # Wall highlight band along the top
    for x in range(w):
        fb.set(x, wall_top, PAL['J'])

    # Wall-floor seam shadow
    fb.fill_rect(0, ground_y - 1, w, 1, (20, 12, 6))

    # Ground bands (3 colors top->bottom)
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


def _draw_wall_bricks(fb, cx, wall_top, wall_bot):
    """Level 1: staggered brick courses with mortar lines."""
    w = fb.w
    for r in range(wall_top, wall_bot + 1, 4):
        for x in range(w):
            fb.set(x, r, PAL['u'])
    for r in range(wall_top, wall_bot + 1, 4):
        course_idx = (r - wall_top) // 4
        course_offset = 5 if (course_idx % 2) else 0
        first = (course_offset - cx) % 10
        if first > 0:
            first -= 10
        for sx in range(first, w, 10):
            for dy in range(0, 4):
                yy = r + dy
                if yy <= wall_bot:
                    fb.set(sx, yy, PAL['u'])


def _draw_wall_servers(fb, cx, wall_top, wall_bot):
    """Level 2: rows of server racks. Each rack unit is 16 wide x 4 tall
    with a darker frame, two LED status lights, and a thin vent grille
    along the bottom. Wires hang from the ceiling at sparse intervals."""
    w = fb.w
    rack_w = 16
    # Horizontal rack rows every 4 px
    for r in range(wall_top + 1, wall_bot + 1, 4):
        for x in range(w):
            fb.set(x, r, PAL['u'])
    # Per-rack frame edges (vertical seams) + LEDs
    first = -(cx % rack_w)
    for sx in range(first, w + rack_w, rack_w):
        for r in range(wall_top + 1, wall_bot + 1, 4):
            # left/right rack edges
            if 0 <= sx < w:
                for dy in range(1, 4):
                    yy = r + dy
                    if yy <= wall_bot:
                        fb.set(sx, yy, PAL['u'])
            # green status LED 3 px in from the left
            led_x = sx + 3
            if 0 <= led_x < w:
                yy = r + 1
                if yy <= wall_bot:
                    fb.set(led_x, yy, (110, 240, 110))
            # red status LED 6 px in
            led2_x = sx + 6
            if 0 <= led2_x < w:
                yy = r + 1
                if yy <= wall_bot:
                    fb.set(led2_x, yy, (255, 110, 110))
    # Hanging wires: a few thin vertical lines drooping from the top of
    # the wall. Deterministic per (rack_w * world_x).
    rng = random.Random(0xCAB1)
    wires = []
    for _ in range(20):
        wires.append((rng.randint(0, 24000),
                      rng.randint(8, 22),       # length
                      rng.choice([(40, 40, 50), (60, 50, 30), (60, 30, 30)])))
    for world_x, length, color in wires:
        sx = (world_x - cx) % (w + 32) - 16   # tile horizontally with margin
        if 0 <= sx < w:
            for dy in range(length):
                yy = wall_top + dy
                if yy <= wall_bot:
                    fb.set(sx, yy, color)
            # dangling connector at the bottom of the wire
            conn_y = wall_top + length
            if conn_y <= wall_bot:
                fb.set(sx - 1, conn_y, color)
                fb.set(sx + 1, conn_y, color)


def _draw_wall_desks(fb, cx, wall_top, wall_bot, ground_y):
    """Level 3: a row of beige cubicles with CRT terminal monitors at
    desk height. The terminal screens flicker phosphor green text on
    a dark background -- the only green in the otherwise grey/beige room."""
    w = fb.w
    # Faint horizontal cubicle fabric weave
    for r in range(wall_top + 2, wall_bot, 4):
        for x in range(0, w, 3):
            fb.set(x, r, PAL['u'])
    # Vertical cubicle dividers every 28 px
    cube_w = 28
    first = -(cx % cube_w)
    for sx in range(first, w + cube_w, cube_w):
        if 0 <= sx < w:
            for yy in range(wall_top + 1, wall_bot + 1):
                fb.set(sx, yy, PAL['u'])
            # Highlight on the divider's right face
            if sx + 1 < w:
                for yy in range(wall_top + 1, wall_bot + 1, 2):
                    fb.set(sx + 1, yy, PAL['J'])
    # Desk surface line (warm wood-ish): a thin band a few px above floor
    desk_top_y = ground_y - 14
    for x in range(w):
        fb.set(x, desk_top_y, (95, 70, 45))
        fb.set(x, desk_top_y + 1, (135, 100, 65))
    # CRT monitors sitting on the desk -- one per cubicle.
    monitor_w = 11
    monitor_h = 9
    for sx in range(first, w + cube_w, cube_w):
        mx = sx + (cube_w - monitor_w) // 2
        my = desk_top_y - monitor_h - 1
        if -monitor_w <= mx < w + monitor_w and my >= wall_top:
            # Cream/beige plastic bezel
            for ix in range(monitor_w):
                for iy in range(monitor_h):
                    px = mx + ix
                    py = my + iy
                    if 0 <= px < w and wall_top <= py <= wall_bot:
                        on_edge = (ix == 0 or ix == monitor_w - 1
                                   or iy == 0 or iy == monitor_h - 1)
                        if on_edge:
                            fb.set(px, py, (200, 190, 160))
                        else:
                            # screen interior: dark phosphor background
                            fb.set(px, py, (12, 18, 12))
            # Green terminal text rows (~3 visible code lines)
            rng = random.Random((sx + 99991) & 0xFFFF)
            for line in range(3):
                line_y = my + 2 + line * 2
                # leading prompt char on each line
                fb.set(mx + 2, line_y, (120, 240, 120))
                # then a varying number of bright pixels for 'code'
                length = rng.randint(3, monitor_w - 5)
                for k in range(length):
                    px = mx + 4 + k
                    if px < mx + monitor_w - 1:
                        if rng.random() < 0.7:
                            fb.set(px, line_y, (120, 240, 120))
            # CRT stand (wider chunky base) -- sits on the desk
            base_y = my + monitor_h
            for ix in range(2, monitor_w - 2):
                px = mx + ix
                if 0 <= px < w and base_y <= wall_bot:
                    fb.set(px, base_y, (160, 150, 130))
            # tiny power LED bottom-right of bezel
            led_x = mx + monitor_w - 2
            led_y = my + monitor_h - 2
            if 0 <= led_x < w and wall_top <= led_y <= wall_bot:
                fb.set(led_x, led_y, (255, 200, 80))


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
    """A slingshot pellet. Tracks which targets THIS pellet has already
    hit so we never double-damage a single target with one PIERCE round.
    We track on the pellet (which is short-lived) instead of on the
    enemy/boss to avoid CPython's id() reuse bug -- when a pellet
    dies and is GC'd, its id() can be assigned to a new pellet, which
    would then be erroneously filtered as 'already hit' by a target's
    set."""
    __slots__ = ("x", "y", "vx", "alive", "pierce", "hit_targets")
    def __init__(self, x, y, vx, pierce=False):
        self.x = x
        self.y = y
        self.vx = vx
        self.alive = True
        self.pierce = pierce
        self.hit_targets = set()   # ids of enemies/boss this pellet has hit


class Enemy:
    """A red ghoul. Moves at vx px/sec (signed -> direction). Reverses
    direction when it would step into a crate or fall into a gap."""
    __slots__ = ("x", "y", "vx", "hp", "alive", "anim_t")
    def __init__(self, x, y, vx):
        self.x = x
        self.y = y
        self.vx = vx
        self.hp = 1
        self.alive = True
        self.anim_t = 0.0


class Boss:
    """The OVERLORD. Bigger, slower, multi-HP. Spawns once you near the
    right edge of the world; must die in addition to the 8 ghouls."""
    __slots__ = ("x", "y", "vx", "hp", "alive", "anim_t", "flash_until")
    def __init__(self, x, y, vx):
        self.x = x
        self.y = y
        self.vx = vx
        self.hp = BOSS_HP
        self.alive = True
        self.anim_t = 0.0
        self.flash_until = 0.0


class Drone:
    """Goofy mechanical delivery drone. Flies in from off-screen, drops
    a weapon crate near a target_x, flies off the other side. State:
      - 'approaching' -- carrying crate, still on its way to target
      - 'leaving'     -- crate dropped, just exiting the world
    """
    __slots__ = ("x", "y", "vx", "target_x", "state", "kind", "alive", "anim_t")
    def __init__(self, x, y, vx, target_x, kind):
        self.x = x
        self.y = y
        self.vx = vx
        self.target_x = target_x
        self.state = "approaching"
        self.kind = kind        # "RAPID" / "SPREAD" / "PIERCE"
        self.alive = True
        self.anim_t = 0.0


class WeaponCrate:
    """A small crate dropped by a drone. Falls under gravity, lands on the
    nearest surface, sits until the player walks over it."""
    __slots__ = ("x", "y", "vy", "kind", "grounded", "claimed")
    def __init__(self, x, y, kind):
        self.x = x
        self.y = y
        self.vy = 0.0
        self.kind = kind
        self.grounded = False
        self.claimed = False


class SSLCert:
    """A golden 7x9 padlock that drops when the boss dies. Walking over
    it advances the level (or wins the game on the final level)."""
    __slots__ = ("x", "y", "claimed")
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.claimed = False


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
        # active weapon mode + expiry. weapon_kind in {"DEFAULT","RAPID","SPREAD","PIERCE"}.
        self.weapon_kind = "DEFAULT"
        self.weapon_until = 0.0
        # delivery drones currently flying through the level
        self.drones = []
        # Mixer channel currently looping the drone buzz (None when no drones)
        self._drone_channel = None
        # weapon crates that have been dropped and are on the ground waiting
        self.weapon_crates = []
        # the boss (None until triggered, then a single Boss instance)
        self.boss = None
        self.boss_announced = False
        # the golden SSL cert that drops when the boss dies; walking over it
        # advances the level (or wins on the final level)
        self.ssl_cert = None
        # current level (1..FINAL_LEVEL). Persists across level transitions.
        self.level = 1
        # Pancakes: cosmetic flag that swaps enemies for French bulldogs
        # and pellets for hearts. Set externally after construction.
        self.pancakes_mode = False
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
        # Fold the level number into the seed so each level gets its own
        # arrangement of crates, gaps, and floppies (without it, all three
        # levels generated the exact same layout).
        rng = random.Random(0xC0FFEE ^ self.world_w ^ self.ground_y
                            ^ (self.level * 0x9E3779B1))
        # Per-level obstacle dimensions. Level 3 uses 12-px-tall chairs
        # (vs 9 for crates / racks) so the silhouette pops as something
        # the player has to clear. Single-stack only on level 3 since
        # 24-px (2 stacked chairs) sits right at max jump height.
        crate_w = len(CRATE[0])
        crate_h = len(CRATE)             # 9 -- collision unit for L1/L2
        if self.level == 3:
            obstacle_h = len(OFFICE_CHAIR)   # 12
            obstacle_stack_choices = [1]
        else:
            obstacle_h = crate_h
            obstacle_stack_choices = [1, 1, 1, 1, 2, 2]
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
                stack = rng.choice(obstacle_stack_choices)
                ow, oh = crate_w, stack * obstacle_h
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
        # Same guarantee for obstacles -- on some seeds the weighted-choice
        # roll skipped 'crate' enough times to produce zero, leaving the
        # level featureless. Force-place 2 single-stack obstacles at
        # 1/3 and 2/3 of the world if we ended up with fewer than 2.
        while len(self.obstacles) < 2:
            slot = self.world_w // 3 if len(self.obstacles) == 0 else (2 * self.world_w) // 3
            ox = self._nearest_safe_x(slot)
            self.obstacles.append((ox, self.ground_y - obstacle_h, crate_w, obstacle_h))
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
        # Active weapon, with auto-revert to DEFAULT when timer elapses
        if now >= self.weapon_until:
            self.weapon_kind = "DEFAULT"
        kind = self.weapon_kind
        cooldown = PELLET_COOLDOWN * 0.5 if kind == "RAPID" else PELLET_COOLDOWN
        if now - self.last_shot < cooldown:
            return
        self.last_shot = now
        sling_x = self.player_x + (12 if self.player_face_right else 1)
        sling_y = self.player_y + 12
        sign = 1 if self.player_face_right else -1
        # Hearts (Pancakes mode) fly slower so the player can actually
        # see what they are.
        speed = PELLET_PX_PER_SEC * (0.5 if self.pancakes_mode else 1.0)
        vx = sign * speed
        if kind == "SPREAD":
            # 3 pellets fanning out (~10 px/s vertical for the outer ones)
            self.pellets.append(Pellet(sling_x, sling_y, vx))
            self.pellets.append(Pellet(sling_x, sling_y - 1, vx * 0.95))   # cosmetic offset
            self.pellets.append(Pellet(sling_x, sling_y + 1, vx * 0.95))
        elif kind == "PIERCE":
            self.pellets.append(Pellet(sling_x, sling_y, vx, pierce=True))
        else:
            # DEFAULT or RAPID: single pellet
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
        self.last_hit = time.time()       # i-frames after respawn
        self.message = "You fell in. Try harder."
        self.message_until = time.time() + 1.4
        play("hit")

    def _maybe_send_drone(self, kill_x, kill_y):
        """25% chance after a ghoul kill: a delivery drone flies in from
        off-screen and drops a random weapon crate near the kill site."""
        if random.random() > 0.30:
            return
        kind = random.choice(["RAPID", "SPREAD", "PIERCE"])
        # Drone target: near where the ghoul died, but clamped to the
        # currently visible area so the player actually sees the drop.
        target_x = max(self.camera_x + 20,
                       min(self.camera_x + self.fb_w - 20, kill_x))
        # Pick whichever side is closer to off-screen so the drone enters
        # from the same direction the player isn't looking.
        from_left = random.random() < 0.5
        if from_left:
            x = self.camera_x - 14
            vx = +60.0
        else:
            x = self.camera_x + self.fb_w + 4
            vx = -60.0
        y = 6.0   # high in the sky, above the wall_top
        self.drones.append(Drone(x, y, vx, target_x, kind))

    def _advance_level(self):
        """SSL cert claimed -> next level, or final win if we just cleared
        FINAL_LEVEL. Player progress (lives, disks, weapon timer) carries
        over; kills, enemies, drones, crates, boss, ssl_cert all reset."""
        if self.level >= FINAL_LEVEL:
            self.state = "win"
            self.end_message = random.choice(WIN_QUOTES)
            play("win")
            return
        self.level += 1
        # Apply the new theme's PAL overrides and clear sprite caches so
        # cached enemy/crate/etc. surfaces get rebuilt with new colors.
        _apply_level_palette(self.level)
        global _SPRITE_CACHE_VERSION
        _SPRITE_CACHE_VERSION += 1
        # Reset world state for a fresh playfield. Disks reset per-level
        # (consistent with kills) so the HUD can never show > total.
        self.kills = 0
        self.disks = 0
        self.spawned = 0
        self.next_spawn = time.time() + random.uniform(ENEMY_SPAWN_MIN, ENEMY_SPAWN_MAX)
        self.enemies = []
        self.pellets = []
        self.drones = []
        self.weapon_crates = []
        self.boss = None
        self.boss_announced = False
        self.ssl_cert = None
        self.player_x = float(PLAYER_MIN_X + 4)
        self.player_y = self.ground_y - len(NERD)
        self.player_vy = 0.0
        self.player_grounded = True
        self.camera_x = 0.0
        self.last_safe_x = float(PLAYER_MIN_X + 4)
        self._gen_level()
        self.message = f"LEVEL {self.level}: {LEVEL_THEMES[self.level]['name']}"
        self.message_until = time.time() + 2.5
        play("win")    # cheerful arpeggio for level transition

    def _grant_weapon(self, kind, duration=8.0):
        self.weapon_kind = kind
        self.weapon_until = time.time() + duration
        labels = {"RAPID": "*RAPID FIRE*", "SPREAD": "*SPREAD SHOT*",
                  "PIERCE": "*PIERCE ROUND*"}
        self.message = labels.get(kind, "*POWER UP*") + "  ROOT++"
        self.message_until = time.time() + 1.6
        play("kill")

    def _check_pickups(self):
        """Floppy disks + powerups + weapon crates: claim any whose bbox
        intersects the player."""
        ax, ay, aw, ah = self._player_bbox()
        for f in self.floppies:
            if f[2]:
                continue
            fx, fy = f[0], f[1]
            if (fx < ax + aw and fx + 5 > ax and fy < ay + ah and fy + 6 > ay):
                f[2] = True
                self.disks += 1
                play("kill")
        for p in self.powerups:
            if p[3]:
                continue
            px, py = p[1], p[2]
            if (px < ax + aw and px + 5 > ax and py < ay + ah and py + 5 > ay):
                p[3] = True
                if p[0] == "RAPID":
                    self._grant_weapon("RAPID")
        # Drone-delivered weapon crates: 7x6 boxes sitting on the ground
        for c in self.weapon_crates:
            if c.claimed or not c.grounded:
                continue
            if (c.x < ax + aw and c.x + 7 > ax
                    and c.y < ay + ah and c.y + 6 > ay):
                c.claimed = True
                self._grant_weapon(c.kind)
        # Golden SSL cert dropped by the boss
        if self.ssl_cert is not None and not self.ssl_cert.claimed:
            sc = self.ssl_cert
            if (sc.x < ax + aw and sc.x + 7 > ax
                    and sc.y < ay + ah and sc.y + 9 > ay):
                sc.claimed = True
                self._advance_level()

    def tick(self, dt, keys):
        """Advance world state by dt seconds.

        keys is a list mixing held-direction strings and one-frame action
        events (see collect_input):
          * 'LEFT' / 'RIGHT' -- present every frame the key is held
          * 'JUMP'           -- one frame on SPACE press
          * 'SHOOT'          -- one frame on X press
        With pygame providing real key-up events, the rest is trivial:
        held = walk, tapped = act.
        """
        if self.state != "playing":
            return

        held_left  = "LEFT"  in keys
        held_right = "RIGHT" in keys

        # Handle one-frame action events
        for k in keys:
            if k == "JUMP":
                if self.player_grounded:
                    self.player_vy = -JUMP_V0_PX_PER_SEC
                    self.player_grounded = False
            elif k == "SHOOT":
                self.shoot()

        if held_left:
            self.player_face_right = False
        if held_right:
            self.player_face_right = True

        # ---- horizontal motion with obstacle collision ----
        step = PLAYER_PX_PER_SEC * dt
        dx = 0.0
        if held_left:
            dx -= step
        if held_right:
            dx += step
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
            hit_obs = None
            for ox, oy, ow, oh in self.obstacles:
                if (new_x + 1 < ox + ow and new_x + ENEMY_W - 1 > ox
                        and e.y + 1 < oy + oh and e.y + ENEMY_H > oy):
                    hit_obs = (ox, oy, ow, oh)
                    break
            blocked_other = False
            if hit_obs is None:
                lead_x = new_x + (ENEMY_W if e.vx > 0 else 0)
                if self._in_gap(int(lead_x)):
                    blocked_other = True
                elif lead_x <= -8 or lead_x >= self.world_w + 8:
                    blocked_other = True
            if hit_obs is not None:
                # Snap the ghoul flush against the side of the obstacle so it
                # never ends up overlapping (which used to leave it stuck,
                # reversing every frame between two 'inside-obstacle' states).
                ox, oy, ow, oh = hit_obs
                if e.vx > 0:
                    e.x = float(ox - ENEMY_W + 1)
                else:
                    e.x = float(ox + ow - 1)
                e.vx = -e.vx
            elif blocked_other:
                e.vx = -e.vx
            else:
                e.x = new_x
            # Even after snap+reverse, double-check we aren't sitting inside
            # an obstacle (e.g. spawned-into one or flipped into a different
            # crate). If so, push laterally until clear.
            for _ in range(3):
                inside = None
                for ox, oy, ow, oh in self.obstacles:
                    if (e.x + 1 < ox + ow and e.x + ENEMY_W - 1 > ox
                            and e.y + 1 < oy + oh and e.y + ENEMY_H > oy):
                        inside = (ox, oy, ow, oh)
                        break
                if inside is None:
                    break
                ox, oy, ow, oh = inside
                # push out away from the player so the ghoul retreats rather
                # than charges through a crate
                e.x = float(ox - ENEMY_W + 1) if self.player_x > ox else float(ox + ow - 1)
            e.anim_t += dt
            # collide with pellets (bounding box: enemy ~ 12x16 at e.x..e.x+11)
            for p in self.pellets:
                if not p.alive:
                    continue
                if id(e) in p.hit_targets:
                    continue
                if (e.x - 1 <= p.x <= e.x + 12
                        and e.y - 1 <= p.y <= e.y + 16):
                    e.alive = False
                    p.hit_targets.add(id(e))
                    if not p.pierce:
                        p.alive = False
                    self.kills += 1
                    # Pancakes: warm tributes to Pancake Waffles instead of
                    # the technical kill taunts. The Frenchie also yips
                    # happily instead of the regular kill bling.
                    if self.pancakes_mode:
                        self.message = random.choice(PANCAKES_TAUNTS)
                        play("bark")
                    else:
                        self.message = random.choice(KILL_TAUNTS)
                        play("kill")
                    self.message_until = time.time() + 1.4
                    self._maybe_send_drone(e.x, e.y)
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
        # Despawn enemies whose footing has gone out from under them. This
        # covers two cases:
        #   (a) the snap-out-of-obstacle push lands them on top of a gap
        #       and they oscillate between two unwalkable sides
        #   (b) they spawned in/got pushed onto a gap with no obstacle to
        #       stand on -- visually they should fall in, gameplay-wise we
        #       just remove them.
        for e in self.enemies:
            if not e.alive:
                continue
            center_x = e.x + ENEMY_W // 2
            if not self._in_gap(int(center_x)):
                continue
            # Are we standing on a crate-stack that bridges the gap?
            on_obs = False
            for ox, oy, ow, oh in self.obstacles:
                if (e.x < ox + ow and e.x + ENEMY_W > ox
                        and abs((e.y + ENEMY_H) - oy) <= 2):
                    on_obs = True
                    break
            if not on_obs:
                e.alive = False
                # Don't count the gap-fall as a kill (player wasn't involved),
                # but decrement spawned so a replacement comes in -- otherwise
                # bad seeds with a spawn point right next to a gap could
                # stall the kill quota and block boss progression.
                self.spawned = max(0, self.spawned - 1)

        # despawn enemies that walked far off the left side of the camera
        cull_x = self.camera_x - 32
        self.enemies = [e for e in self.enemies if e.alive or e.x > cull_x]

        # ---- delivery drones ----
        for d in self.drones:
            if not d.alive:
                continue
            d.x += d.vx * dt
            d.anim_t += dt
            if d.state == "approaching":
                # Drop the crate when the drone passes its target_x.
                passed = (d.vx > 0 and d.x >= d.target_x) \
                      or (d.vx < 0 and d.x <= d.target_x)
                if passed:
                    self.weapon_crates.append(
                        WeaponCrate(int(d.x + 4), int(d.y + 5), d.kind))
                    d.state = "leaving"
            # Despawn drone once well off-camera
            if d.x < self.camera_x - 30 or d.x > self.camera_x + self.fb_w + 30:
                d.alive = False
        self.drones = [d for d in self.drones if d.alive]
        # Drone propeller buzz: loop while ANY drone is alive, stop when none.
        any_alive = bool(self.drones)
        if any_alive:
            if self._drone_channel is None or not self._drone_channel.get_busy():
                snd = _SFX_CACHE.get("drone")
                if snd is not None:
                    try:
                        self._drone_channel = snd.play(loops=-1)
                    except pygame.error:
                        pass
        else:
            if self._drone_channel is not None:
                try:
                    self._drone_channel.stop()
                except pygame.error:
                    pass
                self._drone_channel = None

        # ---- weapon crates: gravity + ground/obstacle landing ----
        for c in self.weapon_crates:
            if c.claimed:
                continue
            if not c.grounded:
                c.vy += GRAVITY_PX_PER_SEC2 * dt
                new_y = c.y + c.vy * dt
                # Land if the bottom of the crate (y + 5) crosses an
                # obstacle top or the ground at this x.
                top = self.ground_y - 5
                cx_center = c.x + 2
                for ox, oy, ow, oh in self.obstacles:
                    if ox <= cx_center <= ox + ow:
                        cand = oy - 5
                        if cand < top:
                            top = cand
                if not self._in_gap(cx_center) and new_y >= top:
                    c.y = top
                    c.vy = 0.0
                    c.grounded = True
                else:
                    c.y = new_y
                    # If center is in a gap, fall off-screen and despawn
                    if c.y > self.fb_h + 20:
                        c.claimed = True   # treat as gone
        self.weapon_crates = [c for c in self.weapon_crates if not c.claimed]

        # ---- boss: spawn once the player nears the right edge ----
        if (self.boss is None
                and self.kills >= TOTAL_ENEMIES
                and self.player_x >= self.world_w * 0.78):
            bx = self.world_w - 40
            by = self.ground_y - BOSS_H
            self.boss = Boss(bx, by, -BOSS_PX_PER_SEC)
            self.boss_announced = True
            self.message = "OVERLORD APPROACHES."
            self.message_until = time.time() + 2.0
            play("lose")  # ominous low thud reused

        if self.boss is not None and self.boss.alive:
            b = self.boss
            new_bx = b.x + b.vx * dt
            blocked = False
            for ox, oy, ow, oh in self.obstacles:
                if (new_bx + 2 < ox + ow and new_bx + BOSS_W - 2 > ox
                        and b.y + 4 < oy + oh and b.y + BOSS_H > oy):
                    blocked = True
                    break
            if not blocked:
                lead = new_bx + (BOSS_W if b.vx > 0 else 0)
                if self._in_gap(int(lead)) or lead <= 0 or lead >= self.world_w:
                    blocked = True
            if blocked:
                b.vx = -b.vx
            else:
                b.x = new_bx
            b.anim_t += dt
            # Pellets damage the boss. Use p.hit_targets so PIERCE rounds
            # only count each enemy/boss once even though they survive
            # the impact.
            for p in self.pellets:
                if not p.alive:
                    continue
                if id(b) in p.hit_targets:
                    continue
                if (b.x - 2 <= p.x <= b.x + BOSS_W + 2
                        and b.y + 4 <= p.y <= b.y + BOSS_H):
                    b.hp -= 1
                    b.flash_until = time.time() + 0.10
                    p.hit_targets.add(id(b))
                    if not p.pierce:
                        p.alive = False
                    play("hit")
                    if b.hp <= 0:
                        b.alive = False
                        # Drop the golden SSL cert at the boss's location
                        cert_x = int(b.x + (BOSS_W - 7) // 2)
                        cert_y = int(self.ground_y - 9)
                        self.ssl_cert = SSLCert(cert_x, cert_y)
                        self.message = "ROOT CERT DROPPED!"
                        self.message_until = time.time() + 2.4
                        play("kill")
                    break
            # Boss touches player
            if b.alive:
                pleft  = self.player_x + 2
                pright = self.player_x + 12
                ptop   = self.player_y + 4
                pbot   = self.player_y + len(NERD)
                if (b.x < pright and b.x + BOSS_W > pleft
                        and b.y + 4 < pbot and b.y + BOSS_H > ptop):
                    if time.time() - self.last_hit > HIT_COOLDOWN:
                        self.lives -= 1
                        self.last_hit = time.time()
                        play("hit")

        # ---- weapon timer expires -> revert to default ----
        if time.time() >= self.weapon_until and self.weapon_kind != "DEFAULT":
            self.weapon_kind = "DEFAULT"

        # ---- win/lose ----
        if self.lives <= 0:
            self.state = "lose"
            self.end_message = random.choice(DEATH_QUOTES)
            play("lose")


# ===================================================================
# Render
# ===================================================================
def render_world(fb, world):
    fb.clear(PAL['k'])
    ground_y, wall_top, wall_bot = draw_background(fb, world.camera_x, world.level)
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

    # Torches sprinkled along the back wall (level 1 only -- the indoor
    # levels have rack LEDs / monitors providing their own ambience).
    if world.level == 1:
        torch_h = len(TORCH)
        torch_w = len(TORCH[0])
        for tx_w in world.torches:
            sx = tx_w - cx
            if -torch_w <= sx < fb.w + torch_w:
                ty = wall_top + 4
                for d in range(5):
                    rr = 6 - d
                    for dx in range(-rr, rr + 1):
                        fb.set(sx + 2 + dx, ty + d - 2, PAL['j'])
                fb.blit_sprite(TORCH, sx, ty)

    # Obstacles: stack of sprites. Sprite changes per level (crate / server
    # rack / office chair) but the collision footprint stays the same.
    obstacle_sprite = (CRATE if world.level == 1
                       else SERVER_RACK if world.level == 2
                       else OFFICE_CHAIR)
    crate_h = len(obstacle_sprite)
    for ox, oy, ow, oh in world.obstacles:
        sx = ox - cx
        if -ow <= sx < fb.w + ow:
            n = oh // crate_h
            for i in range(n):
                fb.blit_sprite(obstacle_sprite, sx, oy + i * crate_h)

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
            bob = 0 if pulse < 1 else 1
            fb.blit_sprite(POWERUP_RAPID, sx, p[2] - bob)

    # Drone-delivered weapon crates: each kind has its own sprite (no
    # text label needed -- the icon itself tells you what's inside).
    for wc in world.weapon_crates:
        if wc.claimed:
            continue
        sx = wc.x - cx
        if -8 <= sx < fb.w + 8:
            sprite = (WEAPON_CRATE_RAPID if wc.kind == "RAPID"
                      else WEAPON_CRATE_SPREAD if wc.kind == "SPREAD"
                      else WEAPON_CRATE_PIERCE)
            fb.blit_sprite(sprite, sx, int(wc.y))

    # Golden SSL cert (drops when boss dies; bob + sparkle so it sells)
    if world.ssl_cert is not None and not world.ssl_cert.claimed:
        sc = world.ssl_cert
        sx = sc.x - cx
        if -8 <= sx < fb.w + 8:
            bob = int(round(1.5 * math.sin(time.time() * 5)))
            fb.blit_sprite(SSL_CERT, sx, sc.y + bob)
            # sparkle pixel that orbits
            spark_t = time.time() * 4
            sparkle_x = sx + 3 + int(round(4 * math.cos(spark_t)))
            sparkle_y = sc.y + 4 + bob + int(round(2 * math.sin(spark_t)))
            fb.set(sparkle_x, sparkle_y, PAL['S'])

    # Boss (drawn before regular enemies so they overlap correctly)
    if world.boss is not None and world.boss.alive:
        b = world.boss
        sx = int(b.x) - cx
        if -BOSS_W <= sx < fb.w + BOSS_W:
            surf = _get_overlord_surface(world.level, b.vx < 0)
            if time.time() < b.flash_until:
                # Flash white when hit
                flash = surf.copy()
                flash.fill((255, 255, 255, 200), special_flags=pygame.BLEND_RGBA_MULT)
                fb.surface.blit(flash, (sx, int(b.y)))
            else:
                fb.surface.blit(surf, (sx, int(b.y)))

    # Enemies (back to front by world x). Sprite varies by level:
    # ghouls (L1), CPUs (L2), Karens (L3). Pancakes replaces all of
    # them with black-and-white French bulldogs.
    if world.pancakes_mode:
        enemy_a, enemy_b = FRENCHIE_A, FRENCHIE_B
    else:
        enemy_a, enemy_b = LEVEL_ENEMIES.get(world.level, (ENEMY_A, ENEMY_B))
    for e in sorted(world.enemies, key=lambda e: -e.x):
        if not e.alive:
            continue
        sx = int(e.x) - cx
        if -16 <= sx < fb.w + 16:
            frame = enemy_a if int(e.anim_t * 6) % 2 == 0 else enemy_b
            fb.blit_sprite(frame, sx, int(e.y), flip=(e.vx < 0))

    # Player
    psx = int(world.player_x) - cx
    fb.blit_sprite(NERD, psx, int(world.player_y),
                   flip=not world.player_face_right)

    # Pellets. Pancakes renders a heart sprite; everyone else gets the
    # 2x2 ball + glow + trailing streak (PIERCE rounds use a red core).
    for p in world.pellets:
        if not p.alive:
            continue
        spx = int(p.x) - cx
        py = int(p.y)
        sign = -1 if p.vx > 0 else 1
        if world.pancakes_mode:
            # heart at sprite center (heart is 5x5; offset to align with
            # original 2x2 core anchor)
            fb.blit_sprite(HEART, spx - 2, py - 2)
            # tiny pink streak so the heart's velocity reads
            for d in range(1, 4):
                fb.set(spx + sign * d, py + 1, PAL['M'])
        else:
            glow = PAL['o']
            core = PAL['D'] if p.pierce else PAL['O']
            for d in range(1, 5):
                fb.set(spx + sign * d, py, glow)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                fb.set(spx + dx, py + dy, glow)
            fb.fill_rect(spx, py, 2, 2, core)

    # Delivery drones (drawn after game entities so they hover overhead).
    # Bob the drone +-2 px on a 1.5 Hz sine so it reads as flying, not
    # gliding on a rail.
    for d in world.drones:
        if not d.alive:
            continue
        sx = int(d.x) - cx
        if -12 <= sx < fb.w + 12:
            frame = DRONE_A if int(d.anim_t * 14) % 2 == 0 else DRONE_B
            bob = int(round(2 * math.sin(d.anim_t * 9.0)))
            dy = int(d.y) + bob
            fb.blit_sprite(frame, sx, dy)
            # cable + dangling crate while still carrying
            if d.state == "approaching":
                fb.set(sx + 5, dy + 6, PAL['$'])
                fb.set(sx + 5, dy + 7, PAL['$'])
                fb.set(sx + 6, dy + 6, PAL['$'])
                fb.set(sx + 6, dy + 7, PAL['$'])
                fb.blit_sprite(WEAPON_CRATE, sx + 3, dy + 8)

    # HUD bar (top). Stat is left-aligned, weapon timer right-aligned.
    hud_h = 9
    fb.fill_rect(0, 0, fb.w, hud_h, (10, 16, 12))
    now = time.time()
    weapon_left = max(0.0, world.weapon_until - now) if world.weapon_kind != "DEFAULT" else 0.0
    weapon_text = f"{world.weapon_kind} {weapon_left:.0f}S" if weapon_left > 0 else ""
    weapon_w = (len(weapon_text) * 6 - 1) if weapon_text else 0
    budget = fb.w - 4 - (weapon_w + 4 if weapon_text else 0)

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
    # HUD text always gold -- 'C' was getting overridden to dark navy in
    # the level-3 cubicle palette, making the stats unreadable.
    fb.blit_text(stat, 2, 1, PAL['Y'])
    if weapon_text:
        fb.blit_text(weapon_text, fb.w - weapon_w - 2, 1, PAL['Y'])

    # Boss HP bar (just below the HUD when boss is on-screen)
    if world.boss is not None and world.boss.alive:
        bar_y = hud_h + 1
        bar_w = fb.w - 20
        bar_x = 10
        fb.fill_rect(bar_x - 1, bar_y - 1, bar_w + 2, 5, (60, 60, 80))
        fb.fill_rect(bar_x, bar_y, bar_w, 3, (40, 0, 20))
        filled = int(bar_w * world.boss.hp / BOSS_HP)
        fb.fill_rect(bar_x, bar_y, filled, 3, PAL['I'])
        label = "OVERLORD"
        lw = len(label) * 6 - 1
        fb.blit_text(label, max(2, (fb.w - lw) // 2), bar_y + 5, PAL['I'])

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
    prompt = "R RESTART  -  Q QUIT"
    pw = len(prompt) * 6 - 1
    fb.blit_text(prompt, max(2, (fb.w - pw) // 2), y0 + 28, PAL['Y'])


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

    prompt = "R RESTART  -  Q QUIT"
    pw = len(prompt) * 6 - 1
    fb.blit_text(prompt, max(2, (fb.w - pw) // 2),
                 min(fb.h - 9, prompt_y), border)


# ===================================================================
# Audio (kept from prior version)
# ===================================================================
def play(name):
    """Play a one-shot SFX. Silently no-ops if audio init failed."""
    if not _MIXER_OK:
        return
    snd = _SFX_CACHE.get(name)
    if snd is not None:
        try:
            snd.play()
        except pygame.error:
            pass


def _generate_sfx_wav(spec, path):
    """Synthesize one of the procedural SFX to a WAV file."""
    (f0, f1), dur, mode = spec
    SR = 22050
    n = int(SR * dur)
    samples = array.array("h", [0] * n)
    VOL = 9000
    rng = random.Random(int(f0 * 1000 + dur * 100))
    if mode == "noise":
        # Pitched noise that decays
        for i in range(n):
            env = 1.0 - i / max(1, n - 1)
            s = int(rng.uniform(-1.0, 1.0) * VOL * env * env)
            samples[i] = max(-32767, min(32767, s))
    elif mode == "arp":
        # Quick arpeggio: 4 ascending notes from f0 to f1
        steps = 4
        per = n // steps
        for k in range(steps):
            f = f0 * ((f1 / f0) ** (k / max(1, steps - 1)))
            period = max(2, int(round(SR / f)))
            half = period // 2
            for i in range(per):
                idx = k * per + i
                if idx >= n:
                    break
                env = 1.0 - (i / max(1, per - 1)) * 0.5  # slight per-note decay
                v = VOL if (i % period) < half else -VOL
                samples[idx] = max(-32767, min(32767, int(v * env)))
    elif mode == "bark":
        # Two-note happy chime (B5 -> E6, perfect 4th up). Pure sine +
        # gentle 2nd-harmonic warmth, no noise, no glides. Plays like a
        # phone notification ping. Two prior synths got progressively
        # worse trying to imitate a dog vocally; this one stops trying
        # and just sounds nice.
        note1 = 988.0    # B5
        note2 = 1318.0   # E6 -- perfect 4th up, the universally-happy
                         # interval ('Here Comes the Bride' opener, etc.)
        for i in range(n):
            p = i / max(1, n - 1)
            if p < 0.30:
                freq = note1
                env = max(0.0, 1.0 - p / 0.30) ** 0.4
            elif p < 0.42:
                samples[i] = 0
                continue
            else:
                freq = note2
                env = max(0.0, 1.0 - (p - 0.42) / 0.58) ** 0.4
            t = i / SR
            sample = (math.sin(2 * math.pi * freq * t) * 0.70 +
                      math.sin(2 * math.pi * freq * 2 * t) * 0.20)
            samples[i] = max(-32767, min(32767, int(sample * env * 22000)))
    elif mode == "buzz":
        # Drone propeller, designed to actually sound like a quadcopter:
        #
        #   - 90 Hz fundamental + 100 Hz slightly-detuned partner. The 10 Hz
        #     beat between them gives the chopping motor texture you hear
        #     when standing near a real drone.
        #   - 270 Hz harmonic for the high whine of the rotor blades.
        #   - 1-pole low-passed white noise for air rush.
        #   - Blade-pass envelope: an exponential thump every 1/90 s, so
        #     the sound has 90 Hz "wapwapwap" rhythm sitting on top of
        #     the harmonic content.
        #
        # All component frequencies (90/100/270) divide evenly into 0.5 s
        # so the loop is seamless.
        rng = random.Random(0xD0CE)
        noise_lpf = 0.0
        f1, f2, f3 = 90.0, 100.0, 270.0
        blade_rate = 90.0
        for i in range(n):
            t = i / SR
            s1 = math.sin(2 * math.pi * f1 * t)
            s2 = math.sin(2 * math.pi * f2 * t)
            s3 = math.sin(2 * math.pi * f3 * t)
            raw = rng.uniform(-1.0, 1.0)
            noise_lpf += (raw - noise_lpf) * 0.20    # LPF for 'air rush'
            blade_phase = (t * blade_rate) % 1.0
            blade_env = 0.45 + 0.55 * math.exp(-blade_phase * 5.0)
            sig = (s1 * 0.32 + s2 * 0.24 + s3 * 0.14
                   + noise_lpf * 0.20) * blade_env
            samples[i] = max(-32767, min(32767, int(sig * 22000)))
    else:  # 'down' (or 'up') -- linear pitch glide
        for i in range(n):
            t = i / max(1, n - 1)
            f = f0 + (f1 - f0) * t
            period = max(2, int(round(SR / f)))
            half = period // 2
            env = 1.0 - t
            v = VOL if (i % period) < half else -VOL
            samples[i] = max(-32767, min(32767, int(v * env)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def init_audio():
    """Initialize pygame.mixer, generate the theme + SFX (cached on disk)
    and load them into pygame.mixer.Sound objects. Idempotent.

    On systems where pygame was built without SDL2_mixer support (e.g. on
    Linux when SDL2_mixer-devel wasn't installed at compile time), the
    whole pygame.mixer attribute raises NotImplementedError on access.
    We catch broadly here so the game still runs silently in that case
    instead of crashing on startup."""
    global _MUSIC_PATH, _MIXER_OK
    try:
        pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
        pygame.mixer.init()
        _MIXER_OK = True
    except (pygame.error, NotImplementedError, AttributeError, ImportError):
        sys.stderr.write(
            "rm-rf-em-all: pygame.mixer unavailable -- running silent.\n"
            "  (Install SDL2_mixer dev headers and 'pip install --force-reinstall\n"
            "   --no-binary :all: pygame' to get sound back.)\n"
        )
        return
    tmp = tempfile.gettempdir()
    # Theme
    _MUSIC_PATH = os.path.join(tmp, "rm_rf_em_all_theme_v2.wav")
    if not os.path.exists(_MUSIC_PATH):
        try:
            generate_theme(_MUSIC_PATH)
        except OSError:
            _MUSIC_PATH = None
    # SFX
    for name, spec in SFX_SPECS.items():
        # v4 -- bumped again. The v3 sine-harmonic bark still sounded
        # off, so it's been replaced with a clean two-note ping.
        path = os.path.join(tmp, f"rm_rf_em_all_sfx_{name}_v4.wav")
        if not os.path.exists(path):
            try:
                _generate_sfx_wav(spec, path)
            except OSError:
                continue
        try:
            _SFX_CACHE[name] = pygame.mixer.Sound(path)
            _SFX_CACHE[name].set_volume(0.55)
        except pygame.error:
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


def start_music():
    """Loop the theme via pygame.mixer.music."""
    if not _MIXER_OK or _MUSIC_PATH is None:
        return
    try:
        pygame.mixer.music.load(_MUSIC_PATH)
        pygame.mixer.music.set_volume(0.45)
        pygame.mixer.music.play(loops=-1)
    except (pygame.error, NotImplementedError, AttributeError):
        pass


def stop_music():
    if not _MIXER_OK:
        return
    try:
        pygame.mixer.music.stop()
    except (pygame.error, NotImplementedError, AttributeError):
        pass


# ===================================================================
# Input: pygame events + key state. We translate pygame to the same
# string-based key vocabulary the rest of the code already uses
# ("LEFT", "RIGHT") plus a few new tap names ("JUMP", "SHOOT", "QUIT",
# "ENTER"). The world's tick() reads "LEFT"/"RIGHT" as held state and
# "JUMP"/"SHOOT" as one-frame events.
# ===================================================================
def collect_input():
    """Return (keys, quit). 'keys' is a list mixing held-direction strings
    and one-frame action strings. 'quit' is True if the user closed the
    window or pressed Q / Esc."""
    keys = []
    quit_flag = False
    enter_flag = False

    # Drain event queue so KEYDOWN taps for jump/shoot/quit/enter are caught
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            quit_flag = True
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_q, pygame.K_ESCAPE):
                quit_flag = True
            elif event.key == pygame.K_SPACE:
                keys.append("JUMP")
            elif event.key == pygame.K_x:
                keys.append("SHOOT")
            elif event.key == pygame.K_F11:
                keys.append("TOGGLE_FULLSCREEN")
            elif event.key == pygame.K_r:
                keys.append("RESTART")
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                enter_flag = True
                keys.append("ENTER")

    # Held-state for movement
    pressed = pygame.key.get_pressed()
    if pressed[pygame.K_LEFT] or pressed[pygame.K_a]:
        keys.append("LEFT")
    if pressed[pygame.K_RIGHT] or pressed[pygame.K_d]:
        keys.append("RIGHT")

    return keys, quit_flag, enter_flag


def present(fb, screen):
    """With pygame.SCALED the screen surface IS at INTERNAL_W x INTERNAL_H,
    so we just blit straight in and pygame upscales nearest-neighbor."""
    screen.blit(fb.surface, (0, 0))
    pygame.display.flip()


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

    # CRT scanlines: dim every other row by overlaying a translucent black
    scanline = pygame.Surface((fb.w, 1), pygame.SRCALPHA)
    scanline.fill((0, 0, 0, 50))   # ~20% darkening
    for sy in range(1, fb.h, 2):
        fb.surface.blit(scanline, (0, sy))

    # bezel border (1 pixel)
    border = (60, 60, 100)
    for x in range(fb.w):
        fb.set(x, 0, border)
        fb.set(x, fb.h - 1, border)
    for yy in range(fb.h):
        fb.set(0, yy, border)
        fb.set(fb.w - 1, yy, border)


def splash(fb, screen, clock):
    """Run the pixel-art splash loop. Returns True for ENTER, False for Q
    or window-close."""
    blink_on = True
    last_blink = time.time()
    while True:
        now = time.time()
        if now - last_blink > 0.45:
            blink_on = not blink_on
            last_blink = now

        keys, quit_flag, enter_flag = collect_input()
        if quit_flag:
            return False
        if enter_flag:
            return True

        render_splash(fb, blink_on)
        present(fb, screen)
        clock.tick(TARGET_FPS)


# ===================================================================
# Main
# ===================================================================
def make_screen(fullscreen):
    """Open / re-open the display. With pygame.SCALED, pygame handles
    nearest-neighbor scaling from INTERNAL_W x INTERNAL_H to the actual
    window/display size, so all our drawing stays at the internal
    resolution."""
    flags = pygame.SCALED
    if fullscreen:
        flags |= pygame.FULLSCREEN
    screen = pygame.display.set_mode((INTERNAL_W, INTERNAL_H), flags)
    pygame.display.set_caption(WINDOW_TITLE)
    pygame.mouse.set_visible(False)
    return screen


def main():
    pygame.init()
    init_audio()

    fullscreen = "--windowed" not in sys.argv
    pancakes_mode = ("--pancakes" in sys.argv) or ("--pancakes" in sys.argv)
    screen = make_screen(fullscreen)
    clock = pygame.time.Clock()
    fb = Framebuffer(INTERNAL_W, INTERNAL_H)

    try:
        # ---- splash ----
        start_music()
        cont = splash(fb, screen, clock)
        stop_music()
        if not cont:
            return 0

        # ---- gameplay ----
        world = World(INTERNAL_W, INTERNAL_H)
        world.pancakes_mode = pancakes_mode
        last = time.time()
        while True:
            now = time.time()
            dt = max(0.0, min(1.0 / 20.0, now - last))   # clamp big frame jumps
            last = now

            keys, quit_flag, _ = collect_input()
            # F11 toggles fullscreen
            if "TOGGLE_FULLSCREEN" in keys:
                fullscreen = not fullscreen
                screen = make_screen(fullscreen)
            if quit_flag:
                break
            # R restarts the run from level 1 when the game has ended
            if "RESTART" in keys and world.state in ("win", "lose"):
                _apply_level_palette(1)
                global _SPRITE_CACHE_VERSION
                _SPRITE_CACHE_VERSION += 1
                world = World(INTERNAL_W, INTERNAL_H)
                world.pancakes_mode = pancakes_mode
            if world.state == "playing":
                world.tick(dt, keys)

            render_world(fb, world)
            present(fb, screen)
            clock.tick(TARGET_FPS)
    finally:
        stop_music()
        pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())

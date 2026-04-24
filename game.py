#!/usr/bin/env python3
"""RM -RF 'EM ALL -- a dumb terminal first-person shooter.

A tiny Wolfenstein-3D style raycaster rendered in color ASCII inside your
terminal. Runs locally on macOS. macOS `afplay` provides sound effects using
built-in system sounds, and we generate our own obnoxious 8-bit theme music
at startup (square waves + stdlib `wave` module -- no deps).
"""

import array
import math
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

# ---- game tuning --------------------------------------------------------
FOV = math.pi / 3.0
MAX_DEPTH = 20.0
MOVE_SPEED = 0.18
TURN_SPEED = 0.09
HIT_ANGLE = 0.15
PLAYER_HIT_RADIUS = 0.65
ENEMY_SPEED = 0.025

MAP = [
    "####################",
    "#..................#",
    "#..................#",
    "#....####..........#",
    "#....#...........E.#",
    "#....#....E........#",
    "#..................#",
    "#......E...........#",
    "#..................#",
    "#..................#",
    "#..................#",
    "#.........P........#",
    "#..................#",
    "####################",
]

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
}

# ---- color palette (ANSI truecolor) ------------------------------------
def _fg(r, g, b): return "\x1b[38;2;{};{};{}m".format(r, g, b)
def _bg(r, g, b): return "\x1b[48;2;{};{};{}m".format(r, g, b)

RESET  = "\x1b[0m"
FG_DEF = "\x1b[39m"
BG_DEF = "\x1b[49m"

CEIL_BG  = _bg(18, 22, 48)
FLOOR_BG = _bg(50, 38, 24)
BLACK_BG = _bg(0, 0, 0)
HUD_FG   = _fg(100, 255, 140)
MSG_FG   = _fg(255, 230, 100)
WIN_FG   = _fg(100, 255, 100)
LOSE_FG  = _fg(255, 80, 80)
CROSS_FG = _fg(255, 230, 50)

ENEMY_SPRITE = [
    "  _--_  ",
    " /.__.\\ ",
    " |oo..| ",
    " \\_vv_/ ",
    "  /||\\  ",
    " / || \\ ",
    "   /\\   ",
    "  /  \\  ",
]


def wall_fg(dist):
    if dist < 2.0:  return _fg(230, 205, 160)
    if dist < 5.0:  return _fg(185, 165, 125)
    if dist < 10.0: return _fg(125, 110, 80)
    return _fg(75, 65, 45)


def wall_char(dist):
    if dist < 2.0:  return "\u2588"  # full block
    if dist < 5.0:  return "\u2593"  # dark shade
    if dist < 10.0: return "\u2592"  # medium shade
    return "\u2591"                   # light shade


def enemy_fg(dist):
    if dist < 3.0: return _fg(255, 85, 85)
    if dist < 7.0: return _fg(215, 65, 65)
    return _fg(170, 50, 50)

# ---- audio --------------------------------------------------------------
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
    """Write a palm-muted, tritone-soaked square-wave riff to `path`.

    Death-metal-adjacent (as adjacent as one square-wave voice can get):
      * Low register: E2 root (82 Hz) for gut-punch weight
      * Power-chord feel: root mixed with a perfect fifth
      * Palm-mute envelope: sharp attack, fast decay, simulates chugging
      * Tritones (E vs Bb) for the Slayer-flavored evil
      * Fast 16th-note gallop tempo
    """
    SR = 22050
    NOTE_DUR = 0.085   # ~176 bpm sixteenth notes -- fast gallop
    VOL = 5800
    FIFTH_VOL = 3100   # mixed softer than root

    # Low-octave, chromatic-heavy palette
    NOTES = {
        "E": 82.41,    # E2  (root)
        "F": 87.31,    # F2  (evil minor 2nd)
        "G": 98.00,    # G2  (minor 3rd)
        "A": 110.00,   # A2  (4th)
        "b": 116.54,   # Bb2 (TRITONE, the devil's interval)
        "B": 123.47,   # B2  (5th, for a breath of tonality)
        "C": 130.81,   # C3
        "D": 146.83,   # D3
        "e": 164.81,   # E3  (octave up for bite)
        "f": 174.61,
        "g": 196.00,
        "-": 0.0,
    }

    # Palm-muted gallop riff. Lots of E, chromatic slides, tritone stabs.
    SONG = (
        "EEEFEEEE" "EEEFEEbE"    # gallop + chromatic slide + tritone
        "EEEGEEEE" "EEEbEEbE"    # minor 3rd then tritone wall
        "EFGFEDEF" "bAGFEFGA"    # chromatic runs
        "EEEEEEEE" "EbEbEEEE"    # tremolo + tritone blast
        "eEeEeEeE" "EFEFEFEF"    # octave stabs + minor 2nd
        "bGEbGEbG" "EDEFGFED"    # tritone + chromatic lead
        "EEEEEEEE" "EEbEFGEE"    # tremolo wall
        "EFGFEDEF" "EEEEEEEE"    # descending run + blast finale
    )

    samples = array.array("h")
    for ch in SONG:
        freq = NOTES.get(ch, 0.0)
        n = int(SR * NOTE_DUR)
        if freq <= 0:
            samples.extend([0] * n)
            continue

        # Root square wave
        root_p = max(2, int(round(SR / freq)))
        root_half = root_p // 2
        root_one = [VOL] * root_half + [-VOL] * (root_p - root_half)
        root_full = n // root_p
        root_rem = n - root_full * root_p
        root = root_one * root_full + root_one[:root_rem]

        # Perfect fifth (freq * 1.5) -- gives power-chord weight
        fifth_freq = freq * 1.5
        fifth_p = max(2, int(round(SR / fifth_freq)))
        fifth_half = fifth_p // 2
        fifth_one = [FIFTH_VOL] * fifth_half + [-FIFTH_VOL] * (fifth_p - fifth_half)
        fifth_full = n // fifth_p
        fifth_rem = n - fifth_full * fifth_p
        fifth = fifth_one * fifth_full + fifth_one[:fifth_rem]

        # Palm-mute envelope: fast attack, aggressive decay
        attack_n = max(1, int(n * 0.015))
        decay_floor = 0.22  # decays down to this fraction
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

# ---- terminal helpers ---------------------------------------------------
def get_screen_size():
    try:
        sz = os.get_terminal_size()
        w = max(40, min(120, sz.columns))
        h = max(15, min(40, sz.lines - 1))
        return w, h
    except OSError:
        return 80, 24


def get_keys():
    """Drain every pending keypress. Returns a list (possibly empty).

    Uses raw os.read on the fd (not sys.stdin.read) because TextIOWrapper's
    internal buffering fights with select() and caused dropped arrow keys.
    Handles both `ESC [ X` (normal) and `ESC O X` (application) cursor modes.
    """
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

    # If the buffer ends mid-escape, wait briefly for the rest so we don't
    # split "\x1b[A" across two frames (which would lose the arrow key).
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
                i += 2  # partial "ESC [" -- drop both, don't emit bare "["
                continue
            i += 1  # lone ESC: drop it
        elif b < 128:
            keys.append(chr(b))
            i += 1
        else:
            i += 1
    return keys

# ---- world / game state -------------------------------------------------
class World:
    def __init__(self):
        self.enemies = []
        self.player_x = 1.5
        self.player_y = 1.5
        cleaned = []
        for y, row in enumerate(MAP):
            new_row = ""
            for x, c in enumerate(row):
                if c == "P":
                    self.player_x = x + 0.5
                    self.player_y = y + 0.5
                    new_row += "."
                elif c == "E":
                    self.enemies.append({
                        "x": x + 0.5, "y": y + 0.5, "alive": True,
                    })
                    new_row += "."
                else:
                    new_row += c
            cleaned.append(new_row)
        self.map = cleaned
        self.player_angle = 0.0
        self.message = "Kill 'em all. rm -rf 'em hard."
        self.message_until = time.time() + 3.0
        self.state = "playing"

    def is_wall(self, x, y):
        ix, iy = int(x), int(y)
        if iy < 0 or iy >= len(self.map):
            return True
        row = self.map[iy]
        if ix < 0 or ix >= len(row):
            return True
        return row[ix] == "#"

    def cast_ray(self, px, py, angle, max_dist=MAX_DEPTH):
        # DDA raycaster -- steps cell-to-cell instead of tiny fixed steps.
        # Wolfenstein 3D / Lode Runner style. Much faster than the naive loop.
        dx = math.cos(angle)
        dy = math.sin(angle)
        if abs(dx) < 1e-9: dx = 1e-9 if dx >= 0 else -1e-9
        if abs(dy) < 1e-9: dy = 1e-9 if dy >= 0 else -1e-9

        map_x = int(px)
        map_y = int(py)
        delta_x = abs(1.0 / dx)
        delta_y = abs(1.0 / dy)

        if dx < 0:
            step_x = -1
            side_x = (px - map_x) * delta_x
        else:
            step_x = 1
            side_x = (map_x + 1 - px) * delta_x
        if dy < 0:
            step_y = -1
            side_y = (py - map_y) * delta_y
        else:
            step_y = 1
            side_y = (map_y + 1 - py) * delta_y

        last_side = 0
        for _ in range(200):
            if side_x < side_y:
                side_x += delta_x
                map_x += step_x
                last_side = 0
            else:
                side_y += delta_y
                map_y += step_y
                last_side = 1
            if map_y < 0 or map_y >= len(self.map):
                return max_dist
            row = self.map[map_y]
            if map_x < 0 or map_x >= len(row):
                return max_dist
            if row[map_x] == "#":
                break
        dist = (side_x - delta_x) if last_side == 0 else (side_y - delta_y)
        return min(dist, max_dist)

    def try_move(self, mx, my):
        buf = 0.2
        nx = self.player_x + mx
        ny = self.player_y + my
        check_x = nx + math.copysign(buf, mx) if mx else nx
        check_y = ny + math.copysign(buf, my) if my else ny
        if not self.is_wall(check_x, self.player_y):
            self.player_x = nx
        if not self.is_wall(self.player_x, check_y):
            self.player_y = ny

    def shoot(self):
        play("shoot")
        wall_dist = self.cast_ray(self.player_x, self.player_y, self.player_angle)
        hit = None
        hit_dist = wall_dist
        for e in self.enemies:
            if not e["alive"]:
                continue
            dx, dy = e["x"] - self.player_x, e["y"] - self.player_y
            edist = math.hypot(dx, dy)
            eangle = math.atan2(dy, dx) - self.player_angle
            while eangle > math.pi:
                eangle -= 2 * math.pi
            while eangle < -math.pi:
                eangle += 2 * math.pi
            if abs(eangle) < HIT_ANGLE and edist < hit_dist:
                hit_dist = edist
                hit = e
        if hit:
            hit["alive"] = False
            play("kill")
            self.message = random.choice(KILL_TAUNTS)
            self.message_until = time.time() + 2.5
            if all(not e["alive"] for e in self.enemies):
                self.state = "won"
                self.message = random.choice(WIN_QUOTES)
                play("win")
        else:
            play("miss")
            self.message = "Missed. Your aim is cursed."
            self.message_until = time.time() + 1.5

    def update_enemies(self):
        for e in self.enemies:
            if not e["alive"]:
                continue
            dx = self.player_x - e["x"]
            dy = self.player_y - e["y"]
            dist = math.hypot(dx, dy)
            if dist < PLAYER_HIT_RADIUS:
                self.state = "lost"
                self.message = random.choice(DEATH_QUOTES)
                play("lose")
                return
            if dist > 0.01:
                nx = e["x"] + (dx / dist) * ENEMY_SPEED
                ny = e["y"] + (dy / dist) * ENEMY_SPEED
                if not self.is_wall(nx, e["y"]):
                    e["x"] = nx
                if not self.is_wall(e["x"], ny):
                    e["y"] = ny

# ---- render -------------------------------------------------------------
def render(world, W, H):
    empty = (" ", "", "")
    frame = [[empty] * W for _ in range(H)]
    wall_dists = [0.0001] * W

    for col in range(W):
        ray_angle = world.player_angle - FOV / 2 + (col / W) * FOV
        dist = world.cast_ray(world.player_x, world.player_y, ray_angle)
        dist *= math.cos(ray_angle - world.player_angle)  # fisheye fix
        wall_dists[col] = max(dist, 0.0001)

        wall_h = int(H / max(dist, 0.1))
        wall_h = min(wall_h, H)
        top = max(0, (H - wall_h) // 2)
        bot = min(H, top + wall_h)
        ch = wall_char(dist)
        fg = wall_fg(dist)

        for r in range(H):
            if r < top:
                frame[r][col] = (" ", "", CEIL_BG)
            elif r < bot:
                frame[r][col] = (ch, fg, BLACK_BG)
            else:
                frame[r][col] = (" ", "", FLOOR_BG)

    # Billboard enemies
    visible = []
    for e in world.enemies:
        if not e["alive"]:
            continue
        dx, dy = e["x"] - world.player_x, e["y"] - world.player_y
        edist = math.hypot(dx, dy)
        if edist < 0.1:
            continue
        eangle = math.atan2(dy, dx) - world.player_angle
        while eangle > math.pi:
            eangle -= 2 * math.pi
        while eangle < -math.pi:
            eangle += 2 * math.pi
        if abs(eangle) > FOV / 2 + 0.3:
            continue
        visible.append((edist, eangle, e))
    visible.sort(key=lambda t: -t[0])  # far to near

    src_rows = len(ENEMY_SPRITE)
    src_cols = len(ENEMY_SPRITE[0])
    for edist, eangle, _e in visible:
        col_center = int((eangle + FOV / 2) / FOV * W)
        sprite_h = int(H / edist)
        sprite_w = max(1, int(sprite_h * 0.8))
        sprite_h = min(sprite_h, H)
        sprite_w = min(sprite_w, W)
        top = max(0, (H - sprite_h) // 2)
        bot = min(H, top + sprite_h)
        left = col_center - sprite_w // 2
        right = col_center + sprite_w // 2
        efg = enemy_fg(edist)

        for r in range(top, bot):
            for c in range(max(0, left), min(W, right)):
                if edist >= wall_dists[c]:
                    continue
                sy = int((r - top) / max(1, bot - top) * src_rows)
                sx = int((c - left) / max(1, right - left) * src_cols)
                sy = min(sy, src_rows - 1)
                sx = min(sx, src_cols - 1)
                ch = ENEMY_SPRITE[sy][sx]
                if ch != " ":
                    existing_bg = frame[r][c][2]
                    frame[r][c] = (ch, efg, existing_bg if existing_bg else BLACK_BG)

    # Crosshair
    _ch, _fg_, bg_ = frame[H // 2][W // 2]
    frame[H // 2][W // 2] = ("+", CROSS_FG, bg_ if bg_ else BLACK_BG)

    # HUD top
    alive = sum(1 for e in world.enemies if e["alive"])
    hud = " RM -RF 'EM ALL   Enemies: {}/{}   ARROWS move/turn   SPACE shoot   Q quit".format(
        alive, len(world.enemies)
    )
    for i in range(W):
        ch = hud[i] if i < len(hud) else " "
        frame[0][i] = (ch, HUD_FG, BLACK_BG)

    # Message bottom
    show_msg = time.time() < world.message_until or world.state != "playing"
    if show_msg:
        if world.state == "won":
            msg = " *** YOU WIN. {} Press Q. *** ".format(world.message)
            mfg = WIN_FG
        elif world.state == "lost":
            msg = " *** YOU DIED. {} Press Q. *** ".format(world.message)
            mfg = LOSE_FG
        else:
            msg = " >> {} ".format(world.message)
            mfg = MSG_FG
        msg = msg[:W]
        start = max(0, (W - len(msg)) // 2)
        for i in range(W):
            frame[H - 1][i] = (" ", "", BLACK_BG)
        for i, c in enumerate(msg):
            if start + i < W:
                frame[H - 1][start + i] = (c, mfg, BLACK_BG)

    # Blit, only emitting color changes when they actually change.
    out = ["\x1b[H"]
    cur_fg = None
    cur_bg = None
    for r, row in enumerate(frame):
        out.append("\x1b[{};1H".format(r + 1))
        for c in range(W):
            ch, fg, bg = row[c]
            want_fg = fg if fg else FG_DEF
            want_bg = bg if bg else BG_DEF
            if want_fg != cur_fg:
                out.append(want_fg)
                cur_fg = want_fg
            if want_bg != cur_bg:
                out.append(want_bg)
                cur_bg = want_bg
            out.append(ch)
        if cur_bg != BG_DEF:
            out.append(BG_DEF)
            cur_bg = BG_DEF
        out.append("\x1b[K")
    out.append(RESET)
    sys.stdout.write("".join(out))
    sys.stdout.flush()

# ---- splash screen ------------------------------------------------------
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
    _fg(255, 60, 60),
    _fg(255, 100, 60),
    _fg(255, 150, 60),
    _fg(255, 200, 60),
    _fg(255, 240, 60),
    _fg(255, 240, 60),
    _fg(240, 255, 60),
    _fg(200, 255, 80),
    _fg(150, 255, 120),
    _fg(100, 255, 160),
]

TAGLINE = "A terminal FPS for the terminally online."
SUBLINE = "Built in Python. Runs in your shell. Smells like a burned CPU."
PROMPT  = "[ press ENTER to rm -rf em all   //   press Q to chicken out ]"


def splash(W, H):
    """Show the title screen. Returns True to start the game, False to quit."""
    blink_on = True
    last_blink = time.time()

    while True:
        now = time.time()
        if now - last_blink > 0.45:
            blink_on = not blink_on
            last_blink = now

        out = ["\x1b[H", RESET, BLACK_BG]
        for r in range(H):
            out.append("\x1b[{};1H".format(r + 1))
            out.append(BLACK_BG + " " * W)

        banner_top = max(1, (H - len(BANNER) - 6) // 2)
        for i, line in enumerate(BANNER):
            row = banner_top + i
            if row >= H - 3:
                break
            col = max(0, (W - len(line)) // 2)
            out.append("\x1b[{};{}H".format(row + 1, col + 1))
            out.append(BANNER_COLORS[min(i, len(BANNER_COLORS) - 1)])
            out.append(line)

        tag_row = banner_top + len(BANNER) + 1
        if tag_row < H - 2:
            col = max(0, (W - len(TAGLINE)) // 2)
            out.append("\x1b[{};{}H".format(tag_row + 1, col + 1))
            out.append(_fg(220, 220, 220) + TAGLINE)

        sub_row = tag_row + 1
        if sub_row < H - 2:
            col = max(0, (W - len(SUBLINE)) // 2)
            out.append("\x1b[{};{}H".format(sub_row + 1, col + 1))
            out.append(_fg(140, 140, 140) + SUBLINE)

        prompt_row = sub_row + 2
        if prompt_row >= H:
            prompt_row = H - 1
        col = max(0, (W - len(PROMPT)) // 2)
        out.append("\x1b[{};{}H".format(prompt_row + 1, col + 1))
        if blink_on:
            out.append(_fg(255, 230, 80) + PROMPT)
        else:
            out.append(" " * len(PROMPT))

        out.append(RESET)
        sys.stdout.write("".join(out))
        sys.stdout.flush()

        for k in get_keys():
            if k == "\r" or k == "\n":
                return True
            if k in ("q", "Q"):
                return False
        time.sleep(0.05)

# ---- main ---------------------------------------------------------------
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
        pass  # music is non-essential

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # \x1b[?25l hides cursor; \x1b[?1l forces normal (not application)
        # cursor-key mode so arrow keys send ESC [ X, not ESC O X.
        sys.stdout.write("\x1b[2J\x1b[?25l\x1b[?1l")
        sys.stdout.flush()

        if not splash(*get_screen_size()):
            stop_music()
            return 0

        stop_music()
        sys.stdout.write(RESET + "\x1b[2J")
        sys.stdout.flush()

        world = World()
        W, H = get_screen_size()
        last_enemy_tick = time.time()
        while True:
            nw, nh = get_screen_size()
            if (nw, nh) != (W, H):
                W, H = nw, nh
                sys.stdout.write("\x1b[2J")

            render(world, W, H)

            quit_flag = False
            for k in get_keys():
                if k in ("q", "Q"):
                    quit_flag = True
                    break
                if world.state == "playing":
                    if k == "UP":
                        world.try_move(
                            math.cos(world.player_angle) * MOVE_SPEED,
                            math.sin(world.player_angle) * MOVE_SPEED,
                        )
                    elif k == "DOWN":
                        world.try_move(
                            -math.cos(world.player_angle) * MOVE_SPEED,
                            -math.sin(world.player_angle) * MOVE_SPEED,
                        )
                    elif k == "LEFT":
                        world.player_angle -= TURN_SPEED
                    elif k == "RIGHT":
                        world.player_angle += TURN_SPEED
                    elif k == " ":
                        world.shoot()
            if quit_flag:
                break

            if world.state == "playing":
                now = time.time()
                if now - last_enemy_tick > 0.08:
                    world.update_enemies()
                    last_enemy_tick = now

            time.sleep(0.02)
    finally:
        stop_music()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(RESET + "\x1b[2J\x1b[H\x1b[?25h")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

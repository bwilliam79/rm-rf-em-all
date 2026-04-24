#!/usr/bin/env python3
"""RM -RF 'EM ALL -- a dumb terminal first-person shooter.

A tiny Wolfenstein-3D style raycaster rendered in ASCII inside your terminal.
Runs locally on macOS. macOS `afplay` provides sound effects using built-in
system sounds so there are no audio dependencies to install.
"""

import math
import os
import random
import select
import subprocess
import sys
import termios
import time
import tty

FOV = math.pi / 3.0
MAX_DEPTH = 20.0
MOVE_SPEED = 0.18
TURN_SPEED = 0.09
HIT_ANGLE = 0.15           # how wide a "bullet" is, in radians
PLAYER_HIT_RADIUS = 0.65   # how close an enemy can get before it kills you
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


def get_screen_size():
    try:
        sz = os.get_terminal_size()
        w = max(40, min(120, sz.columns))
        h = max(15, min(40, sz.lines - 1))
        return w, h
    except OSError:
        return 80, 24


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
        dx, dy = math.cos(angle), math.sin(angle)
        d = 0.0
        step = 0.02
        while d < max_dist:
            if self.is_wall(px + dx * d, py + dy * d):
                return d
            d += step
        return max_dist

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


def render(world, W, H):
    frame = [[" "] * W for _ in range(H)]
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

        if dist < 2.0:
            ch = "\u2588"  # full block
        elif dist < 5.0:
            ch = "\u2593"  # dark shade
        elif dist < 10.0:
            ch = "\u2592"  # medium shade
        else:
            ch = "\u2591"  # light shade

        for r in range(H):
            if r < top:
                frame[r][col] = "`" if (r * 7 + col * 3) % 11 == 0 else " "
            elif r < bot:
                frame[r][col] = ch
            else:
                frame[r][col] = "." if (r * 5 + col * 3) % 7 == 0 else " "

    # Billboard the enemies
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
    visible.sort(key=lambda t: -t[0])  # far to near, so near paints on top

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
                    frame[r][c] = ch

    # Crosshair
    frame[H // 2][W // 2] = "+"

    # HUD -- top row
    alive = sum(1 for e in world.enemies if e["alive"])
    hud = " RM -RF 'EM ALL   Enemies: {}/{}   WASD move/turn  SPACE shoot  Q quit".format(
        alive, len(world.enemies)
    )
    for i, ch in enumerate(hud[:W]):
        frame[0][i] = ch

    # Bottom message line
    show_msg = time.time() < world.message_until or world.state != "playing"
    if show_msg:
        if world.state == "won":
            msg = " *** YOU WIN. {} Press Q. *** ".format(world.message)
        elif world.state == "lost":
            msg = " *** YOU DIED. {} Press Q. *** ".format(world.message)
        else:
            msg = " >> {} ".format(world.message)
        msg = msg[:W]
        start = max(0, (W - len(msg)) // 2)
        for i, ch in enumerate(msg):
            if start + i < W:
                frame[H - 1][start + i] = ch

    # Blit to terminal. Position each row explicitly so nothing scrolls.
    out = ["\x1b[H"]
    for r, row in enumerate(frame):
        out.append("\x1b[{};1H".format(r + 1))
        out.append("".join(row))
        out.append("\x1b[K")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def get_key():
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        # Eat the rest of an ANSI escape sequence (arrow keys, etc).
        for _ in range(2):
            if select.select([sys.stdin], [], [], 0.01)[0]:
                sys.stdin.read(1)
        return None
    return ch


def main():
    if not sys.stdin.isatty():
        print("RM -RF 'EM ALL needs a real terminal. Run it directly.", file=sys.stderr)
        return 1

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[2J\x1b[?25l")  # clear screen, hide cursor
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

            k = get_key()
            if k in ("q", "Q"):
                break
            if world.state == "playing":
                if k == "w":
                    world.try_move(
                        math.cos(world.player_angle) * MOVE_SPEED,
                        math.sin(world.player_angle) * MOVE_SPEED,
                    )
                elif k == "s":
                    world.try_move(
                        -math.cos(world.player_angle) * MOVE_SPEED,
                        -math.sin(world.player_angle) * MOVE_SPEED,
                    )
                elif k == "a":
                    world.player_angle -= TURN_SPEED
                elif k == "d":
                    world.player_angle += TURN_SPEED
                elif k == " ":
                    world.shoot()

                now = time.time()
                if now - last_enemy_tick > 0.08:
                    world.update_enemies()
                    last_enemy_tick = now

            time.sleep(0.02)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

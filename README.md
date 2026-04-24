# RM -RF 'EM ALL

A goofy first-person shooter that runs entirely in your terminal.
Think Wolfenstein 3D, rendered in ASCII, with developer humor instead of taste.

## What it is

A minimal raycaster written in pure-stdlib Python. One room, a few enemies that
shuffle toward you, one gun with infinite ammo, and a pile of dumb taunts.
Crude on purpose.

## Requirements

- **macOS** (uses `afplay` + system sounds so there's zero install)
- **Python 3.8+**
- A terminal window at least **40 cols wide x 15 rows tall** (80x24+ recommended)
- A terminal with Unicode + ANSI escape support (basically any modern one: Terminal.app, iTerm2, Ghostty, etc)

## Run it

```bash
python3 game.py
```

## Controls

| Key        | Action           |
|------------|------------------|
| `W` / `S`  | Forward / back   |
| `A` / `D`  | Turn left / right |
| `SPACE`    | Shoot            |
| `Q`        | Quit             |

## How to win

Kill every enemy before they touch you. That's the whole game.

## How to uninstall

```bash
rm -rf em-all
```
(It is literally in the name.)

## Status

v0.1 prototype. Monochrome, one room, single gun, macOS-only audio. Next up
(maybe): color, more enemy types, better sprites, a real gunshot `.wav`,
and cross-platform audio.

# Lyrik

A small always-on-top Linux overlay for Spotify. Keep writing, designing, or coding, and still see **synced lyrics**, **track info**, and **playback controls** without opening Spotify or paying for a Premium in-app overlay.

Lyrik talks to the official Spotify desktop app through [MPRIS](https://wiki.archlinux.org/title/MPRIS) (D-Bus). Lyrics come from [LRCLIB](https://lrclib.net/), not from Spotify — Premium is not required.

## What you get

- Floating overlay that stays above your work
- Current title, artist, album, artwork, and progress
- Play / pause, previous, next, and seeking
- Synced, auto-scrolling lyrics when LRCLIB has them
- Compact mini-player mode when you only need controls
- Optional click-through so the overlay does not steal mouse clicks
- Works on X11; on Wayland install GTK4 layer-shell for a true overlay

## Requirements

- Linux with the Spotify desktop client (or another MPRIS player)
- Python 3.10+ with GObject introspection
- GTK 4
- Don't worry about storage, less than 50 KB (0.05 MB)

Debian / Ubuntu:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
# Wayland overlay (recommended)
sudo apt install gir1.2-gtk4layershell-1.0
```

Use a Python that can import `gi` (GTK). A project venv with access to system packages is already set up:

```bash
.venv/bin/python -m lyrik
```

Or, without the venv:

```bash
/usr/bin/python3 run.py
```

Do not use a random virtualenv — those hide `gi` and will fail. If Cursor still says `No module named lyrik`, pick the interpreter `.venv/bin/python` in this project.

Install a launcher:

```bash
/usr/bin/python3 -m pip install --user -e .
# copy desktop entry and icon if you want it in the app menu
mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
cp lyrik/resources/lyrik.desktop ~/.local/share/applications/
cp lyrik/resources/lyrik.svg ~/.local/share/icons/hicolor/scalable/apps/lyrik.svg
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

## Using it while you work

1. Start Spotify and play a track.
2. Start Lyrik. The overlay sits on top of other windows.
3. Press **C** or use the restore button for a slim control bar.
4. Turn on **click-through** (lock button, or `Ctrl+L`) so clicks pass through the lyrics to your editor. Click the same **green lock button** in the header to unlock. The top bar stays clickable on purpose.

### Keys (overlay focused)

| Key | Action |
| --- | --- |
| Space / K | Play / pause |
| Left / P | Previous |
| Right / N | Next |
| C / Escape | Compact mode |
| Ctrl+L | Click-through |
| Ctrl+Q | Quit |

Settings (opacity, artwork, prefer Spotify) live in the menu and are saved to `~/.config/lyrik/config.json`. Lyrics are cached in `~/.cache/lyrik/`.

## How it works

Spotify on Linux already publishes now-playing data and transport methods on D-Bus. Lyrik never scrapes the Spotify UI and never needs a Spotify API key. If Spotify is closed, Lyrik waits and reconnects. Other MPRIS players can be used if Spotify is not running.

## Troubleshooting

- **“Spotify is not running”** — open the Spotify desktop app, not only a browser tab. Browser playback is not a full MPRIS player in most setups.
- **Overlay sinks under windows on Wayland** — install `gir1.2-gtk4layershell-1.0` and restart Lyrik.
- **`No module named gi`** — you are in a virtualenv. Run with `/usr/bin/python3 -m lyrik`.
- **No lyrics** — LRCLIB may not have the track. Playback controls still work.

## License

MIT

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the keyboard

The keyboard **must** be launched from a terminal opened inside the AnyDesk/desktop session — not from a Claude Code terminal. GTK requires an active X display.

```bash
bash /home/bigboy/onscreen_keyboard/launch.sh
```

`launch.sh` auto-detects `DISPLAY`, `XAUTHORITY`, `DBUS_SESSION_BUS_ADDRESS`, and `AT_SPI_BUS_ADDRESS` (live AT-SPI2 socket from the X11 root window property) before exec-ing `keyboard.py`.

Autostart is configured at `~/.config/autostart/osk.desktop`.

## Architecture

Three files:

- **`keyboard.py`** — GTK3 window + all UI logic
- **`predictor.py`** — dictionary word predictor (no external deps)
- **`style.css`** — GTK CSS, loaded at runtime

### keyboard.py structure

`OnScreenKeyboard(Gtk.Window)` owns everything. Key subsystems:

**Window setup** (`_setup_window`): `DOCK` type hint + `set_accept_focus(False)` + `set_focus_on_map(False)` — this combination keeps compositor popup menus (e.g. Cinnamon app-menu) from closing when keyboard buttons are clicked. No `override_redirect` needed.

**Typing** (`KeyTyper`): AT-SPI2 via `Atspi.generate_keyboard_event()` is the primary method (routes through accessibility framework, doesn't disturb X11 grabs). Falls back to python-xlib XTEST if AT-SPI2 is unavailable. `ATSPI_OK` / `XLIB_OK` flags control which path is used.

**Layout** (`KEY_ROWS`): List of rows, each a list of `(label, action, base_width, css_classes)`. Keys expand to fill the window via `pack_start(..., True, True, 0)`.

**Sticky modifiers**: `ctrl_active` / `alt_active` booleans latch on click (turn blue via `.active` CSS class), auto-release after the next keypress via `_active_sticky_modifiers()` + clear in `_on_key_clicked`.

**Manual resize**: 8-zone edge/corner resize (because the WM doesn't provide resize handles for DOCK windows). Top/bottom 7px `EventBox` strips handle `n/s/nw/ne/sw/se` edges; left/right 8px margins on the window handle `e/w` edges. All tracked via `_resize_start_*` state in `_on_resize_motion`.

**Suggestions**: `predictor.predict(current_word, n=5)` is called after every keypress; results populate 5 `Gtk.Button` widgets in the suggestion bar. `current_word` resets on space/return/backspace-to-empty.

**App launcher** (`_app_mode`): Win/⊞ button toggles a built-in app search (bypasses Cinnamon's menu, which cannot stay open when our keyboard is clicked — see architecture notes). Uses `Gio.AppInfo.get_all()` to enumerate apps. Results replace the suggestion buttons; clicking launches via `app_info.launch()`.

**Settings** (`self.settings`): Persisted to `~/.config/onscreen_keyboard/settings.json`. Options: `theme` (dark/light/midnight/hc), `dwell_enabled`/`dwell_delay` (hover-to-click), `click_sound`. The ⚙ button switches `_key_stack` between "keys" and "settings" pages. Themes applied as CSS classes on the window (`ctx.add_class(theme)`). Dwell uses `enter-notify-event` / `leave-notify-event` on every key button with `GLib.timeout_add`. Click sound is a generated WAV at `/tmp/osk_click.wav` played via `paplay`/`aplay`.

### predictor.py

Loads `/usr/share/dict/american-english`, filters to alpha-only 2–15 char words, sorts into a list. `predict(prefix)` uses `bisect.bisect_left` for O(log n) lookup, scans up to 200 candidates, sorts by `(0 if common else 1, len, word)` to put short common words first.

## GTK CSS constraints

- No `!important` — unsupported in GTK CSS parser
- No `cursor` property — unsupported in GTK CSS
- Use `-gtk-outline-radius` not `outline-radius`
- `.active` class (Windows blue `#0078d4`) is added/removed programmatically for latched modifier keys

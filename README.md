# On-Screen Keyboard

A full-featured on-screen keyboard for Linux and Windows, built with Python 3 and GTK3. Designed for Linux Mint Cinnamon but compatible with any X11 desktop (XFCE, MATE, etc.) and Windows 10/11.

---

## Windows — Download and run

**No installation required.**

1. Go to the [Releases page](https://github.com/Gitties67/Onscreen-keyboard/releases/latest)
2. Download **`OnScreenKeyboard.exe`**
3. Double-click it

That's it. No Python, no GTK, no dependencies to install.

> **First launch takes ~10 seconds** while the app unpacks its resources. Subsequent launches are faster.
>
> **Windows SmartScreen warning:** Click **More info → Run anyway**. This appears because the exe isn't code-signed yet.

---

## Linux

### Requirements

| Package | Purpose | Install |
|---|---|---|
| `python3-gi` | GTK3 bindings | `sudo apt install python3-gi` |
| `python3-xlib` | XTEST key synthesis (primary typing method) | `sudo apt install python3-xlib` |
| `gir1.2-gtk-3.0` | GTK3 GObject introspection | `sudo apt install gir1.2-gtk-3.0` |
| `at-spi2-core` | AT-SPI2 fallback typing | `sudo apt install at-spi2-core` |
| `xdotool` | Emoji insertion | `sudo apt install xdotool` |
| `flameshot` | PrtScn snipping tool | `sudo apt install flameshot` |

### Installation

```bash
git clone git@github.com:Gitties67/Onscreen-keyboard.git ~/onscreen_keyboard
cd ~/onscreen_keyboard
sudo apt install python3-gi python3-xlib gir1.2-gtk-3.0 at-spi2-core xdotool flameshot
bash install.sh
```

`install.sh` creates an app menu entry and configures autostart on login. After that, launch from your app menu — no terminal needed.

### Running manually

```bash
bash ~/onscreen_keyboard/launch.sh
```

Always use `launch.sh` — it auto-detects the X display, Xauthority, and AT-SPI2 bus before starting.

> Do not run `python3 keyboard.py` directly from SSH or a remote terminal — GTK requires an active desktop session.

---

## Features

- **Reliable typing** — XTEST on Linux (works in browsers, terminals, all apps without stealing focus); pynput on Windows
- **Full key layout** — F1–F12, Esc, Del, Home, End, PrtScn, arrows, QWERTY/AZERTY/QWERTZ
- **Word prediction** — 5 suggestions from system dictionary, custom words, and bigram next-word model
- **Spell-check** — fuzzy matching catches common misspellings; correction appears ~250 ms after you pause
- **Emoji panel** — browsable grid with live search; emoji suggestions in the suggestion bar
- **Custom dictionary** — add names, slang, abbreviations; they appear first in suggestions
- **Macros** — trigger → expansion pairs with `{date}` / `{time}` tokens
- **Clipboard history** — 📋 shows last 20 clipboard entries; click any to type it
- **Sticky modifiers** — Ctrl, Alt latch on click; configurable auto-release or persistent toggle
- **DIY theme builder** — 6-step colour wizard with live preview
- **4 built-in themes** — Dark, Light, Midnight, High Contrast
- **Dwell click** — hover-to-click accessibility mode with adjustable delay (0.3–2.0 s)
- **Click sound** — audible feedback on each keypress
- **Adjustable key size, font, opacity** — all live, all remembered on restart
- **Free resize** — drag any edge or corner; keys scale to fit
- **App launcher** — Win/⊞ button opens built-in app search (Linux) or Start menu (Windows)
- **Updates** — built-in update checker in ⚙ Settings

---

## Settings

Click **⚙** in the suggestion bar to open settings.

| Setting | Description |
|---|---|
| **Theme** | Dark / Light / Midnight / High Contrast + custom themes |
| **Layout** | QWERTY / AZERTY / QWERTZ |
| **Font** | Ubuntu / Noto Sans / DejaVu Sans / Monospace |
| **Font size** | 10–22 px, live |
| **Key size** | 0.3× – 1.5×, live (or drag a window edge) |
| **Opacity** | 30% – 100% |
| **Dwell click** | Hover-to-click with adjustable delay |
| **Click sound** | Audible key feedback |
| **Modifier keys** | Auto-release or sticky |
| **Shift key** | One-shot or sticky |
| **Taskbar** | Pin to Cinnamon panel + autostart (Linux) |
| **Custom dictionary** | Add/remove personal words and phrases |
| **Macros** | Trigger → expansion pairs |
| **Updates** | Check for updates; install or download latest version |

Settings saved to `~/.config/onscreen_keyboard/settings.json` (Linux) or `~\.config\onscreen_keyboard\settings.json` (Windows).

---

## Custom dictionary

Add your own words so they appear first in suggestions.

1. Open **⚙ Settings → Custom dictionary → + Add word or phrase**
2. Type your entry using the keys (Shift works normally)
3. Press **↵** to save

**Example:** Add `Dinglebob` — typing `din` will offer it as the first suggestion. Click it and the keyboard backtracks the prefix and types `Dinglebob ` with the correct capital.

Phrases work the same way. Add `See you later` and typing `see` offers the full phrase.

Custom words are stored in `~/.config/onscreen_keyboard/custom_words.json` — you can also edit it directly to bulk-import entries.

---

## Key layout

```
Esc  F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12  Del  Home  End  PrtScn
`  1  2  3  4  5  6  7  8  9  0  -  =  ⌫
Tab  q  w  e  r  t  y  u  i  o  p  [  ]  \
Caps  a  s  d  f  g  h  j  k  l  ;  '  ↵
⇧  z  x  c  v  b  n  m  ,  .  /  ⇧
Ctrl  ⊞  Alt  [         Space         ]  Alt  Ctrl  ←  ↑  ↓  →
```

| Key | Behaviour |
|---|---|
| **⇧ Shift** | One-shot — capitalises next keypress then auto-releases (configurable) |
| **Caps** | Persistent uppercase toggle |
| **Ctrl / Alt** | Sticky — latch on click; release set by Modifier keys setting |
| **⊞ Win** | App launcher (Linux) / opens Start menu (Windows) |
| **😊** | Emoji panel |
| **PrtScn** | Screenshot tool (Linux) |

---

## File structure

```
onscreen_keyboard/
├── keyboard.py          # Main GTK3 window and all UI logic
├── predictor.py         # Dictionary word predictor + bigram model
├── emojis.py            # Emoji data, search, and suggestion ranking
├── style.css            # GTK CSS (layout, themes, transitions)
├── words.txt            # Bundled English dictionary (used by Windows exe)
├── VERSION              # Current version string (read by update checker)
├── launch.sh            # Linux: env setup + launch
├── launch.bat           # Windows: run from source via MSYS2
├── install.sh           # Linux: app menu entry + autostart setup
├── osk.spec             # PyInstaller spec — builds the Windows exe
├── rthook_gtk.py        # PyInstaller runtime hook — sets GTK env vars in exe
└── .github/
    └── workflows/
        └── build-windows.yml   # GitHub Actions: auto-builds exe on every push
```

---

## How the Windows exe is built

Every push to `main` triggers a GitHub Actions build on a Windows runner. It:

1. Installs MSYS2 + GTK3 + Python (MINGW64)
2. Installs PyInstaller and pynput
3. Runs `pyinstaller osk.spec` — bundles everything into a single exe
4. Uploads `OnScreenKeyboard.exe` as a build artifact

When a version tag (`v*`) is pushed, the exe is also attached to a GitHub Release automatically.

**To trigger a release:**

```bash
git tag v0.1.0-beta
git push origin v0.1.0-beta
```

The Actions workflow runs, builds the exe, and publishes it to the Releases page.

---

## Troubleshooting

**Windows: nothing is typed when I press keys**
- Run as Administrator if typing into elevated windows (Task Manager, UAC dialogs)
- pynput cannot inject into some protected system windows — this is a Windows security constraint

**Windows: first launch is slow**
- Normal — the exe is unpacking GTK on first run. Subsequent launches are faster.

**Windows: SmartScreen blocks the exe**
- Click **More info → Run anyway**. The exe is not signed yet.

**Linux: keyboard launches but nothing is typed**
- Ensure `python3-xlib` is installed: `sudo apt install python3-xlib`
- Launch via `launch.sh`, not directly with `python3`

**Linux: emoji buttons do nothing**
- Install xdotool: `sudo apt install xdotool`

**Linux: "Could not connect to a display" error**
- Open a terminal inside your desktop session; do not run from SSH

**Settings not saving**
- Check write permissions: `ls -la ~/.config/onscreen_keyboard/`

---

## License

MIT

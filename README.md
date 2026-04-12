# On-Screen Keyboard

A full-featured on-screen keyboard for Linux, built with Python 3 and GTK3. Designed for Linux Mint Cinnamon but compatible with any X11 desktop (XFCE, MATE, etc.).

---

## Features

- **Reliable typing** via XTEST — works in browsers, terminals, and all GTK/Qt apps without stealing focus
- **Full key layout** — function keys (F1–F12), Esc, Del, Home, End, PrtScn, arrows, and standard QWERTY
- **Lowercase labels** with shifted-symbol hints in the corner of each key
- **Sticky modifiers** — Ctrl and Alt latch on click; configurable auto-release after one keypress or persistent toggle
- **Key repeat** — hold any key to continuously send it (400 ms delay, 50 ms interval)
- **Word prediction** — suggestions drawn from the system dictionary as you type
- **Custom dictionary** — add your own words and phrases (names, slang, abbreviations) that appear first in suggestions
- **Emoji panel** — browsable grid with live search; emoji suggestions appear in the suggestion bar automatically
- **PrtScn snipping tool** — launches the best available screenshot tool (flameshot → gnome-screenshot → fallbacks)
- **4 themes** — Dark, Light, Midnight, High Contrast — with theme-aware active/modifier colours
- **Adjustable font size** — 10–22 px, applied live
- **Dwell click** — hover-to-click accessibility mode with adjustable delay
- **Click sound** — optional audible feedback
- **Drag to move** and **manual resize** via 8-zone edge/corner handles
- **Pin to Cinnamon panel** — one-click shortcut from inside the settings panel
- **Autostart** support via `.desktop` file

---

## Requirements

### Required

| Package | Purpose | Install |
|---|---|---|
| `python3` | Runtime | Usually pre-installed |
| `python3-gi` | GTK3 bindings | `sudo apt install python3-gi` |
| `python3-xlib` | XTEST key synthesis (primary typing method) | `sudo apt install python3-xlib` |
| `gir1.2-gtk-3.0` | GTK3 GObject introspection | `sudo apt install gir1.2-gtk-3.0` |
| `at-spi2-core` | AT-SPI2 fallback typing | `sudo apt install at-spi2-core` |

### Recommended

| Package | Purpose | Install |
|---|---|---|
| `xdotool` | Emoji insertion (Unicode typing) | `sudo apt install xdotool` |
| `xprop` | Reads live AT-SPI2 bus address | `sudo apt install x11-utils` |
| `flameshot` | Best snipping tool experience for PrtScn | `sudo apt install flameshot` |

### Optional screenshot tools (PrtScn fallback chain)

The PrtScn key tries these in order — install whichever you prefer:

```
sudo apt install flameshot          # recommended — annotate before saving
sudo apt install gnome-screenshot   # ships with many distros already
sudo apt install scrot              # lightweight CLI option
```

---

## Installation

### 1. Clone the repository

```bash
git clone git@github.com:Gitties67/Onscreen-keyboard.git ~/onscreen_keyboard
cd ~/onscreen_keyboard
```

### 2. Install dependencies

```bash
sudo apt install python3 python3-gi python3-xlib gir1.2-gtk-3.0 \
                 at-spi2-core xdotool x11-utils flameshot
```

### 3. Make the launch script executable

```bash
chmod +x ~/onscreen_keyboard/launch.sh
```

### 4. Update the path in launch.sh

Open `launch.sh` and update the last line to match where you cloned the repo:

```bash
exec python3 /home/YOUR_USERNAME/onscreen_keyboard/keyboard.py
```

---

## Running

Always launch via `launch.sh` — it auto-detects the X display, Xauthority, D-Bus session address, and AT-SPI2 bus before starting the keyboard.

```bash
bash ~/onscreen_keyboard/launch.sh
```

> **Do not run `python3 keyboard.py` directly from a remote or SSH terminal** — GTK requires an active X display session. Open a terminal inside your desktop and run from there.

---

## Autostart (launch on login)

### Option A — Autostart .desktop file

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/osk.desktop << EOF
[Desktop Entry]
Type=Application
Name=On-Screen Keyboard
Exec=bash /home/$USER/onscreen_keyboard/launch.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
```

The keyboard will now launch automatically whenever you log in.

### Option B — Pin to panel (easiest on Cinnamon)

1. Launch the keyboard manually first
2. Click the **⚙ Settings** button in the suggestion bar
3. Find the **Taskbar** row and click **Pin to panel**

This writes the `.desktop` file to `~/.local/share/applications/` and adds a launcher icon directly to your Cinnamon panel. The panel updates live — no restart needed.

---

## Settings

Click the **⚙** button in the suggestion bar to open the settings panel.

| Setting | Description |
|---|---|
| **Theme** | Dark / Light / Midnight / High Contrast |
| **Font size** | Key label size from 10–22 px, adjusted with − and + buttons, applied live |
| **Dwell click** | Hover-to-click — enable the toggle and set the delay (0.3–2.0 s) |
| **Click sound** | Audible click feedback on each keypress |
| **Modifier keys** | **Auto-release** — Ctrl/Alt clear after one keypress. **Sticky** — stay active until clicked again |
| **Taskbar** | Creates a `.desktop` launcher and pins it to the Cinnamon panel |
| **Custom dictionary** | Add and manage your own words and phrases — see below |

Settings are saved to `~/.config/onscreen_keyboard/settings.json` and restored automatically on next launch.

---

## Custom dictionary

The built-in dictionary covers standard English words, but it won't know your friend's nickname, your company name, a street address, or any other personal vocabulary. The custom dictionary lets you add anything you like.

### Adding a word or phrase

1. Open **⚙ Settings** and scroll to the **Custom dictionary** section
2. Click **+ Add word or phrase**
3. The keyboard switches to capture mode — the suggestion bar shows:
   ```
   + type word/phrase, ↵ to save
   ```
4. Type your entry using the keys. Shift works normally for capitals and symbols
5. Press **↵** to save, or **Esc** / backspace to empty to cancel

**Example:** Your friend is called Dinglebob. Type `D` (Shift + d), `i`, `n`, `g`, `l`, `e`, `b`, `o`, `b`, then press ↵. From now on, typing `din` will show **Dinglebob** as the first suggestion in the bar — click it and the keyboard backtracks the partial prefix and types `Dinglebob` with the correct capital D.

Phrases work the same way. Add `See you later` and typing `see` will offer the full phrase as a suggestion.

### Removing a word

Open **⚙ Settings → Custom dictionary** and click the **×** button next to the entry you want to remove.

### How suggestions work

Custom entries always appear **before** standard dictionary words in the suggestion bar. When you click a custom suggestion:

- The partially typed prefix is automatically backspaced
- The full custom entry is typed with its original casing
- A space is appended

So if you typed `din` and click `Dinglebob`, the result in your document is `Dinglebob ` — not `dinGlebob`.

### Storage

Custom words are saved to `~/.config/onscreen_keyboard/custom_words.json` as a plain JSON array, so you can also edit the file directly if you want to bulk-import entries:

```json
[
  "Dinglebob",
  "See you later",
  "Acme Corp",
  "R2D2"
]
```

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

### Special keys

| Key | Behaviour |
|---|---|
| **⇧ Shift** | One-shot — capitalises next keypress then auto-releases |
| **Caps** | Persistent uppercase toggle |
| **Ctrl / Alt** | Sticky — latch on click; release behaviour set by Modifier keys setting |
| **⊞ Win** | Opens the built-in app launcher — type to search installed apps, click to launch |
| **😊** | Opens the emoji panel |
| **PrtScn** | Launches your installed snipping/screenshot tool |

---

## How typing works

Key events are sent via **XTEST** (python-xlib), which injects keystrokes directly at the X11 level. This is the most compatible approach — it works in browsers (including Google Docs/Chrome), terminals, Electron apps, and all native apps without stealing focus from the target window.

AT-SPI2 is initialised as a fallback for systems where python-xlib is unavailable.

Emoji are typed via `xdotool type --clearmodifiers` which handles arbitrary Unicode codepoints.

---

## File structure

```
onscreen_keyboard/
├── keyboard.py      # Main GTK3 window and all UI logic
├── predictor.py     # Dictionary word predictor (no external deps)
├── emojis.py        # Emoji data, search, and suggestion ranking
├── style.css        # Structural GTK CSS (layout, radii, transitions)
├── launch.sh        # Environment setup script — always use this to launch
└── CLAUDE.md        # Architecture notes for AI-assisted development
```

---

## Troubleshooting

**Keyboard launches but nothing is typed**
- Ensure `python3-xlib` is installed: `sudo apt install python3-xlib`
- Make sure you launched via `launch.sh`, not directly with `python3`

**Emoji buttons do nothing**
- Install xdotool: `sudo apt install xdotool`

**PrtScn button does nothing**
- Install a screenshot tool: `sudo apt install flameshot`

**"Could not connect to a display" error**
- Open a terminal inside your desktop session and run `launch.sh` from there, not from SSH

**Keyboard appears behind other windows**
- This should not happen with the DOCK window type hint; if it does, try toggling keep-above in your window manager

**Settings not saving**
- Check write permissions: `ls -la ~/.config/onscreen_keyboard/`

---

## License

MIT

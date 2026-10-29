# On-Screen Keyboard

> **Heads up — this is a one-person hobby project.** I'm a single person, not a professional developer. This keyboard might be shit. It also might not be shit. Either way, it's free, it's built in my spare time, and bug reports and suggestions are genuinely welcome via [GitHub Issues](https://github.com/Gitties67/Onscreen-keyboard/issues).

### Previously, on On-Screen Keyboard...

A while back, someone tried this keyboard on Windows and summed it up in one word: **"hell"**.

Honestly? Fair. Back then the Windows version was a lone `.exe` with no installer, minimising it froze the whole thing, the suggestions were a little unhinged, and every key press went *BOOP* like a microwave that had just finished your soup.

Since then it has been **heavily edited, fixed, re-fixed, and tested on an actual Windows PC** by its one-person dev team (me) and a small army of very patient robots. As of **v0.1.9**:

| Then ("hell") | Now (hopefully just "heck") |
|---|---|
| A bare `.exe`, good luck | A proper installer: Start menu entry, optional start-at-sign-in, clean uninstall, no admin needed |
| Minimise → frozen keyboard | Minimise and restore actually work, and it shows up in the taskbar like a normal app |
| *BOOP* | A soft click (or a beep, or nothing; your call) |
| Suggestions picked by vibes | Suggestions that read the words before ("how are" → *you*) and learn the words you use, locally |
| Font from the bargain bin | Segoe UI, same as the Windows on-screen keyboard, big and clear |
| "What does this button do?" | A short first-run tour (skippable, replayable from Settings) |
| No resize cursors | Resize arrows on every edge and corner |

If it's still hell for you, please tell me. There's now a **Leave a review** and **Leave a note** button in *⚙ Settings → Feedback*, or go straight to [reviews](https://github.com/Gitties67/Onscreen-keyboard/issues/new?template=review.yml) / [notes](https://github.com/Gitties67/Onscreen-keyboard/issues/new?template=note.yml). Even a one-word review is fine. I know. I've had one.

A full-featured on-screen keyboard for Linux and Windows, built with Python 3 and GTK3. Designed for Linux Mint Cinnamon but compatible with any X11 desktop (XFCE, MATE, etc.) and Windows 10/11.

> **Wayland is not supported.** On Linux the keyboard needs an X11 session (on Ubuntu/Fedora, pick "Xorg" at the login screen).

---

## Windows — Install

**Recommended: the installer. No administrator rights needed.**

1. Go to the [Releases page](https://github.com/Gitties67/Onscreen-keyboard/releases/latest)
2. Download **`OnScreenKeyboard-Setup-<version>.exe`** and run it
3. Choose the optional extras on the Tasks page:
   - **Desktop icon**
   - **Start On-Screen Keyboard when I sign in** (adds a per-user startup entry; removed on uninstall)
4. Finish — tick *Launch On-Screen Keyboard* to start it right away

By default it installs for your user only (`%LOCALAPPDATA%\Programs\OnScreenKeyboard`) and adds a Start Menu entry. Choose "all users" in the first dialog only if you want it in Program Files (that asks for admin rights). Uninstall from **Settings → Apps**. Your settings are kept.

No Python, GTK or other dependencies are needed.

**Portable alternative:** download **`OnScreenKeyboard-portable-<version>.zip`**, extract the *whole folder* anywhere, and run `OnScreenKeyboard.exe` inside it (keep the exe in its folder; it needs the files beside it).

> **Windows SmartScreen warning:** click **More info → Run anyway**. It appears because the files aren't code-signed yet. Checksums are published in `SHA256SUMS.txt` on each release.

**Where settings live:** `%APPDATA%\OnScreenKeyboard` (settings, custom words, themes, macros). Settings from older versions (`~\.config\onscreen_keyboard`) are copied there automatically on first run.

**Updates:** *Settings → Check for updates* downloads the new installer, verifies its SHA-256 against the release's `SHA256SUMS.txt`, installs silently and restarts the keyboard.

**Debug log:** to troubleshoot, quit the keyboard and start it from the Start-menu shortcut **On-Screen Keyboard (debug)**. The log is written to `%LOCALAPPDATA%\OnScreenKeyboard\osk_debug.log`; typed characters are never recorded. To read it in PowerShell: `Get-Content "$env:LOCALAPPDATA\OnScreenKeyboard\osk_debug.log" -Tail 60`. The app is single-instance: launching it again while it runs just brings the running keyboard back.

---

## Linux

### Requirements

| Package | Required? | Purpose |
|---|---|---|
| `python3-gi`, `gir1.2-gtk-3.0` | Required | GTK3 UI |
| `python3-xlib` | Required | XTEST key synthesis (primary typing method) |
| `at-spi2-core`, `gir1.2-atspi-2.0` | Recommended | AT-SPI2 fallback typing |
| `xdotool` | Recommended | Emoji insertion |
| `flameshot` | Optional | PrtScn snipping tool |

### Installation

```bash
git clone https://github.com/Gitties67/Onscreen-keyboard.git
cd Onscreen-keyboard
sudo apt install python3-gi gir1.2-gtk-3.0 python3-xlib at-spi2-core gir1.2-atspi-2.0 xdotool flameshot
bash install.sh
```

`install.sh` creates an app menu entry and configures autostart on login. After that, launch from your app menu — no terminal needed.

### Running manually

```bash
bash path/to/Onscreen-keyboard/launch.sh
```

Always use `launch.sh` — it auto-detects the X display, Xauthority, and AT-SPI2 bus before starting.

> Do not run `python3 keyboard.py` directly from SSH or a remote terminal — GTK requires an active desktop session.

---

## Features

- **Reliable typing** — XTEST on Linux, direct input injection on Windows; never steals focus from the app you're typing into
- **Behaves like a normal Windows app** — taskbar button, real minimise and restore, Windows-style title-bar buttons, resize cursors on every edge
- **First-run tour** — four quick tips on first launch; skip it, or replay it any time from Settings → Help
- **Full key layout** — F1–F12, Esc, Del, Home, End, PrtScn, arrows, QWERTY/AZERTY/QWERTZ
- **Word prediction** — 5 suggestions from the dictionary, your custom words and a phrase model, keeping the capitals you type ("A" → *Alex*, "i" → *I'm*)
- **Australian, UK or US English** — suggestions and spell-check use your region's spelling (*colour* vs *color*); picked automatically, changeable in Settings
- **Context-aware suggestions** — predictions take the words before into account ("thank" → *you*, "how are" → *you*); the context resets at . ! ? and Enter
- **Learns your words** — remembers the words and word pairs you actually use, locally, to rank them higher (can be turned off or erased)
- **Spell-check** — fuzzy matching catches common misspellings; correction appears ~250 ms after you pause
- **Emoji panel** — browsable grid with live search; emoji suggestions in the suggestion bar
- **Custom dictionary** — add names, slang, abbreviations; they appear first in suggestions
- **Macros** — trigger → expansion pairs with `{date}` / `{time}` tokens
- **Clipboard history** — 📋 shows last 20 clipboard entries; click any to type it
- **Clear key feedback** — the key you press briefly flashes a softly inverted colour, so you can see what registered
- **Sticky modifiers** — Ctrl, Alt latch on click; configurable auto-release or persistent toggle
- **DIY theme builder** — 6-step colour wizard with live preview
- **4 built-in themes** — Dark, Light, Midnight, High Contrast
- **Dwell click** — hover-to-click accessibility mode with adjustable delay (0.3–2.0 s)
- **Key sound** — soft click by default; switch to a beep or turn it off in Settings
- **Adjustable key size, font, opacity** — all live, all remembered on restart
- **Free resize** — drag any edge or corner; keys scale to fit
- **App launcher** — Win/⊞ button opens built-in app search (Linux) or Start menu (Windows)
- **Updates** — built-in update checker in ⚙ Settings

---

## Settings

Click **⚙** in the suggestion bar to open settings. Not sure what a setting does? Tap the little **ⓘ** next to its name.

| Setting | Description |
|---|---|
| **Theme** | Dark / Light / Midnight / High Contrast + custom themes |
| **Layout** | QWERTY / AZERTY / QWERTZ |
| **Font** | Windows: Segoe UI (default) / Arial / Verdana / Tahoma / Calibri / Consolas / Comic Sans MS. Linux: Ubuntu (default) / Noto Sans / DejaVu Sans / Roboto / Arial / Courier New |
| **Font size** | 10–22 px (default 18), live |
| **Key size** | 0.3× – 1.5×, live (or drag a window edge) |
| **Opacity** | 30% – 100% |
| **Dictionary** | English (Australia) / English (UK) / English (US); defaults to your system region |
| **Auto space** | On (default) / Off — type a space after a picked suggestion |
| **Dwell click** | Hover-to-click with adjustable delay |
| **Key sound** | Off / Click (default, soft tick) / Beep |
| **Sound volume** | 5% – 100% (default 70%), previews as you drag |
| **Modifier keys** | Auto-release or sticky |
| **Shift key** | One-shot or sticky |
| **Taskbar** | Linux: pin to Cinnamon panel + autostart. Windows: shows how to pin it (Windows doesn't let apps pin themselves; right-click its taskbar button → *Pin to taskbar*) |
| **Tutorial** | Show the first-run tour again |
| **Custom dictionary** | Add/remove personal words and phrases |
| **Learn my words** | On (default) / Off — learn word usage from your typing to improve suggestions |
| **Forget learned words** | Erase everything learned (click twice to confirm) |
| **Macros** | Trigger → expansion pairs |
| **Feedback** | Leave a review (1–5 stars) or a note on GitHub; opens your browser |
| **Updates** | Check for updates; install or download latest version |

Settings saved to `~/.config/onscreen_keyboard/settings.json` (Linux) or `%APPDATA%\OnScreenKeyboard\settings.json` (Windows).

---

## Custom dictionary

Add your own words so they appear first in suggestions.

1. Open **⚙ Settings → Custom dictionary → + Add word or phrase**
2. Type your entry using the keys (Shift works normally)
3. Press **↵** to save

**Example:** Add `Dinglebob` — typing `din` will offer it as the first suggestion. Click it and the keyboard backtracks the prefix and types `Dinglebob ` with the correct capital.

Phrases work the same way. Add `See you later` and typing `see` offers the full phrase.

Custom words are stored in `custom_words.json` in the settings folder (`~/.config/onscreen_keyboard/` on Linux, `%APPDATA%\OnScreenKeyboard\` on Windows) — you can also edit it directly to bulk-import entries.

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

## Learned words

With **⚙ Settings → Data → Learn my words** on (the default), the keyboard counts which words and two/three-word sequences you type, so words you use often rank higher in suggestions.

- **Local only** — nothing is ever sent anywhere. The data is one file, `learned_words.json`, in the settings folder (`~/.config/onscreen_keyboard/` on Linux, `%APPDATA%\OnScreenKeyboard\` on Windows).
- **Words only** — only lowercase alphabetic dictionary/custom words up to 20 letters are counted; numbers, symbols and unknown strings are never stored, and no raw text is kept. Counts are capped and decay over time so the file stays small.
- **Turn it off** — switch **Learn my words** to *Off*: learning stops and the learned data no longer affects suggestions (the file is kept until you erase it).
- **Erase it** — click **Forget learned words** twice (the second click within 3 s confirms), or delete `learned_words.json`.

Note: the keyboard cannot tell when you are typing into a password field. Words next to digits or symbols (`@ / _ - #` and so on, as in emails, usernames and most passwords) are never learned, but a password made of plain dictionary words separated by spaces would be counted like any other words (still words only, never full text).

---

## File structure

```
onscreen_keyboard/
├── keyboard.py          # Main GTK3 window and all UI logic
├── predictor.py         # Dictionary word predictor + phrase model + learned-words model
├── emojis.py            # Emoji data, search, and suggestion ranking
├── style.css            # GTK CSS (layout, themes, transitions)
├── words.txt            # English (US) dictionary
├── words_en_AU.txt      # English (Australia) dictionary (tools/build_dicts.py)
├── words_en_GB.txt      # English (UK) dictionary
├── THIRD_PARTY_NOTICES.txt  # Licences for the bundled word lists and data
├── word_freq.txt        # Word frequency ranking (CC BY-SA 4.0, see Credits)
├── ngrams.txt           # Bigram/trigram phrase model (Tatoeba, CC BY 2.0 FR, see Credits)
├── VERSION              # Current version string (read by update checker)
├── launch.sh            # Linux: env setup + launch
├── launch.bat           # Windows: run from source via MSYS2 (developers)
├── install.sh           # Linux: app menu entry + autostart setup
├── osk.spec             # PyInstaller spec — builds the Windows onedir app
├── rthook_gtk.py        # PyInstaller runtime hook — GTK/fontconfig env vars
├── osk.ico / osk.png    # App icon (generated by tools/make_icon.py)
├── tools/
│   ├── build_ngrams.py      # Builds ngrams.txt from the Tatoeba English sentences export
│   ├── build_dicts.py       # Builds the UK/Australian dictionaries from SCOWL / hunspell
│   ├── make_icon.py         # Regenerates osk.ico / osk.png (needs Pillow)
│   └── gen_version_info.py  # VERSION -> version_info.txt (exe version resource)
├── installer/
│   └── OnScreenKeyboard.iss # Inno Setup 6 script for the Windows installer
└── .github/
    ├── ISSUE_TEMPLATE/      # "Leave a review" / "Leave a note" forms
    └── workflows/
        └── build-windows.yml   # GitHub Actions: build, smoke-test, package
```

---

## How the Windows release is built

Every push to `main` (and any manual *Run workflow*, on any branch) triggers a GitHub Actions build on a Windows runner. It:

1. Installs MSYS2 + GTK3 + Python (MINGW64) and pinned PyInstaller / hooks-contrib
2. Generates `version_info.txt` from `VERSION`
3. Runs `pyinstaller osk.spec` — a **onedir** build in `dist/OnScreenKeyboard/`
4. Smoke-tests it: runs `OnScreenKeyboard.exe --selftest` with MSYS2 removed from `PATH` (builds the full window, runs the GTK loop for ~3 s, must exit 0)
5. Builds the Inno Setup installer, test-installs it, runs the installed app's selftest, uninstalls
6. Produces `OnScreenKeyboard-Setup-<ver>.exe`, `OnScreenKeyboard-portable-<ver>.zip` and `SHA256SUMS.txt`, uploaded as workflow artifacts

When a version tag (`v*`) is pushed, those three files are also attached to a GitHub Release. The tag must match the `VERSION` file.

**To trigger a release:** update `VERSION` and `__version__` in `keyboard.py` (they must match), then:

```bash
git tag v0.1.9
git push origin v0.1.9
```

Run `python tools/gen_version_info.py` locally to check the two versions agree.

---

## Troubleshooting

**Windows: nothing is typed when I press keys**
- Typing into an elevated (administrator) window, Task Manager or UAC prompts is blocked by Windows unless the keyboard is also elevated. Normal apps never need that; start the keyboard as administrator only for a specific elevated app.
- Set `OSK_DEBUG=1` and check `%LOCALAPPDATA%\OnScreenKeyboard\osk_debug.log`.

**Windows: the app does not start**
- Run `OnScreenKeyboard.exe --selftest`; it writes `osk_selftest.log` next to the exe (or in `%TEMP%`) with the error.

**Windows: SmartScreen blocks the installer**
- Click **More info → Run anyway**. The files are not signed yet.

**Linux: keyboard launches but nothing is typed**
- Ensure `python3-xlib` is installed: `sudo apt install python3-xlib`
- Launch via `launch.sh`, not directly with `python3`

**Linux: emoji buttons do nothing**
- Install xdotool: `sudo apt install xdotool`

**Linux: "Could not connect to a display" error**
- Open a terminal inside your desktop session; do not run from SSH

**Settings not saving**
- Check write permissions: `ls -la ~/.config/onscreen_keyboard/` (Linux) or the `%APPDATA%\OnScreenKeyboard` folder (Windows)

---

## License

MIT

## Credits

The dictionaries (`words.txt`, `words_en_AU.txt`, `words_en_GB.txt`) come from [SCOWL](http://wordlist.aspell.net/) by Kevin Atkinson (the Australian list is expanded from the hunspell en_AU dictionary). Copyright 2000-2011 Kevin Atkinson; used under its permissive licence, reproduced in full in `THIRD_PARTY_NOTICES.txt`.

Word suggestions are ranked using [FrequencyWords](https://github.com/hermitdave/FrequencyWords) by Hermit Dave (OpenSubtitles 2018 English list). That data is licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); the derived `word_freq.txt` is distributed under the same license. Regenerate it with `tools/build_word_freq.py`.

Context-aware suggestions use a bigram/trigram phrase model built from English sentences from [Tatoeba](https://tatoeba.org) (eng_sentences export), licensed [CC BY 2.0 FR](https://creativecommons.org/licenses/by/2.0/fr/). `ngrams.txt` contains only aggregate word-sequence counts derived from that data; regenerate it with `tools/build_ngrams.py`.

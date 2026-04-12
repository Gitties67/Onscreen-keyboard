#!/usr/bin/env python3
"""
Custom on-screen keyboard — Windows-style dark theme.
Typed into active window via AT-SPI2 (primary) or python-xlib XTEST (fallback).

Run:  python3 keyboard.py
      bash launch.sh
"""

import glob
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import wave
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Gdk, GLib, Gio

# XTEST — primary method; works reliably with all targets including browsers
XLIB_OK = False
try:
    from Xlib import display as xdisplay, X
    from Xlib.ext import xtest
    XLIB_OK = True
    print("[keyboard] python-xlib available — using XTEST key synthesis.")
except ImportError:
    print("[keyboard] python3-xlib not found — will try AT-SPI2.")
    print("           Install with: sudo apt install python3-xlib")

# AT-SPI2 — fallback only; can segfault with browser targets
ATSPI_OK = False
try:
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    ATSPI_OK = True
    if not XLIB_OK:
        print("[keyboard] AT-SPI2 available — using accessibility key synthesis.")
    else:
        print("[keyboard] AT-SPI2 also available (unused; XTEST is primary).")
except Exception as _e:
    if not XLIB_OK:
        print(f"[keyboard] AT-SPI2 also unavailable ({_e}) — typing disabled.")

from predictor import WordPredictor
from emojis import EMOJI_DATA, search as emoji_search, suggest as emoji_suggest

# xdotool — used for typing emoji (arbitrary Unicode)
XDOTOOL_OK = bool(shutil.which("xdotool"))
if not XDOTOOL_OK:
    print("[keyboard] xdotool not found — emoji typing may not work.")
    print("           Install with: sudo apt install xdotool")

# ── Persistent settings ───────────────────────────────────────────────────────
CONFIG_DIR  = os.path.expanduser("~/.config/onscreen_keyboard")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
CLICK_WAV   = "/tmp/osk_click.wav"

DEFAULT_SETTINGS: dict = {
    "theme":                "dark",   # dark | light | midnight | hc
    "font_size":            14,       # key label font size in px (10–22)
    "dwell_enabled":        False,
    "dwell_delay":          0.8,      # seconds (0.3 – 2.0)
    "click_sound":          False,
    "modifier_auto_release": True,    # release Ctrl/Alt automatically after next keypress
}

THEMES = ["dark", "light", "midnight", "hc"]
THEME_LABELS = {"dark": "Dark", "light": "Light",
                "midnight": "Midnight", "hc": "High Contrast"}

# Each theme defines colours injected as a dynamic CSS provider at runtime.
# Keys: bg, bar_bg, bar_border, key_bg, key_fg, key_border, key_bot,
#       special_bg, special_fg, special_border, special_bot,
#       sugg_fg, sugg_hover, sugg_divider,
#       close_bg, close_fg, close_border, close_bot,
#       settings_bg, settings_fg, choice_bg, choice_fg, choice_border, choice_bot,
#       sublabel_fg
THEME_COLORS: dict[str, dict] = {
    "dark": {
        "bg": "#1c1c1c", "bar_bg": "#141414", "bar_border": "#333333",
        "key_bg": "#2d2d2d", "key_fg": "#f0f0f0",
        "key_border": "#404040", "key_bot": "#111111",
        "key_hover": "#3a3a3a", "key_hover_border": "#555555",
        "special_bg": "#242424", "special_fg": "#b0b0b0",
        "special_border": "#383838", "special_bot": "#0d0d0d",
        "special_hover": "#303030",
        "sugg_fg": "#4fc3f7", "sugg_hover": "#2a2a2a", "sugg_divider": "#2e2e2e",
        "search_fg": "#ffffff",
        "close_bg": "#3a1010", "close_fg": "#ff6b6b",
        "close_border": "#5a1a1a", "close_bot": "#1a0000",
        "close_hover_bg": "#c0392b", "close_hover_fg": "#ffffff",
        "settings_bg": "#2a2a2a", "settings_fg": "#888888",
        "settings_border": "#404040", "settings_bot": "#111111",
        "choice_bg": "#2d2d2d", "choice_fg": "#d0d0d0",
        "choice_border": "#404040", "choice_bot": "#111111",
        "toggle_fg": "#888888",
        "sublabel_fg": "#666666",
        "active_bg": "#0078d4", "active_fg": "#ffffff",
        "active_border": "#005a9e", "active_bot": "#003c6a",
        "active_hover": "#1a8ae0",
    },
    "light": {
        "bg": "#e8e8e8", "bar_bg": "#d4d4d4", "bar_border": "#bbbbbb",
        "key_bg": "#ffffff", "key_fg": "#1a1a1a",
        "key_border": "#c0c0c0", "key_bot": "#999999",
        "key_hover": "#f0f0f0", "key_hover_border": "#aaaaaa",
        "special_bg": "#dcdcdc", "special_fg": "#444444",
        "special_border": "#bbbbbb", "special_bot": "#888888",
        "special_hover": "#ebebeb",
        "sugg_fg": "#0066aa", "sugg_hover": "#cccccc", "sugg_divider": "#bbbbbb",
        "search_fg": "#111111",
        "close_bg": "#f0d0d0", "close_fg": "#cc2200",
        "close_border": "#ddaaaa", "close_bot": "#cc9999",
        "close_hover_bg": "#cc2200", "close_hover_fg": "#ffffff",
        "settings_bg": "#dcdcdc", "settings_fg": "#555555",
        "settings_border": "#bbbbbb", "settings_bot": "#999999",
        "choice_bg": "#ffffff", "choice_fg": "#333333",
        "choice_border": "#c0c0c0", "choice_bot": "#999999",
        "toggle_fg": "#666666",
        "sublabel_fg": "#aaaaaa",
        "active_bg": "#0063b1", "active_fg": "#ffffff",
        "active_border": "#004f8e", "active_bot": "#003570",
        "active_hover": "#1a75c4",
    },
    "midnight": {
        "bg": "#0d1117", "bar_bg": "#090d12", "bar_border": "#1f2937",
        "key_bg": "#1a2332", "key_fg": "#c9d1d9",
        "key_border": "#30404d", "key_bot": "#060a0f",
        "key_hover": "#243040", "key_hover_border": "#4a6080",
        "special_bg": "#111a26", "special_fg": "#7a9ab8",
        "special_border": "#243040", "special_bot": "#040709",
        "special_hover": "#1a2a3a",
        "sugg_fg": "#58a6ff", "sugg_hover": "#1a2a3a", "sugg_divider": "#1f2937",
        "search_fg": "#c9d1d9",
        "close_bg": "#2a0f0f", "close_fg": "#f88",
        "close_border": "#4a1a1a", "close_bot": "#100505",
        "close_hover_bg": "#c0392b", "close_hover_fg": "#ffffff",
        "settings_bg": "#1a2332", "settings_fg": "#7a9ab8",
        "settings_border": "#30404d", "settings_bot": "#060a0f",
        "choice_bg": "#1a2332", "choice_fg": "#c9d1d9",
        "choice_border": "#30404d", "choice_bot": "#060a0f",
        "toggle_fg": "#7a9ab8",
        "sublabel_fg": "#4a6080",
        "active_bg": "#1f6fbd", "active_fg": "#e6f0ff",
        "active_border": "#155090", "active_bot": "#0d3868",
        "active_hover": "#2d82d0",
    },
    "hc": {
        "bg": "#000000", "bar_bg": "#000000", "bar_border": "#ffffff",
        "key_bg": "#000000", "key_fg": "#ffffff",
        "key_border": "#ffffff", "key_bot": "#ffffff",
        "key_hover": "#333333", "key_hover_border": "#ffffff",
        "special_bg": "#000000", "special_fg": "#ffff00",
        "special_border": "#ffff00", "special_bot": "#ffff00",
        "special_hover": "#222200",
        "sugg_fg": "#00ffff", "sugg_hover": "#002222", "sugg_divider": "#ffffff",
        "search_fg": "#ffffff",
        "close_bg": "#000000", "close_fg": "#ff4444",
        "close_border": "#ff4444", "close_bot": "#ff4444",
        "close_hover_bg": "#ff0000", "close_hover_fg": "#ffffff",
        "settings_bg": "#000000", "settings_fg": "#ffffff",
        "settings_border": "#ffffff", "settings_bot": "#ffffff",
        "choice_bg": "#000000", "choice_fg": "#ffffff",
        "choice_border": "#ffffff", "choice_bot": "#ffffff",
        "toggle_fg": "#ffffff",
        "sublabel_fg": "#ffff00",
        "active_bg": "#ffff00", "active_fg": "#000000",
        "active_border": "#ffff00", "active_bot": "#cccc00",
        "active_hover": "#ffff66",
    },
}


def _make_theme_css(c: dict, font_size: int = 14) -> str:
    """Generate a complete colour-override CSS string from a theme dict."""
    special_size  = max(9,  font_size - 2)   # modifier/nav labels slightly smaller
    sublabel_size = max(8,  font_size - 4)   # shift-symbol hint in corner
    return f"""
window {{ background-color: {c['bg']}; }}
#main-box {{ background-color: {c['bg']}; }}
#suggestion-bar {{ background-color: {c['bar_bg']}; border-bottom: 1px solid {c['bar_border']}; }}
.suggestion-btn {{ color: {c['sugg_fg']}; background-color: transparent;
                   font-size: {font_size}px; }}
.suggestion-btn:hover {{ background-color: {c['sugg_hover']}; }}
.suggestion-btn + .suggestion-btn {{ border-left-color: {c['sugg_divider']}; }}
#search-label {{ color: {c['search_fg']}; font-size: {font_size}px; }}
.key-btn {{ background-color: {c['key_bg']}; color: {c['key_fg']};
            border: 1px solid {c['key_border']}; border-bottom: 2px solid {c['key_bot']};
            font-size: {font_size}px; }}
.key-btn:hover {{ background-color: {c['key_hover']}; border-color: {c['key_hover_border']};
                  border-bottom-color: {c['key_bot']}; }}
.key-btn:active {{ background-color: {c['key_bg']}; border-color: {c['key_border']}; }}
.special {{ background-color: {c['special_bg']}; color: {c['special_fg']};
             border-color: {c['special_border']}; border-bottom-color: {c['special_bot']};
             font-size: {special_size}px; }}
.special:hover {{ background-color: {c['special_hover']}; }}
.special:active {{ background-color: {c['special_bg']}; }}
.space {{ background-color: {c['key_bg']}; border-color: {c['key_border']}; }}
#close-btn {{ background-color: {c['close_bg']}; color: {c['close_fg']};
              border: 1px solid {c['close_border']}; border-bottom: 2px solid {c['close_bot']}; }}
#close-btn:hover {{ background-color: {c['close_hover_bg']}; color: {c['close_hover_fg']}; }}
#settings-btn {{ background-color: {c['settings_bg']}; color: {c['settings_fg']};
                 border: 1px solid {c['settings_border']}; border-bottom: 2px solid {c['settings_bot']}; }}
#settings-btn:hover {{ color: {c['key_fg']}; background-color: {c['key_hover']}; }}
#settings-panel {{ background-color: {c['bg']}; }}
#settings-label {{ color: {c['key_fg']}; }}
.settings-choice {{ background-color: {c['choice_bg']}; color: {c['choice_fg']};
                    border: 1px solid {c['choice_border']}; border-bottom: 2px solid {c['choice_bot']}; }}
.settings-choice:hover {{ background-color: {c['key_hover']}; }}
#settings-toggle {{ background-color: {c['choice_bg']}; color: {c['toggle_fg']};
                    border: 1px solid {c['choice_border']}; }}
.key-sublabel {{ color: {c['sublabel_fg']}; font-size: {sublabel_size}px; }}
#emoji-search-label {{ color: {c['search_fg']}; background-color: {c['bar_bg']};
                       border-bottom-color: {c['bar_border']}; }}
.emoji-btn {{ background-color: transparent; }}
.emoji-btn:hover {{ background-color: {c['key_hover']}; border-color: {c['key_border']}; }}
.emoji-btn:active {{ background-color: {c['key_bg']}; }}
.active {{ background-color: {c['active_bg']}; background-image: none;
           color: {c['active_fg']};
           border-color: {c['active_border']}; border-bottom-color: {c['active_bot']}; }}
.active:hover  {{ background-color: {c['active_hover']}; background-image: none; }}
.active:active {{ background-color: {c['active_border']}; background-image: none; }}
#settings-btn.active {{ background-color: {c['active_bg']}; color: {c['active_fg']};
                        border-color: {c['active_border']}; background-image: none; }}
.settings-choice.active {{ background-color: {c['active_bg']}; color: {c['active_fg']};
                            border-color: {c['active_border']}; background-image: none; }}
#settings-toggle:checked {{ background-color: {c['active_bg']}; color: {c['active_fg']};
                             border-color: {c['active_border']}; background-image: none; }}
"""

# ── Keysyms for special keys ──────────────────────────────────────────────────
KEYSYMS = {
    "backspace": 0xFF08,
    "return":    0xFF0D,
    "tab":       0xFF09,
    "left":      0xFF51,
    "up":        0xFF52,
    "right":     0xFF53,
    "down":      0xFF54,
    "space":     0x0020,
    "escape":    0xFF1B,
    "delete":    0xFFFF,
    "shift_l":   0xFFE1,
    "ctrl_l":    0xFFE3,
    "ctrl_r":    0xFFE4,
    "alt_l":     0xFFE9,
    "alt_r":     0xFFEA,
    "super_l":   0xFFEB,
    # Function keys
    "f1":  0xFFBE, "f2":  0xFFBF, "f3":  0xFFC0, "f4":  0xFFC1,
    "f5":  0xFFC2, "f6":  0xFFC3, "f7":  0xFFC4, "f8":  0xFFC5,
    "f9":  0xFFC6, "f10": 0xFFC7, "f11": 0xFFC8, "f12": 0xFFC9,
    # Navigation
    "home":   0xFF50,
    "end":    0xFF57,
    "prtscn": 0xFF61,  # fallback if no snipping tool found
}

SHIFT_MAP = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
    ";": ":", "'": '"', ",": "<", ".": ">", "/": "?",
    "`": "~",
}

# ── Layout constants ─────────────────────────────────────────────────────────
# Base key dimensions — used as the reference for proportional scaling
BASE_KEY_W  = 52
BASE_KEY_H  = 52
SUGGESTION_H = 38   # height of the suggestion bar row
EDGE_ZONE    = 10   # px from window edge that triggers resize

# ── Key repeat ────────────────────────────────────────────────────────────────
REPEAT_DELAY_MS    = 400   # delay before repeat starts (ms)
REPEAT_INTERVAL_MS = 50    # interval between repeated keypresses (ms)
# Actions that should never repeat (they're toggles, not typeable keys)
NON_REPEAT_ACTIONS = {"shift", "caps", "ctrl", "alt", "win"}

# ── Key layout ────────────────────────────────────────────────────────────���───
# Each entry: (display_label, action, base_width, extra_css_classes)
KEY_ROWS = [
    # Row 0 — function keys + navigation
    [
        ("Esc",    "escape", 52, ["special"]),
        ("F1",     "f1",     52, ["special"]), ("F2",  "f2",  52, ["special"]),
        ("F3",     "f3",     52, ["special"]), ("F4",  "f4",  52, ["special"]),
        ("F5",     "f5",     52, ["special"]), ("F6",  "f6",  52, ["special"]),
        ("F7",     "f7",     52, ["special"]), ("F8",  "f8",  52, ["special"]),
        ("F9",     "f9",     52, ["special"]), ("F10", "f10", 52, ["special"]),
        ("F11",    "f11",    52, ["special"]), ("F12", "f12", 52, ["special"]),
        ("Del",    "delete", 52, ["special"]),
        ("Home",   "home",   52, ["special"]),
        ("End",    "end",    52, ["special"]),
        ("PrtScn", "prtscn", 64, ["special"]),
    ],
    # Row 1 — number row
    [
        ("`", "`",  52, []), ("1", "1",  52, []), ("2", "2",  52, []),
        ("3", "3",  52, []), ("4", "4",  52, []), ("5", "5",  52, []),
        ("6", "6",  52, []), ("7", "7",  52, []), ("8", "8",  52, []),
        ("9", "9",  52, []), ("0", "0",  52, []), ("-", "-",  52, []),
        ("=", "=",  52, []), ("⌫", "backspace", 95, ["special"]),
    ],
    # Row 2 — QWERTY
    [
        ("Tab", "tab", 80, ["special"]),
        ("q", "q", 52, []), ("w", "w", 52, []), ("e", "e", 52, []),
        ("r", "r", 52, []), ("t", "t", 52, []), ("y", "y", 52, []),
        ("u", "u", 52, []), ("i", "i", 52, []), ("o", "o", 52, []),
        ("p", "p", 52, []), ("[", "[", 52, []), ("]", "]", 52, []),
        ("\\", "\\", 63, []),
    ],
    # Row 3 — ASDF
    [
        ("Caps", "caps", 92, ["special"]),
        ("a", "a", 52, []), ("s", "s", 52, []), ("d", "d", 52, []),
        ("f", "f", 52, []), ("g", "g", 52, []), ("h", "h", 52, []),
        ("j", "j", 52, []), ("k", "k", 52, []), ("l", "l", 52, []),
        (";", ";", 52, []), ("'", "'", 52, []),
        ("↵", "return", 111, ["special"]),
    ],
    # Row 4 — ZXCV
    [
        ("⇧", "shift", 128, ["special"]),
        ("z", "z", 52, []), ("x", "x", 52, []), ("c", "c", 52, []),
        ("v", "v", 52, []), ("b", "b", 52, []), ("n", "n", 52, []),
        ("m", "m", 52, []), (",", ",", 52, []), (".", ".", 52, []),
        ("/", "/", 52, []),
        ("⇧", "shift", 128, ["special"]),
    ],
    # Row 5 — bottom
    [
        ("Ctrl", "ctrl", 60, ["special"]),
        ("⊞",   "win",  52, ["special"]),
        ("Alt",  "alt",  52, ["special"]),
        ("",     "space", 330, ["space"]),
        ("Alt",  "alt",  52, ["special"]),
        ("Ctrl", "ctrl", 60, ["special"]),
        ("←", "left",  52, ["special"]),
        ("↑", "up",    52, ["special"]),
        ("↓", "down",  52, ["special"]),
        ("→", "right", 52, ["special"]),
    ],
]

# Widest row natural width (row 1): 13×52 + 95 + 13 gaps×3px = 810
BASE_KB_W = 810
# 6 rows × base key height (5 typing rows + 1 function key row)
BASE_KB_H = BASE_KEY_H * 6


# ── Key typer ────────────────────────────────────────────────────────────────
class KeyTyper:
    """
    Sends keystrokes to the active window.

    Primary:  python-xlib XTEST — reliable for all targets including browsers.
    Fallback: AT-SPI2 `Atspi.generate_keyboard_event()` — used only when
              python-xlib is unavailable; AT-SPI2 can crash (native segfault)
              when the target is a browser widget (e.g. Google Docs).
    """

    def __init__(self):
        self._disp = xdisplay.Display() if XLIB_OK else None

    # ── Public API ────────────────────────────────────────────────────────────

    def type_char(self, char: str, mods: list[str] | None = None) -> None:
        keysym = ord(char)
        needs_shift = char.isupper() or char in "!@#$%^&*()_+{}|:\"<>?~"
        if XLIB_OK and self._disp:
            keycode = self._disp.keysym_to_keycode(keysym)
            if keycode == 0 and char.isupper():
                keycode = self._disp.keysym_to_keycode(ord(char.lower()))
            if keycode:
                self._xtest_send(keycode, needs_shift, mods or [])
                return
        if ATSPI_OK:
            self._atspi_send_keysym(keysym, needs_shift, mods or [])

    def send_special(self, name: str, mods: list[str] | None = None) -> None:
        keysym = KEYSYMS.get(name)
        if keysym is None:
            return
        if XLIB_OK and self._disp:
            keycode = self._disp.keysym_to_keycode(keysym)
            if keycode:
                self._xtest_send(keycode, False, mods or [])
                return
        if ATSPI_OK:
            self._atspi_send_keysym(keysym, False, mods or [])

    # ── AT-SPI2 implementation ────────────────────────────────────────────────

    def _atspi_send_keysym(self, keysym: int, with_shift: bool,
                           mods: list[str]) -> None:
        """Synthesise a key via AT-SPI2 accessibility layer."""
        try:
            # Build full modifier list
            all_mods: list[int] = []
            if with_shift:
                all_mods.append(KEYSYMS["shift_l"])
            for name in mods:
                sym = KEYSYMS.get(name)
                if sym:
                    all_mods.append(sym)

            # Press modifiers
            for sym in all_mods:
                Atspi.generate_keyboard_event(sym, None, Atspi.KeySynthType.PRESS)

            # Press+release the key
            Atspi.generate_keyboard_event(keysym, None,
                                          Atspi.KeySynthType.PRESSRELEASE)

            # Release modifiers in reverse order
            for sym in reversed(all_mods):
                Atspi.generate_keyboard_event(sym, None,
                                              Atspi.KeySynthType.RELEASE)

        except Exception as exc:
            print(f"[atspi] Error sending keysym {keysym:#x}: {exc}")

    # ── Emoji (arbitrary Unicode) ─────────────────────────────────────────────

    def type_emoji(self, emoji_str: str) -> None:
        """Type an emoji / arbitrary Unicode string."""
        if XDOTOOL_OK:
            try:
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--", emoji_str],
                    check=False, timeout=3,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        # Fallback: X11 Unicode keysyms (works for most single-codepoint emoji)
        if XLIB_OK and self._disp:
            try:
                for ch in emoji_str:
                    cp = ord(ch)
                    ks = 0x01000000 | cp
                    kc = self._disp.keysym_to_keycode(ks)
                    if kc:
                        xtest.fake_input(self._disp, X.KeyPress,   kc)
                        xtest.fake_input(self._disp, X.KeyRelease, kc)
                self._disp.flush()
            except Exception as exc:
                print(f"[xtest] emoji fallback error: {exc}")

    # ── XTEST fallback ────────────────────────────────────────────────────────

    def _xtest_send(self, keycode: int, with_shift: bool,
                    mods: list[str]) -> None:
        """Press optional modifiers, then the key, then release everything."""
        mod_keycodes: list[int] = []
        if with_shift:
            mod_keycodes.append(self._disp.keysym_to_keycode(KEYSYMS["shift_l"]))
        for mod_name in mods:
            kc = self._disp.keysym_to_keycode(KEYSYMS[mod_name])
            if kc:
                mod_keycodes.append(kc)

        for kc in mod_keycodes:
            xtest.fake_input(self._disp, X.KeyPress, kc)
        xtest.fake_input(self._disp, X.KeyPress,   keycode)
        xtest.fake_input(self._disp, X.KeyRelease, keycode)
        for kc in reversed(mod_keycodes):
            xtest.fake_input(self._disp, X.KeyRelease, kc)
        self._disp.flush()


# ── Main keyboard window ───────────────────���────────────────────────────────���─
class OnScreenKeyboard(Gtk.Window):

    def __init__(self):
        super().__init__()
        self.predictor    = WordPredictor()
        self.typer        = KeyTyper()
        self.current_word = ""
        self.shift_active = False
        self.caps_lock    = False

        # Widget references
        self._letter_btns:     dict[str, Gtk.Button] = {}
        self._shift_btns:      list[Gtk.Button]       = []
        self._caps_btn:        Gtk.Button | None       = None
        self._suggestion_btns: list[Gtk.Button]       = []
        self._modifier_btns:   dict[str, list[Gtk.Button]] = {
            "ctrl": [], "alt": [], "win": []
        }
        self._search_label:    Gtk.Label | None        = None
        self._settings_btn:    Gtk.Button | None       = None
        self._key_stack:       Gtk.Stack | None        = None
        self._theme_btns:      dict[str, Gtk.Button]  = {}
        # Dual-label symbol keys: action → (main_label, sub_label)
        self._symbol_btns:     dict[str, tuple[Gtk.Label, Gtk.Label]] = {}

        # Built-in app launcher (replaces Win→Cinnamon menu, which can't stay
        # open when our keyboard is clicked due to Cinnamon's Clutter-level
        # event capture that fires for ALL external button presses).
        self._app_mode      = False   # True while launcher search is active
        self._app_query     = ""
        self._app_results:  list[Gio.AppInfo] = []

        # Emoji panel (built lazily — avoids FlowBoxChild GdkWindows blocking keys)
        self._emoji_mode         = False
        self._emoji_panel_ready  = False   # True once the panel has been built
        self._emoji_query        = ""
        self._emoji_btn:         Gtk.Button | None  = None
        self._emoji_flowbox:     Gtk.FlowBox | None = None
        self._emoji_label:       Gtk.Label | None   = None
        self._mod_hint_label:    Gtk.Label | None   = None
        self._font_size_label:   Gtk.Label | None   = None
        self._pin_status_label:  Gtk.Label | None   = None
        # Fast lookup for filter func: char → (name, keywords)
        self._emoji_lookup:   dict[str, tuple[str, list[str]]] = {
            char: (name, kws) for char, name, kws in EMOJI_DATA
        }

        # Suggestion bar: what value each slot will type (word or emoji char)
        self._suggestion_values: list[str] = [""] * 5
        self._suggestion_is_emoji: list[bool] = [False] * 5

        # Sticky modifier state (Ctrl / Alt / Win latch until next keypress)
        self.ctrl_active = False
        self.alt_active  = False
        self.win_active  = False

        # Drag-to-move state
        self._drag_active  = False
        self._drag_start_x = 0.0

        # Manual resize state (used because override_redirect disables WM resize)
        self._resizing        = False
        self._resize_edge     = ""
        self._resize_start_x  = 0.0
        self._resize_start_y  = 0.0
        self._resize_start_w  = 0
        self._resize_start_h  = 0
        self._resize_start_wx = 0
        self._resize_start_wy = 0
        self._drag_start_y = 0.0
        self._drag_win_x   = 0
        self._drag_win_y   = 0

        # Key repeat state
        self._repeat_timers: dict[str, int] = {}  # action → GLib source ID
        self._repeat_active: set[str]        = set()  # actions in fast-repeat phase

        # Settings + dwell state
        self.settings:       dict = self._load_settings()
        self._dwell_timers:  dict[str, int] = {}  # action → GLib source ID
        self._settings_mode: bool = False

        self._setup_window()
        self._apply_css()
        self._build_ui()
        self._apply_theme(self.settings["theme"], save=False)
        self._init_click_sound()
        self.show_all()

    # ── Window setup ──────────────────────────���──────────────────────────────

    def _setup_window(self):
        self.set_title("OSK")
        self.set_decorated(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        # DOCK tells the compositor this is a panel/OSK — it won't steal focus
        # and clicks on it won't trigger "outside-grab" detection in popups
        # (e.g. the Cinnamon app-menu search bar stays open when we type).
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_resizable(True)

        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo     = monitor.get_geometry()
        sw, sh  = geo.width, geo.height

        kb_h = BASE_KEY_H * 6 + SUGGESTION_H + 7 * 4 + 12
        self.set_default_size(sw, kb_h)
        self.move(0, sh - kb_h)
        self.connect("destroy", Gtk.main_quit)

        # No override_redirect — DOCK type hint only works when Muffin actually
        # manages the window.  With override_redirect the WM ignores the DOCK
        # hint entirely, and Clutter treats our window as unmanaged (causing
        # menu close events to fire as if we clicked on the stage background).

        # Window-level events for left/right edge resize (8px margins)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("button-press-event",   self._on_window_button_press)
        self.connect("motion-notify-event",  self._on_resize_motion)
        self.connect("button-release-event", self._on_resize_release)

    # ── CSS ──────────────────────────────────────────────────────────────────

    def _apply_css(self):
        # Base structural styles (layout, radii, transitions — no colours)
        base = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
        base.load_from_path(css_path)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), base,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        # Theme colour provider — loaded/reloaded whenever the theme changes
        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

    # ── UI construction ────────────────────────────���──────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        root.set_name("main-box")
        root.set_margin_start(8)   # left/right margins act as resize zones
        root.set_margin_end(8)
        root.set_margin_top(0)     # resize strips cover top/bottom instead
        root.set_margin_bottom(0)
        self.add(root)

        # Top resize strip — covers top edge + corners
        root.pack_start(self._make_resize_strip("top"), False, False, 0)

        root.pack_start(self._build_suggestion_bar(), False, False, 0)

        # Stack switches between the key grid and the settings panel
        self._key_stack = Gtk.Stack()
        self._key_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._key_stack.set_transition_duration(120)

        keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for row_def in KEY_ROWS:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            for label, action, width, extra_classes in row_def:
                btn = self._make_key(label, action, width, extra_classes)
                row_box.pack_start(btn, True, True, 0)
            keys_box.pack_start(row_box, True, True, 0)

        self._key_stack.add_named(keys_box,                   "keys")
        self._key_stack.add_named(self._build_settings_panel(), "settings")
        # Emoji panel is built lazily in _open_emoji_mode() to avoid
        # FlowBoxChild GdkWindows (created during show_all) blocking key clicks.
        root.pack_start(self._key_stack, True, True, 0)

        # Bottom resize strip — covers bottom edge + corners
        root.pack_start(self._make_resize_strip("bottom"), False, False, 0)

    def _make_resize_strip(self, position: str) -> Gtk.EventBox:
        """Thin strip at top or bottom — handles edge/corner resize manually."""
        strip = Gtk.EventBox()
        strip.set_name(f"resize-strip-{position}")
        strip.set_size_request(-1, 7)
        strip.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        strip.connect("button-press-event",   self._on_strip_press,   position)
        strip.connect("motion-notify-event",  self._on_resize_motion)
        strip.connect("button-release-event", self._on_resize_release)
        return strip

    def _on_strip_press(self, widget, event, position: str):
        if event.button != 1:
            return False
        w, _ = self.get_size()
        left_corner  = event.x < EDGE_ZONE
        right_corner = event.x > w - EDGE_ZONE
        if position == "top":
            edge = "nw" if left_corner else ("ne" if right_corner else "n")
        else:
            edge = "sw" if left_corner else ("se" if right_corner else "s")
        self._start_resize(event, edge)
        return True

    def _build_suggestion_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.set_name("suggestion-bar")

        # Drag handle
        drag = Gtk.EventBox()
        drag.set_name("drag-handle")
        drag.add(Gtk.Label(label="⠿"))
        drag.set_size_request(28, SUGGESTION_H)
        drag.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        drag.connect("button-press-event",   self._on_drag_press)
        drag.connect("button-release-event", self._on_drag_release)
        drag.connect("motion-notify-event",  self._on_drag_motion)
        bar.pack_start(drag, False, False, 4)

        # Search label — shown only in app-launcher mode
        self._search_label = Gtk.Label(label="")
        self._search_label.set_name("search-label")
        self._search_label.set_xalign(0.0)
        self._search_label.set_no_show_all(True)
        bar.pack_start(self._search_label, False, False, 6)

        # Suggestion / app-result buttons (index passed so click handler knows slot)
        for i in range(5):
            btn = Gtk.Button(label="")
            btn.get_style_context().add_class("suggestion-btn")
            btn.set_size_request(-1, SUGGESTION_H)
            btn.connect("clicked", lambda b, idx=i: self._on_suggestion_clicked(b, idx))
            bar.pack_start(btn, True, True, 0)
            self._suggestion_btns.append(btn)

        # Emoji toggle button
        self._emoji_btn = Gtk.Button(label="😊")
        self._emoji_btn.set_name("settings-btn")   # reuse settings-btn style
        self._emoji_btn.set_size_request(36, SUGGESTION_H)
        self._emoji_btn.connect("clicked", lambda _: self._toggle_emoji_mode())
        bar.pack_end(self._emoji_btn, False, False, 2)

        # Settings (gear) button
        self._settings_btn = Gtk.Button(label="⚙")
        self._settings_btn.set_name("settings-btn")
        self._settings_btn.set_size_request(36, SUGGESTION_H)
        self._settings_btn.connect("clicked", lambda _: self._toggle_settings())
        bar.pack_end(self._settings_btn, False, False, 2)

        # Close button
        close_btn = Gtk.Button(label="✕")
        close_btn.set_name("close-btn")
        close_btn.set_size_request(40, SUGGESTION_H)
        close_btn.connect("clicked", lambda _: Gtk.main_quit())
        bar.pack_end(close_btn, False, False, 4)

        return bar

    def _make_key(self, label: str, action: str, width: int,
                  extra_classes: list[str]) -> Gtk.Button:
        is_special = "special" in extra_classes or "space" in extra_classes
        shift_sym = None if is_special else SHIFT_MAP.get(action)

        btn = Gtk.Button()
        if shift_sym:
            # Dual-label: main char centred + shift-char hint floating top-right
            overlay = Gtk.Overlay()
            main_lbl = Gtk.Label(label=label)
            main_lbl.set_halign(Gtk.Align.CENTER)
            main_lbl.set_valign(Gtk.Align.CENTER)
            overlay.add(main_lbl)

            sub_lbl = Gtk.Label(label=shift_sym)
            sub_lbl.get_style_context().add_class("key-sublabel")
            sub_lbl.set_halign(Gtk.Align.END)
            sub_lbl.set_valign(Gtk.Align.START)
            overlay.add_overlay(sub_lbl)
            overlay.set_overlay_pass_through(sub_lbl, True)

            btn.add(overlay)
            self._symbol_btns[action] = (main_lbl, sub_lbl)
        else:
            btn.set_label(label)

        ctx = btn.get_style_context()
        ctx.add_class("key-btn")
        for cls in extra_classes:
            ctx.add_class(cls)

        btn.set_size_request(width, BASE_KEY_H)
        btn.connect("clicked", self._on_key_clicked, action)

        # Key repeat — press starts the timer; release cancels it
        btn.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                       Gdk.EventMask.BUTTON_RELEASE_MASK)
        btn.connect("button-press-event",   self._on_key_press_event,   action)
        btn.connect("button-release-event", self._on_key_release_event, action)

        # Dwell (hover-to-click) — events always connected; only active when enabled
        btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK |
                       Gdk.EventMask.LEAVE_NOTIFY_MASK)
        btn.connect("enter-notify-event", self._on_dwell_enter, action)
        btn.connect("leave-notify-event", self._on_dwell_leave, action)

        if len(action) == 1 and action.isalpha():
            self._letter_btns[action.lower()] = btn
        if action == "shift":
            self._shift_btns.append(btn)
        elif action == "caps":
            self._caps_btn = btn
        elif action in ("ctrl", "alt", "win"):
            self._modifier_btns[action].append(btn)

        return btn

    # ── Edge/corner resize ─────────────────────────────��──────────────────────


    def _on_window_button_press(self, window, event):
        """Handle left/right edge resize from the 8px side margins."""
        if event.button != 1:
            return False
        w, h = self.get_size()
        E = EDGE_ZONE
        left  = event.x < E
        right = event.x > w - E
        if not (left or right):
            return False
        top    = event.y < E
        bottom = event.y > h - E
        if left:
            edge = "nw" if top else ("sw" if bottom else "w")
        else:
            edge = "ne" if top else ("se" if bottom else "e")
        self._start_resize(event, edge)
        return True

    # ── Manual resize ─────────────────────────────────────────────────────────

    def _start_resize(self, event, edge: str):
        self._resizing        = True
        self._resize_edge     = edge
        self._resize_start_x  = event.x_root
        self._resize_start_y  = event.y_root
        self._resize_start_w, self._resize_start_h = self.get_size()
        self._resize_start_wx, self._resize_start_wy = self.get_position()

    def _on_resize_motion(self, widget, event):
        if not self._resizing:
            return
        dx = event.x_root - self._resize_start_x
        dy = event.y_root - self._resize_start_y
        edge = self._resize_edge
        w  = self._resize_start_w
        h  = self._resize_start_h
        wx = self._resize_start_wx
        wy = self._resize_start_wy

        if "e" in edge:
            w = max(400, w + int(dx))
        if "w" in edge:
            new_w = max(400, w - int(dx))
            wx = wx + (w - new_w)
            w = new_w
        if "s" in edge:
            h = max(200, h + int(dy))
        if "n" in edge:
            new_h = max(200, h - int(dy))
            wy = wy + (h - new_h)
            h = new_h

        self.resize(w, h)
        if "w" in edge or "n" in edge:
            self.move(wx, wy)

    def _on_resize_release(self, widget, event):
        self._resizing    = False
        self._resize_edge = ""

    # ── Drag to move ──────────────────────────────────────────────────────────

    def _on_drag_press(self, widget, event):
        if event.button == 1:
            self._drag_active  = True
            self._drag_start_x = event.x_root
            self._drag_start_y = event.y_root
            self._drag_win_x, self._drag_win_y = self.get_position()

    def _on_drag_release(self, widget, event):
        self._drag_active = False

    def _on_drag_motion(self, widget, event):
        if self._drag_active:
            dx = event.x_root - self._drag_start_x
            dy = event.y_root - self._drag_start_y
            self.move(int(self._drag_win_x + dx), int(self._drag_win_y + dy))

    # ── Key event handling ───────────────────────────────────────────────────

    def _on_key_press_event(self, _widget, event, action: str):
        """Start the key-repeat timer when a button is held down."""
        if event.button != 1 or action in NON_REPEAT_ACTIONS:
            return False
        self._repeat_stop(action)

        def _begin_repeat():
            # Switch from initial-delay phase to fast-repeat phase
            self._repeat_active.add(action)
            self._repeat_timers[action] = GLib.timeout_add(
                REPEAT_INTERVAL_MS, _tick)
            return False  # remove the delay source

        def _tick():
            if action not in self._repeat_active:
                return False
            self._handle_key(action)
            return True

        self._repeat_timers[action] = GLib.timeout_add(
            REPEAT_DELAY_MS, _begin_repeat)
        return False  # let the click event propagate normally

    def _on_key_release_event(self, _widget, event, action: str):
        """Cancel the repeat timer when the button is released."""
        if event.button == 1:
            self._repeat_stop(action)
        return False

    def _repeat_stop(self, action: str):
        """Cancel any pending repeat timer for this action."""
        tid = self._repeat_timers.pop(action, None)
        if tid is not None:
            GLib.source_remove(tid)
        self._repeat_active.discard(action)

    def _on_key_clicked(self, _btn, action: str):
        # When the repeat is active for this key, the GTK 'clicked' signal fires
        # on release — ignore it since repeat is already handling the key.
        if action in self._repeat_active:
            return
        self._handle_key(action)

    def _handle_key(self, action: str):
        self._play_click_sound()
        if action in ("ctrl", "alt"):
            self._toggle_modifier(action)
            return

        if action == "win":
            self._toggle_app_mode()
            return

        if action == "shift":
            self.shift_active = not self.shift_active
            self._update_modifier_visuals()
            return

        if action == "caps":
            self.caps_lock = not self.caps_lock
            self._update_modifier_visuals()
            return

        # ── Emoji mode — keys filter the emoji grid ──────────────────────────
        if self._emoji_mode:
            if action == "backspace":
                if self._emoji_query:
                    self._emoji_query = self._emoji_query[:-1]
                    self._update_emoji_search()
                else:
                    self._close_emoji_mode()
            elif action in ("escape", "return"):
                self._close_emoji_mode()
            elif len(action) == 1 and action.isalpha():
                self._emoji_query += action.lower()
                self._update_emoji_search()
            return

        # ── App launcher mode — keys type into the search query ──────────────
        if self._app_mode:
            if action == "backspace":
                self._app_query = self._app_query[:-1]
            elif action == "escape":
                self._close_app_mode()
                return
            elif action == "return":
                # Launch first result if available
                if self._app_results:
                    self._launch_app(self._app_results[0])
                    self._close_app_mode()
                return
            elif len(action) == 1 and (action.isalpha() or action.isdigit()):
                char = action.upper() if (self.shift_active ^ self.caps_lock) else action.lower()
                self._app_query += char
                if self.shift_active:
                    self.shift_active = False
                    self._update_modifier_visuals()
            self._refresh_app_results()
            return

        # ── Normal typing ────────────────────────────────────────────────────
        mods = self._active_sticky_modifiers()

        if action == "backspace":
            self.typer.send_special("backspace", mods)
            if not mods and self.current_word:
                self.current_word = self.current_word[:-1]
            elif mods:
                self.current_word = ""
            self._refresh_suggestions()

        elif action == "return":
            self.typer.send_special("return", mods)
            self.current_word = ""
            self._refresh_suggestions()

        elif action == "space":
            self.typer.send_special("space", mods)
            self.current_word = ""
            self._refresh_suggestions()

        elif action == "tab":
            self.typer.send_special("tab", mods)

        elif action == "prtscn":
            self._launch_snipping_tool()

        elif action in ("left", "right", "up", "down", "escape", "delete",
                        "home", "end",
                        "f1", "f2", "f3", "f4", "f5", "f6",
                        "f7", "f8", "f9", "f10", "f11", "f12"):
            self.typer.send_special(action, mods)

        else:
            self._type_character(action, mods)

        if mods:
            if self.settings.get("modifier_auto_release", True):
                # Auto-release: all modifiers clear after one keypress (one-shot)
                self.ctrl_active = False
                self.alt_active  = False
            else:
                # Persistent: Ctrl stays latched; only Alt clears
                self.alt_active = False
            self.win_active = False
            self._update_modifier_visuals()

    # ── Built-in app launcher ─────────────────────────────────────────────────

    def _toggle_app_mode(self):
        if self._app_mode:
            self._close_app_mode()
        else:
            if self._emoji_mode:
                self._close_emoji_mode()
            if self._settings_mode:
                self._toggle_settings()
            self._open_app_mode()

    # ── Snipping tool ─────────────────────────────────────────────────────────

    def _launch_snipping_tool(self):
        """Launch the best available screenshot/snipping tool for region capture."""
        # Ordered by preference: flameshot has the best UX, then gnome-screenshot,
        # then lighter alternatives. Fall back to sending the raw PrtScn key.
        candidates = [
            ["flameshot",          "gui"],
            ["gnome-screenshot",   "--interactive"],
            ["xfce4-screenshooter","--region"],
            ["mate-screenshot",    "--area"],
            ["spectacle",          "--region", "--gui"],
            ["scrot",              "--select", "--freeze"],
        ]
        for cmd in candidates:
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(
                        cmd, close_fds=True, start_new_session=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception as exc:
                    print(f"[snip] Failed to launch {cmd[0]}: {exc}")
                return
        # No tool found — fall back to the raw Print Screen key
        self.typer.send_special("prtscn")

    # ── Settings ─────────────────────────────────────────────────────────────

    @staticmethod
    def _load_settings() -> dict:
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            # Fill in any missing keys with defaults
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            return dict(DEFAULT_SETTINGS)

    def _save_settings(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as exc:
            print(f"[settings] Could not save: {exc}")

    def _toggle_settings(self):
        self._settings_mode = not self._settings_mode
        if self._settings_mode:
            self._key_stack.set_visible_child_name("settings")
            if self._settings_btn:
                self._settings_btn.get_style_context().add_class("active")
        else:
            self._key_stack.set_visible_child_name("keys")
            if self._settings_btn:
                self._settings_btn.get_style_context().remove_class("active")

    def _build_settings_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_name("settings-panel")
        panel.set_margin_start(16)
        panel.set_margin_end(16)
        panel.set_margin_top(8)
        panel.set_margin_bottom(8)

        # ── Theme row ──────────────────────────────────────────────────────
        theme_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label="Theme")
        lbl.set_name("settings-label")
        lbl.set_xalign(0.0)
        lbl.set_size_request(90, -1)
        theme_row.pack_start(lbl, False, False, 0)

        for key in THEMES:
            btn = Gtk.Button(label=THEME_LABELS[key])
            btn.get_style_context().add_class("settings-choice")
            btn.connect("clicked", self._on_theme_clicked, key)
            theme_row.pack_start(btn, True, True, 0)
            self._theme_btns[key] = btn

        panel.pack_start(theme_row, False, False, 0)

        # ── Dwell row ──────────────────────────────────────────────────────
        dwell_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl2 = Gtk.Label(label="Dwell click")
        lbl2.set_name("settings-label")
        lbl2.set_xalign(0.0)
        lbl2.set_size_request(90, -1)
        dwell_row.pack_start(lbl2, False, False, 0)

        dwell_toggle = Gtk.ToggleButton(label="Off")
        dwell_toggle.set_name("settings-toggle")
        dwell_toggle.set_size_request(60, -1)
        dwell_toggle.set_active(self.settings["dwell_enabled"])
        dwell_toggle.set_label("On" if self.settings["dwell_enabled"] else "Off")
        dwell_toggle.connect("toggled", self._on_dwell_toggled, dwell_toggle)
        dwell_row.pack_start(dwell_toggle, False, False, 0)

        lbl3 = Gtk.Label(label="Delay:")
        lbl3.set_name("settings-label")
        dwell_row.pack_start(lbl3, False, False, 6)

        dec_btn = Gtk.Button(label="−")
        dec_btn.get_style_context().add_class("settings-choice")
        dec_btn.set_size_request(36, -1)
        dec_btn.connect("clicked", self._on_dwell_delay, -0.1)
        dwell_row.pack_start(dec_btn, False, False, 0)

        self._dwell_label = Gtk.Label(
            label=f"{self.settings['dwell_delay']:.1f}s")
        self._dwell_label.set_name("settings-label")
        self._dwell_label.set_size_request(42, -1)
        dwell_row.pack_start(self._dwell_label, False, False, 0)

        inc_btn = Gtk.Button(label="+")
        inc_btn.get_style_context().add_class("settings-choice")
        inc_btn.set_size_request(36, -1)
        inc_btn.connect("clicked", self._on_dwell_delay, +0.1)
        dwell_row.pack_start(inc_btn, False, False, 0)

        panel.pack_start(dwell_row, False, False, 0)

        # ── Sound row ──────────────────────────────────────────────────────
        sound_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl4 = Gtk.Label(label="Click sound")
        lbl4.set_name("settings-label")
        lbl4.set_xalign(0.0)
        lbl4.set_size_request(90, -1)
        sound_row.pack_start(lbl4, False, False, 0)

        sound_toggle = Gtk.ToggleButton(
            label="On" if self.settings["click_sound"] else "Off")
        sound_toggle.set_name("settings-toggle")
        sound_toggle.set_size_request(60, -1)
        sound_toggle.set_active(self.settings["click_sound"])
        sound_toggle.connect("toggled", self._on_sound_toggled, sound_toggle)
        sound_row.pack_start(sound_toggle, False, False, 0)

        panel.pack_start(sound_row, False, False, 0)

        # ── Modifier auto-release row ───────────────────────────────────────
        mod_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl5 = Gtk.Label(label="Modifier keys")
        lbl5.set_name("settings-label")
        lbl5.set_xalign(0.0)
        lbl5.set_size_request(90, -1)
        mod_row.pack_start(lbl5, False, False, 0)

        auto_release = self.settings.get("modifier_auto_release", True)
        mod_toggle = Gtk.ToggleButton(label="Auto-release" if auto_release else "Sticky")
        mod_toggle.set_name("settings-toggle")
        mod_toggle.set_size_request(100, -1)
        mod_toggle.set_active(auto_release)
        mod_toggle.connect("toggled", self._on_mod_release_toggled, mod_toggle)
        mod_row.pack_start(mod_toggle, False, False, 0)

        mod_hint = Gtk.Label(
            label="releases after keypress" if auto_release else "stays until clicked again")
        mod_hint.set_name("settings-label")
        mod_hint.set_xalign(0.0)
        self._mod_hint_label = mod_hint
        mod_row.pack_start(mod_hint, False, False, 4)

        panel.pack_start(mod_row, False, False, 0)

        # ── Font size row ───────────────────────────────────────────────────
        font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl6 = Gtk.Label(label="Font size")
        lbl6.set_name("settings-label")
        lbl6.set_xalign(0.0)
        lbl6.set_size_request(90, -1)
        font_row.pack_start(lbl6, False, False, 0)

        font_dec = Gtk.Button(label="−")
        font_dec.get_style_context().add_class("settings-choice")
        font_dec.set_size_request(36, -1)
        font_dec.connect("clicked", self._on_font_size_change, -1)
        font_row.pack_start(font_dec, False, False, 0)

        cur_size = self.settings.get("font_size", 14)
        self._font_size_label = Gtk.Label(label=f"{cur_size}px")
        self._font_size_label.set_name("settings-label")
        self._font_size_label.set_size_request(42, -1)
        font_row.pack_start(self._font_size_label, False, False, 0)

        font_inc = Gtk.Button(label="+")
        font_inc.get_style_context().add_class("settings-choice")
        font_inc.set_size_request(36, -1)
        font_inc.connect("clicked", self._on_font_size_change, +1)
        font_row.pack_start(font_inc, False, False, 0)

        panel.pack_start(font_row, False, False, 0)

        # ── Panel shortcut row ──────────────────────────────────────────────
        shortcut_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl7 = Gtk.Label(label="Taskbar")
        lbl7.set_name("settings-label")
        lbl7.set_xalign(0.0)
        lbl7.set_size_request(90, -1)
        shortcut_row.pack_start(lbl7, False, False, 0)

        pin_btn = Gtk.Button(label="Pin to panel")
        pin_btn.get_style_context().add_class("settings-choice")
        pin_btn.set_size_request(100, -1)
        pin_btn.connect("clicked", self._on_pin_to_panel, pin_btn)
        shortcut_row.pack_start(pin_btn, False, False, 0)

        self._pin_status_label = Gtk.Label(label="")
        self._pin_status_label.set_name("settings-label")
        self._pin_status_label.set_xalign(0.0)
        shortcut_row.pack_start(self._pin_status_label, False, False, 4)

        panel.pack_start(shortcut_row, False, False, 0)

        self._refresh_theme_buttons()
        return panel

    def _build_emoji_panel(self) -> Gtk.Box:
        """Browsable + searchable emoji grid (shown when emoji mode is active)."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.set_name("emoji-panel")

        # Search label at top
        self._emoji_label = Gtk.Label(label="😊  Type to search • click an emoji to insert")
        self._emoji_label.set_name("emoji-search-label")
        self._emoji_label.set_xalign(0.0)
        self._emoji_label.set_margin_start(8)
        self._emoji_label.set_margin_top(4)
        self._emoji_label.set_margin_bottom(4)
        panel.pack_start(self._emoji_label, False, False, 0)

        # Scrollable FlowBox of emoji buttons
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self._emoji_flowbox = Gtk.FlowBox()
        self._emoji_flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._emoji_flowbox.set_homogeneous(True)
        self._emoji_flowbox.set_min_children_per_line(6)
        self._emoji_flowbox.set_max_children_per_line(40)
        self._emoji_flowbox.set_row_spacing(2)
        self._emoji_flowbox.set_column_spacing(2)
        self._emoji_flowbox.set_margin_start(4)
        self._emoji_flowbox.set_margin_end(4)
        self._emoji_flowbox.set_margin_bottom(4)

        for char, name, _ in EMOJI_DATA:
            btn = Gtk.Button(label=char)
            btn.get_style_context().add_class("emoji-btn")
            btn.set_tooltip_text(name)
            btn.connect("clicked", self._on_emoji_clicked, char)
            self._emoji_flowbox.add(btn)

        self._emoji_flowbox.set_filter_func(self._emoji_filter_func)

        scroll.add(self._emoji_flowbox)
        panel.pack_start(scroll, True, True, 0)
        return panel

    def _emoji_filter_func(self, child: Gtk.FlowBoxChild) -> bool:
        """FlowBox filter: show emoji if it matches _emoji_query."""
        if not self._emoji_query:
            return True
        q = self._emoji_query.lower()
        btn = child.get_child()
        if btn is None:
            return False
        char = btn.get_label()
        entry = self._emoji_lookup.get(char)
        if not entry:
            return False
        name, keywords = entry
        return q in name or any(q in kw for kw in keywords)

    def _refresh_theme_buttons(self):
        current = self.settings.get("theme", "dark")
        for key, btn in self._theme_btns.items():
            ctx = btn.get_style_context()
            if key == current:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    # ── Settings callbacks ────────────────────────────────────────────────────

    def _on_theme_clicked(self, _btn, theme: str):
        self._apply_theme(theme)

    def _on_dwell_toggled(self, toggle: Gtk.ToggleButton, label_btn):
        enabled = toggle.get_active()
        label_btn.set_label("On" if enabled else "Off")
        self.settings["dwell_enabled"] = enabled
        self._save_settings()

    def _on_dwell_delay(self, _btn, delta: float):
        delay = round(
            max(0.3, min(2.0, self.settings["dwell_delay"] + delta)), 1)
        self.settings["dwell_delay"] = delay
        if hasattr(self, "_dwell_label"):
            self._dwell_label.set_label(f"{delay:.1f}s")
        self._save_settings()

    def _on_sound_toggled(self, toggle: Gtk.ToggleButton, label_btn):
        enabled = toggle.get_active()
        label_btn.set_label("On" if enabled else "Off")
        self.settings["click_sound"] = enabled
        self._save_settings()

    def _on_mod_release_toggled(self, toggle: Gtk.ToggleButton, label_btn):
        enabled = toggle.get_active()
        label_btn.set_label("Auto-release" if enabled else "Sticky")
        self.settings["modifier_auto_release"] = enabled
        if hasattr(self, "_mod_hint_label"):
            self._mod_hint_label.set_text(
                "releases after keypress" if enabled else "stays until clicked again")
        self._save_settings()

    def _on_font_size_change(self, _btn, delta: int):
        size = max(10, min(22, self.settings.get("font_size", 14) + delta))
        self.settings["font_size"] = size
        if self._font_size_label:
            self._font_size_label.set_label(f"{size}px")
        self._reload_theme_css()
        self._save_settings()

    def _on_pin_to_panel(self, _btn, pin_btn: Gtk.Button):
        pin_btn.set_sensitive(False)
        status = self._create_panel_shortcut()
        if self._pin_status_label:
            self._pin_status_label.set_text(status)
        # Re-enable after a moment so the user can see the result
        GLib.timeout_add(3000, lambda: pin_btn.set_sensitive(True) or False)

    def _create_panel_shortcut(self) -> str:
        """Create .desktop file and add keyboard launcher to the Cinnamon panel."""
        # ── 1. Write the .desktop file ────────────────────────────────────────
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.sh")
        desktop_content = (
            "[Desktop Entry]\n"
            "Name=On-Screen Keyboard\n"
            "Comment=Custom GTK on-screen keyboard\n"
            f"Exec=bash {script}\n"
            "Icon=input-keyboard\n"
            "Type=Application\n"
            "Categories=Utility;Accessibility;\n"
            "StartupNotify=false\n"
            "NoDisplay=false\n"
        )
        apps_dir = os.path.expanduser("~/.local/share/applications")
        os.makedirs(apps_dir, exist_ok=True)
        desktop_file = os.path.join(apps_dir, "onscreen-keyboard.desktop")
        try:
            with open(desktop_file, "w") as f:
                f.write(desktop_content)
            os.chmod(desktop_file, 0o755)
        except Exception as exc:
            return f"Error: {exc}"

        # ── 2. Add / update the Cinnamon panel-launchers applet ───────────────
        try:
            cinnamon_settings = Gio.Settings(schema_id="org.cinnamon")
            applets: list[str] = list(cinnamon_settings.get_strv("enabled-applets"))
            launcher_name = os.path.basename(desktop_file)  # onscreen-keyboard.desktop

            # If the panel-launchers applet is already in the panel, just
            # add our launcher to its existing config file(s).
            existing_ids = [
                int(a.split(":")[4])
                for a in applets
                if "panel-launchers@cinnamon.org" in a and len(a.split(":")) >= 5
            ]
            if existing_ids:
                config_dir = os.path.expanduser(
                    "~/.config/cinnamon/spices/panel-launchers@cinnamon.org/")
                for iid in existing_ids:
                    cfg_path = os.path.join(config_dir, f"{iid}.json")
                    try:
                        with open(cfg_path) as f:
                            cfg = json.load(f)
                        lst = cfg.get("launcherList", {})
                        vals = lst.get("value", lst.get("default", []))
                        if launcher_name not in vals:
                            vals.append(launcher_name)
                            lst["value"] = vals
                            cfg["launcherList"] = lst
                            with open(cfg_path, "w") as f:
                                json.dump(cfg, f, indent=2)
                    except Exception:
                        pass
                return "Added to panel ✓"

            # Applet not on panel yet — add it.
            max_id = max(
                (int(a.split(":")[4]) for a in applets if len(a.split(":")) >= 5),
                default=0,
            )
            new_id = max_id + 1

            # Write the applet config
            config_dir = os.path.expanduser(
                "~/.config/cinnamon/spices/panel-launchers@cinnamon.org/")
            os.makedirs(config_dir, exist_ok=True)
            cfg = {
                "launcherList": {
                    "type": "generic",
                    "default": ["nemo.desktop"],
                    "value": [launcher_name],
                },
                "allow-dragging": {
                    "type": "switch",
                    "default": True,
                    "value": True,
                },
            }
            with open(os.path.join(config_dir, f"{new_id}.json"), "w") as f:
                json.dump(cfg, f, indent=2)

            # Insert after the separator (panel1:left:1) so it appears near the left
            applets.append(f"panel1:left:3:panel-launchers@cinnamon.org:{new_id}")
            cinnamon_settings.set_strv("enabled-applets", applets)
            return "Pinned to panel ✓ (reload may take a moment)"

        except Exception as exc:
            print(f"[pin] Error: {exc}")
            return "Added to app menu ✓ (panel step failed)"

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _reload_theme_css(self):
        """Regenerate and reload the theme CSS (colours + font size)."""
        colors    = THEME_COLORS.get(self.settings.get("theme", "dark"), THEME_COLORS["dark"])
        font_size = self.settings.get("font_size", 14)
        css = _make_theme_css(colors, font_size)
        self._theme_provider.load_from_data(css.encode())

    def _apply_theme(self, theme: str, save: bool = True):
        self.settings["theme"] = theme
        self._reload_theme_css()
        self._refresh_theme_buttons()
        if save:
            self._save_settings()

    # ── Dwell (hover-to-click) ────────────────────────────────────────────────

    def _on_dwell_enter(self, widget, event, action: str):
        if not self.settings.get("dwell_enabled"):
            return
        # Cancel any existing timer for this action
        self._dwell_cancel(action)
        delay_ms = int(self.settings.get("dwell_delay", 0.8) * 1000)
        widget.get_style_context().add_class("dwell-pending")

        def _fire():
            widget.get_style_context().remove_class("dwell-pending")
            self._dwell_timers.pop(action, None)
            self._on_key_clicked(widget, action)
            return False

        timer_id = GLib.timeout_add(delay_ms, _fire)
        self._dwell_timers[action] = timer_id

    def _on_dwell_leave(self, widget, event, action: str):
        self._dwell_cancel(action)
        widget.get_style_context().remove_class("dwell-pending")

    def _dwell_cancel(self, action: str):
        tid = self._dwell_timers.pop(action, None)
        if tid is not None:
            GLib.source_remove(tid)

    # ── Click sound ───────────────────────────────────────────────────────────

    def _init_click_sound(self):
        """Generate a short click WAV to /tmp/osk_click.wav."""
        try:
            if not os.path.exists(CLICK_WAV):
                rate, dur = 22050, 0.04
                n = int(rate * dur)
                with wave.open(CLICK_WAV, "w") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(rate)
                    frames = bytearray()
                    for i in range(n):
                        t = i / rate
                        amp = 28000 * math.exp(-t * 140) * \
                              math.sin(2 * math.pi * 1100 * t)
                        frames += struct.pack("<h", int(amp))
                    wf.writeframes(bytes(frames))
        except Exception as exc:
            print(f"[sound] Could not generate click WAV: {exc}")

    def _play_click_sound(self):
        if not self.settings.get("click_sound"):
            return
        if not os.path.exists(CLICK_WAV):
            return
        for player in ("paplay", "aplay"):
            try:
                subprocess.Popen(
                    [player, CLICK_WAV],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except FileNotFoundError:
                continue

    # ── Emoji panel ───────────────────────────────────────────────────────────

    def _toggle_emoji_mode(self):
        if self._emoji_mode:
            self._close_emoji_mode()
        else:
            # Mutually exclusive with other panels
            if self._settings_mode:
                self._toggle_settings()
            self._open_emoji_mode()

    def _open_emoji_mode(self):
        self._emoji_mode  = True
        self._emoji_query = ""

        # Build the emoji panel on first use (lazy init avoids FlowBoxChild
        # GdkWindows being present during the initial show_all()).
        if not self._emoji_panel_ready and self._key_stack:
            panel = self._build_emoji_panel()
            self._key_stack.add_named(panel, "emoji")
            panel.show_all()
            self._emoji_panel_ready = True

        if self._emoji_label:
            self._emoji_label.set_text("😊  Type to search • click an emoji to insert")
        if self._emoji_flowbox:
            self._emoji_flowbox.invalidate_filter()
        if self._emoji_btn:
            self._emoji_btn.get_style_context().add_class("active")
        if self._key_stack:
            self._key_stack.set_visible_child_name("emoji")

    def _close_emoji_mode(self):
        self._emoji_mode  = False
        self._emoji_query = ""
        if self._emoji_btn:
            self._emoji_btn.get_style_context().remove_class("active")
        if self._key_stack:
            self._key_stack.set_visible_child_name("keys")
        self._refresh_suggestions()

    def _on_emoji_clicked(self, _btn, char: str):
        """Type the chosen emoji and close the panel."""
        self.typer.type_emoji(char)
        self._close_emoji_mode()

    def _update_emoji_search(self):
        """Refresh the emoji grid filter and search label after query change."""
        if self._emoji_label:
            if self._emoji_query:
                self._emoji_label.set_text(f"🔍  {self._emoji_query}▏")
            else:
                self._emoji_label.set_text("😊  Type to search • click an emoji to insert")
        if self._emoji_flowbox:
            self._emoji_flowbox.invalidate_filter()

    def _open_app_mode(self):
        self._app_mode  = True
        self._app_query = ""
        self._app_results = []
        for btn in self._modifier_btns["win"]:
            btn.get_style_context().add_class("active")
        if self._search_label:
            self._search_label.set_text("⊞ ")
            self._search_label.show()
        self._refresh_app_results()

    def _close_app_mode(self):
        self._app_mode = False
        self._app_query = ""
        self._app_results = []
        for btn in self._modifier_btns["win"]:
            btn.get_style_context().remove_class("active")
        if self._search_label:
            self._search_label.hide()
        self._refresh_suggestions()

    def _refresh_app_results(self):
        """Search installed apps via Gio and populate the suggestion buttons."""
        q = self._app_query or ""
        if self._search_label:
            self._search_label.set_text(f"⊞  {q}▏" if q else "⊞  ")

        results = self._search_apps(q)
        self._app_results = results

        for i, btn in enumerate(self._suggestion_btns):
            if i < len(results):
                btn.set_label(results[i].get_display_name())
                btn.set_sensitive(True)
            else:
                btn.set_label("")
                btn.set_sensitive(False)

    @staticmethod
    def _search_apps(query: str) -> list[Gio.AppInfo]:
        """Return up to 5 Gio.AppInfo objects whose display name contains query."""
        q = query.strip().lower()
        results: list[Gio.AppInfo] = []
        for app in Gio.AppInfo.get_all():
            if not app.should_show():
                continue
            name = app.get_display_name() or ""
            if not name:
                continue
            if q and q not in name.lower():
                continue
            results.append(app)
        results.sort(key=lambda a: a.get_display_name().lower())
        return results[:5]

    def _launch_app(self, app_info: Gio.AppInfo):
        """Launch a Gio.AppInfo entry."""
        try:
            app_info.launch([], None)
        except Exception as exc:
            # Fallback: parse Exec= manually
            cmd = app_info.get_commandline() or ""
            cmd = re.sub(r"%[A-Za-z]", "", cmd).strip()
            if cmd:
                try:
                    subprocess.Popen(
                        cmd, shell=True, close_fds=True, start_new_session=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception as exc2:
                    print(f"[launcher] Failed: {exc2}")

    def _active_sticky_modifiers(self) -> list[str]:
        """Return list of latched modifier keysym names."""
        mods = []
        if self.ctrl_active: mods.append("ctrl_l")
        if self.alt_active:  mods.append("alt_l")
        return mods

    def _toggle_modifier(self, action: str):
        if action == "ctrl":
            self.ctrl_active = not self.ctrl_active
        elif action == "alt":
            self.alt_active = not self.alt_active
        self._update_modifier_visuals()

    def _type_character(self, action: str, mods: list[str]):
        upper = self.shift_active ^ self.caps_lock

        if action.isalpha():
            char = action.upper() if upper else action.lower()
        elif self.shift_active and action in SHIFT_MAP:
            char = SHIFT_MAP[action]
        else:
            char = action

        self.typer.type_char(char, mods)

        if char.isalpha() and not mods:
            self.current_word += char.lower()
        else:
            self.current_word = ""

        self._refresh_suggestions()

        if self.shift_active:
            self.shift_active = False
            self._update_modifier_visuals()

    # ── Suggestion bar ────────────────────────────────��───────────────────────

    def _refresh_suggestions(self):
        word_sugg  = self.predictor.predict(self.current_word, n=3)
        emoji_sugg = emoji_suggest(self.current_word, n=2) if self.current_word else []
        all_sugg   = word_sugg + emoji_sugg

        self._suggestion_values   = [""] * 5
        self._suggestion_is_emoji = [False] * 5

        for i, btn in enumerate(self._suggestion_btns):
            if i < len(all_sugg):
                val = all_sugg[i]
                is_emoji = (i >= len(word_sugg))
                btn.set_label(val)
                btn.set_sensitive(True)
                self._suggestion_values[i]   = val
                self._suggestion_is_emoji[i] = is_emoji
            else:
                btn.set_label("")
                btn.set_sensitive(False)

    def _on_suggestion_clicked(self, btn: Gtk.Button, idx: int = 0):
        label = btn.get_label()
        if not label:
            return

        if self._app_mode:
            for app_info in self._app_results:
                if app_info.get_display_name() == label:
                    self._launch_app(app_info)
                    break
            self._close_app_mode()
            return

        # Emoji suggestion — type the emoji character directly
        if idx < len(self._suggestion_is_emoji) and self._suggestion_is_emoji[idx]:
            self.typer.type_emoji(label)
            self.current_word = ""
            self._refresh_suggestions()
            return

        # Normal word-completion
        remaining = label[len(self.current_word):]
        for ch in remaining:
            self.typer.type_char(ch)
        self.typer.send_special("space")
        self.current_word = ""
        self._refresh_suggestions()

    # ── Modifier visuals ────────────��─────────────────────────���───────────────

    def _update_modifier_visuals(self):
        upper = self.shift_active ^ self.caps_lock
        for letter, btn in self._letter_btns.items():
            btn.set_label(letter.upper() if upper else letter.lower())

        # Symbol keys: show shifted label as main when shift is active,
        # otherwise show base char and restore the dim shift-hint sub-label.
        for action, (main_lbl, sub_lbl) in self._symbol_btns.items():
            if self.shift_active:
                main_lbl.set_text(SHIFT_MAP[action])
                sub_lbl.set_visible(False)
            else:
                main_lbl.set_text(action)
                sub_lbl.set_visible(True)

        def _set_active(btns, state):
            for btn in btns:
                ctx = btn.get_style_context()
                if state:
                    ctx.add_class("active")
                else:
                    ctx.remove_class("active")

        _set_active(self._shift_btns, self.shift_active)
        if self._caps_btn:
            _set_active([self._caps_btn], self.caps_lock)
        _set_active(self._modifier_btns["ctrl"], self.ctrl_active)
        _set_active(self._modifier_btns["alt"],  self.alt_active)
        _set_active(self._modifier_btns["win"],  self.win_active)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ok, _ = Gtk.init_check(sys.argv)
    if not ok:
        print("ERROR: Could not connect to a display.")
        print(f"  DISPLAY    = {os.environ.get('DISPLAY', '(not set)')}")
        print(f"  XAUTHORITY = {os.environ.get('XAUTHORITY', '(not set)')}")
        print("Run this from a terminal opened inside your desktop session.")
        sys.exit(1)

    win = OnScreenKeyboard()
    Gtk.main()


if __name__ == "__main__":
    main()

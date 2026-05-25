#!/usr/bin/env python3
"""
Custom on-screen keyboard — GTK3, cross-platform (Linux + Windows).
Typed into active window via XTEST (Linux primary), AT-SPI2 (Linux fallback),
or pynput (Windows primary / universal fallback).

Run:  bash launch.sh          (Linux)
      python keyboard.py      (Windows, after installing GTK3 + pynput)
"""

import contextlib
import glob
import json
import math
import os
import platform
import re
import shlex
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

IS_WINDOWS = platform.system() == "Windows"

# Windows focus-restoration: keeps track of the last non-OSK foreground window
# so KeyTyper can redirect keystrokes to it even if GTK stole focus on click.
_win_last_target: list[int] = [0]  # mutable so the SetWinEventHook closure can write it

def _win_focus_restore() -> None:
    hwnd = _win_last_target[0]
    if hwnd:
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

# ── pynput — cross-platform key synthesis (primary on Windows) ────────────────
PYNPUT_OK      = False
PYNPUT_KEY_MAP: dict = {}
_pynput_ctrl   = None   # module-level Controller instance (set below if available)
try:
    from pynput.keyboard import Key as _PKey, Controller as _PController
    PYNPUT_OK    = True
    _pynput_ctrl = _PController()
    PYNPUT_KEY_MAP = {
        "backspace": _PKey.backspace, "return": _PKey.enter,
        "tab":       _PKey.tab,       "space":  _PKey.space,
        "escape":    _PKey.esc,       "delete": _PKey.delete,
        "home":      _PKey.home,      "end":    _PKey.end,
        "left":      _PKey.left,      "right":  _PKey.right,
        "up":        _PKey.up,        "down":   _PKey.down,
        "f1":  _PKey.f1,  "f2":  _PKey.f2,  "f3":  _PKey.f3,  "f4":  _PKey.f4,
        "f5":  _PKey.f5,  "f6":  _PKey.f6,  "f7":  _PKey.f7,  "f8":  _PKey.f8,
        "f9":  _PKey.f9,  "f10": _PKey.f10, "f11": _PKey.f11, "f12": _PKey.f12,
        "shift_l":  _PKey.shift_l,  "ctrl_l": _PKey.ctrl_l,
        "ctrl_r":   _PKey.ctrl_r,   "alt_l":  _PKey.alt_l,
        "alt_r":    _PKey.alt_r,    "super_l": _PKey.cmd,
        "prtscn":   _PKey.print_screen,
    }
    print("[keyboard] pynput available — cross-platform key synthesis enabled.")
except ImportError:
    if IS_WINDOWS:
        print("[keyboard] pynput not found — typing will not work.")
        print("           Install with: pip install pynput")
    else:
        print("[keyboard] pynput not found (optional on Linux).")

# ── XTEST — Linux primary (most reliable for browsers, terminals, etc.) ───────
XLIB_OK = False
if not IS_WINDOWS:
    try:
        from Xlib import display as xdisplay, X
        from Xlib.ext import xtest
        XLIB_OK = True
        print("[keyboard] python-xlib available — using XTEST key synthesis.")
    except ImportError:
        print("[keyboard] python3-xlib not found — will try AT-SPI2 / pynput.")
        print("           Install with: sudo apt install python3-xlib")

# ── AT-SPI2 — Linux fallback ──────────────────────────────────────────────────
ATSPI_OK = False
if not IS_WINDOWS:
    try:
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        ATSPI_OK = True
        if not XLIB_OK:
            print("[keyboard] AT-SPI2 available — using accessibility key synthesis.")
        else:
            print("[keyboard] AT-SPI2 also available (unused; XTEST is primary).")
    except Exception as _e:
        if not XLIB_OK and not PYNPUT_OK:
            print(f"[keyboard] AT-SPI2 also unavailable ({_e}) — typing disabled.")

from predictor import WordPredictor
from emojis import EMOJI_DATA, search as emoji_search, suggest as emoji_suggest


def _resolve_dict_path() -> str:
    if getattr(sys, "frozen", False):
        p = os.path.join(sys._MEIPASS, "words.txt")
        if os.path.exists(p):
            return p
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.txt")
    if os.path.exists(local):
        return local
    return "/usr/share/dict/american-english"

# xdotool — Linux only, used for emoji Unicode typing
XDOTOOL_OK = False
if not IS_WINDOWS:
    XDOTOOL_OK = bool(shutil.which("xdotool"))
    if not XDOTOOL_OK:
        print("[keyboard] xdotool not found — emoji will use pynput fallback.")
        print("           Install with: sudo apt install xdotool")

# ── Persistent settings ───────────────────────────────────────────────────────
CONFIG_DIR        = os.path.expanduser("~/.config/onscreen_keyboard")
CONFIG_FILE       = os.path.join(CONFIG_DIR, "settings.json")
CUSTOM_WORDS_FILE  = os.path.join(CONFIG_DIR, "custom_words.json")
CUSTOM_THEMES_FILE = os.path.join(CONFIG_DIR, "custom_themes.json")
MACROS_FILE        = os.path.join(CONFIG_DIR, "macros.json")
CLICK_WAV          = os.path.join(CONFIG_DIR, "click.wav")

DEFAULT_SETTINGS: dict = {
    "theme":                "dark",   # dark | light | midnight | hc
    "font_size":            14,       # key label font size in px (10–22)
    "dwell_enabled":        False,
    "dwell_delay":          0.8,      # seconds (0.3 – 2.0)
    "click_sound":          False,
    "modifier_auto_release": True,    # release Ctrl/Alt automatically after next keypress
    "shift_sticky":          False,   # False = one-shot (release after keypress), True = sticky like Ctrl
    "opacity":              1.0,      # window opacity (0.3 – 1.0)
    "key_scale":            1.0,      # key size scale factor (0.3 – 1.5)
    "font_family":          "Ubuntu", # key label font family
    "layout":               "qwerty", # keyboard layout: qwerty | azerty | qwertz
    "update_check_enabled": True,
}

DEFAULT_MACROS = [
    {"trigger": "date", "expansion": "{date}"},
    {"trigger": "time", "expansion": "{time}"},
]

FONT_FAMILIES = ["Ubuntu", "Noto Sans", "DejaVu Sans", "Roboto", "Arial", "Courier New"]

__version__ = "0.1.2"

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
        "titlebar_bg": "#1a1a1a", "titlebar_fg": "#cccccc", "titlebar_border": "#333333",
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
        "titlebar_bg": "#e0e0e0", "titlebar_fg": "#333333", "titlebar_border": "#bdbdbd",
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
        "titlebar_bg": "#0a0a1a", "titlebar_fg": "#8888cc", "titlebar_border": "#1a1a3a",
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
        "titlebar_bg": "#000000", "titlebar_fg": "#ffffff", "titlebar_border": "#ffffff",
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

# ── DIY theme builder constants ───────────────────────────────────────────────
WIZARD_STEPS = [
    ("Background colour",                          "bg"),
    ("Key colour",                                 "key_bg"),
    ("Key text colour",                            "key_fg"),
    ("Border colour",                              "border"),
    ("Accent colour  (active keys / suggestions)", "accent"),
    ("Close button colour",                        "danger"),
]
WIZARD_DEFAULTS = {
    "bg": "#1c1c1c", "key_bg": "#2d2d2d", "key_fg": "#f0f0f0",
    "border": "#404040", "accent": "#0078d4", "danger": "#ff6b6b",
}


def _make_theme_css(c: dict, font_size: int = 14, font_family: str = "Ubuntu") -> str:
    """Generate a complete colour-override CSS string from a theme dict."""
    if font_family not in FONT_FAMILIES:
        print(f"[theme] Unknown font_family {font_family!r}, falling back to Ubuntu")
        font_family = "Ubuntu"
    special_size  = max(9,  font_size - 2)   # modifier/nav labels slightly smaller
    sublabel_size = max(8,  font_size - 4)   # shift-symbol hint in corner
    return f"""* {{ font-family: "{font_family}", sans-serif; }}

window {{ background-color: {c['bg']}; }}
#main-box {{ background-color: {c['bg']}; }}
#title-bar {{ background-color: {c['titlebar_bg']}; border-bottom-color: {c['titlebar_border']}; }}
#titlebar-title {{ color: {c['titlebar_fg']}; }}
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
#minimize-btn {{ background-color: {c['settings_bg']}; color: {c['settings_fg']};
                 border: 1px solid {c['settings_border']}; border-bottom: 2px solid {c['settings_bot']}; }}
#minimize-btn:hover {{ color: {c['key_fg']}; background-color: {c['key_hover']}; }}
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

# ── Colour helpers for the DIY theme builder ─────────────────────────────────

def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    if len(h) < 6:
        raise ValueError(f"Invalid hex colour: #{h}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"

def _adjust(color: str, factor: float) -> str:
    r, g, b = _hex_to_rgb(color)
    return _rgb_to_hex(
        max(0, min(255, int(r * factor))),
        max(0, min(255, int(g * factor))),
        max(0, min(255, int(b * factor))),
    )

def _mix(a: str, b: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(a)
    r2, g2, b2 = _hex_to_rgb(b)
    return _rgb_to_hex(
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )

def _luminance(color: str) -> float:
    def lin(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _hex_to_rgb(color)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

def _contrast_text(bg: str) -> str:
    return "#ffffff" if _luminance(bg) < 0.179 else "#000000"

def _hex_to_gdk_rgba(h: str) -> "Gdk.RGBA":
    rgba = Gdk.RGBA()
    rgba.parse(h)
    return rgba

def _gdk_rgba_to_hex(rgba: "Gdk.RGBA") -> str:
    return _rgb_to_hex(int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))

def _build_custom_theme(bg: str, key_bg: str, key_fg: str,
                        border: str, accent: str, danger: str) -> dict:
    """Derive a complete THEME_COLORS-style dict from 6 user-chosen hex colours."""
    bar_bg        = _adjust(bg, 0.85)
    key_hover     = _mix(key_bg, "#ffffff", 0.10)
    special_bg    = _mix(bg, key_bg, 0.5)
    special_fg    = _mix(key_fg, bg, 0.35)
    accent_fg     = _contrast_text(accent)
    accent_dark   = _adjust(accent, 0.72)
    accent_darker = _adjust(accent, 0.55)
    danger_light  = _mix(danger, bg, 0.78)
    sugg_hover    = _mix(bg, key_bg, 0.5)
    toggle_fg     = _mix(key_fg, bg, 0.40)
    sublabel      = _mix(key_fg, bg, 0.55)
    # Title bar mirrors the suggestion bar: slightly darker than the background,
    # body text colour for the label, accent border for the bottom edge.
    titlebar_bg = _adjust(bg, 0.80)
    titlebar_fg = _mix(key_fg, bg, 0.15)
    return {
        "titlebar_bg": titlebar_bg, "titlebar_fg": titlebar_fg,
        "titlebar_border": border,
        "bg": bg, "bar_bg": bar_bg, "bar_border": border,
        "key_bg": key_bg, "key_fg": key_fg,
        "key_border": border, "key_bot": _adjust(border, 0.65),
        "key_hover": key_hover,
        "key_hover_border": _mix(border, "#ffffff", 0.25),
        "special_bg": special_bg, "special_fg": special_fg,
        "special_border": border, "special_bot": _adjust(border, 0.55),
        "special_hover": _mix(special_bg, "#ffffff", 0.07),
        "sugg_fg": accent, "sugg_hover": sugg_hover, "sugg_divider": border,
        "search_fg": key_fg,
        "close_bg": danger_light, "close_fg": danger,
        "close_border": _mix(danger, bg, 0.55),
        "close_bot": _mix(danger, bg, 0.35),
        "close_hover_bg": _mix(danger, "#ffffff", 0.15),
        "close_hover_fg": accent_fg,
        "settings_bg": special_bg, "settings_fg": special_fg,
        "settings_border": border, "settings_bot": _adjust(border, 0.65),
        "choice_bg": key_bg, "choice_fg": key_fg,
        "choice_border": border, "choice_bot": _adjust(border, 0.65),
        "toggle_fg": toggle_fg,
        "sublabel_fg": sublabel,
        "active_bg": accent, "active_fg": accent_fg,
        "active_border": accent_dark, "active_bot": accent_darker,
        "active_hover": _mix(accent, "#ffffff", 0.18),
    }


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
TITLEBAR_H   = 26   # height of the custom title-bar strip
STRIP_H      = 11   # height of the top/bottom resize strips
MIN_W        = 400  # minimum window width during resize
EDGE_ZONE    = 14   # px from window edge that triggers resize

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

# ── Alternative layouts ───────────────────────────────────────────────────────
_FN_ROW  = KEY_ROWS[0]   # function keys — same for all layouts
_NUM_ROW = KEY_ROWS[1]   # number row — same for all layouts
_BOT_ROW = KEY_ROWS[5]   # bottom row — same for all layouts

LAYOUT_ROWS: dict[str, list] = {
    "qwerty": KEY_ROWS,
    "azerty": [
        _FN_ROW, _NUM_ROW,
        [("Tab","tab",80,["special"]),
         ("a","a",52,[]),("z","z",52,[]),("e","e",52,[]),("r","r",52,[]),
         ("t","t",52,[]),("y","y",52,[]),("u","u",52,[]),("i","i",52,[]),
         ("o","o",52,[]),("p","p",52,[]),
         ("[","[",52,[]),("]","]",52,[]),("\\","\\",63,[])],
        [("Caps","caps",92,["special"]),
         ("q","q",52,[]),("s","s",52,[]),("d","d",52,[]),("f","f",52,[]),
         ("g","g",52,[]),("h","h",52,[]),("j","j",52,[]),("k","k",52,[]),
         ("l","l",52,[]),("m","m",52,[]),("'","'",52,[]),
         ("↵","return",111,["special"])],
        [("⇧","shift",128,["special"]),
         ("w","w",52,[]),("x","x",52,[]),("c","c",52,[]),("v","v",52,[]),
         ("b","b",52,[]),("n","n",52,[]),(",",",",52,[]),(".",".",52,[]),("/","/",52,[]),
         ("⇧","shift",128,["special"])],
        _BOT_ROW,
    ],
    "qwertz": [
        _FN_ROW, _NUM_ROW,
        [("Tab","tab",80,["special"]),
         ("q","q",52,[]),("w","w",52,[]),("e","e",52,[]),("r","r",52,[]),
         ("t","t",52,[]),("z","z",52,[]),("u","u",52,[]),("i","i",52,[]),
         ("o","o",52,[]),("p","p",52,[]),
         ("[","[",52,[]),("]","]",52,[]),("\\","\\",63,[])],
        [("Caps","caps",92,["special"]),
         ("a","a",52,[]),("s","s",52,[]),("d","d",52,[]),("f","f",52,[]),
         ("g","g",52,[]),("h","h",52,[]),("j","j",52,[]),("k","k",52,[]),
         ("l","l",52,[]),(";",";",52,[]),("'","'",52,[]),
         ("↵","return",111,["special"])],
        [("⇧","shift",128,["special"]),
         ("y","y",52,[]),("x","x",52,[]),("c","c",52,[]),("v","v",52,[]),
         ("b","b",52,[]),("n","n",52,[]),("m","m",52,[]),
         (",",",",52,[]),(".",".",52,[]),("/","/",52,[]),
         ("⇧","shift",128,["special"])],
        _BOT_ROW,
    ],
}

# Widest row natural width (row 1): 13×52 + 95 + 13 gaps×3px = 810
BASE_KB_W = 810
# 6 rows × base key height (5 typing rows + 1 function key row)
BASE_KB_H = BASE_KEY_H * 6


# ── Key typer ────────────────────────────────────────────────────────────────
class KeyTyper:
    """
    Sends keystrokes to the active window.

    Windows:  pynput (Win32 SendInput — works in all apps).
    Linux:    XTEST via python-xlib (primary, most reliable).
              AT-SPI2 (fallback when python-xlib is absent).
              pynput (last-resort Linux fallback).
    """

    def __init__(self):
        self._disp = xdisplay.Display() if XLIB_OK else None

    # ── Public API ────────────────────────────────────────────────────────────

    def type_char(self, char: str, mods: list[str] | None = None) -> None:
        # Windows — restore focus to the target window then use pynput
        if IS_WINDOWS:
            _win_focus_restore()
            if PYNPUT_OK:
                self._pynput_type_char(char, mods or [])
            return
        # Linux — prefer XTEST, fall back to AT-SPI2, then pynput
        keysym      = ord(char)
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
            return
        if PYNPUT_OK:
            self._pynput_type_char(char, mods or [])

    def send_special(self, name: str, mods: list[str] | None = None) -> None:
        # Windows — restore focus to the target window then use pynput
        if IS_WINDOWS:
            _win_focus_restore()
            if PYNPUT_OK:
                self._pynput_send_special(name, mods or [])
            return
        # Linux — prefer XTEST, fall back to AT-SPI2, then pynput
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
            return
        if PYNPUT_OK:
            self._pynput_send_special(name, mods or [])

    # ── pynput implementation (Windows primary / Linux last-resort) ───────────

    def _pynput_type_char(self, char: str, mods: list[str]) -> None:
        ctrl = _pynput_ctrl
        try:
            for m in mods:
                k = PYNPUT_KEY_MAP.get(m)
                if k:
                    ctrl.press(k)
            ctrl.press(char)
            ctrl.release(char)
            for m in reversed(mods):
                k = PYNPUT_KEY_MAP.get(m)
                if k:
                    ctrl.release(k)
        except Exception as exc:
            print(f"[pynput] type_char {char!r}: {exc}")

    def _pynput_send_special(self, name: str, mods: list[str]) -> None:
        key = PYNPUT_KEY_MAP.get(name)
        if key is None:
            return
        ctrl = _pynput_ctrl
        try:
            for m in mods:
                k = PYNPUT_KEY_MAP.get(m)
                if k:
                    ctrl.press(k)
            ctrl.press(key)
            ctrl.release(key)
            for m in reversed(mods):
                k = PYNPUT_KEY_MAP.get(m)
                if k:
                    ctrl.release(k)
        except Exception as exc:
            print(f"[pynput] send_special {name!r}: {exc}")

    # ── AT-SPI2 implementation ────────────────────────────────────────────────

    def _atspi_send_keysym(self, keysym: int, with_shift: bool,
                           mods: list[str]) -> None:
        """Synthesise a key via AT-SPI2 accessibility layer."""
        try:
            all_mods: list[int] = []
            if with_shift:
                all_mods.append(KEYSYMS["shift_l"])
            for name in mods:
                sym = KEYSYMS.get(name)
                if sym:
                    all_mods.append(sym)
            for sym in all_mods:
                Atspi.generate_keyboard_event(sym, None, Atspi.KeySynthType.PRESS)
            Atspi.generate_keyboard_event(keysym, None,
                                          Atspi.KeySynthType.PRESSRELEASE)
            for sym in reversed(all_mods):
                Atspi.generate_keyboard_event(sym, None,
                                              Atspi.KeySynthType.RELEASE)
        except Exception as exc:
            print(f"[atspi] Error sending keysym {keysym:#x}: {exc}")

    # ── Emoji (arbitrary Unicode) ─────────────────────────────────────────────

    def type_emoji(self, emoji_str: str) -> None:
        """Type an emoji / arbitrary Unicode string."""
        # pynput.type() handles arbitrary Unicode on all platforms
        if PYNPUT_OK and (IS_WINDOWS or not XDOTOOL_OK):
            if IS_WINDOWS:
                _win_focus_restore()
            try:
                _pynput_ctrl.type(emoji_str)
                return
            except Exception as exc:
                print(f"[pynput] emoji type error: {exc}")
        # Linux — prefer xdotool (most reliable for multi-codepoint emoji)
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
        # Last resort: X11 Unicode keysyms
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
            shift_kc = self._disp.keysym_to_keycode(KEYSYMS["shift_l"])
            if shift_kc:
                mod_keycodes.append(shift_kc)
        for mod_name in mods:
            kc = self._disp.keysym_to_keycode(KEYSYMS[mod_name])
            if kc:
                mod_keycodes.append(kc)

        pressed: list[int] = []
        try:
            for kc in mod_keycodes:
                xtest.fake_input(self._disp, X.KeyPress, kc)
                pressed.append(kc)
            xtest.fake_input(self._disp, X.KeyPress,   keycode)
            xtest.fake_input(self._disp, X.KeyRelease, keycode)
            for kc in reversed(pressed):
                xtest.fake_input(self._disp, X.KeyRelease, kc)
            self._disp.flush()
        except Exception as exc:
            print(f"[xtest] send error (attempting modifier cleanup): {exc}")
            for kc in reversed(pressed):
                try:
                    xtest.fake_input(self._disp, X.KeyRelease, kc)
                except Exception:
                    pass
            try:
                self._disp.flush()
            except Exception:
                pass


# ── Main keyboard window ───────────────────���────────────────────────────────���─
class OnScreenKeyboard(Gtk.Window):

    def __init__(self):
        super().__init__()
        self.predictor    = WordPredictor(_resolve_dict_path())
        self.typer        = KeyTyper()
        self.current_word = ""
        self._last_word   = ""   # last fully typed word, for next-word prediction
        self.shift_active = False
        self.caps_lock    = False
        self._custom_words: list[str] = []

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
        # All key buttons for key-size scaling: (button, base_width)
        self._all_key_btns:    list[tuple[Gtk.Button, int]] = []
        # Theme / layout / font family button dicts
        self._layout_btns:     dict[str, Gtk.Button] = {}
        self._font_family_btns: dict[str, Gtk.Button] = {}

        # Built-in app launcher (replaces Win→Cinnamon menu, which can't stay
        # open when our keyboard is clicked due to Cinnamon's Clutter-level
        # event capture that fires for ALL external button presses).
        self._app_mode      = False   # True while launcher search is active
        self._app_query     = ""
        self._app_results:  list[Gio.AppInfo] = []
        self._cached_apps:  list | None = None  # populated once on launcher open

        # Emoji panel (built lazily — avoids FlowBoxChild GdkWindows blocking keys)
        self._emoji_mode         = False
        self._emoji_panel_ready  = False   # True once the panel has been built
        self._emoji_query        = ""
        self._emoji_btn:         Gtk.Button | None  = None
        self._emoji_flowbox:     Gtk.FlowBox | None = None
        self._emoji_label:          Gtk.Label | None   = None
        self._mod_hint_label:       Gtk.Label | None   = None
        self._font_size_label:      Gtk.Label | None   = None
        self._pin_status_label:     Gtk.Label | None   = None
        self._custom_words_listbox: Gtk.ListBox | None = None
        self._custom_words_count:   Gtk.Label | None   = None
        # Fast lookup for filter func: char → (name, keywords)
        self._emoji_lookup:   dict[str, tuple[str, list[str]]] = {
            char: (name, kws) for char, name, kws in EMOJI_DATA
        }

        # Clipboard history
        self._clipboard_history: list[str] = []
        self._clipboard_mode:    bool = False
        self._clipboard_btn:     Gtk.Button | None = None
        self._clipboard_panel_ready: bool = False
        self._clipboard_listbox: Gtk.ListBox | None = None

        # Macros
        self._macros:         list[dict] = []
        self._macros_listbox: Gtk.ListBox | None = None
        self._macros_count:   Gtk.Label | None = None
        self._macro_draft:    dict = {}

        # Suggestion bar: what value each slot will type (word, emoji, or custom)
        self._suggestion_values:    list[str]  = [""] * 5
        self._suggestion_is_emoji:  list[bool] = [False] * 5
        self._suggestion_is_custom: list[bool] = [False] * 5
        self._suggestion_is_fuzzy:  list[bool] = [False] * 5
        self._suggestion_is_macro:  list[bool] = [False] * 5

        # Custom dictionary input mode
        self._custom_input_mode: bool = False
        self._custom_input_text: str  = ""
        self._custom_input_mode_target: str = "word"   # "word" | "theme_name" | "macro_trigger" | "macro_expansion"

        # DIY theme wizard state
        self._custom_themes: dict[str, dict] = {}
        self._wizard_colors: dict[str, str]  = dict(WIZARD_DEFAULTS)
        self._wizard_name:   str             = ""
        self._wizard_step:   int             = 0   # 1–6 when wizard page is open
        self._wizard_page_ready: bool        = False
        self._wizard_step_label:     Gtk.Label | None       = None
        self._wizard_question_label: Gtk.Label | None       = None
        self._wizard_color_btn:      Gtk.ColorButton | None = None
        self._wizard_next_btn:       Gtk.Button | None      = None
        self._wizard_name_display:   Gtk.Label | None       = None
        self._wizard_preview_swatches: list[Gtk.EventBox]   = []
        self._custom_theme_btns_box: Gtk.Box | None         = None
        self._custom_theme_btns:     dict[str, Gtk.Button]  = {}

        # Sticky modifier state (Ctrl / Alt / Win latch until next keypress)
        self.ctrl_active = False
        self.alt_active  = False
        self.win_active  = False

        # Manual resize state (used because override_redirect disables WM resize)
        self._resizing        = False
        self._resize_did_move = False
        self._resize_edge     = ""
        self._resize_start_x  = 0.0
        self._resize_start_y  = 0.0
        self._resize_start_w  = 0
        self._resize_start_h  = 0
        self._resize_start_wx = 0
        self._resize_start_wy = 0
        self._resize_grip:  Gtk.EventBox | None = None

        # Manual title-bar move state.  begin_move_drag() is a no-op on Muffin
        # for DOCK windows (treated as struts → _NET_WM_MOVERESIZE ignored), so
        # we track the drag ourselves: save pointer + window origin on press,
        # self.move() by the delta on motion.
        self._moving         = False
        self._move_start_x   = 0.0
        self._move_start_y   = 0.0
        self._move_start_wx  = 0
        self._move_start_wy  = 0

        # Window-control / collapse state (Windows 11-style minimize)
        self._minimize_btn:   Gtk.Button | None  = None
        self._close_btn:      Gtk.Button | None  = None
        self._update_status_label: Gtk.Label | None  = None
        self._update_btn:          Gtk.Button | None = None
        self._titlebar:       Gtk.EventBox | None = None
        self._suggestion_bar: Gtk.Box | None     = None
        self._top_strip:      Gtk.EventBox | None = None
        self._bottom_strip:   Gtk.EventBox | None = None
        self._collapsed       = False
        self._pre_collapse_h  = 0
        # Height of the collapsed strip: title bar + suggestion bar + top resize
        # strip + chrome.  The collapsed state now keeps the title bar AND
        # suggestion bar visible; the title bar is the only way to expand again.
        # The bottom resize strip is hidden when collapsed, so only one strip.
        self._collapsed_h     = TITLEBAR_H + SUGGESTION_H + STRIP_H + 8

        # Key repeat state
        self._repeat_timers: dict[str, int] = {}  # action → GLib source ID
        self._repeat_active: set[str]        = set()  # actions in fast-repeat phase

        # Fuzzy spell-check debounce timer
        self._fuzzy_timer: int | None = None

        # Settings + dwell state
        self.settings:       dict = self._load_settings()
        self._dwell_timers:  dict[int, int] = {}  # id(widget) → GLib source ID
        self._settings_mode: bool = False

        self._custom_words = self._load_custom_words()
        self.predictor.set_custom_words(self._custom_words)

        self._custom_themes = self._load_custom_themes()
        for _name, _colors in self._custom_themes.items():
            THEME_COLORS[_name] = _colors

        self._macros = self._load_macros()

        self._setup_window()
        self._apply_css()
        self._build_ui()
        self._apply_theme(self.settings["theme"], save=False)
        self._apply_key_scale(self.settings.get("key_scale", 1.0))
        self.set_opacity(self.settings.get("opacity", 1.0))
        self._init_click_sound()
        self.show_all()

        # Connect clipboard monitoring after show_all
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.connect("owner-change", self._on_clipboard_change)

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

        kb_h = BASE_KEY_H * 6 + TITLEBAR_H + SUGGESTION_H + 7 * 4 + 12
        # Restore saved position/size, or default to full-width bar at screen bottom
        saved_x = self.settings.get("win_x", 0)
        saved_y = self.settings.get("win_y", sh - kb_h)
        saved_w = self.settings.get("win_w", sw)
        saved_h = self.settings.get("win_h", kb_h)
        # Clamp so it can't start fully off-screen
        saved_x = max(-saved_w + 50, min(saved_x, sw - 50))
        saved_y = max(0, min(saved_y, sh - 50))
        self.set_default_size(saved_w, saved_h)
        self.move(saved_x, saved_y)
        self.connect("destroy", Gtk.main_quit)
        self.connect("delete-event", self._on_close)

        if IS_WINDOWS:
            # Apply WS_EX_NOACTIVATE after the window is realised so the OSK
            # never steals focus from the application being typed into.
            self.connect("realize", self._on_realized_windows)

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

    def _on_realized_windows(self, _win):
        """Windows only: prevent the OSK from stealing focus on click.

        Three complementary mechanisms:
          1. WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW — tells Windows not to activate
             the window when clicked.
          2. WM_MOUSEACTIVATE subclass — returns MA_NOACTIVATE so GDK's backend
             can't activate the window even via DefWindowProc.
          3. SetWinEventHook — tracks the last non-OSK foreground window so
             KeyTyper.type_char/send_special can restore focus before each
             SendInput call, making typing work even if 1+2 fail.
        """
        try:
            import ctypes

            user32 = ctypes.windll.user32

            # ── 1. Obtain the HWND ──────────────────────────────────────────
            gdk_win = self.get_window()
            hwnd = gdk_win.get_handle() if gdk_win is not None else None

            if not hwnd:
                # get_handle() absent or unimplemented in this PyGObject build —
                # enumerate visible top-level windows by our own PID instead.
                our_pid = os.getpid()
                found: list[int] = []
                EnumWindowsProc = ctypes.WINFUNCTYPE(
                    ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
                )
                def _enum_cb(h, _):
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    if pid.value == our_pid and user32.IsWindowVisible(h):
                        found.append(h)
                    return True
                _cb = EnumWindowsProc(_enum_cb)
                user32.EnumWindows(_cb, 0)
                hwnd = found[0] if found else None

            if not hwnd:
                print("[windows] Could not obtain HWND — WS_EX_NOACTIVATE/subclass skipped")
                # Focus tracking (step 3) still works without an HWND, so continue.
            else:
                self._win_hwnd = hwnd
                print(f"[windows] HWND: {hwnd:#010x}")

            if hwnd:
                # ── 2. Set WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW ─────────────
                GWL_EXSTYLE      = -20
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_TOOLWINDOW = 0x00000080

                cur_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(
                    hwnd, GWL_EXSTYLE,
                    cur_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                )
                print("[windows] WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW applied")

                # ── 3. Subclass WNDPROC to handle WM_MOUSEACTIVATE ──────────
                # WNDPROC: LRESULT CALLBACK(HWND, UINT, WPARAM, LPARAM)
                # LRESULT is LONG_PTR — 64 bits on 64-bit Windows; c_ssize_t matches.
                WNDPROCTYPE = ctypes.WINFUNCTYPE(
                    ctypes.c_ssize_t,  # LRESULT (LONG_PTR — pointer-sized)
                    ctypes.c_void_p,   # HWND
                    ctypes.c_uint,     # UINT  (message id)
                    ctypes.c_void_p,   # WPARAM
                    ctypes.c_void_p,   # LPARAM
                )

                WM_MOUSEACTIVATE = 0x0021
                MA_NOACTIVATE    = 3
                GWLP_WNDPROC     = -4

                try:
                    GetWindowLongPtr = user32.GetWindowLongPtrW
                    SetWindowLongPtr = user32.SetWindowLongPtrW
                except AttributeError:
                    GetWindowLongPtr = user32.GetWindowLongW
                    SetWindowLongPtr = user32.SetWindowLongW
                    print("[windows] SetWindowLongPtrW unavailable — using 32-bit variant")

                original_wndproc_addr = GetWindowLongPtr(hwnd, GWLP_WNDPROC)
                if original_wndproc_addr:
                    self._win_original_addr = original_wndproc_addr
                    original_wndproc = ctypes.cast(
                        ctypes.c_void_p(original_wndproc_addr), WNDPROCTYPE
                    )

                    def _wndproc(hwnd_, msg, wparam, lparam):
                        if msg == WM_MOUSEACTIVATE:
                            return MA_NOACTIVATE
                        return original_wndproc(hwnd_, msg, wparam, lparam)

                    self._win_wndproc = WNDPROCTYPE(_wndproc)
                    SetWindowLongPtr(hwnd, GWLP_WNDPROC,
                                     ctypes.cast(self._win_wndproc, ctypes.c_void_p).value)
                    print("[windows] WM_MOUSEACTIVATE subclass installed")
                else:
                    print("[windows] GetWindowLongPtr(GWLP_WNDPROC) returned 0 — subclass skipped")

            # ── 4. Track last non-OSK foreground window (focus restoration) ─
            # SetWinEventHook fires in this thread via the GLib message pump.
            # _win_focus_restore() reads _win_last_target[0] before each SendInput
            # call so keys always land in the right window even if focus was stolen.
            WINEVENT_OUTOFCONTEXT    = 0x0000
            EVENT_SYSTEM_FOREGROUND = 0x0003
            our_hwnd = getattr(self, "_win_hwnd", None)

            WinEventProc = ctypes.WINFUNCTYPE(
                None,
                ctypes.c_void_p,  # hWinEventHook
                ctypes.c_uint,    # event
                ctypes.c_void_p,  # hwnd
                ctypes.c_long,    # idObject
                ctypes.c_long,    # idChild
                ctypes.c_ulong,   # dwEventThread
                ctypes.c_ulong,   # dwmsEventTime
            )

            def _on_fg_change(hook, event, fg_hwnd, id_obj, id_child, thread, evt_time):
                if fg_hwnd and fg_hwnd != our_hwnd:
                    _win_last_target[0] = fg_hwnd

            self._win_event_proc = WinEventProc(_on_fg_change)
            self._win_event_hook = user32.SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
                None, self._win_event_proc, 0, 0, WINEVENT_OUTOFCONTEXT,
            )
            print("[windows] foreground-window tracking hook installed")

            self.connect("destroy", self._on_windows_destroy)

        except Exception as exc:
            print(f"[windows] Focus setup failed: {exc}")

    def _on_windows_destroy(self, _widget):
        """Tear down Win32 hooks before the window is destroyed."""
        try:
            import ctypes
            user32 = ctypes.windll.user32

            hook = getattr(self, "_win_event_hook", None)
            if hook:
                user32.UnhookWinEvent(hook)

            hwnd = getattr(self, "_win_hwnd", None)
            addr = getattr(self, "_win_original_addr", None)
            if hwnd and addr:
                GWLP_WNDPROC = -4
                try:
                    user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, addr)
                except AttributeError:
                    user32.SetWindowLongW(hwnd, GWLP_WNDPROC, addr)
        except Exception as exc:
            print(f"[windows] Teardown failed: {exc}")

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
        # Scale provider — overrides min-height so keys can shrink below CSS default
        self._scale_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._scale_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

    # ── UI construction ────────────────────────────���──────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        root.set_name("main-box")
        root.set_margin_start(8)   # left/right margins act as resize zones
        root.set_margin_end(8)
        root.set_margin_top(0)     # resize strips cover top/bottom instead
        root.set_margin_bottom(0)

        # Overlay so the always-visible resize grip can float over the
        # bottom-right corner without being clipped by the resize strips.
        overlay = Gtk.Overlay()
        overlay.add(root)
        overlay.add_overlay(self._make_resize_grip())
        self.add(overlay)

        # Top resize strip — covers top edge + corners.  Packed FIRST so it
        # sits above the title bar; the title bar must never overlap it (no
        # Overlay here — the Overlay above only floats the bottom-right grip).
        self._top_strip = self._make_resize_strip("top")
        root.pack_start(self._top_strip, False, False, 0)

        # Title bar — drag surface + window controls (─ ✕).  Sits between the
        # top resize strip and the suggestion bar; always visible (it is the
        # only way to expand the keyboard once collapsed).
        self._titlebar = self._build_title_bar()
        root.pack_start(self._titlebar, False, False, 0)

        self._suggestion_bar = self._build_suggestion_bar()
        root.pack_start(self._suggestion_bar, False, False, 0)

        # Stack switches between the key grid and the settings panel
        self._key_stack = Gtk.Stack()
        self._key_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._key_stack.set_transition_duration(120)

        self._all_key_btns = []
        layout = self.settings.get("layout", "qwerty")
        rows = LAYOUT_ROWS.get(layout, KEY_ROWS)
        keys_box = self._build_keys_box(rows)

        self._key_stack.add_named(keys_box,                   "keys")
        self._key_stack.add_named(self._build_settings_panel(), "settings")
        # Emoji panel is built lazily in _open_emoji_mode() to avoid
        # FlowBoxChild GdkWindows (created during show_all) blocking key clicks.
        root.pack_start(self._key_stack, True, True, 0)

        # Bottom resize strip — covers bottom edge + corners
        self._bottom_strip = self._make_resize_strip("bottom")
        root.pack_start(self._bottom_strip, False, False, 0)

    def _build_keys_box(self, rows) -> Gtk.Box:
        """Build a vertical box of key rows from a layout definition."""
        keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for row_def in rows:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            for label, action, width, extra_classes in row_def:
                btn = self._make_key(label, action, width, extra_classes)
                row_box.pack_start(btn, True, True, 0)
            keys_box.pack_start(row_box, True, True, 0)
        return keys_box

    def _make_resize_strip(self, position: str) -> Gtk.EventBox:
        """Thin strip at top or bottom — handles edge/corner resize manually."""
        strip = Gtk.EventBox()
        strip.set_name(f"resize-strip-{position}")
        strip.set_size_request(-1, STRIP_H)
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

    def _make_resize_grip(self) -> Gtk.EventBox:
        """Always-visible bottom-right grip — click-drag to south-east resize.

        Floats as an Overlay child anchored to the bottom-right corner.  Gives
        users a clear, dwell-friendly resize target while the invisible 8-zone
        edge/corner handlers remain functional.
        """
        grip = Gtk.EventBox()
        grip.set_name("resize-grip")
        grip.set_halign(Gtk.Align.END)
        grip.set_valign(Gtk.Align.END)
        # 22×14: covers the 7px bottom strip plus ~7px of the key row above it —
        # a deliberate trade-off for a dwell-friendly target size.
        grip.set_size_request(22, 14)
        lbl = Gtk.Label(label="◢")   # corner triangle reads as a resize grip
        lbl.set_name("resize-label")
        grip.add(lbl)
        grip.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK   |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        grip.connect("button-press-event",   self._on_grip_press)
        grip.connect("motion-notify-event",  self._on_resize_motion)
        grip.connect("button-release-event", self._on_resize_release)
        # CSS `cursor` is unsupported in GTK; drive the Gdk cursor directly.
        grip.connect("enter-notify-event", self._on_grip_enter)
        grip.connect("leave-notify-event", self._on_grip_leave)
        self._resize_grip = grip
        return grip

    def _on_grip_press(self, widget, event):
        if event.button != 1:
            return False
        self._start_resize(event, "se")
        return True

    def _on_grip_enter(self, widget, _event):
        gdk_win = widget.get_window()
        if gdk_win is not None:
            display = widget.get_display()
            cursor = Gdk.Cursor.new_from_name(display, "nwse-resize") \
                or Gdk.Cursor.new_from_name(display, "se-resize")
            gdk_win.set_cursor(cursor)
        return False

    def _on_grip_leave(self, widget, _event):
        gdk_win = widget.get_window()
        if gdk_win is not None:
            gdk_win.set_cursor(None)   # revert to the inherited/default cursor
        return False

    def _build_title_bar(self) -> Gtk.EventBox:
        """Custom 26px title bar: draggable title label + window controls.

        The EventBox itself is the drag surface — left-click-drag moves the
        window via manual move tracking (Muffin ignores begin_move_drag for DOCK
        windows, so we self.move() against press-time baselines), double-click
        toggles collapse.  The minimize (─) and close (✕) buttons live at the
        far right.
        """
        bar = Gtk.EventBox()
        bar.set_name("title-bar")
        bar.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        bar.connect("button-press-event",   self._on_titlebar_press)
        bar.connect("motion-notify-event",  self._on_titlebar_motion)
        bar.connect("button-release-event", self._on_titlebar_release)
        bar.get_accessible().set_name("Keyboard title bar — drag to move")

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.add(hbox)

        # Version badge — top-left corner, small and unobtrusive.
        ver_lbl = Gtk.Label(label=f"β {__version__}")
        ver_lbl.set_name("version-badge")
        hbox.pack_start(ver_lbl, False, False, 6)

        # Title label — fills the bar so the whole strip is a drag target.
        title = Gtk.Label(label="On-Screen Keyboard")
        title.set_name("titlebar-title")
        title.set_xalign(0.0)
        hbox.pack_start(title, True, True, 0)

        # ── Window controls (Windows 11 style: [─] [✕] at the top-right) ──────
        # pack_end packs right-to-left, so the FIRST pack_end ends up rightmost.
        # Close is packed first (far right); minimize second (to its left).

        # Close button — far top-right corner
        self._close_btn = Gtk.Button(label="✕")
        self._close_btn.set_name("close-btn")
        self._close_btn.set_size_request(40, TITLEBAR_H)
        self._close_btn.connect("clicked", lambda _: self._quit())
        hbox.pack_end(self._close_btn, False, False, 4)

        # Minimize / collapse button — directly left of the close button.
        # Shows ▼ when expanded (click to collapse), ▲ when collapsed.
        self._minimize_btn = Gtk.Button(label="▼")
        self._minimize_btn.set_name("minimize-btn")
        self._minimize_btn.set_tooltip_text("Collapse / expand keyboard")
        self._minimize_btn.set_size_request(40, TITLEBAR_H)
        self._minimize_btn.connect("clicked", lambda _: self._toggle_collapse())
        hbox.pack_end(self._minimize_btn, False, False, 2)

        return bar

    def _on_titlebar_press(self, widget, event):
        if event.button != 1:
            return False
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            # Cancel any move started by the preceding single-press of the pair.
            self._moving = False
            self._toggle_collapse()
            return True
        # Manual move: begin_move_drag() is silently ignored by Muffin for DOCK
        # windows, so track the drag ourselves against press-time baselines.
        self._moving        = True
        self._move_start_x  = event.x_root
        self._move_start_y  = event.y_root
        self._move_start_wx, self._move_start_wy = self.get_position()
        return True

    def _on_titlebar_motion(self, widget, event):
        if not self._moving:
            return False
        dx = int(event.x_root - self._move_start_x)
        dy = int(event.y_root - self._move_start_y)
        self.move(self._move_start_wx + dx, self._move_start_wy + dy)
        return True

    def _on_titlebar_release(self, widget, event):
        if event.button != 1:
            return False
        if self._moving:
            self._moving = False
            self._save_geometry()
        return True

    def _build_suggestion_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.set_name("suggestion-bar")

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

        # ── Panel-mode toggles (right side): [settings] [emoji] [clipboard] ───
        # pack_end packs right-to-left, so the FIRST pack_end ends up rightmost.
        # Window controls (─ ✕) now live in the title bar above this row.

        # Clipboard button
        self._clipboard_btn = Gtk.Button(label="📋")
        self._clipboard_btn.set_name("settings-btn")
        self._clipboard_btn.set_size_request(36, SUGGESTION_H)
        self._clipboard_btn.connect("clicked", lambda _: self._toggle_clipboard_mode())
        bar.pack_end(self._clipboard_btn, False, False, 2)

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
        self._all_key_btns.append((btn, width))
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
        """Handle left/right edge resize from the side margins.

        Only fires for the left/right edge zones — the top/bottom edges (and
        their corners) are owned by the 7px resize strips, whose own EventBox
        handlers run before this window-level handler.  The corner zones here
        use STRIP_H (not EDGE_ZONE) so a press in the title-bar rows (y >= 7)
        is treated as a side EDGE, never a top/bottom CORNER — otherwise the
        title bar's top rows would be misclassified as nw/ne resize.
        """
        if event.button != 1:
            return False
        w, h = self.get_size()
        left  = event.x < EDGE_ZONE
        right = event.x > w - EDGE_ZONE
        if not (left or right):
            return False
        top    = event.y < STRIP_H
        bottom = event.y > h - STRIP_H
        if left:
            edge = "nw" if top else ("sw" if bottom else "w")
        else:
            edge = "ne" if top else ("se" if bottom else "e")
        self._start_resize(event, edge)
        return True

    # ── Manual resize ─────────────────────────────────────────────────────────

    def _start_resize(self, event, edge: str):
        self._resizing        = True
        self._resize_did_move = False
        self._resize_edge     = edge
        self._resize_start_x  = event.x_root
        self._resize_start_y  = event.y_root
        self._resize_start_w, self._resize_start_h = self.get_size()
        self._resize_start_wx, self._resize_start_wy = self.get_position()
        # Clear button height minimums so GTK won't resist vertical shrinking
        if "n" in edge or "s" in edge:
            for btn, base_w in self._all_key_btns:
                btn.set_size_request(int(base_w * self.settings.get("key_scale", 1.0)), 1)

    def _on_resize_motion(self, widget, event):
        if not self._resizing:
            return
        dx = int(event.x_root - self._resize_start_x)
        dy = int(event.y_root - self._resize_start_y)
        if abs(dx) <= 2 and abs(dy) <= 2:
            return
        self._resize_did_move = True
        edge = self._resize_edge

        # Compute every output (w, h, wx, wy) strictly from the press-time
        # baselines.  Never read get_size()/get_position() here — resize() and
        # move() are async, so the configure-event hasn't landed yet and those
        # getters return stale values, which is what made the window "walk".
        start_w  = self._resize_start_w
        start_h  = self._resize_start_h
        start_wx = self._resize_start_wx
        start_wy = self._resize_start_wy

        min_h = int(BASE_KEY_H * 6 * 0.3) + TITLEBAR_H + SUGGESTION_H + STRIP_H * 4 + 12

        # Start from the baselines.  Each edge touches ONLY the geometry
        # components it owns: horizontal edges (e/w) read dx and write width
        # (+ wx for w); vertical edges (n/s) read dy and write height (+ wy for
        # n).  dx never reaches height and dy never reaches width — so a pure
        # n/s/e/w drag stays single-axis and corners change exactly two.
        w, h   = start_w, start_h
        wx, wy = start_wx, start_wy

        # East/south edges: only size grows, the opposite edge/position anchored.
        if "e" in edge:
            w = max(MIN_W, start_w + dx)
        if "s" in edge:
            h = max(min_h, start_h + dy)

        # West/north edges: the moving edge's position shifts by the *clamped*
        # size delta so the opposite edge stays pinned.  Deriving the move from
        # (start_size - new_size) rather than from dx makes the clamp and the
        # move agree even when the size hits MIN_W / min_h.
        if "w" in edge:
            w  = max(MIN_W, start_w - dx)
            wx = start_wx + (start_w - w)
        if "n" in edge:
            h  = max(min_h, start_h - dy)
            wy = start_wy + (start_h - h)

        # Pin ONLY the dragged axes before resize().  resize() is only a
        # *request* on a resizable DOCK window; Muffin reconciles it against the
        # content's natural size on the axis we did NOT change, which is what
        # made a vertical drag also grow width (and vice-versa).  A min==max
        # request forces the WM to honour the exact computed geometry.
        #
        # But pin ONLY the axis this edge owns.  For a pure e/w drag, h is just
        # the stale start_h baseline (get_size() at press time), which does NOT
        # match the content's natural height on a resizable DOCK window — pinning
        # it to that stale value makes Muffin reject the *entire* configure
        # request, dropping the width change too, so e/w resize appeared dead.
        # Leave the un-dragged axis free (-1) so GTK uses natural size for it.
        # Cleared in _on_resize_release so normal layout/scaling resumes.
        pin_w = w if ("e" in edge or "w" in edge) else -1
        pin_h = h if ("n" in edge or "s" in edge) else -1
        self.set_size_request(pin_w, pin_h)
        self.resize(w, h)
        if "w" in edge or "n" in edge:
            self.move(wx, wy)

    def _on_resize_release(self, widget, event):
        if event.button != 1:
            return False
        # A pure click (press + release, no motion) must not reshape anything.
        # All geometry-touching work below is guarded on _resize_did_move; only
        # the state teardown runs unconditionally.
        if self._resizing and self._resize_did_move:
            # Release the min==max pin set during the drag so the window can
            # flow to its content again (and so _apply_key_scale's resize() can
            # take).  Only _on_resize_motion ever sets this pin, so it's only
            # present when motion occurred.
            self.set_size_request(-1, -1)
            scale = self.settings.get("key_scale", 1.0)
            if self._resize_edge and not self._collapsed:
                # Derive scale from the new window height and snap buttons to fit
                _, new_h = self.get_size()
                overhead = TITLEBAR_H + SUGGESTION_H + STRIP_H * 4 + 12
                scale = round(
                    max(0.3, min(1.5, (new_h - overhead) / (BASE_KEY_H * 6))), 2
                )
                self.settings["key_scale"] = scale
                self._save_settings()
                if hasattr(self, "_key_scale_label") and self._key_scale_label:
                    self._key_scale_label.set_label(f"{scale:.2f}×")
            # Always restore button sizes (covers the collapsed-drag path where
            # scale doesn't change but min-heights were stripped by _start_resize)
            self._apply_key_scale(scale)
        elif self._resizing and ("n" in self._resize_edge or "s" in self._resize_edge):
            # No motion, but _start_resize stripped the button height minimums
            # to 1px for n/s edges.  Restore them at the current scale without
            # touching window geometry, otherwise a pure click on the top/bottom
            # strip leaves the keys squashed.
            scale = self.settings.get("key_scale", 1.0)
            h = int(BASE_KEY_H * scale)
            for btn, base_w in self._all_key_btns:
                btn.set_size_request(int(base_w * scale), h)
        self._resizing        = False
        self._resize_did_move = False
        self._resize_edge     = ""

    # ── Drag to move ──────────────────────────────────────────────────────────

    def _save_geometry(self):
        """Persist current window position and size to settings."""
        x, y = self.get_position()
        w, h = self.get_size()
        # If collapsed, persist the expanded height so we don't relaunch stuck
        # at the strip height with _collapsed=False (which would be unrecoverable).
        if self._collapsed and self._pre_collapse_h:
            h = self._pre_collapse_h
        self.settings["win_x"] = x
        self.settings["win_y"] = y
        self.settings["win_w"] = w
        self.settings["win_h"] = h
        self._save_settings()

    def _cancel_all_timers(self):
        """Cancel all pending GLib timers to prevent callbacks firing on torn-down widgets."""
        for tid in list(self._repeat_timers.values()):
            GLib.source_remove(tid)
        self._repeat_timers.clear()
        self._repeat_active.clear()
        for tid in list(self._dwell_timers.values()):
            GLib.source_remove(tid)
        self._dwell_timers.clear()
        if self._fuzzy_timer is not None:
            GLib.source_remove(self._fuzzy_timer)
            self._fuzzy_timer = None

    def _quit(self):
        self._cancel_all_timers()
        self._save_geometry()
        Gtk.main_quit()

    def _on_close(self, widget, event):
        """Save window geometry when closed via WM (alt-F4 etc.)."""
        self._cancel_all_timers()
        self._save_geometry()
        return False  # allow the close to proceed

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

        # ── Clipboard mode — Escape closes it ───────────────────────────────
        if self._clipboard_mode:
            if action == "escape":
                self._close_clipboard_mode()
            return

        # ── Custom word input mode ───────────────────────────────────────────
        if self._custom_input_mode:
            if action == "escape":
                self._close_custom_input_mode()
            elif action == "return":
                self._confirm_custom_word()
            elif action == "backspace":
                if self._custom_input_text:
                    self._custom_input_text = self._custom_input_text[:-1]
                    self._update_custom_input_display()
                else:
                    self._close_custom_input_mode()
            elif action == "space":
                self._custom_input_text += " "
                self._update_custom_input_display()
            elif len(action) == 1 and action.isalpha():
                char = action.upper() if (self.shift_active ^ self.caps_lock) else action.lower()
                self._custom_input_text += char
                if self.shift_active and not self.settings.get("shift_sticky", False):
                    self.shift_active = False
                    self._update_modifier_visuals()
                self._update_custom_input_display()
            elif action in SHIFT_MAP:
                char = SHIFT_MAP[action] if self.shift_active else action
                self._custom_input_text += char
                if self.shift_active and not self.settings.get("shift_sticky", False):
                    self.shift_active = False
                    self._update_modifier_visuals()
                self._update_custom_input_display()
            elif len(action) == 1 and action.isdigit():
                self._custom_input_text += action
                self._update_custom_input_display()
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
                if self.shift_active and not self.settings.get("shift_sticky", False):
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
            if self.current_word:
                self._last_word = self.current_word
            self.current_word = ""
            self._refresh_suggestions()

        elif action == "space":
            self.typer.send_special("space", mods)
            if self.current_word:
                self._last_word = self.current_word
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

    # ── Custom word input mode ────────────────────────────────────────────────

    def _open_custom_input_mode(self, target: str = "word"):
        """Switch to key grid and capture typing into the custom word buffer."""
        self._custom_input_mode_target = target
        # Close any other active panel
        if self._settings_mode:
            self._toggle_settings()
        if self._emoji_mode:
            self._close_emoji_mode()
        self._custom_input_mode = True
        self._custom_input_text = ""
        if self._key_stack:
            self._key_stack.set_visible_child_name("keys")
        self._update_custom_input_display()

    def _close_custom_input_mode(self):
        target = self._custom_input_mode_target
        self._custom_input_mode = False
        self._custom_input_text = ""
        self._custom_input_mode_target = "word"
        if target in ("macro_trigger", "macro_expansion"):
            self._macro_draft = {}
        if self._search_label:
            self._search_label.hide()
        self._refresh_suggestions()

    def _confirm_custom_word(self):
        word = self._custom_input_text.strip()
        if self._custom_input_mode_target == "theme_name":
            if word:
                self._wizard_name = word
            self._close_custom_input_mode()
            if self._wizard_name:
                self._wizard_step   = 1
                self._wizard_colors = dict(WIZARD_DEFAULTS)
                self._open_wizard_color_step()
        elif self._custom_input_mode_target == "macro_trigger":
            if word:
                self._macro_draft["trigger"] = word
                self._open_custom_input_mode(target="macro_expansion")
            else:
                self._close_custom_input_mode()
        elif self._custom_input_mode_target == "macro_expansion":
            if word and "trigger" in self._macro_draft:
                self._add_macro(self._macro_draft["trigger"], word)
            self._macro_draft = {}
            self._close_custom_input_mode()
        else:
            if word:
                self._add_custom_word(word)
            self._close_custom_input_mode()

    def _update_custom_input_display(self):
        if self._search_label:
            txt = self._custom_input_text
            if self._custom_input_mode_target == "theme_name":
                prompt = "🎨 Type theme name, ↵ to confirm"
                self._search_label.set_text(f"🎨 {txt}▏" if txt else prompt)
            elif self._custom_input_mode_target == "macro_trigger":
                prompt = "🔤 Macro shortcut: type trigger, ↵"
                self._search_label.set_text(f"🔤 {txt}▏" if txt else prompt)
            elif self._custom_input_mode_target == "macro_expansion":
                trigger = self._macro_draft.get("trigger", "")
                prompt = f"↪ Expansion for '{trigger}': type text, ↵"
                self._search_label.set_text(f"↪ {txt}▏" if txt else prompt)
            else:
                self._search_label.set_text(f"+ {txt}▏" if txt else "+ type word/phrase, ↵ to save")
            self._search_label.show()
        for btn in self._suggestion_btns:
            btn.set_label("")
            btn.set_sensitive(False)

    def _add_custom_word(self, word: str):
        if word not in self._custom_words:
            self._custom_words.append(word)
            self.predictor.set_custom_words(self._custom_words)
            self._save_custom_words()
            self._rebuild_custom_words_list()

    def _remove_custom_word(self, word: str):
        if word in self._custom_words:
            self._custom_words.remove(word)
            self.predictor.set_custom_words(self._custom_words)
            self._save_custom_words()
            self._rebuild_custom_words_list()

    def _load_custom_words(self) -> list[str]:
        try:
            with open(CUSTOM_WORDS_FILE, "r") as f:
                data = json.load(f)
            return [w for w in data if isinstance(w, str)]
        except Exception:
            return []

    def _save_custom_words(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CUSTOM_WORDS_FILE, "w") as f:
                json.dump(self._custom_words, f, indent=2)
        except Exception as exc:
            print(f"[custom] Could not save: {exc}")

    def _rebuild_custom_words_list(self):
        """Refresh the ListBox in the settings panel to reflect current custom words."""
        lb = self._custom_words_listbox
        if lb is None:
            return
        # Remove all existing rows
        for row in lb.get_children():
            lb.remove(row)
        # Re-add
        for word in self._custom_words:
            lb.add(self._make_custom_word_row(word))
        lb.show_all()
        # Update the count label
        if self._custom_words_count:
            n = len(self._custom_words)
            self._custom_words_count.set_text(f"{n} word{'s' if n != 1 else ''}")

    def _make_custom_word_row(self, word: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(6)
        box.set_margin_end(4)
        box.set_margin_top(2)
        box.set_margin_bottom(2)

        lbl = Gtk.Label(label=word)
        lbl.set_name("settings-label")
        lbl.set_xalign(0.0)
        lbl.set_hexpand(True)
        box.pack_start(lbl, True, True, 0)

        remove_btn = Gtk.Button(label="×")
        remove_btn.get_style_context().add_class("settings-choice")
        remove_btn.set_size_request(30, -1)
        remove_btn.connect("clicked", lambda _b, w=word: self._remove_custom_word(w))
        box.pack_end(remove_btn, False, False, 0)

        row.add(box)
        return row

    # ── Macros ────────────────────────────────────────────────────────────────

    def _load_macros(self) -> list[dict]:
        try:
            with open(MACROS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict)]
        except FileNotFoundError:
            self._save_macros_list(DEFAULT_MACROS)
            return list(DEFAULT_MACROS)
        except Exception:
            pass
        return []

    def _save_macros(self):
        self._save_macros_list(self._macros)

    def _save_macros_list(self, macros: list[dict]):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(MACROS_FILE, "w") as f:
                json.dump(macros, f, indent=2)
        except Exception as exc:
            print(f"[macros] Could not save: {exc}")

    def _expand_macro(self, expansion: str) -> str:
        import datetime
        now = datetime.datetime.now()
        expansion = expansion.replace("{date}", now.strftime("%d/%m/%Y"))
        expansion = expansion.replace("{time}", now.strftime("%H:%M"))
        return expansion

    def _add_macro(self, trigger: str, expansion: str):
        self._macros.append({"trigger": trigger, "expansion": expansion})
        self._save_macros()
        self._rebuild_macros_list()

    def _remove_macro(self, trigger: str):
        self._macros = [m for m in self._macros if m.get("trigger") != trigger]
        self._save_macros()
        self._rebuild_macros_list()

    def _rebuild_macros_list(self):
        lb = self._macros_listbox
        if lb is None:
            return
        for row in lb.get_children():
            lb.remove(row)
        for macro in self._macros:
            lb.add(self._make_macro_row(macro))
        lb.show_all()
        if self._macros_count:
            n = len(self._macros)
            self._macros_count.set_text(f"{n} macro{'s' if n != 1 else ''}")

    def _make_macro_row(self, macro: dict) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(6)
        box.set_margin_end(4)
        box.set_margin_top(2)
        box.set_margin_bottom(2)

        trigger = macro.get("trigger", "")
        expansion = macro.get("expansion", "")
        lbl = Gtk.Label(label=f"{trigger} → {expansion}")
        lbl.set_name("settings-label")
        lbl.set_xalign(0.0)
        lbl.set_hexpand(True)
        box.pack_start(lbl, True, True, 0)

        remove_btn = Gtk.Button(label="×")
        remove_btn.get_style_context().add_class("settings-choice")
        remove_btn.set_size_request(30, -1)
        remove_btn.connect("clicked", lambda _b, t=trigger: self._remove_macro(t))
        box.pack_end(remove_btn, False, False, 0)

        row.add(box)
        return row

    # ── Clipboard ─────────────────────────────────────────────────────────────

    def _on_clipboard_change(self, clipboard, event):
        clipboard.request_text(self._on_clipboard_text_received)

    def _on_clipboard_text_received(self, clipboard, text):
        if not text:
            return
        text = text.strip()
        if not text:
            return
        # Cap individual items to 10 KB to avoid memory bloat from large copies
        text = text[:10000]
        # Deduplicate: remove existing copy then prepend
        if text in self._clipboard_history:
            self._clipboard_history.remove(text)
        self._clipboard_history.insert(0, text)
        # Cap at 10 items
        self._clipboard_history = self._clipboard_history[:10]

    def _toggle_clipboard_mode(self):
        if self._clipboard_mode:
            self._close_clipboard_mode()
        else:
            self._open_clipboard_mode()

    def _open_clipboard_mode(self):
        self._clipboard_mode = True
        # Close other panels
        if self._settings_mode:
            self._toggle_settings()
        if self._emoji_mode:
            self._close_emoji_mode()

        if not self._clipboard_panel_ready and self._key_stack:
            panel = self._build_clipboard_panel()
            self._key_stack.add_named(panel, "clipboard")
            panel.show_all()
            self._clipboard_panel_ready = True
        else:
            # Rebuild the list with latest history
            self._rebuild_clipboard_list()

        if self._clipboard_btn:
            self._clipboard_btn.get_style_context().add_class("active")
        if self._key_stack:
            self._key_stack.set_visible_child_name("clipboard")

    def _close_clipboard_mode(self):
        self._clipboard_mode = False
        if self._clipboard_btn:
            self._clipboard_btn.get_style_context().remove_class("active")
        if self._key_stack:
            self._key_stack.set_visible_child_name("keys")
        self._refresh_suggestions()

    def _build_clipboard_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        panel.set_name("settings-panel")
        panel.set_margin_start(8)
        panel.set_margin_end(8)
        panel.set_margin_top(4)
        panel.set_margin_bottom(4)

        # Clear button at top
        clear_btn = Gtk.Button(label="Clear history")
        clear_btn.get_style_context().add_class("settings-choice")
        clear_btn.connect("clicked", self._on_clipboard_clear)
        panel.pack_start(clear_btn, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self._clipboard_listbox = Gtk.ListBox()
        self._clipboard_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._clipboard_listbox.set_name("settings-panel")
        self._rebuild_clipboard_list()

        scroll.add(self._clipboard_listbox)
        panel.pack_start(scroll, True, True, 0)
        return panel

    def _rebuild_clipboard_list(self):
        lb = self._clipboard_listbox
        if lb is None:
            return
        for row in lb.get_children():
            lb.remove(row)
        for item in self._clipboard_history:
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            box.set_margin_start(4)
            box.set_margin_end(4)
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            # Truncate display text
            display = item[:40] + ("…" if len(item) > 40 else "")
            btn = Gtk.Button(label=display)
            btn.get_style_context().add_class("settings-choice")
            btn.set_hexpand(True)
            btn.connect("clicked", lambda _b, text=item: self._on_clipboard_item_clicked(text))
            box.pack_start(btn, True, True, 0)
            row.add(box)
            lb.add(row)
        lb.show_all()

    def _on_clipboard_item_clicked(self, text: str):
        for ch in text:
            self.typer.type_char(ch)
        self._close_clipboard_mode()

    def _on_clipboard_clear(self, _btn):
        self._clipboard_history = []
        self._rebuild_clipboard_list()

    # ── Key size scaling ──────────────────────────────────────────────────────

    def _apply_key_scale(self, scale: float, reposition: bool = False):
        h = int(BASE_KEY_H * scale)
        for btn, base_w in self._all_key_btns:
            btn.set_size_request(int(base_w * scale), h)
        keys_h = int(BASE_KEY_H * 6 * scale)
        total_h = keys_h + TITLEBAR_H + SUGGESTION_H + 7 * 4 + 12
        w, _ = self.get_size()
        self.resize(w, total_h)
        if reposition:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geo = monitor.get_geometry()
            self.move(0, geo.height - total_h)

    # ── Layout switcher ───────────────────────────────────────────────────────

    def _switch_layout(self, layout: str):
        self._cancel_all_timers()
        self.settings["layout"] = layout
        self._save_settings()
        # Clear tracked widget collections
        self._letter_btns   = {}
        self._shift_btns    = []
        self._caps_btn      = None
        self._modifier_btns = {"ctrl": [], "alt": [], "win": []}
        self._symbol_btns   = {}
        self._all_key_btns  = []

        if self._key_stack:
            old_child = self._key_stack.get_child_by_name("keys")
            if old_child:
                self._key_stack.remove(old_child)

        rows = LAYOUT_ROWS.get(layout, KEY_ROWS)
        keys_box = self._build_keys_box(rows)
        self._key_stack.add_named(keys_box, "keys")
        keys_box.show_all()

        if not (self._settings_mode or self._emoji_mode or
                self._clipboard_mode or self._app_mode or
                self._custom_input_mode):
            self._key_stack.set_visible_child_name("keys")

        self._apply_key_scale(self.settings.get("key_scale", 1.0))
        self._refresh_layout_buttons()

    def _refresh_layout_buttons(self):
        current = self.settings.get("layout", "qwerty")
        for key, btn in self._layout_btns.items():
            ctx = btn.get_style_context()
            if key == current:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    # ── Font family ───────────────────────────────────────────────────────────

    def _on_font_family_clicked(self, family: str):
        self.settings["font_family"] = family
        self._reload_theme_css()
        self._save_settings()
        self._refresh_font_family_buttons()

    def _refresh_font_family_buttons(self):
        current = self.settings.get("font_family", "Ubuntu")
        for family, btn in self._font_family_btns.items():
            ctx = btn.get_style_context()
            if family == current:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    # ── DIY theme wizard ──────────────────────────────────────────────────────

    def _open_theme_wizard(self):
        """Start the theme wizard: collect name via keyboard, then 6 colour steps."""
        self._wizard_name   = ""
        self._wizard_step   = 0
        self._wizard_colors = dict(WIZARD_DEFAULTS)
        self._open_custom_input_mode(target="theme_name")

    def _open_wizard_color_step(self):
        """Show the colour-picker page for the current wizard step (1–6)."""
        if not self._wizard_page_ready and self._key_stack:
            page = self._build_theme_wizard_page()
            self._key_stack.add_named(page, "theme-wizard")
            page.show_all()
            self._wizard_page_ready = True

        self._wizard_update_step()
        if self._key_stack:
            self._key_stack.set_visible_child_name("theme-wizard")

        # Show a live preview of current wizard colours
        self._wizard_preview_theme()

    def _build_theme_wizard_page(self) -> Gtk.Box:
        """Build the compact theme-wizard UI (added to the stack lazily)."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_name("settings-panel")
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(12)
        page.set_margin_bottom(12)

        # ── Top row: step counter + cancel button ──────────────────────────
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._wizard_step_label = Gtk.Label(label="Step 1 / 6")
        self._wizard_step_label.set_name("settings-label")
        self._wizard_step_label.set_xalign(0.0)
        self._wizard_step_label.set_hexpand(True)
        top_row.pack_start(self._wizard_step_label, True, True, 0)

        cancel_btn = Gtk.Button(label="✕ Cancel")
        cancel_btn.get_style_context().add_class("settings-choice")
        cancel_btn.connect("clicked", lambda _: self._cancel_theme_wizard())
        top_row.pack_end(cancel_btn, False, False, 0)
        page.pack_start(top_row, False, False, 0)

        # ── Question label ─────────────────────────────────────────────────
        self._wizard_question_label = Gtk.Label(label="")
        self._wizard_question_label.set_name("settings-label")
        self._wizard_question_label.set_xalign(0.0)
        page.pack_start(self._wizard_question_label, False, False, 0)

        # ── Colour picker button ───────────────────────────────────────────
        picker_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self._wizard_color_btn = Gtk.ColorButton()
        self._wizard_color_btn.set_use_alpha(False)
        self._wizard_color_btn.set_size_request(160, 52)
        self._wizard_color_btn.connect("color-set", self._on_wizard_color_set)
        picker_row.pack_start(self._wizard_color_btn, False, False, 0)

        # Colour swatches: one box per wizard step, filled in as user proceeds
        swatch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        swatch_box.set_valign(Gtk.Align.CENTER)
        self._wizard_preview_swatches = []
        for _i in range(len(WIZARD_STEPS)):
            eb = Gtk.EventBox()
            eb.set_size_request(28, 28)
            swatch_box.pack_start(eb, False, False, 0)
            self._wizard_preview_swatches.append(eb)
        picker_row.pack_start(swatch_box, True, True, 0)
        page.pack_start(picker_row, False, False, 0)

        # ── Theme name display ─────────────────────────────────────────────
        self._wizard_name_display = Gtk.Label(label="")
        self._wizard_name_display.set_name("settings-label")
        self._wizard_name_display.set_xalign(0.0)
        page.pack_start(self._wizard_name_display, False, False, 0)

        # ── Navigation buttons ─────────────────────────────────────────────
        nav_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav_row.set_vexpand(True)
        nav_row.set_valign(Gtk.Align.END)

        back_btn = Gtk.Button(label="◀ Back")
        back_btn.get_style_context().add_class("settings-choice")
        back_btn.set_size_request(100, -1)
        back_btn.connect("clicked", lambda _: self._wizard_go_back())
        nav_row.pack_start(back_btn, False, False, 0)

        spacer = Gtk.Label(label="")
        nav_row.pack_start(spacer, True, True, 0)

        self._wizard_next_btn = Gtk.Button(label="Next ▶")
        self._wizard_next_btn.get_style_context().add_class("settings-choice")
        self._wizard_next_btn.set_size_request(100, -1)
        self._wizard_next_btn.connect("clicked", lambda _: self._wizard_go_next())
        nav_row.pack_end(self._wizard_next_btn, False, False, 0)

        page.pack_start(nav_row, True, True, 0)
        return page

    def _wizard_update_step(self):
        """Refresh wizard page labels and colour button for the current step."""
        step = self._wizard_step
        total = len(WIZARD_STEPS)

        if self._wizard_step_label:
            self._wizard_step_label.set_text(f"Step {step} / {total}")

        question, color_key = WIZARD_STEPS[step - 1]
        if self._wizard_question_label:
            self._wizard_question_label.set_text(question)

        if self._wizard_color_btn:
            self._wizard_color_btn.set_rgba(
                _hex_to_gdk_rgba(self._wizard_colors.get(color_key, "#888888")))

        if self._wizard_next_btn:
            self._wizard_next_btn.set_label(
                "Save ✓" if step == total else "Next ▶")

        if hasattr(self, "_wizard_name_display") and self._wizard_name_display:
            self._wizard_name_display.set_text(f"Theme: {self._wizard_name}")

        # Update swatches
        for i, (_, ck) in enumerate(WIZARD_STEPS):
            if i < len(self._wizard_preview_swatches):
                eb = self._wizard_preview_swatches[i]
                hex_c = self._wizard_colors.get(ck, "#888888")
                # Mark steps already configured with their chosen colour;
                # future steps shown dimmed.
                if (i + 1) <= step:
                    self._set_swatch_color(eb, hex_c)
                else:
                    self._set_swatch_color(eb, _mix(hex_c, "#888888", 0.7))

    def _set_swatch_color(self, eb: Gtk.EventBox, hex_c: str):
        """Apply a background colour to an EventBox via an inline CSS provider."""
        provider = Gtk.CssProvider()
        provider.load_from_data(
            f"* {{ background-color: {hex_c}; border-radius: 4px; }}".encode()
        )
        eb.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10)

    def _on_wizard_color_set(self, btn: Gtk.ColorButton):
        """User confirmed a colour in the picker — store it and preview live."""
        _, color_key = WIZARD_STEPS[self._wizard_step - 1]
        self._wizard_colors[color_key] = _gdk_rgba_to_hex(btn.get_rgba())
        # Update the swatch for this step immediately
        if (self._wizard_step - 1) < len(self._wizard_preview_swatches):
            self._set_swatch_color(
                self._wizard_preview_swatches[self._wizard_step - 1],
                self._wizard_colors[color_key],
            )
        self._wizard_preview_theme()

    def _wizard_preview_theme(self):
        """Apply current wizard colours to the keyboard CSS as a live preview."""
        c = _build_custom_theme(**self._wizard_colors)
        font_size   = self.settings.get("font_size", 14)
        font_family = self.settings.get("font_family", "Ubuntu")
        self._theme_provider.load_from_data(_make_theme_css(c, font_size, font_family).encode())

    def _wizard_go_back(self):
        """Go back one colour step, or back to name entry if at step 1."""
        if self._wizard_step <= 1:
            # Return to settings, restore original theme
            self._cancel_theme_wizard()
        else:
            self._wizard_step -= 1
            self._wizard_update_step()

    def _wizard_go_next(self):
        """Advance one colour step, or save and finish if on last step."""
        if self._wizard_step >= len(WIZARD_STEPS):
            self._wizard_save_theme()
        else:
            self._wizard_step += 1
            self._wizard_update_step()
            self._wizard_preview_theme()

    def _wizard_save_theme(self):
        """Persist the new custom theme, apply it, and return to settings."""
        colors = _build_custom_theme(**self._wizard_colors)
        name   = self._wizard_name

        # Register in the global table and in our custom themes dict
        THEME_COLORS[name]          = colors
        self._custom_themes[name]   = self._wizard_colors.copy()
        self._save_custom_themes()

        # Apply the new theme
        self.settings["theme"] = name
        self._reload_theme_css()
        self._save_settings()

        # Rebuild the custom theme buttons in settings
        self._rebuild_custom_theme_buttons()

        # Return to settings panel
        self._wizard_page_ready = False
        if self._key_stack:
            # Remove the stale wizard page so it gets rebuilt fresh next time
            wizard_page = self._key_stack.get_child_by_name("theme-wizard")
            if wizard_page:
                self._key_stack.remove(wizard_page)
        self._toggle_settings()
        self._refresh_theme_buttons()

    def _cancel_theme_wizard(self):
        """Discard wizard changes and return to the settings panel."""
        self._wizard_page_ready = False
        if self._key_stack:
            wizard_page = self._key_stack.get_child_by_name("theme-wizard")
            if wizard_page:
                self._key_stack.remove(wizard_page)
        # Restore original theme CSS
        self._reload_theme_css()
        self._toggle_settings()

    # ── Custom theme persistence ───────────────────────────────────────────────

    def _load_custom_themes(self) -> dict:
        """Load custom themes from disk: {name: {bg, key_bg, key_fg, ...}}"""
        try:
            with open(CUSTOM_THEMES_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for colors in data.values():
                    if isinstance(colors, dict):
                        self._backfill_theme_keys(colors)
                return data
        except Exception:
            pass
        return {}

    @staticmethod
    def _backfill_theme_keys(colors: dict) -> None:
        """Add any colour keys missing from an older saved theme, in place.

        Themes saved before a new key was introduced (e.g. the title-bar keys)
        would otherwise raise KeyError in _make_theme_css.  Derive sane defaults
        from existing colours so legacy themes keep working.
        """
        try:
            if "titlebar_bg" not in colors:
                colors["titlebar_bg"] = _adjust(colors.get("bar_bg", colors["bg"]), 0.95)
            if "titlebar_fg" not in colors:
                colors["titlebar_fg"] = _mix(colors["key_fg"], colors["bg"], 0.15)
            if "titlebar_border" not in colors:
                colors["titlebar_border"] = colors.get("bar_border", colors.get("key_border", "#333333"))
        except (KeyError, ValueError):
            # Malformed theme — fall back to dark-theme title-bar colours.
            colors.setdefault("titlebar_bg", "#1a1a1a")
            colors.setdefault("titlebar_fg", "#cccccc")
            colors.setdefault("titlebar_border", "#333333")

    def _save_custom_themes(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CUSTOM_THEMES_FILE, "w") as f:
                json.dump(self._custom_themes, f, indent=2)
        except Exception as exc:
            print(f"[themes] Could not save custom themes: {exc}")

    def _delete_custom_theme(self, name: str):
        """Remove a custom theme and rebuild buttons."""
        self._custom_themes.pop(name, None)
        THEME_COLORS.pop(name, None)
        self._save_custom_themes()
        # If the deleted theme is active, fall back to dark
        if self.settings.get("theme") == name:
            self._apply_theme("dark")
        self._rebuild_custom_theme_buttons()
        self._refresh_theme_buttons()

    def _rebuild_custom_theme_buttons(self):
        """Repopulate the custom-themes row in the settings panel."""
        box = self._custom_theme_btns_box
        if box is None:
            return
        for child in box.get_children():
            box.remove(child)

        current = self.settings.get("theme", "dark")
        for name in list(self._custom_themes.keys()):
            # Wrapper so each name + del button stay together
            wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

            sel_btn = Gtk.Button(label=name)
            sel_btn.get_style_context().add_class("settings-choice")
            sel_btn.connect("clicked", lambda _b, n=name: self._apply_theme(n))
            if name == current:
                sel_btn.get_style_context().add_class("active")
            wrap.pack_start(sel_btn, True, True, 0)
            self._custom_theme_btns[name] = sel_btn

            del_btn = Gtk.Button(label="×")
            del_btn.get_style_context().add_class("settings-choice")
            del_btn.set_size_request(28, -1)
            del_btn.connect("clicked", lambda _b, n=name: self._delete_custom_theme(n))
            wrap.pack_start(del_btn, False, False, 0)

            box.pack_start(wrap, True, True, 0)

        box.show_all()

    # ── Toggle app mode ───────────────────────────────────────────────────────

    def _toggle_app_mode(self):
        if IS_WINDOWS:
            # On Windows, just open the Start menu via Win key press
            if PYNPUT_OK and _pynput_ctrl:
                import pynput.keyboard as _pk
                _pynput_ctrl.press(_pk.Key.cmd)
                _pynput_ctrl.release(_pk.Key.cmd)
            return
        # Showing/hiding self._search_label and swapping the suggestion-button
        # labels for app names changes the suggestion bar's minimum size, which
        # makes GTK re-expand (or revert) the window.  Pin the user's dragged
        # geometry across the whole mode switch so the Win key never resizes us.
        if self._collapsed:
            self._expand()
        with self._preserve_window_size():
            if self._app_mode:
                self._close_app_mode()
            else:
                if self._emoji_mode:
                    self._close_emoji_mode()
                if self._settings_mode:
                    self._toggle_settings()
                self._open_app_mode()

    @contextlib.contextmanager
    def _preserve_window_size(self):
        """Snapshot the current window size, run the body, then re-pin the size.

        Child-widget changes (showing the search label, relabelling the
        suggestion buttons) can shift the container's minimum size and cause GTK
        to grow or shrink the toplevel.  GTK recomputes geometry on the next
        main-loop iteration, so we re-assert the size both immediately and via
        an idle callback to catch the deferred resize.
        """
        w, h = self.get_size()
        try:
            yield
        finally:
            if self.get_size() != (w, h):
                self.resize(w, h)

            def _repin():
                if self.get_size() != (w, h):
                    self.resize(w, h)
                return False  # one-shot

            GLib.idle_add(_repin)

    # ── Collapse / expand (Windows 11-style minimize) ─────────────────────────

    def _toggle_collapse(self):
        if self._collapsed:
            self._expand()
        else:
            self._collapse()

    def _collapse(self):
        """Shrink to the title bar + suggestion bar; hide only the key grid.

        The title bar (with its ─/✕ controls) and the suggestion bar both stay
        visible — the title bar is the only way to expand again, and keeping the
        suggestion bar lets word prediction / panel toggles remain reachable in
        the collapsed state.
        """
        if self._collapsed:
            return
        w, h = self.get_size()
        self._pre_collapse_h = h
        self._collapsed = True

        # Hide only the key grid and the bottom resize strip.  The title bar is
        # never hidden, and the suggestion bar stays visible (the collapsed
        # height budgets for both — see _collapsed_h).
        if self._key_stack:
            self._key_stack.hide()
        if self._bottom_strip:
            self._bottom_strip.hide()

        if self._minimize_btn:
            self._minimize_btn.set_label("▲")   # chevron-up: click to expand

        # Cancel timers tied to now-hidden keys so they can't fire on hidden widgets
        self._cancel_all_timers()
        self.resize(w, self._collapsed_h)

    def _expand(self):
        """Restore the full keyboard to its pre-collapse height."""
        if not self._collapsed:
            return
        self._collapsed = False
        w, _ = self.get_size()

        if self._key_stack:
            self._key_stack.show()
        if self._bottom_strip:
            self._bottom_strip.show()

        if self._minimize_btn:
            self._minimize_btn.set_label("▼")   # chevron-down: click to collapse

        target_h = self._pre_collapse_h or (
            int(BASE_KEY_H * 6 * self.settings.get("key_scale", 1.0))
            + TITLEBAR_H + SUGGESTION_H + 7 * 4 + 12
        )
        self.resize(w, target_h)
        # Repopulate the suggestion bar for whatever mode is active.
        if self._app_mode:
            self._refresh_app_results()
        elif not (self._custom_input_mode or self._emoji_mode or
                  self._clipboard_mode or self._settings_mode):
            self._refresh_suggestions()

    # ── Snipping tool ─────────────────────────────────────────────────────────

    def _launch_snipping_tool(self):
        """Launch the best available screenshot/snipping tool for region capture."""
        if IS_WINDOWS:
            # Windows 10/11 — open Snipping Tool snip mode via its URI
            for cmd in (
                ["explorer", "ms-screenclip:"],
                ["SnippingTool.exe", "/clip"],
                ["SnippingTool.exe"],
            ):
                if shutil.which(cmd[0]) or cmd[0] == "explorer":
                    try:
                        subprocess.Popen(
                            cmd, close_fds=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    except Exception as exc:
                        print(f"[snip] Windows: {exc}")
                    return
            # Last resort: Win+Shift+S via pynput
            if PYNPUT_OK:
                try:
                    from pynput.keyboard import Key as _K
                    with _pynput_ctrl.pressed(_K.cmd):
                        with _pynput_ctrl.pressed(_K.shift):
                            _pynput_ctrl.press('s')
                            _pynput_ctrl.release('s')
                except Exception as exc:
                    print(f"[snip] pynput Win+Shift+S: {exc}")
            return

        # Linux — ordered by preference
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

        # Wrap in a scrolled window so all settings are accessible
        scroll_outer = Gtk.ScrolledWindow()
        scroll_outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_outer.set_vexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_name("settings-panel")
        inner.set_margin_start(0)
        inner.set_margin_end(0)
        inner.set_margin_top(0)
        inner.set_margin_bottom(0)

        # ── 1. Theme row ───────────────────────────────────────────────────
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

        inner.pack_start(theme_row, False, False, 0)

        # ── 2. Layout row ──────────────────────────────────────────────────
        layout_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_layout = Gtk.Label(label="Layout")
        lbl_layout.set_name("settings-label")
        lbl_layout.set_xalign(0.0)
        lbl_layout.set_size_request(90, -1)
        layout_row.pack_start(lbl_layout, False, False, 0)

        for key in ("qwerty", "azerty", "qwertz"):
            btn = Gtk.Button(label=key.upper())
            btn.get_style_context().add_class("settings-choice")
            btn.connect("clicked", lambda _b, k=key: self._switch_layout(k))
            layout_row.pack_start(btn, True, True, 0)
            self._layout_btns[key] = btn

        inner.pack_start(layout_row, False, False, 0)

        # ── 3. Font family row ─────────────────────────────────────────────
        font_fam_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_ff = Gtk.Label(label="Font")
        lbl_ff.set_name("settings-label")
        lbl_ff.set_xalign(0.0)
        lbl_ff.set_size_request(90, -1)
        font_fam_row.pack_start(lbl_ff, False, False, 0)

        for family in FONT_FAMILIES:
            # Use abbreviated label for common families
            short = family.split()[0]
            btn = Gtk.Button(label=short)
            btn.get_style_context().add_class("settings-choice")
            btn.set_tooltip_text(family)
            btn.connect("clicked", lambda _b, f=family: self._on_font_family_clicked(f))
            font_fam_row.pack_start(btn, True, True, 0)
            self._font_family_btns[family] = btn

        inner.pack_start(font_fam_row, False, False, 0)

        # ── 4. Font size row ───────────────────────────────────────────────
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

        inner.pack_start(font_row, False, False, 0)

        # ── 5. Key size row ────────────────────────────────────────────────
        key_scale_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_ks = Gtk.Label(label="Key size")
        lbl_ks.set_name("settings-label")
        lbl_ks.set_xalign(0.0)
        lbl_ks.set_size_request(90, -1)
        key_scale_row.pack_start(lbl_ks, False, False, 0)

        ks_dec = Gtk.Button(label="−")
        ks_dec.get_style_context().add_class("settings-choice")
        ks_dec.set_size_request(36, -1)
        ks_dec.connect("clicked", self._on_key_scale_change, -0.05)
        key_scale_row.pack_start(ks_dec, False, False, 0)

        cur_scale = self.settings.get("key_scale", 1.0)
        self._key_scale_label = Gtk.Label(label=f"{cur_scale:.2f}×")
        self._key_scale_label.set_name("settings-label")
        self._key_scale_label.set_size_request(46, -1)
        key_scale_row.pack_start(self._key_scale_label, False, False, 0)

        ks_inc = Gtk.Button(label="+")
        ks_inc.get_style_context().add_class("settings-choice")
        ks_inc.set_size_request(36, -1)
        ks_inc.connect("clicked", self._on_key_scale_change, +0.05)
        key_scale_row.pack_start(ks_inc, False, False, 0)

        inner.pack_start(key_scale_row, False, False, 0)

        # ── 6. Opacity row ─────────────────────────────────────────────────
        opacity_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_op = Gtk.Label(label="Opacity")
        lbl_op.set_name("settings-label")
        lbl_op.set_xalign(0.0)
        lbl_op.set_size_request(90, -1)
        opacity_row.pack_start(lbl_op, False, False, 0)

        cur_opacity = self.settings.get("opacity", 1.0)
        opacity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                  0.3, 1.0, 0.05)
        opacity_scale.set_value(cur_opacity)
        opacity_scale.set_draw_value(False)
        opacity_scale.set_hexpand(True)
        opacity_scale.set_size_request(120, -1)

        self._opacity_label = Gtk.Label(label=f"{int(cur_opacity * 100)}%")
        self._opacity_label.set_name("settings-label")
        self._opacity_label.set_size_request(40, -1)

        opacity_scale.connect("value-changed", self._on_opacity_changed)
        opacity_row.pack_start(opacity_scale, True, True, 0)
        opacity_row.pack_start(self._opacity_label, False, False, 0)

        inner.pack_start(opacity_row, False, False, 0)

        # ── 7. Dwell row ───────────────────────────────────────────────────
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

        inner.pack_start(dwell_row, False, False, 0)

        # ── 8. Sound row ───────────────────────────────────────────────────
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

        inner.pack_start(sound_row, False, False, 0)

        # ── 9. Modifier auto-release row ───────────────────────────────────
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

        inner.pack_start(mod_row, False, False, 0)

        # ── 9b. Shift behaviour row ────────────────────────────────────────
        shift_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_sh = Gtk.Label(label="Shift key")
        lbl_sh.set_name("settings-label")
        lbl_sh.set_xalign(0.0)
        lbl_sh.set_size_request(90, -1)
        shift_row.pack_start(lbl_sh, False, False, 0)

        shift_sticky = self.settings.get("shift_sticky", False)
        self._shift_toggle = Gtk.ToggleButton(
            label="Sticky" if shift_sticky else "One-shot")
        self._shift_toggle.set_name("settings-toggle")
        self._shift_toggle.set_size_request(100, -1)
        self._shift_toggle.set_active(shift_sticky)
        self._shift_toggle.connect("toggled", self._on_shift_sticky_toggled)
        shift_row.pack_start(self._shift_toggle, False, False, 0)

        self._shift_hint_label = Gtk.Label(
            label="stays until clicked again" if shift_sticky else "releases after keypress")
        self._shift_hint_label.set_name("settings-label")
        self._shift_hint_label.set_xalign(0.0)
        shift_row.pack_start(self._shift_hint_label, False, False, 4)

        inner.pack_start(shift_row, False, False, 0)

        # ── 10. Panel shortcut row (Linux / Cinnamon only) ─────────────────
        if not IS_WINDOWS:
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

            inner.pack_start(shortcut_row, False, False, 0)

        # ── 11. Custom dictionary section ──────────────────────────────────
        dict_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dict_lbl = Gtk.Label(label="Custom dictionary")
        dict_lbl.set_name("settings-label")
        dict_lbl.set_xalign(0.0)
        dict_lbl.set_hexpand(True)
        dict_header.pack_start(dict_lbl, True, True, 0)

        n = len(self._custom_words)
        self._custom_words_count = Gtk.Label(
            label=f"{n} word{'s' if n != 1 else ''}")
        self._custom_words_count.set_name("settings-label")
        dict_header.pack_end(self._custom_words_count, False, False, 0)
        inner.pack_start(dict_header, False, False, 0)

        add_btn = Gtk.Button(label="+ Add word or phrase")
        add_btn.get_style_context().add_class("settings-choice")
        add_btn.connect("clicked", lambda _: self._open_custom_input_mode())
        inner.pack_start(add_btn, False, False, 0)

        scroll_words = Gtk.ScrolledWindow()
        scroll_words.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_words.set_size_request(-1, 80)

        self._custom_words_listbox = Gtk.ListBox()
        self._custom_words_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._custom_words_listbox.set_name("settings-panel")

        for word in self._custom_words:
            self._custom_words_listbox.add(self._make_custom_word_row(word))

        scroll_words.add(self._custom_words_listbox)
        inner.pack_start(scroll_words, False, False, 0)

        # ── 12. Macros section ─────────────────────────────────────────────
        macros_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        macros_lbl = Gtk.Label(label="Macros")
        macros_lbl.set_name("settings-label")
        macros_lbl.set_xalign(0.0)
        macros_lbl.set_hexpand(True)
        macros_header.pack_start(macros_lbl, True, True, 0)

        nm = len(self._macros)
        self._macros_count = Gtk.Label(label=f"{nm} macro{'s' if nm != 1 else ''}")
        self._macros_count.set_name("settings-label")
        macros_header.pack_end(self._macros_count, False, False, 0)
        inner.pack_start(macros_header, False, False, 0)

        add_macro_btn = Gtk.Button(label="+ Add macro")
        add_macro_btn.get_style_context().add_class("settings-choice")
        add_macro_btn.connect("clicked",
                              lambda _: self._open_custom_input_mode(target="macro_trigger"))
        inner.pack_start(add_macro_btn, False, False, 0)

        scroll_macros = Gtk.ScrolledWindow()
        scroll_macros.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_macros.set_size_request(-1, 80)

        self._macros_listbox = Gtk.ListBox()
        self._macros_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._macros_listbox.set_name("settings-panel")

        for macro in self._macros:
            self._macros_listbox.add(self._make_macro_row(macro))

        scroll_macros.add(self._macros_listbox)
        inner.pack_start(scroll_macros, False, False, 0)

        # ── 13. Custom themes section ──────────────────────────────────────
        ctheme_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_ct = Gtk.Label(label="Custom themes")
        lbl_ct.set_name("settings-label")
        lbl_ct.set_xalign(0.0)
        lbl_ct.set_hexpand(True)
        ctheme_hdr.pack_start(lbl_ct, True, True, 0)

        new_theme_btn = Gtk.Button(label="+ New theme")
        new_theme_btn.get_style_context().add_class("settings-choice")
        new_theme_btn.connect("clicked", lambda _: self._open_theme_wizard())
        ctheme_hdr.pack_end(new_theme_btn, False, False, 0)
        inner.pack_start(ctheme_hdr, False, False, 0)

        self._custom_theme_btns_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.pack_start(self._custom_theme_btns_box, False, False, 0)
        self._rebuild_custom_theme_buttons()

        # ── 14. Updates ────────────────────────────────────────────────────────
        update_hdr = Gtk.Label(label="Updates")
        update_hdr.set_name("settings-label")
        update_hdr.set_xalign(0.0)
        update_hdr.set_margin_top(4)
        inner.pack_start(update_hdr, False, False, 0)

        self._update_status_label = Gtk.Label(label="")
        self._update_status_label.set_name("settings-label")
        self._update_status_label.set_xalign(0.0)
        self._update_status_label.set_line_wrap(True)
        inner.pack_start(self._update_status_label, False, False, 0)

        self._update_btn = Gtk.Button(label="Check for updates")
        self._update_btn.get_style_context().add_class("settings-choice")
        self._update_btn.connect("clicked", self._on_check_for_updates)
        inner.pack_start(self._update_btn, False, False, 0)

        scroll_outer.add(inner)
        panel.pack_start(scroll_outer, True, True, 0)

        self._refresh_theme_buttons()
        self._refresh_layout_buttons()
        self._refresh_font_family_buttons()
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
        for key, btn in self._custom_theme_btns.items():
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

    def _on_shift_sticky_toggled(self, toggle: Gtk.ToggleButton):
        sticky = toggle.get_active()
        toggle.set_label("Sticky" if sticky else "One-shot")
        self.settings["shift_sticky"] = sticky
        if hasattr(self, "_shift_hint_label"):
            self._shift_hint_label.set_text(
                "stays until clicked again" if sticky else "releases after keypress")
        self._save_settings()

    def _on_font_size_change(self, _btn, delta: int):
        size = max(10, min(22, self.settings.get("font_size", 14) + delta))
        self.settings["font_size"] = size
        if self._font_size_label:
            self._font_size_label.set_label(f"{size}px")
        self._reload_theme_css()
        self._save_settings()

    def _on_key_scale_change(self, _btn, delta: float):
        scale = round(max(0.3, min(1.5, self.settings.get("key_scale", 1.0) + delta)), 2)
        self.settings["key_scale"] = scale
        if hasattr(self, "_key_scale_label") and self._key_scale_label:
            self._key_scale_label.set_label(f"{scale:.2f}×")
        self._apply_key_scale(scale, reposition=True)
        self._save_settings()

    def _on_check_for_updates(self, _btn):
        if self._update_status_label:
            self._update_status_label.set_text("Checking…")
        if self._update_btn:
            self._update_btn.set_sensitive(False)
        import threading
        threading.Thread(target=self._do_update_check, daemon=True).start()

    def _do_update_check(self):
        if getattr(sys, "frozen", False):
            self._do_update_check_exe_mode()
            return
        import urllib.request, urllib.error, json as _json
        status = ""
        can_pull = False
        try:
            install_dir = os.path.dirname(os.path.abspath(__file__))
            # Get current local commit SHA
            local_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=install_dir, text=True
            ).strip()
            # Get remote HEAD SHA via GitHub API (anonymous HTTPS, no auth needed)
            api_url = "https://api.github.com/repos/Gitties67/Onscreen-keyboard/commits/main"
            req = urllib.request.Request(api_url, headers={"User-Agent": "OSK-updater/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
            remote_sha = data["sha"]

            if local_sha == remote_sha:
                status = "You're on the latest version."
            else:
                # Check if remote is SSH (can't auto-pull without credentials)
                remote_url = subprocess.check_output(
                    ["git", "remote", "get-url", "origin"], cwd=install_dir, text=True
                ).strip()
                is_ssh = remote_url.startswith("git@")

                # Check if working tree is clean
                dirty = subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=install_dir, text=True
                ).strip()

                if is_ssh:
                    status = (
                        "Update available!\n"
                        "Run in a terminal:\n"
                        "  cd ~/onscreen_keyboard && git pull"
                    )
                elif dirty:
                    status = (
                        "Update available, but your local files have changes.\n"
                        "Commit or stash them first, then run: git pull"
                    )
                else:
                    status = "Update available — click Install to apply."
                    can_pull = True
        except subprocess.CalledProcessError:
            status = "Not a git repository — cannot check for updates."
        except urllib.error.HTTPError as e:
            if e.code == 404:
                status = "Could not reach the update server (404). The repository may be private or the branch may have moved."
            else:
                status = f"Update server returned HTTP {e.code} {e.reason}."
        except urllib.error.URLError as e:
            status = f"Network error — check your internet connection.\n({e.reason})"
        except Exception as e:
            status = f"Check failed: {e}"

        def _apply(status=status, can_pull=can_pull):
            if self._update_status_label:
                self._update_status_label.set_text(status)
            if self._update_btn:
                self._update_btn.set_sensitive(True)
                if can_pull:
                    self._update_btn.set_label("Install update")
                    # Reconnect to install handler
                    try:
                        self._update_btn.disconnect_by_func(self._on_check_for_updates)
                    except Exception:
                        pass
                    self._update_btn.connect("clicked", self._on_install_update)
                else:
                    self._update_btn.set_label("Check for updates")

        GLib.idle_add(_apply)

    def _do_update_check_exe_mode(self):
        import urllib.request, urllib.error, json as _json, webbrowser
        RELEASES_URL = "https://api.github.com/repos/Gitties67/Onscreen-keyboard/releases/latest"
        DOWNLOAD_URL = "https://github.com/Gitties67/Onscreen-keyboard/releases/latest"

        version_file = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "VERSION")
        local_ver = "unknown"
        if os.path.exists(version_file):
            with open(version_file) as f:
                local_ver = f.read().strip()

        if local_ver == "unknown":
            status = "Cannot determine installed version — VERSION file missing."
            show_download = False
        else:
            try:
                req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": "OSK-updater/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = _json.loads(resp.read())
                latest_tag = data.get("tag_name", "unknown")
                if local_ver == latest_tag:
                    status = "You're on the latest version."
                    show_download = False
                else:
                    status = (f"Update available! You have {local_ver}, latest is {latest_tag}.\n"
                              "Click below to open the download page.")
                    show_download = True
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    status = "No releases have been published yet — this build is the latest."
                else:
                    status = f"Update server returned HTTP {e.code} {e.reason}."
                show_download = False
            except urllib.error.URLError as e:
                status = f"Network error — check your internet connection.\n({e.reason})"
                show_download = False
            except Exception as e:
                status = f"Update check failed: {e}"
                show_download = False

        def _apply(status=status, show_download=show_download):
            if self._update_status_label:
                self._update_status_label.set_text(status)
            if self._update_btn:
                self._update_btn.set_sensitive(True)
                if show_download:
                    self._update_btn.set_label("Download update")
                    try:
                        self._update_btn.disconnect_by_func(self._on_check_for_updates)
                    except Exception:
                        pass
                    self._update_btn.connect(
                        "clicked", lambda _: webbrowser.open(DOWNLOAD_URL))
                else:
                    self._update_btn.set_label("Check for updates")
        GLib.idle_add(_apply)

    def _on_install_update(self, _btn):
        if self._update_status_label:
            self._update_status_label.set_text("Applying update…")
        if self._update_btn:
            self._update_btn.set_sensitive(False)
        import threading
        threading.Thread(target=self._do_install_update, daemon=True).start()

    def _do_install_update(self):
        install_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            subprocess.check_output(
                ["git", "pull", "--ff-only"], cwd=install_dir,
                text=True, stderr=subprocess.STDOUT
            )
            status = "Update installed! Click Restart to apply."
            show_restart = True
        except subprocess.CalledProcessError as e:
            status = f"Update failed:\n{e.output.strip()}"
            show_restart = False

        def _apply(status=status, show_restart=show_restart):
            if self._update_status_label:
                self._update_status_label.set_text(status)
            if self._update_btn:
                self._update_btn.set_sensitive(True)
                if show_restart:
                    self._update_btn.set_label("Restart now")
                    try:
                        self._update_btn.disconnect_by_func(self._on_install_update)
                    except Exception:
                        pass
                    self._update_btn.connect("clicked", self._on_restart_to_apply)
                else:
                    self._update_btn.set_label("Check for updates")

        GLib.idle_add(_apply)

    def _on_restart_to_apply(self, _btn):
        if IS_WINDOWS or getattr(sys, "frozen", False):
            # os.execv on Windows doesn't replace the process before GTK tears down;
            # spawn a new process first, then quit cleanly.
            subprocess.Popen([sys.executable])
            Gtk.main_quit()
            return
        launch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.sh")
        if os.path.exists(launch):
            os.execv("/bin/bash", ["/bin/bash", launch])
        else:
            os.execv(sys.executable, [sys.executable, __file__])

    def _on_opacity_changed(self, scale_widget: Gtk.Scale):
        value = round(scale_widget.get_value(), 2)
        self.settings["opacity"] = value
        self.set_opacity(value)
        if hasattr(self, "_opacity_label") and self._opacity_label:
            self._opacity_label.set_label(f"{int(value * 100)}%")
        self._save_settings()

    def _on_pin_to_panel(self, _btn, pin_btn: Gtk.Button):
        pin_btn.set_sensitive(False)
        status = self._create_panel_shortcut()
        if self._pin_status_label:
            self._pin_status_label.set_text(status)
        # Re-enable after a moment so the user can see the result
        GLib.timeout_add(3000, lambda: pin_btn.set_sensitive(True) or False)

    def _create_panel_shortcut(self) -> str:
        """Create .desktop file, autostart entry, and add launcher to the Cinnamon panel."""
        # ── 1. Write the .desktop file ────────────────────────────────────────
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.sh")
        desktop_content = (
            "[Desktop Entry]\n"
            "Name=On-Screen Keyboard\n"
            "Comment=Custom GTK on-screen keyboard\n"
            f'Exec=bash "{script}"\n'
            "Icon=input-keyboard\n"
            "Type=Application\n"
            "Categories=Utility;Accessibility;\n"
            "Terminal=false\n"
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

        # ── 1b. Write autostart entry so keyboard launches on login ──────────
        autostart_dir = os.path.expanduser("~/.config/autostart")
        os.makedirs(autostart_dir, exist_ok=True)
        autostart_content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=On-Screen Keyboard\n"
            f'Exec=bash "{script}"\n'
            "Terminal=false\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        try:
            with open(os.path.join(autostart_dir, "onscreen-keyboard.desktop"), "w") as f:
                f.write(autostart_content)
        except Exception:
            pass  # autostart failure is non-fatal

        # Refresh desktop database so the app appears in menus immediately
        try:
            subprocess.Popen(["update-desktop-database", apps_dir],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

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
        """Regenerate and reload the theme CSS (colours + font size + font family)."""
        colors      = THEME_COLORS.get(self.settings.get("theme", "dark"), THEME_COLORS["dark"])
        font_size   = self.settings.get("font_size", 14)
        font_family = self.settings.get("font_family", "Ubuntu")
        css = _make_theme_css(colors, font_size, font_family)
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
        # Ignore inferior crossings (pointer moving to/from a child widget)
        if event.detail == Gdk.NotifyType.INFERIOR:
            return
        # Cancel any existing timer for this widget
        self._dwell_cancel(widget)
        delay_ms = int(self.settings.get("dwell_delay", 0.8) * 1000)
        widget.get_style_context().add_class("dwell-pending")
        wid = id(widget)

        def _fire():
            widget.get_style_context().remove_class("dwell-pending")
            self._dwell_timers.pop(wid, None)
            self._on_key_clicked(widget, action)
            return False

        self._dwell_timers[wid] = GLib.timeout_add(delay_ms, _fire)

    def _on_dwell_leave(self, widget, event, action: str):
        # Ignore inferior crossings (pointer moving to/from a child widget)
        if event.detail == Gdk.NotifyType.INFERIOR:
            return
        self._dwell_cancel(widget)
        widget.get_style_context().remove_class("dwell-pending")

    def _dwell_cancel(self, widget):
        tid = self._dwell_timers.pop(id(widget), None)
        if tid is not None:
            GLib.source_remove(tid)

    # ── Click sound ───────────────────────────────────────────────────────────

    def _init_click_sound(self):
        """Generate a short click WAV to the config directory (always regenerated)."""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
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
        if IS_WINDOWS:
            try:
                import winsound
                winsound.PlaySound(CLICK_WAV, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass
            return
        for player in ("paplay", "aplay"):
            try:
                subprocess.Popen(
                    [player, CLICK_WAV],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)  # start_new_session detaches; init reaps
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
        # Cache app list once per launcher session — Gio.AppInfo.get_all() reads
        # all .desktop files and is too slow to call on every keystroke.
        self._cached_apps = Gio.AppInfo.get_all()
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
        self._cached_apps = None
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

    def _search_apps(self, query: str) -> list[Gio.AppInfo]:
        """Return up to 5 Gio.AppInfo objects whose display name contains query."""
        q = query.strip().lower()
        results: list[Gio.AppInfo] = []
        app_list = self._cached_apps if self._cached_apps is not None else Gio.AppInfo.get_all()
        for app in app_list:
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
            # Fallback: parse Exec= manually (without shell=True to avoid metachar hazard)
            cmd = app_info.get_commandline() or ""
            cmd = re.sub(r"%[A-Za-z]", "", cmd).strip()
            if cmd:
                try:
                    subprocess.Popen(
                        shlex.split(cmd), shell=False, close_fds=True,
                        start_new_session=True,
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

        if self.shift_active and not self.settings.get("shift_sticky", False):
            self.shift_active = False
            self._update_modifier_visuals()

    # ── Suggestion bar ────────────────────────────────��───────────────────────

    def _refresh_suggestions(self):
        w = self.current_word

        # Cancel any pending fuzzy update from a previous keypress
        if self._fuzzy_timer is not None:
            GLib.source_remove(self._fuzzy_timer)
            self._fuzzy_timer = None

        # ── Build word candidates for slots 0–4 ──────────────────────────────
        # slot 4 is reserved for emoji if there's a match; otherwise a 5th word.
        # slots: (label, is_emoji, is_custom, is_fuzzy, is_macro, expansion, is_fallback)
        slots: list[tuple[str, bool, bool, bool, bool, str, bool]] = []

        # Macro exact match — highest priority, takes slot 0
        macro_match = None
        if w:
            w_lower = w.lower()
            for m in self._macros:
                if m.get("trigger", "").lower() == w_lower:
                    macro_match = m
                    break
        if macro_match:
            exp     = macro_match.get("expansion", "")
            display = exp[:28] + ("…" if len(exp) > 28 else "")
            slots.append((display, False, False, False, True, exp, False))

        # Custom words
        custom_sugg  = self.predictor.custom_matches(w) if w else []
        custom_lower = {c.lower() for c in custom_sugg}
        for v in custom_sugg:
            slots.append((v, False, True, False, False, "", False))

        # Exact-prefix dictionary words — or next-word prediction when no prefix
        n_word_slots = 4 - len(slots)   # fill up to 4 word slots total
        if w:
            exact_sugg = [x for x in self.predictor.predict(w, n=n_word_slots + 2)
                          if x not in custom_lower]
        else:
            # After a space: predict based on the previous word
            exact_sugg = self.predictor.predict_next(self._last_word, n=n_word_slots + 2)

        seen_words = {s[0].lower() for s in slots}
        for v in exact_sugg:
            if len(slots) >= 4:
                break
            if v.lower() not in seen_words:
                slots.append((v, False, False, False, False, "", False))
                seen_words.add(v.lower())

        # Pad to 4 word slots with common fallback words (marked as fallback so
        # the fuzzy timer knows these aren't real predictions for the current prefix)
        fallback = self.predictor.predict_padded("", 20)
        for v in fallback:
            if len(slots) >= 4:
                break
            if v.lower() not in seen_words:
                slots.append((v, False, False, False, False, "", True))
                seen_words.add(v.lower())

        # Slot 4: emoji if any of the 4 word slots has an emoji match,
        # otherwise pad with a 5th word
        emoji_slot: tuple[str, bool, bool, bool, bool, str, bool] | None = None
        check_words = [s[0] for s in slots[:4] if not s[1]]  # word labels only
        for word in check_words:
            hits = emoji_suggest(word, n=1)
            if hits:
                emoji_slot = (hits[0], True, False, False, False, "", False)
                break
        if emoji_slot:
            slots.append(emoji_slot)
        else:
            # 5th word
            for v in fallback:
                if v.lower() not in seen_words:
                    slots.append((v, False, False, False, False, "", True))
                    seen_words.add(v.lower())
                    break

        # ── Populate buttons ──────────────────────────────────────────────────
        self._suggestion_values    = [""] * 5
        self._suggestion_is_emoji  = [False] * 5
        self._suggestion_is_custom = [False] * 5
        self._suggestion_is_fuzzy  = [False] * 5
        self._suggestion_is_macro  = [False] * 5
        self._suggestion_macro_expansions: list[str] = [""] * 5

        for i, btn in enumerate(self._suggestion_btns):
            if i < len(slots):
                val, is_emoji, is_custom, is_fuzzy, is_macro, expansion, _is_fb = slots[i]
                # Store raw value; display with shift/caps casing applied
                display = val if (is_emoji or is_macro) else self._case_word(val)
                btn.set_label(display)
                btn.set_sensitive(True)
                self._suggestion_values[i]    = val
                self._suggestion_is_emoji[i]  = is_emoji
                self._suggestion_is_custom[i] = is_custom
                self._suggestion_is_fuzzy[i]  = is_fuzzy
                self._suggestion_is_macro[i]  = is_macro
                self._suggestion_macro_expansions[i] = expansion
            else:
                btn.set_label("")
                btn.set_sensitive(False)

        # Schedule fuzzy spell-check only when there are real empty word slots —
        # fallback words don't count as real predictions for the current prefix.
        n_real = sum(1 for s in slots[:4] if not s[1] and not s[4] and not s[6])
        if w and n_real < 4 and len(w) >= 3:
            self._fuzzy_timer = GLib.timeout_add(250, self._run_fuzzy_update)

    def _run_fuzzy_update(self) -> bool:
        """Run fuzzy spell-check and fill remaining suggestion slots (debounced)."""
        self._fuzzy_timer = None
        w = self.current_word
        if not w or len(w) < 3:
            return False

        custom_sugg  = self.predictor.custom_matches(w)
        custom_lower = {c.lower() for c in custom_sugg}
        # Count macro slots first so we can correctly budget the exact-word query
        n_macro      = sum(1 for m in self._suggestion_is_macro[:4] if m)
        # Word slots are 0–3; find which are already filled with exact matches
        exact_sugg   = [x for x in self.predictor.predict(w, n=max(1, 4 - n_macro))
                        if x not in custom_lower]
        # Slots already occupied by custom + exact words + any macro slot
        n_filled     = len(custom_sugg) + len(exact_sugg) + n_macro
        n_fuzzy_need = max(0, 4 - n_filled)
        if n_fuzzy_need <= 0:
            return False

        exact_lower = {x.lower() for x in exact_sugg}
        fuzzy_sugg  = [x for x in self.predictor.fuzzy_predict(w, n=n_fuzzy_need)
                       if x not in custom_lower and x not in exact_lower]

        # Fill empty word slots (0–3) with fuzzy suggestions
        slot_start = n_filled
        for i, v in enumerate(fuzzy_sugg):
            idx = slot_start + i
            if idx >= 4:   # don't overwrite slot 4 (emoji/5th word)
                break
            self._suggestion_btns[idx].set_label(self._case_word(v))
            self._suggestion_btns[idx].set_sensitive(True)
            self._suggestion_values[idx]   = v
            self._suggestion_is_fuzzy[idx] = True

        return False  # don't repeat

    def _on_suggestion_clicked(self, btn: Gtk.Button, idx: int = 0):
        label = btn.get_label()
        if not label:
            return

        if self._app_mode:
            if idx < len(self._app_results):
                self._launch_app(self._app_results[idx])
            self._close_app_mode()
            return

        # Macro suggestion — backspace trigger, type expanded text + space
        if idx < len(self._suggestion_is_macro) and self._suggestion_is_macro[idx]:
            expansion = ""
            if hasattr(self, "_suggestion_macro_expansions"):
                expansion = self._suggestion_macro_expansions[idx]
            if not expansion:
                expansion = label
            expanded = self._expand_macro(expansion)[:500]  # cap to prevent UI freeze
            # current_word equals the trigger at this point (exact match required for
            # macro to surface), so unconditionally erase it before typing the expansion
            for _ in range(len(self.current_word)):
                self.typer.send_special("backspace")
            for ch in expanded:
                self.typer.type_char(ch)
            self.typer.send_special("space")
            self.current_word = ""
            self._refresh_suggestions()
            return

        # Emoji suggestion
        if idx < len(self._suggestion_is_emoji) and self._suggestion_is_emoji[idx]:
            self.typer.type_emoji(label)
            self.current_word = ""
            self._refresh_suggestions()
            return

        # Custom word/phrase — backspace the partial prefix then type full entry
        # with its original casing (e.g. "Dinglebob" even if user typed "din")
        if idx < len(self._suggestion_is_custom) and self._suggestion_is_custom[idx]:
            if label.lower().startswith(self.current_word.lower()):
                for _ in range(len(self.current_word)):
                    self.typer.send_special("backspace")
            for ch in label:
                self.typer.type_char(ch)
            self.typer.send_special("space")
            self.current_word = ""
            self._refresh_suggestions()
            return

        # Fuzzy spell-check suggestion — always replace in full (prefix never matches)
        if idx < len(self._suggestion_is_fuzzy) and self._suggestion_is_fuzzy[idx]:
            for _ in range(len(self.current_word)):
                self.typer.send_special("backspace")
            for ch in label:
                self.typer.type_char(ch)
            self.typer.send_special("space")
            self.current_word = ""
            self._refresh_suggestions()
            return

        # Normal dictionary word-completion
        cased = self._case_word(label)
        if self.shift_active or self.caps_lock:
            # Retype the whole word with casing (backspace the partial prefix first)
            for _ in range(len(self.current_word)):
                self.typer.send_special("backspace")
            for ch in cased:
                self.typer.type_char(ch)
        else:
            # Fast path: just type the remaining suffix
            for ch in cased[len(self.current_word):]:
                self.typer.type_char(ch)
        self.typer.send_special("space")
        self._last_word   = label
        self.current_word = ""
        self._refresh_suggestions()

    # ── Modifier visuals ────────────��─────────────────────────���───────────────

    def _refresh_suggestion_casing(self):
        """Re-label suggestion buttons to reflect current shift/caps state,
        without rerunning the prediction logic."""
        for i, btn in enumerate(self._suggestion_btns):
            val = self._suggestion_values[i] if i < len(self._suggestion_values) else ""
            if not val:
                continue
            is_emoji = i < len(self._suggestion_is_emoji) and self._suggestion_is_emoji[i]
            is_macro = i < len(self._suggestion_is_macro) and self._suggestion_is_macro[i]
            if not is_emoji and not is_macro:
                btn.set_label(self._case_word(val))

    def _case_word(self, word: str) -> str:
        """Apply current shift/caps state to a suggestion word."""
        if not word:
            return word
        if self.caps_lock:
            return word.upper()
        if self.shift_active:
            return word[0].upper() + word[1:]
        return word

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

        # Update suggestion bar labels to reflect the new casing
        self._refresh_suggestion_casing()
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

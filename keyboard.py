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
import time
import wave

# Windowed (console=False) PyInstaller builds start with sys.stdout/stderr = None;
# any print() would then raise.  Point them at devnull before anything prints.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        try:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
        except Exception:
            pass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Gdk, GLib, Gio

IS_WINDOWS = platform.system() == "Windows"

# Windows focus-restoration: keeps track of the last non-OSK foreground window
# so KeyTyper can redirect keystrokes to it even if GTK stole focus on click.
_win_last_target: list[int] = [0]  # last non-OSK foreground HWND, updated by 50ms poll
_win_our_pid: int = os.getpid()    # cached once; used to detect when OSK owns the foreground
# Registered window message id for "OSK_SHOW_v1" (second launch asks the running
# instance to show itself).  0 until _win_single_instance_or_exit() registers it.
_win_show_msg: list[int] = [0]

def _win_focus_restore() -> None:
    """Restore focus to the last target window — but ONLY if the OSK itself currently
    owns the foreground (i.e. WS_EX_NOACTIVATE failed to prevent the steal).
    If any other process (Chrome, Notepad, …) already has the foreground, leave it
    completely alone — calling SetForegroundWindow would pull the wrong window to the
    front or disrupt that app's internal focus state."""
    target = _win_last_target[0]
    if not target:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        current = user32.GetForegroundWindow()
        if not current or current == target:
            return  # target already has focus, nothing to do
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(current, ctypes.byref(pid))
        if pid.value != _win_our_pid:
            return  # another app has focus — leave it, pynput will send there
        user32.SetForegroundWindow(target)
        # Give Windows time to process the foreground change before SendInput fires.
        time.sleep(0.02)
    except Exception:
        pass


# ── Windows direct message injection (focus-independent typing) ───────────────
#
# Why this exists: every attempt to keep keyboard *focus* on the target app while
# clicking the OSK has failed (WS_EX_NOACTIVATE, WM_MOUSEACTIVATE→MA_NOACTIVATE,
# WM_SETFOCUS/WM_ACTIVATE intercepts, WH_CBT HCBT_SETFOCUS cancel).  The reasons
# they fail are structural, not tuning issues:
#   • WH_CBT on GetCurrentThreadId() only sees focus changes whose target HWND
#     belongs to *our* thread's queue — it never observes the target app losing
#     focus, and HCBT_SETFOCUS's return value is not a reliable veto anyway.
#   • SetForegroundWindow from a background process is silently rate-limited by
#     Windows (since Vista) unless we own recent input — so "restore focus" is a
#     coin flip at the instant SendInput fires.
#   • SendInput injects into the focused control of the *foreground thread*; if
#     foreground is even momentarily wrong, the keystroke lands nowhere useful.
#
# The fix: stop fighting focus.  PostMessage WM_KEYDOWN/WM_CHAR/WM_KEYUP straight
# to the focused child control of the target process.  This needs neither focus
# nor foreground.  The one subtlety prior rounds would have missed: the message
# must go to the *focused control inside the target's thread*, found via
# AttachThreadInput+GetFocus / GetGUIThreadInfo — NOT the top-level window.

# Diagnostics are off unless OSK_DEBUG=1 and never record typed characters.
# --debug == OSK_DEBUG=1 (must be decided here: the log path is computed at import).
if "--debug" in sys.argv[1:]:
    os.environ["OSK_DEBUG"] = "1"
_WIN_DEBUG = os.environ.get("OSK_DEBUG") == "1"
_WIN_DEBUG_LOG = ""
if IS_WINDOWS and _WIN_DEBUG:
    import tempfile
    _log_dir = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                            "OnScreenKeyboard")
    try:
        os.makedirs(_log_dir, exist_ok=True)
    except Exception:
        _log_dir = tempfile.gettempdir()
    _WIN_DEBUG_LOG = os.path.join(_log_dir, "osk_debug.log")
_win_protos_ready = False

def _win_log(msg: str) -> None:
    """Append a diagnostic line to the per-user debug log (EXE has console=False)."""
    if not IS_WINDOWS or not _WIN_DEBUG_LOG:
        return
    try:
        # ASCII-only: PowerShell 5 reads BOM-less UTF-8 as ANSI (mojibake).
        msg = msg.replace("\u2014", "-").replace("\u2192", "->")
        msg = msg.encode("ascii", "replace").decode("ascii")
        with open(_WIN_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

def _win_setup_protos() -> None:
    """Pin restype/argtypes on the user32 calls we use so 64-bit HWNDs are not
    truncated to 32 bits.  ctypes defaults every return value and untyped pointer
    argument to C int; on Win64 a handle silently loses its top 32 bits, which is
    a notorious source of "the call returns garbage / posts to nothing" bugs."""
    global _win_protos_ready
    if _win_protos_ready or not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetWindowThreadProcessId.restype  = wintypes.DWORD
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.GetGUIThreadInfo.restype  = wintypes.BOOL
        u.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        u.GetFocus.restype          = wintypes.HWND
        u.AttachThreadInput.restype = wintypes.BOOL
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u.MapVirtualKeyW.restype    = wintypes.UINT
        u.MapVirtualKeyW.argtypes   = [wintypes.UINT, wintypes.UINT]
        u.PostMessageW.restype      = wintypes.BOOL
        u.PostMessageW.argtypes     = [wintypes.HWND, wintypes.UINT,
                                       ctypes.c_size_t, ctypes.c_ssize_t]
        u.GetForegroundWindow.restype = wintypes.HWND
        # HWND-acquisition path (EnumWindows fallback + style application).
        # GetWindow returns an HWND — must not be truncated when we test it as
        # an owner.  EnumWindows takes a callback + LPARAM.
        u.GetWindow.restype         = wintypes.HWND
        u.GetWindow.argtypes        = [wintypes.HWND, wintypes.UINT]
        # Style get/set: the HWND arg must be HWND (not the default C int) or a
        # 64-bit handle is truncated and the call targets the wrong/no window.
        u.GetWindowLongW.restype    = wintypes.LONG
        u.GetWindowLongW.argtypes   = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.restype    = wintypes.LONG
        u.SetWindowLongW.argtypes   = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        # *Ptr variants carry pointer-sized values (the WNDPROC address).  Their
        # 3rd arg and return are LONG_PTR — default int would truncate the proc
        # pointer on Win64 and corrupt the subclass.  Present only on 64-bit
        # builds; guarded because the 32-bit fallback uses the non-Ptr names.
        u.GetClassNameW.restype  = ctypes.c_int
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        if hasattr(u, "GetWindowLongPtrW"):
            u.GetWindowLongPtrW.restype  = ctypes.c_ssize_t
            u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            u.SetWindowLongPtrW.restype  = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                            ctypes.c_ssize_t]
        u.GetForegroundWindow.argtypes = []
        # Minimize/restore path (HWND must not be truncated to C int).
        u.ShowWindow.restype         = wintypes.BOOL
        u.ShowWindow.argtypes        = [wintypes.HWND, ctypes.c_int]
        u.IsIconic.restype           = wintypes.BOOL
        u.IsIconic.argtypes          = [wintypes.HWND]
        u.SetForegroundWindow.restype  = wintypes.BOOL
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        _win_protos_ready = True
    except Exception as exc:
        _win_log(f"setup_protos error: {exc}")


# Virtual-key codes for the named special keys we expose.
_WIN_VK = {
    "backspace": 0x08, "tab": 0x09, "return": 0x0D, "escape": 0x1B,
    "space": 0x20, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "prtscn": 0x2C,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
# Extended-key VKs need bit 24 set in the WM_KEY* lParam so the target app reads
# the correct scan code (arrows, nav cluster, etc. live on the extended block).
_WIN_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x2C}

# Window messages / constants
_WM_KEYDOWN, _WM_KEYUP, _WM_CHAR = 0x0100, 0x0101, 0x0102


def _win_target_focus_hwnd():
    """Return the HWND that should receive keystrokes: the focused control inside
    the last non-OSK foreground window's thread, or the top-level as a fallback.

    We AttachThreadInput to the target's GUI thread so GetFocus() can read that
    thread's focused control (GetFocus is per-thread; without attaching it only
    reports our own thread's focus).  GetGUIThreadInfo is tried first because it
    does not require attaching and reports hwndFocus directly."""
    target = _win_last_target[0]
    if not target:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        _win_setup_protos()

        target_tid = user32.GetWindowThreadProcessId(target, None)

        # ── Attempt 1: GetGUIThreadInfo (no attach needed) ──────────────────
        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(target_tid, ctypes.byref(gti)) and gti.hwndFocus:
            return gti.hwndFocus

        # ── Attempt 2: AttachThreadInput + GetFocus ─────────────────────────
        our_tid = user32.GetCurrentThreadId()
        focused = None
        if user32.AttachThreadInput(our_tid, target_tid, True):
            try:
                focused = user32.GetFocus()
            finally:
                user32.AttachThreadInput(our_tid, target_tid, False)
        if focused:
            return focused
    except Exception as exc:
        _win_log(f"focus_hwnd error: {exc}")
    # Fall back to the top-level foreground window — works for simple apps where
    # the top-level window is itself the edit surface.
    return target


def _win_make_lparam(vk: int, key_up: bool) -> int:
    """Build the WM_KEYDOWN/WM_KEYUP lParam: repeat count, scan code, extended
    bit, and the transition/previous-state bits for the up message."""
    import ctypes
    user32 = ctypes.windll.user32
    MAPVK_VK_TO_VSC = 0
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF
    lparam = 1 | (scan << 16)
    if vk in _WIN_EXTENDED_VK:
        lparam |= (1 << 24)          # extended-key flag
    if key_up:
        lparam |= (1 << 30) | (1 << 31)  # previous-down + transition (release)
    return lparam


def _win_post_char(hwnd: int, ch: str) -> bool:
    """Deliver a single printable character via WM_CHAR.

    WM_CHAR carries the already-translated character, so it reproduces the exact
    glyph (including shifted symbols and case) without us having to model the
    keyboard layout or hold a Shift key in the target's input state.  This is the
    reliable path for plain text in Edit/RichEdit/most browser text fields."""
    try:
        import ctypes
        import struct
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # WM_CHAR wParam is a single UTF-16 code unit. Astral chars (emoji beyond
        # the BMP) must be sent as their surrogate pair — one WM_CHAR per 16-bit
        # code unit.  Decode the UTF-16-LE byte stream into raw code units so an
        # astral codepoint becomes two messages, not one out-of-range wParam.
        raw = ch.encode("utf-16-le")
        units = struct.unpack(f"<{len(raw) // 2}H", raw)
        for unit in units:
            # PostMessageW returns 0 (without raising) when the post is blocked
            # by UIPI — e.g. the target runs at a higher integrity level than us
            # (an elevated/admin app).  If we ignored the BOOL and returned True,
            # the caller would never fall back to pynput and the keystroke would
            # vanish silently.  Clear last-error first so a stale value can't
            # masquerade as a fresh UIPI block.
            kernel32.SetLastError(0)
            if not user32.PostMessageW(hwnd, _WM_CHAR, unit, 1):
                err = kernel32.GetLastError()
                _win_log(f"post_char len={len(ch)}: PostMessage failed err={err} "
                         f"({'UIPI/access-denied' if err == 5 else 'other'})")
                return False
        return True
    except Exception as exc:
        _win_log(f"post_char len={len(ch)} error: {exc}")
        return False


def _win_post_vk(hwnd: int, vk: int) -> bool:
    """Deliver a non-text virtual key (arrows, F-keys, backspace, Enter, etc.)
    as a WM_KEYDOWN/WM_KEYUP pair with a correctly-built lParam."""
    try:
        import ctypes
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        ok_down = user32.PostMessageW(hwnd, _WM_KEYDOWN, vk, _win_make_lparam(vk, False))
        ok_up   = user32.PostMessageW(hwnd, _WM_KEYUP,   vk, _win_make_lparam(vk, True))
        if not (ok_down and ok_up):
            err = kernel32.GetLastError()
            _win_log(f"post_vk {vk:#x}: PostMessage failed err={err} "
                     f"({'UIPI/access-denied' if err == 5 else 'other'})")
            return False
        return True
    except Exception as exc:
        _win_log(f"post_vk {vk:#x} error: {exc}")
        return False


def _win_inject_char(char: str, mods: list[str]) -> bool:
    """Try to deliver a printable character via PostMessage (focus-independent).

    Returns True if handled here, False if the caller should fall back to pynput.
    Modified combos (Ctrl/Alt) are deliberately NOT handled here: WM_CHAR carries
    no modifier state and the target thread's async key state isn't shared with
    us, so Ctrl+C posted as messages would type a literal 'c'.  Those go to
    pynput, which uses real SendInput against the (focus-restored) target."""
    if mods:
        return False
    hwnd = _win_target_focus_hwnd()
    if not hwnd:
        _win_log(f"inject_char len={len(char)}: no target hwnd")
        return False
    ok = _win_post_char(hwnd, char)
    _win_log(f"inject_char len={len(char)} -> hwnd={hwnd:#x} ok={ok}")
    return ok


def _win_inject_special(name: str, mods: list[str]) -> bool:
    """Try to deliver a named special key via PostMessage WM_KEYDOWN/UP.

    Unmodified navigation/editing keys post cleanly.  Modified combos fall back
    to pynput for the same reason as _win_inject_char."""
    if mods:
        return False
    vk = _WIN_VK.get(name)
    if vk is None:
        return False
    hwnd = _win_target_focus_hwnd()
    if not hwnd:
        _win_log(f"inject_special {name!r}: no target hwnd")
        return False
    # space is a real character to text controls — send WM_CHAR so it lands in
    # the edit buffer even on controls that ignore VK_SPACE key messages.
    if name == "space":
        ok = _win_post_char(hwnd, " ")
    else:
        ok = _win_post_vk(hwnd, vk)
    _win_log(f"inject_special {name!r} vk={vk:#x} -> hwnd={hwnd:#x} ok={ok}")
    return ok

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
        "pgup":      _PKey.page_up,   "pgdn":   _PKey.page_down,
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

from predictor import (WordPredictor, UserModel, DICTIONARIES, ELISION_LANGUAGES,
                       is_english)
from hangul import HangulComposer, jamo_for
from emojis import EMOJI_DATA, search as emoji_search, suggest as emoji_suggest


LANGUAGE_LABELS = {"en_AU": "English (Australia)", "en_GB": "English (UK)",
                   "en_US": "English (US)",
                   "de": "Deutsch", "fr": "Français", "es": "Español",
                   "it": "Italiano", "pt": "Português", "nl": "Nederlands",
                   "id": "Indonesia", "ms": "Melayu", "tl": "Filipino",
                   "vi": "Tiếng Việt", "ko": "한국어"}
ENGLISH_CODES = ("en_AU", "en_GB", "en_US")
EUROPEAN_CODES = ("de", "fr", "es", "it", "pt", "nl")
ASIAN_CODES = ("id", "ms", "tl", "vi", "ko")
OTHER_LANGUAGE_CODES = EUROPEAN_CODES + ASIAN_CODES
# Locale language tags that mean one of ours ("fil-PH" is Filipino)
LOCALE_ALIASES = {"fil": "tl", "in": "id", "zsm": "ms"}

# The keyboard layout people of each language normally use.  Korean 2-set
# keys are defined by QWERTY positions, so Korean always uses QWERTY.
LANGUAGE_LAYOUT = {"de": "qwertz", "fr": "azerty", "ko": "qwerty"}

# Hold a letter key to get these (the plain letter stays the tap).  English
# gets a general set, for café, naïve, señor and friends.
ACCENTS = {
    "en": {"a": "á à â ä", "e": "é è ê ë", "i": "í ï", "o": "ó ô ö",
           "u": "ú ü", "c": "ç", "n": "ñ"},
    "de": {"a": "ä", "o": "ö", "u": "ü", "s": "ß", "e": "é"},
    "fr": {"e": "é è ê ë", "a": "à â æ", "c": "ç", "i": "î ï", "o": "ô œ",
           "u": "ù û ü", "y": "ÿ"},
    "es": {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú ü", "n": "ñ"},
    "it": {"a": "à", "e": "è é", "i": "ì", "o": "ò ó", "u": "ù"},
    "pt": {"a": "á â ã à", "e": "é ê", "i": "í", "o": "ó ô õ", "u": "ú", "c": "ç"},
    "nl": {"e": "é ë è", "i": "ï", "o": "ö ó", "u": "ü", "a": "á"},
    "tl": {"n": "ñ"},
    # Vietnamese: the hat/horn letters plus each vowel's five tones.  Letters
    # with both (ầ, ướ) come from suggestions: type "nguoi" → người.
    "vi": {"a": "ă â à á ả ã ạ", "e": "ê è é ẻ ẽ ẹ", "o": "ô ơ ò ó ỏ õ ọ",
           "u": "ư ù ú ủ ũ ụ", "i": "ì í ỉ ĩ ị", "y": "ỳ ý ỷ ỹ ỵ", "d": "đ"},
}
LONG_PRESS_MS = 450


def accents_for(language: str) -> dict[str, list[str]]:
    table = ACCENTS.get("en" if is_english(language) else language, {})
    return {k: v.split() for k, v in table.items()}


def dict_path_for(language: str) -> str:
    from predictor import dict_path_for as _p
    return _p(language)


def _system_locale() -> str:
    """The PC's language/region, e.g. "de-DE" or "en_AU.UTF-8"."""
    try:
        if IS_WINDOWS:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return buf.value                        # e.g. "en-AU"
            return ""
        return (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                or os.environ.get("LANG") or "")        # e.g. "en_AU.UTF-8"
    except Exception:
        return ""


def _detect_language(region: str = None) -> str:
    """Language matching the PC: German/French/... when the PC is set to it;
    for English, en-AU/NZ → Australian, UK & most of the Commonwealth →
    British, anything else → US."""
    if region is None:
        region = _system_locale()
    parts = region.replace("-", "_").split(".")[0].split("_")
    lang = LOCALE_ALIASES.get(parts[0].lower(), parts[0].lower())
    if lang in OTHER_LANGUAGE_CODES:
        return lang
    country = parts[1].upper() if len(parts) > 1 else ""
    if country in ("AU", "NZ"):
        return "en_AU"
    if country in ("GB", "IE", "ZA", "IN", "SG", "MY", "HK", "MT", "KE", "NG", "PK"):
        return "en_GB"
    return "en_US"


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

UPDATE_TMP_PREFIX = "osk_update_"   # temp folders holding downloaded installers


def _cleanup_old_update_downloads() -> bool:
    """Delete installers left in %TEMP% by earlier self-updates (they can't
    delete themselves while running).  GLib timeout: runs once."""
    import glob
    import tempfile
    for d in glob.glob(os.path.join(tempfile.gettempdir(), UPDATE_TMP_PREFIX + "*")):
        shutil.rmtree(d, ignore_errors=True)   # an installer still running is skipped
    return False


def _child_env() -> dict:
    """Environment for processes we spawn that must not inherit PyInstaller state.

    A relaunched copy of ourselves (or an installer that relaunches us) would
    otherwise pick up _PYI_*/_MEIPASS2 and GTK paths from this process and
    mis-locate its own bundle.
    """
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        for k in list(env):
            if k.startswith("_PYI") or k == "_MEIPASS2":
                del env[k]
        for k in ("GDK_PIXBUF_MODULE_FILE", "GSETTINGS_SCHEMA_DIR", "GI_TYPELIB_PATH",
                  "GTK_DATA_PREFIX", "GTK_EXE_PREFIX", "XDG_DATA_DIRS",
                  "FONTCONFIG_PATH", "FONTCONFIG_FILE"):
            env.pop(k, None)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _parse_version(v: str):
    """'v0.1.8' / '0.1.8-beta' -> (0, 1, 8); None if no leading numeric version."""
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _version_newer(remote: str, local: str) -> bool:
    """True only when both parse and remote > local (trailing zeros ignored)."""
    r, l = _parse_version(remote), _parse_version(local)
    if r is None or l is None:
        return False
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))


# ── Persistent settings ───────────────────────────────────────────────────────
if IS_WINDOWS:
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                              "OnScreenKeyboard")
    # One-time migration from the pre-0.1.9 location.
    _legacy_cfg = os.path.expanduser("~/.config/onscreen_keyboard")
    if not os.path.exists(CONFIG_DIR) and os.path.isdir(_legacy_cfg):
        try:
            shutil.copytree(_legacy_cfg, CONFIG_DIR)
        except Exception as _e:
            print(f"[config] Could not migrate old settings: {_e}")
else:
    CONFIG_DIR = os.path.expanduser("~/.config/onscreen_keyboard")
CONFIG_FILE       = os.path.join(CONFIG_DIR, "settings.json")
CUSTOM_WORDS_FILE  = os.path.join(CONFIG_DIR, "custom_words.json")
CUSTOM_THEMES_FILE = os.path.join(CONFIG_DIR, "custom_themes.json")
MACROS_FILE        = os.path.join(CONFIG_DIR, "macros.json")
LEARNED_WORDS_FILE = os.path.join(CONFIG_DIR, "learned_words.json")


def learned_words_file(language: str) -> str:
    """Each language learns separately (all English variants share one)."""
    if is_english(language):
        return LEARNED_WORDS_FILE
    return os.path.join(CONFIG_DIR, f"learned_words_{language}.json")
CLICK_WAV          = os.path.join(CONFIG_DIR, "click.wav")
BEEP_WAV           = os.path.join(CONFIG_DIR, "beep.wav")

def _windows_display_scale() -> float:
    """Windows' display scaling (Settings → Display → Scale) as a factor:
    1.0 at 100 %, 1.75 at 175 %.  Read from the registry, which holds the real
    value even when this process isn't DPI-aware (GetDpiForSystem would then
    report 96)."""
    if not IS_WINDOWS:
        return 1.0
    dpi = 0
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Desktop\WindowMetrics") as k:
            dpi = int(winreg.QueryValueEx(k, "AppliedDPI")[0])
    except Exception:
        try:
            import ctypes
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            pass
    return dpi / 96 if dpi >= 96 else 1.0


def _text_scale() -> float:
    """How much to enlarge our pixel sizes so text matches the display scaling.
    GTK already applies whole-number scales itself (×2 at 200 %), so only the
    remainder is ours: 175 % → ×1.75, 200 % → ×1, 250 % → ×1.25.
    OSK_TEXT_SCALE overrides it (testing, or a user who wants it different)."""
    override = os.environ.get("OSK_TEXT_SCALE")
    if override:
        try:
            return max(0.5, min(4.0, float(override)))
        except ValueError:
            pass
    scale = _windows_display_scale()
    try:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        gdk_scale = monitor.get_scale_factor()
    except Exception:
        gdk_scale = 1
    return max(1.0, min(4.0, scale / max(1, gdk_scale)))


TEXT_SCALE = _text_scale()


def _px(n: float) -> int:
    """A size in pixels at 100 % display scaling → pixels on this display."""
    return round(n * TEXT_SCALE)


_CSS_PX_RE = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)px")


def _scale_css_fonts(css: str) -> str:
    """Enlarge a stylesheet's sizes by TEXT_SCALE: text, button heights,
    padding, corner radii.  1–2 px values (borders, hairlines) stay crisp."""
    if TEXT_SCALE == 1.0:
        return css

    def _one(m):
        v = float(m.group(1))
        return m.group(0) if v <= 2 else f"{_px(v)}px"
    return _CSS_PX_RE.sub(_one, css)


# Segoe UI is what the built-in Windows on-screen keyboard uses.
DEFAULT_FONT = "Segoe UI" if IS_WINDOWS else "Ubuntu"
DEFAULT_FONT_SIZE = 18     # big and clear by default
FONT_SIZE_MAX = 36
FONT_SETTINGS_VERSION = 2  # bump to re-apply font defaults once on upgrade
SETTINGS_LABEL_W = _px(150)  # width of the label column on the settings page

# Shared by the running keyboard and the installer's Start-menu shortcut, so a
# pinned taskbar icon and the open keyboard are the same taskbar button.
APP_USER_MODEL_ID = "Gitties67.OnScreenKeyboard"

WINDOWS_PIN_STEPS = (
    "Windows only lets you pin apps yourself, but it's quick:\n\n"
    "1.  Right-click the keyboard's button on the taskbar.\n"
    "2.  Choose \"Pin to taskbar\".\n\n"
    "The icon then stays on your taskbar, even when the keyboard is closed. "
    "To start it automatically when you sign in, re-run the installer and tick "
    "\"Start On-Screen Keyboard when I sign in\".")

# What each setting does and why you'd change it (the (i) buttons in Settings).
SETTINGS_HELP = {
    "Theme": "The keyboard's colours. Dark is easy on the eyes, Light suits bright rooms, "
             "and High Contrast makes every key as easy to see as possible.",
    "Layout": "Which key arrangement to show. Pick the one that matches the language your "
              "computer is set to type in (QWERTY for English).",
    "Font": "The typeface on the keys. Segoe UI matches the built-in Windows on-screen "
            "keyboard; change it only if another font is easier for you to read.",
    "Font size": "How big the letters on the keys are. Make it bigger if the labels are hard "
                 "to read, smaller if long labels feel cramped.",
    "Key size": "How tall the keys are. Bigger keys are easier to hit; smaller keys take up "
                "less of the screen. Dragging the top or bottom edge does the same thing.",
    "Opacity": "How see-through the keyboard is. Lower it to see what's behind the keyboard "
               "while you type.",
    "Language": "The language word suggestions use. It starts on your PC's language. "
                "English comes in Australian/UK ('colour') and US ('color') spelling. "
                "You don't need to type accents: 'uber' suggests 'über'. To type one "
                "yourself, hold a letter key down (e → é è ê ë). Korean keys type "
                "Hangul (the syllable being built shows in the bar); 한/A switches to "
                "English letters and back.",
    "Auto space": "When you pick a suggested word, a space is typed after it so you can go "
                  "straight on to the next word. Turn it off if you'd rather add punctuation "
                  "or the space yourself.",
    "Dwell click": "Hover over a key without clicking and it presses itself after the delay. "
                   "Useful if clicking is hard or tiring, e.g. with a head mouse or eye tracker.",
    "Sound volume": "How loud the key sound is. It only changes the keyboard's own sound, "
                    "not your computer's volume.",
    "Key sound": "A sound on every key press, so you know it registered. Click is soft, Beep "
                 "is louder, Off is silent.",
    "Modifier keys": "How Ctrl and Alt behave. Auto-release lets go after the next key (tap "
                     "Ctrl, then C, to copy). Sticky keeps them on until you tap them again.",
    "Shift key": "One-shot capitalises just the next letter. Sticky keeps Shift on until you "
                 "tap it again, handy for typing several capitals.",
    "Taskbar": ("Keeps the keyboard on your taskbar so it's always one click away. Windows "
                "only lets you pin apps yourself, so this shows you how (it takes two clicks)."
                if IS_WINDOWS else
                "Pins the keyboard to your panel and starts it when you log in, so it's "
                "always one click away."),
    "Tutorial": "Replays the four quick tips shown the first time the keyboard opened.",
    "Learn my words": "Remembers which words you use (on this computer only, never sent "
                      "anywhere) so they're suggested sooner. Words next to numbers or symbols, "
                      "like emails and passwords, are never learned. Forget erases it all.",
    "Custom words": "Your own words and phrases (names, slang, work terms). Tap + Add, type "
                    "the word on the keyboard's keys, then tap ✓ Save. From then on, typing its "
                    "first letters suggests it first, with the capitals you gave it.",
    "Macros": "Short triggers that expand into longer text. Type the trigger and pick it from "
              "the suggestions; {date} and {time} fill in today's date and the time.",
    "Custom themes": "Make your own colour scheme, step by step, with a live preview. "
                     "Tap a saved theme to use it, ✎ to change its colours, × to delete it.",
    "Rate or comment": "Opens GitHub in your browser so you can leave a star rating or a "
                       "note. It's the best way to tell me what to fix next.",
}

# What the PC is set to: the starting language (and layout) on a fresh install
_DETECTED_LANGUAGE = _detect_language()

DEFAULT_SETTINGS: dict = {
    "theme":                "dark",   # dark | light | midnight | hc
    "font_size":            DEFAULT_FONT_SIZE, # key label font size in px (10–36, before display scaling)
    "dwell_enabled":        False,
    "dwell_delay":          0.8,      # seconds (0.3 – 2.0)
    "click_sound":          True,
    "click_style":          "click",  # click (soft tick) | beep (tone)
    "modifier_auto_release": True,    # release Ctrl/Alt automatically after next keypress
    "shift_sticky":          False,   # False = one-shot (release after keypress), True = sticky like Ctrl
    "opacity":              1.0,      # window opacity (0.3 – 1.0)
    "key_scale":            1.0,      # key size scale factor (0.3 – 1.5)
    "font_family":          DEFAULT_FONT, # key label font family
    "layout":               LANGUAGE_LAYOUT.get(_DETECTED_LANGUAGE, "qwerty"),  # qwerty | azerty | qwertz
    "update_check_enabled": True,
    "learn_words":          True,     # learn word usage from what you type (local only)
    "tutorial_done":        False,    # first-run tour shown (fresh installs only; see _load_settings)
    "font_version":         FONT_SETTINGS_VERSION,
    "language":             _DETECTED_LANGUAGE,  # en_AU | en_GB | en_US | de | fr | ... (word suggestions)
    "space_after_suggestion": True,   # type a space after an accepted suggestion
    "sound_volume":         0.7,      # key sound loudness, 0.05 – 1.0
}

DEFAULT_MACROS = [
    {"trigger": "date", "expansion": "{date}"},
    {"trigger": "time", "expansion": "{time}"},
]

# Only fonts that ship with the OS, so a choice never silently falls back.
if IS_WINDOWS:
    FONT_FAMILIES = ["Segoe UI", "Arial", "Verdana", "Tahoma", "Calibri", "Consolas",
                     "Comic Sans MS"]
else:
    FONT_FAMILIES = ["Ubuntu", "Noto Sans", "DejaVu Sans", "Roboto", "Arial", "Courier New"]

__version__ = "0.1.16"

GITHUB_REPO_URL     = "https://github.com/Gitties67/Onscreen-keyboard"
FEEDBACK_REVIEW_URL = GITHUB_REPO_URL + "/issues/new?template=review.yml"
FEEDBACK_NOTE_URL   = GITHUB_REPO_URL + "/issues/new?template=note.yml"

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
        "special_bg": "#242424", "special_fg": "#e8e8e8",
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
        "toggle_fg": "#bdbdbd",
        "sublabel_fg": "#999999",
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
        "special_bg": "#dcdcdc", "special_fg": "#1a1a1a",
        "special_border": "#bbbbbb", "special_bot": "#888888",
        "special_hover": "#ebebeb",
        "sugg_fg": "#005490", "sugg_hover": "#cccccc", "sugg_divider": "#bbbbbb",
        "search_fg": "#111111",
        "close_bg": "#f0d0d0", "close_fg": "#aa1a00",
        "close_border": "#ddaaaa", "close_bot": "#cc9999",
        "close_hover_bg": "#cc2200", "close_hover_fg": "#ffffff",
        "settings_bg": "#dcdcdc", "settings_fg": "#555555",
        "settings_border": "#bbbbbb", "settings_bot": "#999999",
        "choice_bg": "#ffffff", "choice_fg": "#333333",
        "choice_border": "#c0c0c0", "choice_bot": "#999999",
        "toggle_fg": "#3a3a3a",
        "sublabel_fg": "#767676",
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
        "special_bg": "#111a26", "special_fg": "#b8cde0",
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
        "toggle_fg": "#a9c2dc",
        "sublabel_fg": "#8899bb",
        "active_bg": "#1f6fbd", "active_fg": "#ffffff",
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
        # Caption buttons: outlined, with an inverted (yellow) hover so the
        # hover state is unmistakable — a 10% grey overlay vanishes on black.
        "caption_border": "#ffffff",
        "caption_hover_bg": "#ffff00", "caption_hover_fg": "#000000",
        "caption_active_bg": "#cccc00",
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
# What each theme-maker colour paints (shown by the (i) beside the question).
WIZARD_HELP = {
    "bg":     "The colour behind everything: the gaps between keys, the "
              "suggestion bar and the settings page.",
    "key_bg": "The face of the letter and number keys. Function keys (Tab, Shift, "
              "Esc...) use a blend of this and the background.",
    "key_fg": "The letters and symbols printed on the keys, and most text in "
              "Settings. Pick something that stands out from the key colour.",
    "border": "The thin outline around each key and button, plus the divider "
              "lines in the suggestion bar.",
    "accent": "The highlight colour: suggested words, latched keys like Shift or "
              "Ctrl when they're on, and selected options in Settings.",
    "danger": "The close (×) button in the top corner, so it's easy to spot "
              "and hard to hit by accident.",
}

# Theme-maker palette: 12 bright colours, then 12 deep shades / greys.
WIZARD_PALETTE = [
    "#e81123", "#ff8c00", "#fff100", "#16c60c", "#00b294", "#00bcf2",
    "#0078d4", "#5c2d91", "#b4009e", "#e3008c", "#ffffff", "#000000",
    "#7a1f2b", "#8a4b08", "#6b6a0e", "#1e5d24", "#0b4f4a", "#0b4a6b",
    "#1a2b4c", "#2e1a47", "#4a1942", "#2d2d2d", "#808080", "#c8c8c8",
]

WIZARD_DEFAULTS = {
    "bg": "#1c1c1c", "key_bg": "#2d2d2d", "key_fg": "#f0f0f0",
    "border": "#404040", "accent": "#0078d4", "danger": "#ff6b6b",
}


def _make_theme_css(c: dict, font_size: int = DEFAULT_FONT_SIZE, font_family: str = DEFAULT_FONT) -> str:
    """Theme stylesheet, with its text enlarged for the display scaling."""
    return _scale_css_fonts(_theme_css_at_100(c, font_size, font_family))


def _theme_css_at_100(c: dict, font_size: int, font_family: str) -> str:
    """Generate a complete colour-override CSS string from a theme dict."""
    if font_family not in FONT_FAMILIES:
        print(f"[theme] Unknown font_family {font_family!r}, falling back to {DEFAULT_FONT}")
        font_family = DEFAULT_FONT
    special_size  = max(9,  font_size - 2)   # modifier/nav labels slightly smaller
    char_size     = font_size + 8            # letters/digits/symbols: big, like Windows' OSK
    settings_size = max(14, font_size - 2)   # Settings text follows Font size too
    sublabel_size = max(8,  font_size - 4)   # shift-symbol hint in corner
    tour_size     = max(14, font_size)       # tour text never smaller than 14px

    def _safe_mix(a: str, b: str, t: float, fallback: str) -> str:
        try:
            return _mix(a, b, t)
        except (ValueError, TypeError):
            return fallback

    # Key-press flash: a soft inversion (background halfway to the label
    # colour, label in the key colour) rather than a full black/white swap.
    flash_bg    = _safe_mix(c['key_bg'], c['key_fg'], 0.5, c['key_hover'])
    flash_fg    = c['key_bg']
    flash_sp_bg = _safe_mix(c['special_bg'], c['special_fg'], 0.5, c['special_hover'])
    flash_sp_fg = c['special_bg']

    # Windows-style caption buttons: ~10% / ~18% overlay of the title-bar text
    # colour on the title-bar background (themes may override explicitly).
    tb_bg, tb_fg = c['titlebar_bg'], c['titlebar_fg']
    cap_border    = c.get('caption_border', 'transparent')
    cap_hover_bg  = c.get('caption_hover_bg',  _safe_mix(tb_bg, tb_fg, 0.10, c['key_hover']))
    cap_hover_fg  = c.get('caption_hover_fg',  tb_fg)
    cap_active_bg = c.get('caption_active_bg', _safe_mix(tb_bg, tb_fg, 0.18, c['key_hover']))
    # Fallback stack so a missing family never leaves keys without labels.
    return f"""* {{ font-family: "{font_family}", "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif; }}

window {{ background-color: {c['bg']}; }}
#main-box {{ background-color: {c['bg']}; }}
#title-bar {{ background-color: {c['titlebar_bg']}; border-bottom-color: {c['titlebar_border']}; }}
#titlebar-title {{ color: {c['titlebar_fg']}; }}
#suggestion-bar {{ background-color: {c['bar_bg']}; border-bottom: 1px solid {c['bar_border']}; }}
.suggestion-btn {{ color: {c['sugg_fg']}; background-color: transparent;
                   font-size: {font_size}px; }}
.suggestion-btn:hover {{ background-color: {c['sugg_hover']}; }}
.palette-swatch {{ border: 1px solid {c['key_border']}; padding: 0; box-shadow: none;
                   min-width: 32px; min-height: 32px; }}
.palette-swatch.selected, .palette-swatch.current {{ border: 3px solid {c['key_fg']}; }}
.suggestion-btn.ci-save {{ background-color: {c['active_bg']}; color: {c['active_fg']}; }}
.suggestion-btn.ci-save:disabled {{ background-color: transparent; color: {c['sublabel_fg']}; }}
.suggestion-btn + .suggestion-btn {{ border-left-color: {c['sugg_divider']}; }}
#search-label {{ color: {c['search_fg']}; font-size: {font_size}px; }}
.key-btn {{ background-color: {c['key_bg']}; color: {c['key_fg']};
            border: 1px solid {c['key_border']}; border-bottom: 2px solid {c['key_bot']};
            font-size: {font_size}px; }}
.key-btn.char-key {{ font-size: {char_size}px; }}
#settings-label, .settings-choice, #settings-toggle {{ font-size: {settings_size}px; }}
#settings-hint {{ font-size: {settings_size - 1}px; }}
.settings-section {{ font-size: {settings_size + 2}px; }}
.key-btn:hover {{ background-color: {c['key_hover']}; border-color: {c['key_hover_border']};
                  border-bottom-color: {c['key_bot']}; }}
.key-btn:active {{ background-color: {c['key_bg']}; border-color: {c['key_border']}; }}
.special {{ background-color: {c['special_bg']}; color: {c['special_fg']};
             border-color: {c['special_border']}; border-bottom-color: {c['special_bot']};
             font-size: {special_size}px; }}
.special:hover {{ background-color: {c['special_hover']}; }}
.special:active {{ background-color: {c['special_bg']}; }}
.space {{ background-color: {c['key_bg']}; border-color: {c['key_border']}; }}
.key-btn.has-accents {{ background-image: radial-gradient(circle at 86% 18%, {c['sublabel_fg']} 2px, transparent 3px); }}
.accent-choice {{ font-size: {font_size + 2}px; min-width: 44px; min-height: 44px; padding: 0; }}
.key-btn.key-flash {{ background-color: {flash_bg}; color: {flash_fg}; transition: none; }}
.key-btn.special.key-flash {{ background-color: {flash_sp_bg}; color: {flash_sp_fg}; }}
.caption-btn {{ background-color: transparent; background-image: none; color: {tb_fg};
                border: 1px solid {cap_border}; border-radius: 0; box-shadow: none;
                text-shadow: none; }}
.caption-btn:hover {{ background-color: {cap_hover_bg}; color: {cap_hover_fg}; }}
.caption-btn:active {{ background-color: {cap_active_bg}; color: {cap_hover_fg}; }}
.caption-btn.mdl2 label {{ font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets"; font-size: 10px; }}
#caption-close:hover {{ background-color: #c42b1c; color: #ffffff; }}
#caption-close:active {{ background-color: #a52314; color: #ffffff; }}
popover.tour-popover {{ background-color: {c['key_bg']}; color: {c['key_fg']};
                        border: 2px solid {c['active_bg']}; }}
popover.tour-popover .tour-title {{ font-size: {tour_size + 2}px; }}
popover.tour-popover .tour-body {{ font-size: {tour_size}px; }}
popover.tour-popover .tour-count {{ color: {c['sublabel_fg']}; }}
.tour-next {{ background-color: {c['active_bg']}; color: {c['active_fg']};
              border: 1px solid {c['active_border']}; }}
.tour-next:hover {{ background-color: {c['active_hover']}; }}
.tour-skip {{ background-color: {c['choice_bg']}; color: {c['choice_fg']};
              border: 1px solid {c['choice_border']}; }}
.tour-skip:hover {{ background-color: {c['key_hover']}; }}
.tour-btn.dwell-pending {{ background-color: {c['active_hover']}; color: {c['active_fg']};
                           border: 2px solid {c['key_fg']}; }}
.key-btn.dwell-pending {{ background-color: {c['active_hover']}; color: {c['active_fg']};
                          border-color: {c['active_border']}; }}
#settings-btn {{ background-color: {c['settings_bg']}; color: {c['settings_fg']};
                 border: 1px solid {c['settings_border']}; border-bottom: 2px solid {c['settings_bot']}; }}
#settings-btn:hover {{ color: {c['key_fg']}; background-color: {c['key_hover']}; }}
#settings-panel {{ background-color: {c['bg']}; }}
#settings-label {{ color: {c['key_fg']}; }}
.settings-section {{ color: {c['key_fg']}; }}
#settings-scroll, #settings-scroll viewport {{ background-color: transparent; }}
#settings-inner {{ background-color: {c['bg']}; padding: 0 12px 16px 0; }}
#settings-scroll scrollbar {{ background-color: transparent; border: none; }}
#settings-scroll scrollbar slider {{ background-color: {c['key_border']}; border: none;
                                     border-radius: 4px; min-width: 8px; min-height: 40px; }}
#settings-scroll scrollbar slider:hover {{ background-color: {c['sublabel_fg']}; }}
#settings-scroll undershoot.bottom, #settings-scroll undershoot.top {{ background: none; }}
.settings-divider {{ background-color: {c['key_border']}; min-height: 1px; }}
#settings-hint {{ color: {c['sublabel_fg']}; }}
.info-btn {{ color: {c['sublabel_fg']}; background-color: transparent; background-image: none;
             border: 1px solid {c['sublabel_fg']}; border-radius: 10px; box-shadow: none;
             min-width: 18px; min-height: 18px; padding: 0; margin: 0;
             font-size: 11px; font-weight: 700; font-style: italic; }}
.info-btn:hover {{ color: {c['active_fg']}; background-color: {c['active_bg']};
                   border-color: {c['active_bg']}; }}
.settings-list {{ background-color: {c['key_bg']}; border: 1px solid {c['key_border']};
                  border-radius: 6px; }}
.settings-list row + row {{ border-top: 1px solid {c['key_border']}; }}
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
    toggle_fg     = _mix(key_fg, bg, 0.20)
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
    "pgup":   0xFF55,
    "pgdn":   0xFF56,
    "prtscn": 0xFF61,  # fallback if no snipping tool found
}

ACCESSIBLE_KEY_NAMES = {
    "shift": "Shift", "caps": "Caps Lock", "tab": "Tab",
    "backspace": "Backspace", "return": "Enter", "escape": "Escape",
    "ctrl": "Control", "alt": "Alt", "win": "Windows key",
    "space": "Space bar", "delete": "Delete", "home": "Home", "end": "End",
    "left": "Left arrow", "right": "Right arrow",
    "up": "Up arrow", "down": "Down arrow", "prtscn": "Print Screen",
    "pgup": "Page Up", "pgdn": "Page Down", "fn": "Function key",
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
BASE_KEY_H  = _px(52)   # keys grow with the display scaling, like their text
SUGGESTION_H = _px(40)   # height of the suggestion bar row
TITLEBAR_H   = _px(28)   # height of the custom title-bar strip
# Tight gaps, like the Windows on-screen keyboard: the space goes to the keys
ROW_GAP      = 2      # between key rows (and between title/suggestion bar/keys)
KEY_GAP      = 2      # between keys in a row
# Vertical space that isn't keys: 7 gaps + the bars' own padding
LAYOUT_OVERHEAD = 7 * ROW_GAP + 12
CAPTION_W    = 46   # width of a Windows-style caption button (minimize / close)
STRIP_H      = 8    # height of the top/bottom resize strips
MIN_W        = 400  # minimum window width during resize
KEY_ROW_H_OVERHEAD = 32  # side margins + padding around a key row (fallback)
SIDE_ZONE    = 7    # width of the left/right margins that act as e/w resize zones
EDGE_ZONE    = 16   # corner size: px from a window corner that resizes diagonally

# Resize-zone cursors: (CSS cursor name, Gdk.CursorType fallback).  GDK maps the
# CSS names to IDC_SIZEWE / IDC_SIZENS / IDC_SIZENWSE / IDC_SIZENESW on Windows.
RESIZE_CURSORS = {
    "e":  ("ew-resize",   "SB_H_DOUBLE_ARROW"),
    "w":  ("ew-resize",   "SB_H_DOUBLE_ARROW"),
    "n":  ("ns-resize",   "SB_V_DOUBLE_ARROW"),
    "s":  ("ns-resize",   "SB_V_DOUBLE_ARROW"),
    "nw": ("nwse-resize", "TOP_LEFT_CORNER"),
    "se": ("nwse-resize", "BOTTOM_RIGHT_CORNER"),
    "ne": ("nesw-resize", "TOP_RIGHT_CORNER"),
    "sw": ("nesw-resize", "BOTTOM_LEFT_CORNER"),
}

# Windows caption glyphs: (Segoe MDL2 / Fluent Icons code point, Unicode fallback)
CAPTION_GLYPHS = {
    "minimize": ("\uE921", "\u2014"),   # ChromeMinimize  / em dash
    "close":    ("\uE8BB", "\u00D7"),   # ChromeClose     / multiplication x
    "expand":   ("\uE70E", "\u25B2"),   # ChevronUp       / up triangle
}
CAPTION_ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")

# First-run tour: (target, title, body).  Body text kept to <= ~15 words.
TOUR_STEPS = [
    ("suggestions", "Word suggestions",  "Tap a word to finish typing it."),
    ("settings",    "Settings",          "Change theme, key size, sound, dwell-click and more."),
    ("emoji_clip",  "Emoji and clipboard", "Add an emoji, or paste something you copied earlier."),
    ("titlebar",    "Move and resize",
     "Drag to move, drag edges to resize, double-click to shrink to just suggestions."),
]
TOUR_DWELL_MOVE_PX = 24   # pointer must move this far after a step appears before dwell arms

# ── Key repeat ────────────────────────────────────────────────────────────────
REPEAT_DELAY_MS    = 400   # delay before repeat starts (ms)
REPEAT_INTERVAL_MS = 50    # interval between repeated keypresses (ms)
# Actions that should never repeat (they're toggles, not typeable keys)
NON_REPEAT_ACTIONS = {"shift", "caps", "ctrl", "alt", "win", "hanyeong", "fn"}

# ── Key layout ────────────────────────────────────────────────────────────���───
# Each entry: (display_label, action, base_width, extra_css_classes)
KEY_ROWS = [
    # Row 0 — number row (Esc on the left, like the Windows on-screen keyboard;
    # with Fn latched the digits are F1–F12)
    [
        ("Esc", "escape", 60, ["special"]),
        ("`", "`",  52, []), ("1", "1",  52, []), ("2", "2",  52, []),
        ("3", "3",  52, []), ("4", "4",  52, []), ("5", "5",  52, []),
        ("6", "6",  52, []), ("7", "7",  52, []), ("8", "8",  52, []),
        ("9", "9",  52, []), ("0", "0",  52, []), ("-", "-",  52, []),
        ("=", "=",  52, []), ("⌫", "backspace", 95, ["special"]),
    ],
    # Row 1 — QWERTY
    [
        ("Tab", "tab", 80, ["special"]),
        ("q", "q", 52, []), ("w", "w", 52, []), ("e", "e", 52, []),
        ("r", "r", 52, []), ("t", "t", 52, []), ("y", "y", 52, []),
        ("u", "u", 52, []), ("i", "i", 52, []), ("o", "o", 52, []),
        ("p", "p", 52, []), ("[", "[", 52, []), ("]", "]", 52, []),
        ("\\", "\\", 63, []), ("Del", "delete", 64, ["special"]),
    ],
    # Row 2 — ASDF
    [
        ("Caps", "caps", 92, ["special"]),
        ("a", "a", 52, []), ("s", "s", 52, []), ("d", "d", 52, []),
        ("f", "f", 52, []), ("g", "g", 52, []), ("h", "h", 52, []),
        ("j", "j", 52, []), ("k", "k", 52, []), ("l", "l", 52, []),
        (";", ";", 52, []), ("'", "'", 52, []),
        ("↵", "return", 167, ["special"]),
    ],
    # Row 3 — ZXCV
    [
        ("⇧", "shift", 128, ["special"]),
        ("z", "z", 52, []), ("x", "x", 52, []), ("c", "c", 52, []),
        ("v", "v", 52, []), ("b", "b", 52, []), ("n", "n", 52, []),
        ("m", "m", 52, []), (",", ",", 52, []), (".", ".", 52, []),
        ("/", "/", 52, []),
        ("⇧", "shift", 183, ["special"]),
    ],
    # Row 4 — bottom (Fn latches like Ctrl: digits → F1–F12, arrows → Home/
    # End/PgUp/PgDn, Del → PrtScn for the next key)
    [
        ("Fn",   "fn",   52, ["special"]),
        ("Ctrl", "ctrl", 60, ["special"]),
        ("⊞",   "win",  52, ["special"]),
        ("Alt",  "alt",  52, ["special"]),
        ("",     "space", 295, ["space"]),
        ("Alt",  "alt",  52, ["special"]),
        ("Ctrl", "ctrl", 60, ["special"]),
        ("←", "left",  52, ["special"]),
        ("↑", "up",    52, ["special"]),
        ("↓", "down",  52, ["special"]),
        ("→", "right", 52, ["special"]),
    ],
]

# ── Alternative layouts ───────────────────────────────────────────────────────
_NUM_ROW = KEY_ROWS[0]   # number row — same for all layouts
_BOT_ROW = KEY_ROWS[4]   # bottom row — same for all layouts

LAYOUT_ROWS: dict[str, list] = {
    "qwerty": KEY_ROWS,
    "azerty": [
        _NUM_ROW,
        [("Tab","tab",80,["special"]),
         ("a","a",52,[]),("z","z",52,[]),("e","e",52,[]),("r","r",52,[]),
         ("t","t",52,[]),("y","y",52,[]),("u","u",52,[]),("i","i",52,[]),
         ("o","o",52,[]),("p","p",52,[]),
         ("[","[",52,[]),("]","]",52,[]),("\\","\\",63,[]),("Del","delete",64,["special"])],
        [("Caps","caps",92,["special"]),
         ("q","q",52,[]),("s","s",52,[]),("d","d",52,[]),("f","f",52,[]),
         ("g","g",52,[]),("h","h",52,[]),("j","j",52,[]),("k","k",52,[]),
         ("l","l",52,[]),("m","m",52,[]),("'","'",52,[]),
         ("↵","return",167,["special"])],
        [("⇧","shift",128,["special"]),
         ("w","w",52,[]),("x","x",52,[]),("c","c",52,[]),("v","v",52,[]),
         ("b","b",52,[]),("n","n",52,[]),(",",",",52,[]),(".",".",52,[]),("/","/",52,[]),
         ("⇧","shift",183,["special"])],
        _BOT_ROW,
    ],
    "qwertz": [
        _NUM_ROW,
        [("Tab","tab",80,["special"]),
         ("q","q",52,[]),("w","w",52,[]),("e","e",52,[]),("r","r",52,[]),
         ("t","t",52,[]),("z","z",52,[]),("u","u",52,[]),("i","i",52,[]),
         ("o","o",52,[]),("p","p",52,[]),
         ("[","[",52,[]),("]","]",52,[]),("\\","\\",63,[]),("Del","delete",64,["special"])],
        [("Caps","caps",92,["special"]),
         ("a","a",52,[]),("s","s",52,[]),("d","d",52,[]),("f","f",52,[]),
         ("g","g",52,[]),("h","h",52,[]),("j","j",52,[]),("k","k",52,[]),
         ("l","l",52,[]),(";",";",52,[]),("'","'",52,[]),
         ("↵","return",167,["special"])],
        [("⇧","shift",128,["special"]),
         ("y","y",52,[]),("x","x",52,[]),("c","c",52,[]),("v","v",52,[]),
         ("b","b",52,[]),("n","n",52,[]),("m","m",52,[]),
         (",",",",52,[]),(".",".",52,[]),("/","/",52,[]),
         ("⇧","shift",183,["special"])],
        _BOT_ROW,
    ],
}

# Fn layer: what a key does (and shows) while Fn is latched
FN_MAP = {
    "1": "f1", "2": "f2", "3": "f3", "4": "f4", "5": "f5", "6": "f6",
    "7": "f7", "8": "f8", "9": "f9", "0": "f10", "-": "f11", "=": "f12",
    "left": "home", "right": "end", "up": "pgup", "down": "pgdn",
    "delete": "prtscn",
}
FN_LABELS = {"home": "Home", "end": "End", "pgup": "PgUp", "pgdn": "PgDn",
             "prtscn": "PrtScn"}

# Widest row natural width: 831 px of keys + 14 gaps×3px
BASE_KB_W = 873
# Rows of keys (number row, three letter rows, bottom row) — no F-key row:
# F1–F12 live on the Fn layer, as on the Windows on-screen keyboard
KB_ROWS   = 5
BASE_KB_H = BASE_KEY_H * KB_ROWS


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
        self._disp = None
        if XLIB_OK:
            try:
                self._disp = xdisplay.Display()
            except Exception as exc:   # no/invalid DISPLAY (e.g. Wayland) — use fallbacks
                print(f"[typer] XTEST unavailable ({exc}); using fallback typing")

    # ── Public API ────────────────────────────────────────────────────────────

    def type_char(self, char: str, mods: list[str] | None = None) -> None:
        # Windows — primary path is focus-independent PostMessage injection.
        # Only modified combos (Ctrl/Alt) or apps where injection fails fall
        # through to the focus-restore + pynput path.
        if IS_WINDOWS:
            if _win_inject_char(char, mods or []):
                return
            _win_focus_restore()
            if PYNPUT_OK:
                self._pynput_type_char(char, mods or [])
            return
        # Linux — prefer XTEST, fall back to AT-SPI2, then pynput.  Latin-1
        # (é ü ß ñ...) keysyms equal the code point; others (œ ő) use the
        # X11 Unicode keysym range.
        cp          = ord(char)
        keysym      = cp if cp < 0x100 else 0x1000000 + cp
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
        # Windows — focus-independent PostMessage injection first, pynput fallback.
        if IS_WINDOWS:
            if _win_inject_special(name, mods or []):
                return
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
            print(f"[pynput] type_char len={len(char)}: {exc}")

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
        # Windows — focus-independent WM_CHAR injection first (handles surrogate
        # pairs); fall back to focus-restore + pynput.type() if injection fails.
        if IS_WINDOWS:
            hwnd = _win_target_focus_hwnd()
            if hwnd and _win_post_char(hwnd, emoji_str):
                _win_log(f"emoji len={len(emoji_str)} -> hwnd={hwnd:#x} ok")
                return
            _win_focus_restore()
            if PYNPUT_OK:
                try:
                    _pynput_ctrl.type(emoji_str)
                    return
                except Exception as exc:
                    print(f"[pynput] emoji type error: {exc}")
            return
        # pynput.type() handles arbitrary Unicode on all platforms
        if PYNPUT_OK and (IS_WINDOWS or not XDOTOOL_OK):
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
        self.predictor    = WordPredictor(
            language=self._load_settings().get("language", "en_US"))
        self.typer        = KeyTyper()
        self.current_word = ""
        self._last_word   = ""   # last committed word of this sentence ("" = none)
        self._ctx: list[str] = []      # last <=2 committed words of the current sentence
        self._ctx_stem    = ""         # "don'" while typing don't (apostrophe pending)
        self._learn_save_timer = None  # debounce timer for UserModel.save()
        self._forget_timer = None      # confirm-window timer for "Forget learned words"
        self._forget_armed = False
        self._flash_timers: dict = {}  # key button → timer removing its .key-flash
        self._sound_bufs: dict = {}    # Windows: style → (wav bytes, ctypes buffer)
        self._sound_keep: list = []    # buffers kept alive for async PlaySound
        self._volume_timer = None      # debounce for regenerating sounds on slider move
        self._word_title = False       # first letter of current_word typed upper-case
        self._word_upper = False       # every letter of current_word typed upper-case
        self.shift_active = False
        self.caps_lock    = False
        self._custom_words: list[str] = []

        # Widget references
        self._letter_btns:     dict[str, Gtk.Button] = {}
        self._shift_btns:      list[Gtk.Button]       = []
        self._caps_btn:        Gtk.Button | None       = None
        self._suggestion_btns: list[Gtk.Button]       = []
        self._modifier_btns:   dict[str, list[Gtk.Button]] = {
            "ctrl": [], "alt": [], "win": [], "fn": []
        }
        # Plain keys that change label on the Fn layer: action → (button, label)
        self._fn_btns:         dict[str, tuple[Gtk.Button, str]] = {}
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
        self._wizard_current:        Gtk.Button | None = None     # big "current colour" swatch
        self._wizard_next_btn:       Gtk.Button | None      = None
        self._wizard_name_display:   Gtk.Label | None       = None
        self._wizard_preview_swatches: list[Gtk.EventBox]   = []
        self._custom_theme_btns_box: Gtk.Box | None         = None
        self._custom_theme_btns:     dict[str, Gtk.Button]  = {}

        # Sticky modifier state (Ctrl / Alt / Win latch until next keypress)
        self.ctrl_active = False
        self.alt_active  = False
        self.win_active  = False
        self.fn_active   = False   # Fn latched: next key uses its Fn-layer meaning

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
        self._update_etag:         str | None        = None  # ETag for conditional requests
        self._titlebar:       Gtk.EventBox | None = None
        self._suggestion_bar: Gtk.Box | None     = None
        self._top_strip:      Gtk.EventBox | None = None
        self._bottom_strip:   Gtk.EventBox | None = None
        self._collapsed       = False
        self._pre_collapse_h  = 0
        self._expand_btn:     Gtk.Button | None  = None
        # Real minimize (to taskbar / window list) state
        self._minimized       = False
        self._min_watch_id    = 0     # GLib timer: detects restore / WM refusal
        self._min_started     = 0.0
        self._min_saw_iconic  = False
        self._restore_timer_id = 0    # GLib timer: post-restore settle
        self._proxy: Gtk.Window | None = None   # invisible taskbar proxy window
        self._force_id        = 0     # GLib timer: deferred deiconify of the OSK
        # Height of the collapsed strip: title bar + suggestion bar + top resize
        # strip + chrome.  The collapsed state now keeps the title bar AND
        # suggestion bar visible; the title bar is the only way to expand again.
        # The bottom resize strip is hidden when collapsed, so only one strip.
        self._collapsed_h     = TITLEBAR_H + SUGGESTION_H + STRIP_H + 8

        # Key repeat state
        self._repeat_timers: dict[str, int] = {}  # action → GLib source ID
        self._repeat_active: set[str]        = set()  # actions in fast-repeat phase
        # Hold-for-accents: pending timer, the key whose popup is showing (its
        # release must not also type the plain letter) and the popup itself
        self._longpress_timer: int | None    = None
        self._longpress_fired: str | None    = None
        self._accent_pop: Gtk.Popover | None = None
        # Korean: the syllable being built (not yet typed into the app), and
        # whether 한/A has switched the letter keys back to English
        self._ko = HangulComposer()
        self._ko_latin = False
        self._ko_btn: Gtk.Button | None = None

        # Fuzzy spell-check debounce timer
        self._fuzzy_timer: int | None = None

        # Settings + dwell state
        self.settings:       dict = self._load_settings()
        self._dwell_timers:  dict[int, int] = {}  # id(widget) → GLib source ID
        self._settings_mode: bool = False

        # Resize-zone cursors: edge → Gdk.Cursor, and what each GdkWindow shows
        self._resize_cursor_cache: dict = {}
        self._zone_cursor_shown:   dict = {}

        # First-run tour (Gtk.Popover, built lazily in _start_tour)
        self._tour_popover:   Gtk.Popover | None = None
        self._tour_step:      int   = -1          # -1 = tour not running
        self._tour_anchor_xy: tuple | None = None  # pointer pos when step appeared
        self._tour_count_lbl: Gtk.Label | None  = None
        self._tour_title_lbl: Gtk.Label | None  = None
        self._tour_body_lbl:  Gtk.Label | None  = None
        self._tour_next_btn:  Gtk.Button | None = None
        self._tour_skip_btn:  Gtk.Button | None = None

        self._custom_words = self._load_custom_words()
        self.predictor.set_custom_words(self._custom_words)

        # Learned-words model (local JSON; boosts words you actually use)
        self._user_model = UserModel(learned_words_file(self.predictor.language),
                                     self.predictor.is_known)
        self._apply_learn_setting()

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
        GLib.idle_add(lambda: self._refresh_suggestions() and False)  # fill bar at startup
        if IS_WINDOWS:
            self._set_window_icon()
        self.show_all()

        # Connect clipboard monitoring after show_all
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.connect("owner-change", self._on_clipboard_change)

        # First-run tour — deferred so every target widget is allocated.
        GLib.timeout_add(800, self._maybe_start_tour)

    def _set_window_icon(self):
        """Use the bundled osk.png as the GTK window icon (Windows only)."""
        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            png = os.path.join(base, "osk.png")
            if os.path.isfile(png):
                self.set_icon_from_file(png)
        except Exception as exc:
            print(f"[icon] Could not set window icon: {exc}")

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

        kb_h = BASE_KEY_H * KB_ROWS + TITLEBAR_H + SUGGESTION_H + LAYOUT_OVERHEAD
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

        # Window-level events for left/right edge resize (SIDE_ZONE margins)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("leave-notify-event",   self._on_resize_zone_leave)
        self.connect("button-press-event",   self._on_window_button_press)
        self.connect("motion-notify-event",  self._on_resize_motion)
        self.connect("button-release-event", self._on_resize_release)
        self.connect("window-state-event",   self._on_window_state)

    # ── Real minimize (taskbar / window list) ────────────────────────────────

    def _minimize_window(self):
        """Minimize to the taskbar via a PROXY window.

        GDK3 Win32 ignores set_skip_taskbar_hint and the OSK is a NOACTIVATE
        tool window, so iconifying the OSK's own HWND either freezes (raw
        Win32) or leaves it with no taskbar button (GDK).  Instead the OSK is
        simply hidden (GTK-level) and a tiny, invisible, ordinary window — the
        proxy — is shown and iconified: THAT window owns the taskbar button.
        Restoring the proxy (taskbar click) brings the OSK back.  The OSK HWND
        is never iconified, and the focus-block (WNDPROC) is never relaxed.
        """
        if self._minimized:
            return
        # Persist geometry NOW: a hidden/iconic window reports bogus positions.
        self._save_geometry()
        self._cancel_all_timers()
        self._moving = False
        self._resizing = False
        self._resize_did_move = False
        self._resize_edge = ""
        self._minimized = True
        self._min_started = time.monotonic()
        self._min_saw_iconic = False
        _win_log(f"minimize: start (windows={IS_WINDOWS}, collapsed={self._collapsed})")
        try:
            proxy = self._ensure_proxy()
            _win_log("minimize: step osk.hide")
            self.hide()
            _win_log("minimize: step proxy.show")
            proxy.show()
            _win_log("minimize: step proxy.iconify")
            proxy.iconify()
            # GDK may still hold ICONIFIED from last time, so no transition fires
            self._min_saw_iconic = self._iconic(proxy)
            _win_log("minimize: step proxy.iconify returned")
        except Exception as exc:
            _win_log(f"minimize: error ({exc})")
            self._finish_restore("minimize-failed")
            return
        # Safety net: 500 ms ticks, GDK-state reads only, no Win32 calls.
        self._min_watch_id = GLib.timeout_add(500, self._minimize_watch)

    def _ensure_proxy(self) -> Gtk.Window:
        """Create (once) the invisible taskbar proxy window."""
        if self._proxy is not None:
            return self._proxy
        p = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        p.set_title("On-Screen Keyboard")
        p.set_default_size(1, 1)
        p.set_accept_focus(False)
        p.set_focus_on_map(False)
        p.set_skip_taskbar_hint(False)
        p.set_skip_pager_hint(False)
        p.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        p.set_opacity(0.0)
        p.move(-10000, -10000)
        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            png = os.path.join(base, "osk.png")
            if os.path.isfile(png):
                p.set_icon_from_file(png)
        except Exception as exc:
            _win_log(f"proxy: icon error ({exc})")
        p.connect("window-state-event", self._on_proxy_state)
        p.connect("delete-event", self._on_proxy_delete)
        if IS_WINDOWS:
            p.connect("realize", self._on_proxy_realize)
        self._proxy = p
        _win_log("proxy: created")
        return p

    def _on_proxy_realize(self, proxy):
        """WS_EX_APPWINDOW guarantees the proxy a taskbar button whatever else
        GDK sets; without a button a minimized keyboard could not come back."""
        try:
            import ctypes
            _win_setup_protos()
            u = ctypes.windll.user32
            hwnd = int(proxy.get_window().get_handle())
            GWL_EXSTYLE = -20
            ex = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            new = (ex | 0x00040000) & ~(0x08000000 | 0x00000080)  # +APPWINDOW -NOACTIVATE -TOOLWINDOW
            if new != ex:
                u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
            _win_log(f"proxy: exstyle {ex & 0xFFFFFFFF:#x} -> {new & 0xFFFFFFFF:#x}")
        except Exception as exc:
            _win_log(f"proxy: realize style error ({exc})")

    @staticmethod
    def _iconic(win) -> bool:
        """GDK's own view of a window's iconified state (no Win32 calls)."""
        gw = win.get_window() if win is not None else None
        return bool(gw and gw.get_state() & Gdk.WindowState.ICONIFIED)

    def _on_proxy_state(self, _w, event):
        if not self._minimized:
            return False
        if not (event.changed_mask & Gdk.WindowState.ICONIFIED):
            return False
        if event.new_window_state & Gdk.WindowState.ICONIFIED:
            self._min_saw_iconic = True
            self._cancel_restore_timer()     # re-minimized during settle
        elif self._min_saw_iconic:
            _win_log("minimize: proxy ICONIFIED cleared")
            # Do nothing inside GDK's dispatch except arm the timer.
            self._schedule_restore("proxy-state")
        return False

    def _on_proxy_delete(self, _w, _event):
        """Taskbar 'Close' on the proxy: bring the keyboard back, don't quit."""
        _win_log("proxy: delete-event -> restore OSK")
        GLib.idle_add(lambda: self._finish_restore("proxy-closed") and False)
        return True    # keep the proxy alive

    def _on_window_state(self, _w, event):
        """OSK itself: an ICONIFIED we did not ask for (Win+M, Show-desktop)."""
        if self._minimized or self._force_id:
            return False
        if not (event.changed_mask & Gdk.WindowState.ICONIFIED):
            return False
        if event.new_window_state & Gdk.WindowState.ICONIFIED:
            _win_log("osk: unexpected ICONIFIED — scheduling deiconify")
            self._force_id = GLib.timeout_add(150, self._deferred_deiconify)
        return False

    def _deferred_deiconify(self):
        self._force_id = 0
        if not self._minimized and self._iconic(self):
            _win_log("osk: step deiconify")
            self.deiconify()
        return False

    def _cancel_restore_timer(self):
        if self._restore_timer_id:
            GLib.source_remove(self._restore_timer_id)
            self._restore_timer_id = 0

    def _schedule_restore(self, why: str):
        """Finish the restore after activation has settled (~150 ms)."""
        if self._restore_timer_id:
            return
        def _go():
            self._restore_timer_id = 0
            self._finish_restore(why)
            return False
        self._restore_timer_id = GLib.timeout_add(150, _go)

    def _minimize_watch(self):
        """Safety net.  Never leave both windows hidden: if the proxy does not
        become ICONIFIED within 1.5 s, put the OSK back.  Also catches a missed
        restore state-event.  Reads GDK state only."""
        if not self._minimized:
            self._min_watch_id = 0
            return False
        if self._iconic(self._proxy):
            self._min_saw_iconic = True
            return True
        if self._min_saw_iconic:
            _win_log("minimize: watch saw proxy restore (state event missed)")
            self._schedule_restore("watch")      # no-op if already armed
            return True
        if time.monotonic() - self._min_started > 1.5:
            _win_log("minimize: proxy never iconified within 1.5s — reverting")
            self._min_watch_id = 0
            self._finish_restore("refused")
            return False
        return True

    def _finish_restore(self, why: str):
        """Hide the proxy, show the OSK at its saved geometry."""
        if not self._minimized:
            return
        self._minimized = False
        self._restore_osk(why)

    def _restore_osk(self, why: str):
        self._cancel_restore_timer()
        if self._min_watch_id:
            GLib.source_remove(self._min_watch_id)
            self._min_watch_id = 0
        _win_log(f"restore: finishing ({why})")
        try:
            if IS_WINDOWS:
                # Before hiding the proxy: while it holds the foreground,
                # SetForegroundWindow to the user's app is still allowed.
                _win_log("restore: step handback_foreground")
                self._win_handback_foreground()
                _win_log("restore: step handback_foreground returned")
            if self._proxy is not None:
                _win_log("restore: step proxy.hide")
                self._proxy.hide()
            x, y = self.settings.get("win_x"), self.settings.get("win_y")
            if x is not None and y is not None:
                _win_log("restore: step move")
                self.move(int(x), int(y))   # before show: applied at map, no jump
            _win_log("restore: step osk.show")
            if self._iconic(self):
                self.deiconify()
            self.show()          # not show_all: keeps collapsed children hidden
        except Exception as exc:
            _win_log(f"restore: error ({exc})")
        _win_log("restore: done")

    def _force_restore(self):
        """Escape hatch (second launch / OSK_SHOW message): unconditionally get
        the keyboard visible again.  Runs from GLib.idle_add."""
        _win_log("force_restore: begin")
        needs = (self._minimized or not self.get_visible() or self._iconic(self))
        self._minimized = False
        if self._force_id:
            GLib.source_remove(self._force_id)
            self._force_id = 0
        if needs:
            self._restore_osk("force")
        else:
            _win_log("force_restore: already visible")
        return False

    def _win_handback_foreground(self):
        """Give foreground back to the previously tracked target IF we hold it.
        Single SetForegroundWindow; no sleep, no AttachThreadInput."""
        import ctypes
        user32 = ctypes.windll.user32
        target = _win_last_target[0]
        cur = user32.GetForegroundWindow()
        if not target or not cur or cur == target:
            return
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(cur, ctypes.byref(pid))
        if pid.value != _win_our_pid:
            return   # someone else already has foreground — leave it alone
        ok = user32.SetForegroundWindow(target)
        _win_log(f"restore: SetForegroundWindow({int(target):#x}) -> {bool(ok)}")

    def _win_get_hwnd(self):
        """Return this window's native Win32 HWND, or None.

        Two independent strategies, each isolated so one failing never aborts
        the other (the previous single-try version meant an AttributeError from
        get_handle() silently skipped the EnumWindows fallback AND every step
        after it — including the foreground-poll timer the typing path depends
        on, which is the difference between "typing works" and "typing does
        nothing").

          a. gdk_win.get_handle() — exposed by the GdkWin32 backend through GI.
             Returns the HWND as a Python int (GI keeps it 64-bit-clean, unlike
             a raw untyped ctypes return).  Absent in some MSYS2 PyGObject
             builds where the GdkWin32 typelib isn't introspected → AttributeError.
          b. EnumWindows matched by our PID.  EnumWindows enumerates ALL
             top-level windows, not just visible ones (confirmed: the docs say
             "all top-level windows"; IsWindowVisible is a separate predicate).
             At realize time our window is created but NOT yet shown, so the old
             IsWindowVisible(h) filter rejected our own window and the fallback
             found nothing.  Match by PID only; prefer an unowned window so we
             skip GDK's hidden helper windows (clipboard, DnD, IME)."""
        import ctypes
        user32 = ctypes.windll.user32
        _win_setup_protos()  # idempotent — ensures GetWindow/GWTPID are pinned

        # ── Strategy (a): GdkWin32 get_handle() ─────────────────────────────
        try:
            gdk_win = self.get_window()
            if gdk_win is not None:
                hwnd = gdk_win.get_handle()
                if hwnd:
                    _win_log(f"get_hwnd: get_handle() -> {int(hwnd):#x}")
                    return int(hwnd)
                _win_log("get_hwnd: get_handle() returned falsy")
            else:
                _win_log("get_hwnd: get_window() is None")
        except AttributeError as exc:
            _win_log(f"get_hwnd: get_handle() unavailable ({exc}) — trying EnumWindows")
        except Exception as exc:
            _win_log(f"get_hwnd: get_handle() error ({exc}) — trying EnumWindows")

        # ── Strategy (b): EnumWindows by PID (no visibility filter) ─────────
        try:
            our_pid = os.getpid()
            GW_OWNER = 4
            unowned: list[int] = []
            owned:   list[int] = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )
            def _enum_cb(h, _):
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == our_pid:
                    # Unowned top-levels are real app windows; owned ones are
                    # GDK's hidden helpers.  Bucket both, prefer unowned.
                    if user32.GetWindow(h, GW_OWNER):
                        owned.append(h)
                    else:
                        unowned.append(h)
                return True
            _cb = EnumWindowsProc(_enum_cb)
            user32.EnumWindows(_cb, 0)
            hwnd = (unowned or owned)
            hwnd = hwnd[0] if hwnd else None
            _win_log(f"get_hwnd: EnumWindows pid={our_pid} "
                     f"unowned={len(unowned)} owned={len(owned)} -> "
                     f"{(hwnd or 0):#x}")
            return hwnd
        except Exception as exc:
            _win_log(f"get_hwnd: EnumWindows error ({exc})")
            return None

    def _win_apply_styles(self, hwnd):
        """Apply WS_EX_NOACTIVATE|WS_EX_APPWINDOW and install the WNDPROC
        subclass on hwnd.  Idempotent: a second call (e.g. from the 'map'
        handler after realize failed to get the HWND) re-applies the style but
        re-subclasses only if we haven't already, so we never chain our own
        proc on top of itself."""
        import ctypes
        user32 = ctypes.windll.user32

        # ── 2. Set WS_EX_NOACTIVATE | WS_EX_APPWINDOW ──────────────────
        # NOACTIVATE keeps focus in the app being typed into.  A NOACTIVATE
        # window only gets a taskbar button with APPWINDOW (and no TOOLWINDOW).
        # While minimized the OSK is hidden and the proxy window owns the
        # button instead, so the OSK's own styles never change after this.
        GWL_EXSTYLE      = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW  = 0x00040000
        want = WS_EX_NOACTIVATE | WS_EX_APPWINDOW

        cur_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = (cur_style | want) & ~WS_EX_TOOLWINDOW
        if new_style == cur_style:
            _win_log(f"styles: NOACTIVATE|APPWINDOW already set ({cur_style:#x})")
        else:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            _win_log(f"styles: WS_EX_NOACTIVATE|WS_EX_APPWINDOW applied "
                     f"(was {cur_style:#x})")

        if getattr(self, "_win_wndproc", None) is not None:
            _win_log("styles: WNDPROC already subclassed — skipping re-subclass")
            return

        # ── 3. Subclass WNDPROC to handle WM_MOUSEACTIVATE ──────────
        # WNDPROC: LRESULT CALLBACK(HWND, UINT, WPARAM, LPARAM)
        # LRESULT is LONG_PTR — 64 bits on 64-bit Windows; c_ssize_t matches.
        WNDPROCTYPE = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,  # LRESULT (LONG_PTR — pointer-sized)
            ctypes.c_void_p,   # HWND
            ctypes.c_uint,     # UINT  (message id)
            ctypes.c_size_t,   # WPARAM (UINT_PTR — c_void_p would give None for 0)
            ctypes.c_ssize_t,  # LPARAM (LONG_PTR)
        )

        WM_MOUSEACTIVATE = 0x0021
        WM_ACTIVATE      = 0x0006
        WM_NCACTIVATE    = 0x0086
        # GDK can call SetFocus() internally on button-press even after
        # WM_MOUSEACTIVATE returns MA_NOACTIVATE; intercepting WM_SETFOCUS
        # here and returning 0 prevents that steal from completing.
        WM_SETFOCUS      = 0x0007
        WM_SYSCOMMAND    = 0x0112
        SC_MINIMIZE      = 0xF020
        MA_NOACTIVATE    = 3
        GWLP_WNDPROC     = -4

        try:
            GetWindowLongPtr = user32.GetWindowLongPtrW
            SetWindowLongPtr = user32.SetWindowLongPtrW
        except AttributeError:
            GetWindowLongPtr = user32.GetWindowLongW
            SetWindowLongPtr = user32.SetWindowLongW
            _win_log("styles: SetWindowLongPtrW unavailable — using 32-bit variant")

        original_wndproc_addr = GetWindowLongPtr(hwnd, GWLP_WNDPROC)
        if not original_wndproc_addr:
            _win_log("styles: GetWindowLongPtr(GWLP_WNDPROC)==0 — subclass skipped")
            return

        self._win_original_addr = original_wndproc_addr
        original_wndproc = ctypes.cast(
            ctypes.c_void_p(original_wndproc_addr), WNDPROCTYPE
        )

        def _wndproc(hwnd_, msg, wparam, lparam):
            if msg and msg == _win_show_msg[0]:
                # Second launch / escape hatch.  ONLY queue work: no GDK or
                # Win32 calls from inside the window procedure.
                GLib.idle_add(self._force_restore)
                return 0
            if msg == WM_SYSCOMMAND and (wparam & 0xFFF0) == SC_MINIMIZE:
                # Taskbar-button click while shown: use our proxy minimize,
                # never a raw Win32 minimize of the OSK (GDK state desync).
                GLib.idle_add(self._minimize_window)
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if msg == WM_SETFOCUS:
                # Return 0 without passing to DefWindowProc — prevents
                # GDK from registering that we received keyboard focus.
                return 0
            if msg == WM_ACTIVATE:
                # WA_INACTIVE == 0; intercept only when being activated.
                if (wparam & 0xFFFF) != 0:
                    return 0
            if msg == WM_NCACTIVATE:
                # Forward with wParam=0 so GDK sees a deactivation event,
                # lParam=-1 prevents DefWindowProc from calling RedrawWindow.
                return original_wndproc(hwnd_, WM_NCACTIVATE, 0, -1)
            return original_wndproc(hwnd_, msg, wparam, lparam)

        self._win_wndproc = WNDPROCTYPE(_wndproc)
        SetWindowLongPtr(hwnd, GWLP_WNDPROC,
                         ctypes.cast(self._win_wndproc, ctypes.c_void_p).value)
        _win_log("styles: WM_MOUSEACTIVATE/WM_SETFOCUS subclass installed")

    def _win_setup_hwnd_dependent(self, source: str) -> bool:
        """Acquire the HWND and apply the style/subclass.  Returns True once the
        HWND has been obtained (so callers can stop retrying).  Safe to call
        more than once — _win_apply_styles is idempotent."""
        if getattr(self, "_win_hwnd", None):
            return True  # already done in an earlier attempt
        hwnd = self._win_get_hwnd()
        if not hwnd:
            _win_log(f"hwnd_setup({source}): no HWND yet")
            print("[windows] Could not obtain HWND (this attempt) — "
                  "focus poll still active, will retry on map")
            return False
        self._win_hwnd = hwnd
        _win_log(f"hwnd_setup({source}): HWND={hwnd:#x}")
        print(f"[windows] HWND: {hwnd:#010x} (via {source})")
        try:
            self._win_apply_styles(hwnd)
        except Exception as exc:
            _win_log(f"hwnd_setup({source}): apply_styles error ({exc})")
        return True

    def _on_mapped_windows(self, _win):
        """Re-apply WS_EX_NOACTIVATE and WndProc subclass after ShowWindow.

        GDK can reset ExStyles / the WndProc pointer during ShowWindow processing
        (which happens between 'realize' and 'map'), silently overwriting
        WS_EX_NOACTIVATE.  We always re-apply here so the styles survive.
        Also serves as a second-chance HWND acquisition if realize was too early."""
        if not IS_WINDOWS:
            return
        if self._minimized:
            # Minimized: the OSK is hidden behind the proxy window; leave it.
            _win_log("map: skipped style re-apply (minimized)")
            return
        # Ensure HWND is obtained (no-op if already found at realize time).
        self._win_setup_hwnd_dependent("map")
        # Re-apply styles unconditionally: GDK may have reset them during ShowWindow.
        hwnd = getattr(self, "_win_hwnd", None)
        if hwnd:
            try:
                import ctypes
                self._win_apply_styles(hwnd)
                _win_log("map: styles re-applied after ShowWindow; "
                         f"exstyle={ctypes.windll.user32.GetWindowLongW(hwnd, -20):#x}")
            except Exception as exc:
                _win_log(f"map: re-apply styles error ({exc})")

    def _on_realized_windows(self, _win):
        """Windows only: prevent the OSK from stealing focus on click, and (the
        part that actually makes typing work) start the foreground-window poll
        that feeds the focus-independent PostMessage injection path.

        Mechanisms, in order of importance:
          4.   Foreground poll - runs UNCONDITIONALLY, even if
               the HWND can't be obtained.  _win_last_target[0] is the linchpin
               of PostMessage typing; if the poll never starts, every keystroke
               falls through to pynput and lands in the OSK itself.
          1.   WS_EX_NOACTIVATE — best-effort: don't activate
               on click.  Needs the HWND.
          2.   WM_MOUSEACTIVATE / WM_SETFOCUS subclass — best-effort focus-steal
               suppression.  Needs the HWND.

        NB: HWND acquisition is deliberately NOT in the same try/except as the
        poll/hook.  Previously a single AttributeError from get_handle() aborted
        everything, including the poll — which is why three rounds of "fixes"
        all manifested as "typing does nothing".
        """
        try:
            import ctypes

            user32 = ctypes.windll.user32

            # Fresh diagnostic log each session (EXE has console=False, so this
            # is the only way the user can see what the typing path is doing).
            if _WIN_DEBUG_LOG:
                try:
                    with open(_WIN_DEBUG_LOG, "w", encoding="utf-8") as f:
                        f.write(f"=== OSK {__version__} session start "
                                f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                except Exception:
                    pass
            _win_setup_protos()

            # ── 1+2+3. HWND-dependent setup (isolated; failure is non-fatal) ─
            self._win_setup_hwnd_dependent("realize")
            # Always connect to map: GDK can reset ExStyles/WndProc during
            # ShowWindow (between realize and map), so we unconditionally
            # re-apply at map time regardless of what realize found.
            self.connect("map", self._on_mapped_windows)

            # ── 4. Track last non-OSK foreground window (focus restoration) ─
            # Poll every 50 ms via GLib — more reliable than SetWinEventHook
            # which requires Win32 message pumping that GLib doesn't guarantee.
            # Filters by process ID so our own window never overwrites the target.
            our_pid = os.getpid()

            def _win_poll_foreground():
                try:
                    hwnd = user32.GetForegroundWindow()
                    if hwnd:
                        pid = ctypes.c_ulong()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        cls = ctypes.create_unicode_buffer(64)
                        user32.GetClassNameW(hwnd, cls, 64)
                        if pid.value != our_pid and cls.value not in _WIN_SHELL_CLASSES:
                            prev = _win_last_target[0]
                            _win_last_target[0] = hwnd
                            # Log only on change so the linchpin's health is
                            # visible in the debug log without flooding it.
                            if hwnd != prev:
                                _win_log(f"poll: target -> {int(hwnd):#x} "
                                         f"(pid={pid.value})")
                except Exception:
                    pass
                return True  # keep the timeout alive

            self._win_poll_timer = GLib.timeout_add(50, _win_poll_foreground)
            _win_log("poll: foreground-window polling started (50ms)")
            print("[windows] foreground-window polling started")

            # (A WH_CBT focus-veto hook used to be installed here.  It never
            # actually ran on Windows - GetCurrentThreadId was looked up on
            # user32 - and focus behaviour is good without it, so it was removed.)

            self.connect("destroy", self._on_windows_destroy)

        except Exception as exc:
            _win_log(f"realize: focus setup FAILED ({exc})")
            print(f"[windows] Focus setup failed: {exc}")

    def _on_windows_destroy(self, _widget):
        """Tear down Win32 resources before the window is destroyed."""
        try:
            timer = getattr(self, "_win_poll_timer", None)
            if timer:
                GLib.source_remove(timer)

            import ctypes
            user32 = ctypes.windll.user32

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
        with open(css_path, encoding="utf-8") as f:
            base.load_from_data(_scale_css_fonts(f.read()).encode())
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
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ROW_GAP)
        root.set_name("main-box")
        root.set_margin_start(SIDE_ZONE)   # left/right margins act as resize zones
        root.set_margin_end(SIDE_ZONE)
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
        keys_box = self._build_keys_box(self._layout_rows(layout))

        self._key_stack.add_named(keys_box,                   "keys")
        self._key_stack.add_named(self._build_settings_panel(), "settings")
        # Emoji panel is built lazily in _open_emoji_mode() to avoid
        # FlowBoxChild GdkWindows (created during show_all) blocking key clicks.
        root.pack_start(self._key_stack, True, True, 0)

        # Bottom resize strip — covers bottom edge + corners
        self._bottom_strip = self._make_resize_strip("bottom")
        root.pack_start(self._bottom_strip, False, False, 0)

    def _layout_rows(self, layout: str) -> list:
        """The key rows for a layout; Korean swaps the right Alt for 한/A."""
        rows = LAYOUT_ROWS.get(layout, KEY_ROWS)
        if self.predictor.language != "ko":
            return rows
        bottom = list(rows[-1])
        alts = [i for i, k in enumerate(bottom) if k[1] == "alt"]
        if alts:
            _lbl, _act, width, classes = bottom[alts[-1]]
            bottom[alts[-1]] = ("한/A", "hanyeong", width, classes)
        return rows[:-1] + [bottom]

    def _build_keys_box(self, rows) -> Gtk.Box:
        """Build a vertical box of key rows from a layout definition."""
        keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ROW_GAP)
        for row_def in rows:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=KEY_GAP)
            for label, action, width, extra_classes in row_def:
                btn = self._make_key(label, action, width, extra_classes)
                row_box.pack_start(btn, True, True, 0)
            keys_box.pack_start(row_box, True, True, 0)
        if self._korean_active():
            self._update_modifier_visuals()   # Hangul labels on the letter keys
        return keys_box

    def _make_resize_strip(self, position: str) -> Gtk.EventBox:
        """Thin strip at top or bottom — handles edge/corner resize manually."""
        strip = Gtk.EventBox()
        strip.set_name(f"resize-strip-{position}")
        strip.set_size_request(-1, STRIP_H)
        strip.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        strip.connect("button-press-event",   self._on_strip_press,   position)
        strip.connect("motion-notify-event",  self._on_resize_motion)
        strip.connect("button-release-event", self._on_resize_release)
        strip.connect("leave-notify-event",   self._on_resize_zone_leave)
        return strip

    def _event_window_xy(self, event) -> tuple[float, float, int, int]:
        """Pointer position in toplevel-window coordinates, plus window size.

        Derived from root coordinates so it is correct whichever widget the
        event arrived on.  (event.x is relative to event.window — for the
        resize strips that is the strip, which is inset by the SIDE_ZONE
        margins, so comparing it against the *window* width meant the
        right-hand ne/se corners could never trigger.)
        """
        gw = self.get_window()
        _, ox, oy = gw.get_origin()
        return (event.x_root - ox, event.y_root - oy,
                gw.get_width(), gw.get_height())

    def _strip_edge(self, position: str, event) -> str:
        """Edge for a point on the top/bottom strip: n/s, or a corner."""
        x, _y, w, _h = self._event_window_xy(event)
        v = "n" if position == "top" else "s"
        if x < EDGE_ZONE:
            return v + "w"
        if x >= w - EDGE_ZONE:
            return v + "e"
        return v

    def _side_edge(self, event) -> str:
        """Edge for a point in the left/right margins (toplevel window only):
        e/w, a corner near the top/bottom, or "" if outside the side zones."""
        x, y, w, h = self._event_window_xy(event)
        if x < SIDE_ZONE:
            hz = "w"
        elif x >= w - SIDE_ZONE:
            hz = "e"
        else:
            return ""
        if y < EDGE_ZONE:
            return "n" + hz
        if y >= h - EDGE_ZONE:
            return "s" + hz
        return hz

    def _on_strip_press(self, widget, event, position: str):
        if event.button != 1:
            return False
        self._start_resize(event, self._strip_edge(position, event))
        return True

    def _resize_cursor(self, edge: str):
        """Cached bidirectional resize cursor for an edge (None if unavailable)."""
        cache = self._resize_cursor_cache
        if edge not in cache:
            name, fallback = RESIZE_CURSORS[edge]
            display = self.get_display()
            cursor = None
            try:
                cursor = Gdk.Cursor.new_from_name(display, name)
            except Exception:
                cursor = None
            if cursor is None:
                try:
                    cursor = Gdk.Cursor.new_for_display(
                        display, getattr(Gdk.CursorType, fallback))
                except Exception:
                    cursor = None
            cache[edge] = cursor
        return cache[edge]

    def _set_zone_cursor(self, gdk_win, edge: str):
        """Show the resize cursor for `edge` on gdk_win ("" → default cursor)."""
        if gdk_win is None:
            return
        shown = self._zone_cursor_shown
        if shown.get(gdk_win, "") == edge:
            return
        shown[gdk_win] = edge
        gdk_win.set_cursor(self._resize_cursor(edge) if edge else None)

    def _update_resize_hover(self, widget, event):
        """Pointer moved (not resizing): show the matching resize cursor."""
        if widget is self._top_strip or widget is self._bottom_strip:
            position = "top" if widget is self._top_strip else "bottom"
            self._set_zone_cursor(widget.get_window(), self._strip_edge(position, event))
        elif widget is self:
            top = self.get_window()
            # Motion bubbled up from a child (title bar, keys…) is not over a
            # side margin — make sure the toplevel cursor isn't inherited there.
            edge = self._side_edge(event) if event.window == top else ""
            self._set_zone_cursor(top, edge)

    def _on_resize_zone_leave(self, widget, event):
        if not self._resizing:
            self._set_zone_cursor(widget.get_window(), "")
        return False

    def _reset_resize_cursors(self):
        for w in (self, self._top_strip, self._bottom_strip):
            if w is not None:
                self._set_zone_cursor(w.get_window(), "")

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
        grip.set_size_request(_px(22), _px(14))
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
        """Custom title bar (TITLEBAR_H px): draggable title label + window controls.

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

        # ── Window controls (Windows 10/11 caption buttons at the top-right) ──
        # pack_end packs right-to-left, so the FIRST pack_end ends up rightmost.
        # Close is packed first (far right); minimize second (to its left).
        icon_font = self._caption_icon_font()

        # Close button — far top-right corner
        self._close_btn = self._make_caption_button("close", "Close", icon_font)
        self._close_btn.connect("clicked", lambda _: self._quit())
        self._close_btn.get_accessible().set_name("Close keyboard")
        hbox.pack_end(self._close_btn, False, False, 0)

        # Minimize button (real minimize to taskbar) — left of the close button.
        self._minimize_btn = self._make_caption_button("minimize", "Minimize", icon_font)
        self._minimize_btn.connect("clicked", lambda _: self._minimize_window())
        self._minimize_btn.get_accessible().set_name("Minimize keyboard")
        hbox.pack_end(self._minimize_btn, False, False, 0)

        # Expand button — only visible while the key grid is collapsed
        # (double-click the title bar to collapse).
        self._expand_btn = self._make_caption_button("expand", "Expand keys", icon_font)
        self._expand_btn.set_no_show_all(True)
        self._expand_btn.connect("clicked", lambda _: self._expand())
        self._expand_btn.get_accessible().set_name("Expand keyboard keys")
        hbox.pack_end(self._expand_btn, False, False, 0)

        return bar

    def _caption_icon_font(self) -> str | None:
        """Return the installed Windows caption icon font, or None (→ Unicode)."""
        try:
            families = {f.get_name() for f in self.get_pango_context().list_families()}
        except Exception:
            return None
        for name in CAPTION_ICON_FONTS:
            if name in families:
                return name
        return None

    def _make_caption_button(self, kind: str, tooltip: str,
                             icon_font: str | None) -> Gtk.Button:
        """Flat Windows-style caption button (46 px x title-bar height).

        Uses the Segoe MDL2 / Fluent Icons glyph when that font is installed
        (Windows), otherwise a Unicode fallback (— ✕ ▲).  Styling lives in
        style.css (.caption-btn) and _make_theme_css (colours / hover).
        """
        mdl2, fallback = CAPTION_GLYPHS[kind]
        btn = Gtk.Button(label=mdl2 if icon_font else fallback)
        btn.set_name(f"caption-{kind}")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        ctx = btn.get_style_context()
        ctx.add_class("caption-btn")
        ctx.add_class("mdl2" if icon_font else "unicode")
        btn.set_size_request(CAPTION_W, TITLEBAR_H)
        btn.set_valign(Gtk.Align.FILL)
        btn.set_tooltip_text(tooltip)
        return btn

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
        self._clipboard_btn.get_accessible().set_name("Open clipboard history")
        bar.pack_end(self._clipboard_btn, False, False, 2)

        # Emoji toggle button
        self._emoji_btn = Gtk.Button(label="😊")
        self._emoji_btn.set_name("settings-btn")   # reuse settings-btn style
        self._emoji_btn.set_size_request(36, SUGGESTION_H)
        self._emoji_btn.connect("clicked", lambda _: self._toggle_emoji_mode())
        self._emoji_btn.get_accessible().set_name("Open emoji panel")
        bar.pack_end(self._emoji_btn, False, False, 2)

        # Settings (gear) button
        self._settings_btn = Gtk.Button(label="⚙")
        self._settings_btn.set_name("settings-btn")
        self._settings_btn.set_size_request(36, SUGGESTION_H)
        self._settings_btn.connect("clicked", lambda _: self._toggle_settings())
        self._settings_btn.get_accessible().set_name("Open settings panel")
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

        acc_name = ACCESSIBLE_KEY_NAMES.get(action)
        if acc_name:
            btn.get_accessible().set_name(acc_name)

        ctx = btn.get_style_context()
        ctx.add_class("key-btn")
        if not is_special:
            ctx.add_class("char-key")        # typed characters get the big label
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
            if self._accent_variants(action):
                ctx.add_class("has-accents")
        elif action == "hanyeong":
            self._ko_btn = btn
        if action == "shift":
            self._shift_btns.append(btn)
        elif action == "caps":
            self._caps_btn = btn
        elif action in ("ctrl", "alt", "win", "fn"):
            self._modifier_btns[action].append(btn)
        if action in FN_MAP and not shift_sym:
            self._fn_btns[action] = (btn, label)

        return btn

    # ── Edge/corner resize ─────────────────────────────��──────────────────────


    def _on_window_button_press(self, window, event):
        """Handle left/right edge resize from the SIDE_ZONE margins.

        Only presses delivered to the toplevel GdkWindow itself count: a press
        that bubbled up from a child (a key, the title bar…) carries coordinates
        relative to that child's window and must never start a resize.  The
        margins contain no child widgets, so a press there always arrives on
        the toplevel.  Within EDGE_ZONE of the top/bottom it is a corner.
        """
        if event.button != 1 or event.window != self.get_window():
            return False
        edge = self._side_edge(event)
        if not edge:
            return False
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
        # Clear the button minimums on the dragged axis so GTK won't resist
        # shrinking; _apply_key_scale restores them (fitted) on release.
        scale = self.settings.get("key_scale", 1.0)
        wscale = self._key_width_scale(scale, self._resize_start_w)
        vertical = "n" in edge or "s" in edge
        horizontal = "e" in edge or "w" in edge
        if vertical or horizontal:
            for btn, base_w in self._all_key_btns:
                btn.set_size_request(1 if horizontal else int(base_w * wscale),
                                     1 if vertical else int(BASE_KEY_H * scale))
        # The real minimum is whatever the keys need (labels, font size), which
        # is usually more than MIN_W.  Clamp to it, otherwise a west/north drag
        # keeps moving the window after GTK has refused to shrink it further,
        # and the whole keyboard slides across the screen.
        self._resize_min_w, self._resize_min_h = MIN_W, 0
        try:
            child = self.get_child()
            border = 2 * self.get_border_width()
            if child is not None:
                self._resize_min_w = max(MIN_W, child.get_preferred_width()[0] + border)
                self._resize_min_h = child.get_preferred_height()[0] + border
        except Exception:
            pass

    def _on_resize_motion(self, widget, event):
        if not self._resizing:
            self._update_resize_hover(widget, event)
            return False
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

        min_h = max(int(BASE_KEY_H * KB_ROWS * 0.3) + TITLEBAR_H + SUGGESTION_H + STRIP_H * 4 + 12,
                    getattr(self, "_resize_min_h", 0))
        min_w = getattr(self, "_resize_min_w", MIN_W)
        # Never let a measured minimum above the starting size shift the window
        min_w = min(min_w, start_w)
        min_h = min(min_h, start_h)

        # Start from the baselines.  Each edge touches ONLY the geometry
        # components it owns: horizontal edges (e/w) read dx and write width
        # (+ wx for w); vertical edges (n/s) read dy and write height (+ wy for
        # n).  dx never reaches height and dy never reaches width — so a pure
        # n/s/e/w drag stays single-axis and corners change exactly two.
        w, h   = start_w, start_h
        wx, wy = start_wx, start_wy

        # East/south edges: only size grows, the opposite edge/position anchored.
        if "e" in edge:
            w = max(min_w, start_w + dx)
        if "s" in edge:
            h = max(min_h, start_h + dy)

        # West/north edges: the moving edge's position shifts by the *clamped*
        # size delta so the opposite edge stays pinned.  Deriving the move from
        # (start_size - new_size) rather than from dx makes the clamp and the
        # move agree even when the size hits MIN_W / min_h.
        if "w" in edge:
            w  = max(min_w, start_w - dx)
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
            vertical = "n" in self._resize_edge or "s" in self._resize_edge
            if not vertical:
                # Side drag: height and key scale untouched, only refit widths
                self._apply_key_scale(scale, resize=False)
            elif self._resize_edge and not self._collapsed:
                # Derive scale from the new window height and snap buttons to fit
                _, new_h = self.get_size()
                overhead = TITLEBAR_H + SUGGESTION_H + STRIP_H * 4 + 12
                scale = round(
                    max(0.3, min(1.5, (new_h - overhead) / (BASE_KEY_H * KB_ROWS))), 2
                )
                self.settings["key_scale"] = scale
                self._save_settings()
                if hasattr(self, "_key_scale_label") and self._key_scale_label:
                    self._key_scale_label.set_label(f"{scale:.2f}×")
            # Always restore button sizes (covers the collapsed-drag path where
            # scale doesn't change but min-heights were stripped by _start_resize)
            if vertical:
                self._apply_key_scale(scale)
        elif self._resizing and ("n" in self._resize_edge or "s" in self._resize_edge):
            # No motion, but _start_resize stripped the button height minimums
            # to 1px for n/s edges.  Restore them at the current scale without
            # touching window geometry, otherwise a pure click on the top/bottom
            # strip leaves the keys squashed.
            self._apply_key_scale(self.settings.get("key_scale", 1.0), resize=False)
        elif self._resizing and ("e" in self._resize_edge or "w" in self._resize_edge):
            self._apply_key_scale(self.settings.get("key_scale", 1.0), resize=False)
        self._resizing        = False
        self._resize_did_move = False
        self._resize_edge     = ""
        self._reset_resize_cursors()   # next motion re-shows it if still in a zone

    # ── Drag to move ──────────────────────────────────────────────────────────

    def _save_geometry(self):
        """Persist current window position and size to settings."""
        if self._minimized:
            return  # iconic position/size are bogus; geometry was saved on minimize
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
        if self._volume_timer is not None:
            GLib.source_remove(self._volume_timer)
            self._volume_timer = None
        for btn, tid in list(self._flash_timers.items()):
            GLib.source_remove(tid)
            btn.get_style_context().remove_class("key-flash")
        self._flash_timers.clear()
        self._longpress_cancel()
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
        # Persist learned words (called on every exit path)
        if self._learn_save_timer is not None:
            GLib.source_remove(self._learn_save_timer)
            self._learn_save_timer = None
        if self._forget_timer is not None:
            GLib.source_remove(self._forget_timer)
            self._disarm_forget()   # also un-arm, so a later click can't erase
        self._user_model.save()

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

    def _on_key_press_event(self, widget, event, action: str):
        """Start the key-repeat timer when a button is held down (or, for a
        letter with accented forms, the hold-for-accents timer)."""
        if event.button != 1 or action in NON_REPEAT_ACTIONS:
            return False
        if self.fn_active and action in FN_MAP:
            return False          # an Fn-layer key (F5, PgDn...) fires once
        if self._accent_variants(action):
            self._longpress_cancel()
            self._longpress_timer = GLib.timeout_add(
                LONG_PRESS_MS, self._on_long_press, widget, action)
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
            self._longpress_cancel()
        return False

    # -- Korean (Hangul) ---------------------------------------------------------

    def _korean_active(self) -> bool:
        return self.predictor.language == "ko" and not self._ko_latin

    def _korean_key(self, action: str) -> bool:
        """Korean typing: letter keys build the syllable in the bar; finished
        syllables are typed.  Returns True if the key was handled here.
        current_word stays = (this word as typed in the app) + the syllable
        being built, so suggestions see the whole word."""
        mods = self._active_sticky_modifiers()
        pre = self._ko.preedit()
        typed = self.current_word[:len(self.current_word) - len(pre)]
        if not mods and len(action) == 1 and action.isalpha():
            done = self._ko.feed(jamo_for(action, self.shift_active))
            for ch in done:
                self.typer.type_char(ch)
            self.current_word = typed + done + self._ko.preedit()
            self._word_title = self._word_upper = False
            if self.shift_active and not self.settings.get("shift_sticky", False):
                self.shift_active = False
                self._update_modifier_visuals()
        elif action == "backspace" and pre and not mods:
            self._ko.backspace()                  # undo one jamo, not a syllable
            self.current_word = typed + self._ko.preedit()
        else:
            self._ko_flush()                      # any other key: finish it first
            return False
        self._show_preedit()
        self._refresh_suggestions()
        return True

    def _ko_flush(self):
        """Type the syllable being built (it's already part of current_word)."""
        for ch in self._ko.flush():
            self.typer.type_char(ch)
        self._show_preedit()

    def _show_preedit(self):
        lbl = self._search_label
        if lbl is None or self._app_mode or self._custom_input_mode:
            return
        pre = self._ko.preedit()
        if pre:
            lbl.set_markup(f"<span size='x-large' weight='bold'>"
                           f"{GLib.markup_escape_text(pre)}</span>▏")
            lbl.set_tooltip_text("Being typed: finishes when you type the next "
                                 "syllable, a space, or pick a suggestion")
            lbl.show()
        else:
            lbl.hide()

    # -- Hold a letter for its accents ----------------------------------------

    def _accent_variants(self, action: str) -> list[str]:
        if len(action) != 1 or not action.isalpha():
            return []
        return accents_for(self.predictor.language).get(action.lower(), [])

    def _longpress_cancel(self):
        if self._longpress_timer is not None:
            GLib.source_remove(self._longpress_timer)
            self._longpress_timer = None

    def _on_long_press(self, widget, action: str) -> bool:
        self._longpress_timer = None
        self._longpress_fired = action      # the release must not type the letter
        self._open_accent_popup(widget, action)
        return False

    def _open_accent_popup(self, anchor: Gtk.Widget, action: str):
        self._close_accent_popup()
        upper = self.shift_active ^ self.caps_lock
        pop = Gtk.Popover()
        pop.set_modal(False)                # never take focus from the typed-into app
        pop.get_style_context().add_class("tour-popover")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_margin_start(6); row.set_margin_end(6)
        row.set_margin_top(6); row.set_margin_bottom(6)
        for v in [action.lower()] + self._accent_variants(action):
            shown = v.upper() if upper and len(v.upper()) == 1 else v
            b = Gtk.Button(label=shown)
            b.get_style_context().add_class("key-btn")
            b.get_style_context().add_class("accent-choice")
            b.set_size_request(_px(48), _px(48))
            b.get_accessible().set_name(shown)
            b.connect("clicked", lambda _b, ch=v: self._on_accent_chosen(ch))
            row.pack_start(b, False, False, 0)
        row.show_all()
        pop.add(row)
        pop.set_relative_to(anchor)
        pop.set_position(Gtk.PositionType.TOP)
        self._accent_pop = pop
        pop.popup()

    def _on_accent_chosen(self, ch: str):
        self._close_accent_popup()
        self._play_click_sound()
        mods = self._active_sticky_modifiers()
        self._type_character(ch, mods)
        if mods:
            self.ctrl_active = self.alt_active = False
            self._update_modifier_visuals()

    def _close_accent_popup(self):
        pop, self._accent_pop = self._accent_pop, None
        if pop is not None:
            pop.hide()
            pop.destroy()

    def _refresh_accent_marks(self):
        """A small dot on letter keys that have accented forms (hold them)."""
        for letter, btn in self._letter_btns.items():
            ctx = btn.get_style_context()
            if self._accent_variants(letter):
                ctx.add_class("has-accents")
            else:
                ctx.remove_class("has-accents")

    def _repeat_stop(self, action: str):
        """Cancel any pending repeat timer for this action."""
        tid = self._repeat_timers.pop(action, None)
        if tid is not None:
            GLib.source_remove(tid)
        self._repeat_active.discard(action)

    def _on_key_clicked(self, btn, action: str):
        # When the repeat is active for this key, the GTK 'clicked' signal fires
        # on release — ignore it since repeat is already handling the key.
        if action in self._repeat_active:
            return
        if self._longpress_fired == action:  # held for accents: popup is up
            self._longpress_fired = None
            return
        self._longpress_fired = None
        self._flash_key(btn)
        self._handle_key(action)

    KEY_FLASH_MS = 160

    def _flash_key(self, btn):
        """Briefly show the key inverted so it's clear which one was pressed
        (a quick tap or a dwell click is too fast for the :active state)."""
        if btn is None:
            return
        ctx = btn.get_style_context()
        old = self._flash_timers.pop(btn, None)
        if old:
            GLib.source_remove(old)
        ctx.add_class("key-flash")

        def _off():
            self._flash_timers.pop(btn, None)
            ctx.remove_class("key-flash")
            return False
        self._flash_timers[btn] = GLib.timeout_add(self.KEY_FLASH_MS, _off)

    def _handle_key(self, action: str):
        self._close_accent_popup()
        self._play_click_sound()
        if action in ("ctrl", "alt"):
            self._toggle_modifier(action)
            return

        if action == "fn":
            self.fn_active = not self.fn_active
            self._update_modifier_visuals()
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

        if action == "hanyeong":
            self._ko_flush()
            self._ko_latin = not self._ko_latin
            self._update_modifier_visuals()
            self._refresh_suggestions()
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
        if self.fn_active:
            # One-shot, like Shift: 1 → F1, ← → Home ... then back to normal
            self.fn_active = False
            action = FN_MAP.get(action, action)
            self._update_modifier_visuals()
        if self._korean_active() and self._korean_key(action):
            return
        mods = self._active_sticky_modifiers()

        if action == "backspace":
            self.typer.send_special("backspace", mods)
            if not mods and self.current_word:
                self.current_word = self.current_word[:-1]
            else:
                # Deleting past the word start (or Ctrl+Backspace): the words
                # before the caret are no longer what we think they are.
                self.current_word = ""
                self._ctx_reset()
            self._refresh_suggestions()

        elif action == "return":
            self.typer.send_special("return", mods)
            # Enter ends the sentence: learn the final word, then start fresh
            self._ctx_commit(self.current_word)
            self._ctx_reset()
            self.current_word = ""
            self._refresh_suggestions()

        elif action == "space":
            self.typer.send_special("space", mods)
            if mods:
                self._ctx_reset()
            else:
                self._ctx_commit(self.current_word)
            self.current_word = ""
            self._refresh_suggestions()

        elif action == "tab":
            self.typer.send_special("tab", mods)
            self.current_word = ""
            self._ctx_reset()
            self._refresh_suggestions()

        elif action == "prtscn":
            self._launch_snipping_tool()

        elif action in ("left", "right", "up", "down", "escape", "delete",
                        "home", "end", "pgup", "pgdn",
                        "f1", "f2", "f3", "f4", "f5", "f6",
                        "f7", "f8", "f9", "f10", "f11", "f12"):
            self.typer.send_special(action, mods)
            self.current_word = ""
            self._ctx_reset()      # caret moved - sentence context is unknown
            self._refresh_suggestions()

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

    def _close_other_panels(self, keep: str):
        """Close every open panel/mode except `keep` using its own close routine,
        and cancel any pending fuzzy update (it must not repaint over a panel)."""
        if self._ko.preedit():
            self._ko_flush()                # don't lose a half-typed syllable
        self._ctx_reset()
        self.current_word = ""
        if keep != "settings" and self._settings_mode:
            self._toggle_settings()
        if keep != "emoji" and self._emoji_mode:
            self._close_emoji_mode()
        if keep != "clipboard" and self._clipboard_mode:
            self._close_clipboard_mode()
        if keep != "app" and self._app_mode:
            self._close_app_mode()
        if keep != "custom_input" and self._custom_input_mode:
            self._custom_input_return_to_settings = False
            self._close_custom_input_mode(back_to_settings=False)
        if self._fuzzy_timer is not None:
            GLib.source_remove(self._fuzzy_timer)
            self._fuzzy_timer = None

    def _open_custom_input_mode(self, target: str = "word"):
        """Switch to key grid and capture typing into the custom word buffer."""
        if self._settings_mode:
            self._custom_input_return_to_settings = True
        self._close_other_panels("custom_input")
        self._custom_input_mode_target = target
        self._custom_input_mode = True
        self._custom_input_text = ""
        if self._key_stack:
            self._key_stack.set_visible_child_name("keys")
        self._update_custom_input_display()

    def _close_custom_input_mode(self, back_to_settings: bool = True):
        target = self._custom_input_mode_target
        self._custom_input_mode = False
        for btn in self._suggestion_btns:
            btn.get_style_context().remove_class("ci-save")
        self._custom_input_text = ""
        self._custom_input_mode_target = "word"
        if target in ("macro_trigger", "macro_expansion"):
            self._macro_draft = {}
        if self._search_label:
            self._search_label.hide()
        self._refresh_suggestions()
        # Started from Settings (custom word / macro)? Go back there so the
        # user sees the result.  The theme wizard continues on its own page.
        return_to = getattr(self, "_custom_input_return_to_settings", False)
        self._custom_input_return_to_settings = False
        if back_to_settings and target != "theme_name" and return_to:
            if not self._settings_mode:
                self._toggle_settings()
            anchor = (getattr(self, "_custom_words_header", None) if target == "word"
                      else getattr(self, "_macros_header", None))
            if anchor is not None:
                GLib.idle_add(self._scroll_settings_to, anchor)

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
                self._custom_input_mode_target = "macro_expansion"
                self._custom_input_text = ""
                self._update_custom_input_display()
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
            if word:
                self._flash_custom_words_status(f"✓ Added “{word}”")

    # Slots in the suggestion bar used as buttons while typing a custom entry
    CI_SAVE_SLOT, CI_CANCEL_SLOT = 3, 4

    def _update_custom_input_display(self):
        target = self._custom_input_mode_target
        txt = self._custom_input_text
        title, save = {
            "theme_name":      ("Name your theme:", "Next ▶"),
            "macro_trigger":   ("Macro shortcut (what you'll type):", "Next ▶"),
            "macro_expansion": (f"Text that “{self._macro_draft.get('trigger', '')}” becomes:", "✓ Save"),
        }.get(target, ("New custom word:", "✓ Save"))
        if self._search_label:
            esc = GLib.markup_escape_text
            shown = (f"<span weight='bold'>{esc(txt)}</span>▏" if txt
                     else "<i>type it on the keys below</i>")
            self._search_label.set_markup(f"{esc(title)}  {shown}")
            self._search_label.show()
        for i, btn in enumerate(self._suggestion_btns):
            ctx = btn.get_style_context()
            ctx.remove_class("ci-save")
            if i == self.CI_SAVE_SLOT:
                btn.set_label(save)
                btn.set_sensitive(bool(txt.strip()))
                ctx.add_class("ci-save")
            elif i == self.CI_CANCEL_SLOT:
                btn.set_label("✕ Cancel")
                btn.set_sensitive(True)
            else:
                btn.set_label("")
                btn.set_sensitive(False)

    def _scroll_settings_to(self, widget) -> bool:
        """Scroll the settings page so `widget` is near the top."""
        sw = getattr(self, "_settings_scroll", None)
        child = sw.get_child() if sw else None
        if child is None:
            return False
        pos = widget.translate_coordinates(child, 0, 0)
        if pos:
            adj = sw.get_vadjustment()
            adj.set_value(min(max(0, pos[1] - 12), adj.get_upper() - adj.get_page_size()))
        return False

    def _flash_custom_words_status(self, text: str):
        """Briefly show a confirmation beside the Custom words heading."""
        lbl = self._custom_words_count
        if lbl is None:
            return
        lbl.set_text(text)

        def _restore():
            n = len(self._custom_words)
            lbl.set_text(f"{n} word{'s' if n != 1 else ''}")
            return False
        GLib.timeout_add_seconds(4, _restore)

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
            if not isinstance(row, Gtk.ListBoxRow):
                continue   # keep the "empty list" placeholder
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
        box.set_margin_start(10)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        lbl = Gtk.Label(label=word)
        lbl.set_name("settings-label")
        lbl.set_xalign(0.0)
        lbl.set_hexpand(True)
        box.pack_start(lbl, True, True, 0)

        remove_btn = Gtk.Button(label="×")
        remove_btn.get_style_context().add_class("settings-choice")
        remove_btn.get_style_context().add_class("list-remove")
        remove_btn.set_size_request(_px(36), -1)
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.set_tooltip_text("Remove")
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
            if not isinstance(row, Gtk.ListBoxRow):
                continue   # keep the "empty list" placeholder
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
        box.set_margin_start(10)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        trigger = macro.get("trigger", "")
        expansion = macro.get("expansion", "")
        lbl = Gtk.Label(label=f"{trigger} → {expansion}")
        lbl.set_name("settings-label")
        lbl.set_xalign(0.0)
        lbl.set_hexpand(True)
        box.pack_start(lbl, True, True, 0)

        remove_btn = Gtk.Button(label="×")
        remove_btn.get_style_context().add_class("settings-choice")
        remove_btn.get_style_context().add_class("list-remove")
        remove_btn.set_size_request(_px(36), -1)
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.set_tooltip_text("Remove")
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
        self._close_other_panels("clipboard")
        self._clipboard_mode = True

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
        self._clipboard_listbox.set_placeholder(self._empty_list_label(
            "Nothing copied yet — text you copy appears here"))
        self._rebuild_clipboard_list()

        scroll.add(self._clipboard_listbox)
        panel.pack_start(scroll, True, True, 0)
        return panel

    @staticmethod
    def _empty_list_label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_name("settings-label")
        lbl.set_margin_top(8)
        lbl.set_margin_bottom(8)
        lbl.show()
        return lbl

    def _rebuild_clipboard_list(self):
        lb = self._clipboard_listbox
        if lb is None:
            return
        for row in lb.get_children():
            if not isinstance(row, Gtk.ListBoxRow):
                continue   # keep the "empty list" placeholder
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

    def _key_width_scale(self, scale: float, win_w: int) -> float:
        """Width scale for the key minimums: `scale`, or less if the window is
        too narrow for the full layout at that scale.  Heights keep `scale`, so
        dragging a side edge narrows the keys instead of hitting a wall."""
        rows = {}
        for btn, base_w in self._all_key_btns:
            rows.setdefault(btn.get_parent(), []).append(base_w)
        fit = scale
        for parent, widths in rows.items():
            spacing = parent.get_spacing() * (len(widths) - 1) if parent else 0
            overhead = KEY_ROW_H_OVERHEAD
            if parent is not None and parent.get_allocated_width() > 1:
                overhead = max(0, self.get_allocated_width() - parent.get_allocated_width())
            total = sum(widths)
            if total > 0:
                fit = min(fit, (win_w - overhead - spacing) / total)
        return max(0.1, fit)

    def _apply_key_scale(self, scale: float, reposition: bool = False, resize: bool = True):
        h = int(BASE_KEY_H * scale)
        w, _ = self.get_size()
        wscale = self._key_width_scale(scale, w)
        for btn, base_w in self._all_key_btns:
            btn.set_size_request(int(base_w * wscale), h)
        if not resize:
            return
        keys_h = int(BASE_KEY_H * KB_ROWS * scale)
        total_h = keys_h + TITLEBAR_H + SUGGESTION_H + LAYOUT_OVERHEAD
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
        self._ko_btn        = None
        self._shift_btns    = []
        self._caps_btn      = None
        self._modifier_btns = {"ctrl": [], "alt": [], "win": [], "fn": []}
        self._fn_btns       = {}
        self._symbol_btns   = {}
        self._all_key_btns  = []

        if self._key_stack:
            old_child = self._key_stack.get_child_by_name("keys")
            if old_child:
                self._key_stack.remove(old_child)

        keys_box = self._build_keys_box(self._layout_rows(layout))
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

    def _on_language_clicked(self, code: str):
        if code == self.settings.get("language"):
            return
        was_korean = self.predictor.language == "ko"
        if self._ko.preedit():
            self._ko_flush()
        self.settings["language"] = code
        self._save_settings()
        self.predictor.set_language(code)
        self._ko_latin = False
        if code == "ko" or was_korean:
            # Korean keys are QWERTY positions + the 한/A key: rebuild the keys
            layout = LANGUAGE_LAYOUT.get(code, self.settings.get("layout", "qwerty"))
            self._switch_layout(layout if code == "ko" else self.settings.get("layout", "qwerty"))
            self._update_modifier_visuals()
        # Learned words belong to a language: save these, load that one's
        self._user_model.save()
        self._user_model = UserModel(learned_words_file(code), self.predictor.is_known)
        self._apply_learn_setting()
        self.current_word = ""
        self._ctx_reset()
        self._close_accent_popup()
        self._refresh_accent_marks()
        self._refresh_language_buttons()
        self._refresh_suggestions()

    def _refresh_language_buttons(self):
        current = self.settings.get("language", "en_US")
        for code, btn in getattr(self, "_language_btns", {}).items():
            ctx = btn.get_style_context()
            if code == current:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    def _refresh_font_family_buttons(self):
        current = self.settings.get("font_family", DEFAULT_FONT)
        for family, btn in self._font_family_btns.items():
            ctx = btn.get_style_context()
            if family == current:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    # ── DIY theme wizard ──────────────────────────────────────────────────────

    def _edit_custom_theme(self, name: str):
        """Reopen the wizard on a saved theme, starting from its colours."""
        saved = self._custom_themes.get(name, {})
        self._wizard_editing = True
        self._wizard_name    = name
        self._wizard_step    = 1
        self._wizard_colors  = {k: saved.get(k, v) for k, v in WIZARD_DEFAULTS.items()}
        self._close_other_panels("theme_wizard")
        self._open_wizard_color_step()

    def _open_theme_wizard(self):
        """Start the theme wizard: collect name via keyboard, then 6 colour steps."""
        self._wizard_editing = False
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
        q_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._wizard_question_label = Gtk.Label(label="")
        self._wizard_question_label.set_name("settings-label")
        self._wizard_question_label.set_xalign(0.0)
        q_row.pack_start(self._wizard_question_label, False, False, 0)
        self._wizard_info_btn = Gtk.Button(label="i")
        self._wizard_info_btn.get_style_context().add_class("info-btn")
        self._wizard_info_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._wizard_info_btn.set_valign(Gtk.Align.CENTER)
        self._wizard_info_btn.connect("clicked", self._on_wizard_info)
        q_row.pack_start(self._wizard_info_btn, False, False, 0)
        page.pack_start(q_row, False, False, 0)

        # Progress swatches: one per wizard step, filled in as the user goes
        # (in the top row, beside the step counter).
        swatch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        swatch_box.set_valign(Gtk.Align.CENTER)
        self._wizard_preview_swatches = []
        for _i in range(len(WIZARD_STEPS)):
            eb = Gtk.EventBox()
            eb.set_size_request(_px(22), _px(22))
            swatch_box.pack_start(eb, False, False, 0)
            self._wizard_preview_swatches.append(eb)
        top_row.pack_end(swatch_box, False, False, 12)

        # ── Colour picker: built into the page ─────────────────────────────
        # (A GtkColorButton opens a separate dialog window, which ends up
        # BEHIND the always-on-top keyboard; an inline palette can't.)
        picker_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # A (do-nothing) button rather than an EventBox so it gets the swatch
        # border: when picking the background colour it matches the page.
        self._wizard_current = Gtk.Button()
        self._wizard_current.get_style_context().add_class("palette-swatch")
        self._wizard_current.get_style_context().add_class("current")
        self._wizard_current.set_can_focus(False)
        self._wizard_current.get_accessible().set_name("Chosen colour")
        self._wizard_current.set_size_request(_px(72), _px(72))
        self._wizard_current.set_valign(Gtk.Align.CENTER)
        picker_row.pack_start(self._wizard_current, False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(6)
        grid.set_valign(Gtk.Align.CENTER)
        self._wizard_palette_btns = {}
        for i, hex_c in enumerate(WIZARD_PALETTE):
            b = Gtk.Button()
            b.get_style_context().add_class("palette-swatch")
            b.set_size_request(_px(32), _px(32))
            b.set_tooltip_text(hex_c)
            b.get_accessible().set_name(f"Colour {hex_c}")
            self._set_swatch_color(b, hex_c)
            b.connect("clicked", lambda _b, h=hex_c: self._wizard_pick(h))
            grid.attach(b, i % 12, i // 12, 1, 1)
            self._wizard_palette_btns[hex_c] = b
        picker_row.pack_start(grid, False, False, 0)

        tune = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tune.set_valign(Gtk.Align.CENTER)
        for text, toward in (("☀ Lighter", "#ffffff"), ("☾ Darker", "#000000")):
            b = Gtk.Button(label=text)
            b.get_style_context().add_class("settings-choice")
            b.set_size_request(_px(100), -1)
            b.connect("clicked", lambda _b, t=toward: self._wizard_nudge(t))
            tune.pack_start(b, False, False, 0)
        picker_row.pack_start(tune, False, False, 0)
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
        back_btn.set_size_request(_px(100), -1)
        back_btn.connect("clicked", lambda _: self._wizard_go_back())
        nav_row.pack_start(back_btn, False, False, 0)

        spacer = Gtk.Label(label="")
        nav_row.pack_start(spacer, True, True, 0)

        self._wizard_next_btn = Gtk.Button(label="Next ▶")
        self._wizard_next_btn.get_style_context().add_class("settings-choice")
        self._wizard_next_btn.set_size_request(_px(100), -1)
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
        info = getattr(self, "_wizard_info_btn", None)
        if info is not None:
            info.set_tooltip_text(WIZARD_HELP.get(color_key, ""))
            info.get_accessible().set_name(f"About {question}")
        self._wizard_close_help()   # don't leave the previous step's explanation up

        self._wizard_show_current(self._wizard_colors.get(color_key, "#888888"))

        if self._wizard_next_btn:
            self._wizard_next_btn.set_label(
                "Save ✓" if step == total else "Next ▶")

        if hasattr(self, "_wizard_name_display") and self._wizard_name_display:
            verb = "Editing" if getattr(self, "_wizard_editing", False) else "Theme"
            self._wizard_name_display.set_text(f"{verb}: {self._wizard_name}")

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

    def _set_swatch_color(self, widget: Gtk.Widget, hex_c: str):
        """Give a widget a solid background colour (one CSS provider per
        widget, reused on every change)."""
        provider = getattr(widget, "_osk_swatch_provider", None)
        if provider is None:
            provider = Gtk.CssProvider()
            widget._osk_swatch_provider = provider
            widget.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10)
        provider.load_from_data(
            f"* {{ background-color: {hex_c}; background-image: none; "
            f"border-radius: 6px; }}".encode())

    def _wizard_close_help(self):
        pop = getattr(self, "_help_pop", None)
        if pop is not None and pop.get_visible():
            pop.hide()        # immediate; popdown() can leave it up mid-animation

    def _on_wizard_info(self, btn: Gtk.Button):
        question, color_key = WIZARD_STEPS[self._wizard_step - 1]
        self._toggle_setting_help(btn, question.split("  (")[0], WIZARD_HELP.get(color_key, ""))

    def _wizard_show_current(self, hex_c: str):
        """Big preview swatch + highlight the matching palette colour."""
        if getattr(self, "_wizard_current", None) is not None:
            self._set_swatch_color(self._wizard_current, hex_c)
        for h, b in getattr(self, "_wizard_palette_btns", {}).items():
            ctx = b.get_style_context()
            if h.lower() == hex_c.lower():
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _wizard_nudge(self, toward: str):
        """Lighter / Darker: move the current colour 15% toward white/black."""
        _, color_key = WIZARD_STEPS[self._wizard_step - 1]
        self._wizard_pick(_mix(self._wizard_colors.get(color_key, "#888888"), toward, 0.15))

    def _wizard_pick(self, hex_c: str):
        """A colour was chosen for the current step: store it and preview live."""
        _, color_key = WIZARD_STEPS[self._wizard_step - 1]
        self._wizard_colors[color_key] = hex_c
        self._wizard_show_current(hex_c)
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
        font_size   = self.settings.get("font_size", DEFAULT_FONT_SIZE)
        font_family = self.settings.get("font_family", DEFAULT_FONT)
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
        self._wizard_close_help()
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
        self._wizard_close_help()
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

            edit_btn = Gtk.Button(label="✎")
            edit_btn.get_style_context().add_class("settings-choice")
            edit_btn.set_size_request(_px(28), -1)
            edit_btn.set_tooltip_text(f"Edit the colours of {name}")
            edit_btn.get_accessible().set_name(f"Edit theme {name}")
            edit_btn.connect("clicked", lambda _b, n=name: self._edit_custom_theme(n))
            wrap.pack_start(edit_btn, False, False, 0)

            del_btn = Gtk.Button(label="×")
            del_btn.get_style_context().add_class("settings-choice")
            del_btn.set_size_request(_px(28), -1)
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

        Collapsing during the first-run tour ends it (the bubble can't fit, and
        step 4 is what teaches the double-click in the first place).

        The title bar (with its ─/✕ controls) and the suggestion bar both stay
        visible — the title bar is the only way to expand again, and keeping the
        suggestion bar lets word prediction / panel toggles remain reachable in
        the collapsed state.
        """
        if self._collapsed:
            return
        if getattr(self, "_tour_step", -1) >= 0:
            self._end_tour()
        self._reset_resize_cursors()
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

        if self._expand_btn:
            self._expand_btn.show()             # ▲ Expand affordance

        # Cancel timers tied to now-hidden keys so they can't fire on hidden widgets
        self._cancel_all_timers()
        self.resize(w, self._collapsed_h)

    def _expand(self):
        """Restore the full keyboard to its pre-collapse height."""
        if not self._collapsed:
            return
        self._collapsed = False
        self._reset_resize_cursors()
        w, _ = self.get_size()

        if self._key_stack:
            self._key_stack.show()
        if self._bottom_strip:
            self._bottom_strip.show()

        if self._expand_btn:
            self._expand_btn.hide()

        target_h = self._pre_collapse_h or (
            int(BASE_KEY_H * KB_ROWS * self.settings.get("key_scale", 1.0))
            + TITLEBAR_H + SUGGESTION_H + LAYOUT_OVERHEAD
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
                            cmd, close_fds=False, env=_child_env(),
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
            # Existing users (settings file from before the tour) skip the
            # first-run tutorial; only fresh installs see it automatically.
            if isinstance(data, dict) and "tutorial_done" not in data:
                data["tutorial_done"] = True
            # Older versions saved "Ubuntu" (absent on Windows) and 14px:
            # move everyone onto the clear defaults once.
            if isinstance(data, dict) and data.get("font_version", 0) < FONT_SETTINGS_VERSION:
                data["font_family"] = DEFAULT_FONT
                try:
                    data["font_size"] = max(int(data.get("font_size", 0)), DEFAULT_FONT_SIZE)
                except (TypeError, ValueError):
                    data["font_size"] = DEFAULT_FONT_SIZE
                data["font_version"] = FONT_SETTINGS_VERSION
            # Fill in any missing keys with defaults
            merged = {**DEFAULT_SETTINGS, **data}
            if merged.get("click_style") not in ("click", "beep"):
                merged["click_style"] = "click"
            try:
                merged["sound_volume"] = min(1.0, max(0.05, float(merged.get("sound_volume", 0.7))))
            except (TypeError, ValueError):
                merged["sound_volume"] = DEFAULT_SETTINGS["sound_volume"]
            if merged.get("language") not in DICTIONARIES:
                merged["language"] = DEFAULT_SETTINGS["language"]
            for key in ("learn_words", "tutorial_done", "click_sound", "dwell_enabled",
                        "space_after_suggestion"):
                if not isinstance(merged.get(key), bool):
                    merged[key] = DEFAULT_SETTINGS[key]
            return merged
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
        if getattr(self, "_help_pop", None) is not None:
            self._help_pop.popdown()
        if self._settings_mode:
            self._close_other_panels("settings")
            self._key_stack.set_visible_child_name("settings")
            if self._settings_btn:
                self._settings_btn.get_style_context().add_class("active")
        else:
            self._key_stack.set_visible_child_name("keys")
            if self._settings_btn:
                self._settings_btn.get_style_context().remove_class("active")

    def _settings_section_header(self, text: str, is_first: bool = False) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(0 if is_first else 14)
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0.0)
        lbl.get_style_context().add_class("settings-section")
        box.pack_start(lbl, False, False, 0)
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("settings-divider")
        box.pack_start(sep, False, False, 0)
        return box

    def _settings_row_label(self, text: str) -> Gtk.Box:
        """Setting name for the label column, with an (i) button that explains
        the setting when SETTINGS_HELP has an entry for it."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_size_request(SETTINGS_LABEL_W, -1)
        lbl = Gtk.Label(label=text)
        lbl.set_name("settings-label")
        lbl.set_xalign(0.0)
        box.pack_start(lbl, False, False, 0)
        help_text = SETTINGS_HELP.get(text)
        if help_text:
            info = Gtk.Button(label="i")
            info.get_style_context().add_class("info-btn")
            info.set_relief(Gtk.ReliefStyle.NONE)
            info.set_valign(Gtk.Align.CENTER)
            info.set_tooltip_text(help_text)
            info.get_accessible().set_name(f"About {text}")
            info.connect("clicked", lambda b, t=text, h=help_text: self._toggle_setting_help(b, t, h))
            box.pack_start(info, False, False, 0)
        return box

    def _toggle_setting_help(self, anchor: Gtk.Widget, title: str, text: str):
        """Show (or hide, on a second tap) the explanation bubble for a setting."""
        pop = getattr(self, "_help_pop", None)
        if pop is None:
            pop = Gtk.Popover()
            pop.set_modal(False)          # never grab focus from the typed-into app
            pop.get_style_context().add_class("tour-popover")
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            body.set_margin_start(12); body.set_margin_end(12)
            body.set_margin_top(10); body.set_margin_bottom(10)
            self._help_title = Gtk.Label(xalign=0.0)
            self._help_title.get_style_context().add_class("tour-title")
            self._help_body = Gtk.Label(xalign=0.0)
            self._help_body.set_line_wrap(True)
            self._help_body.set_max_width_chars(46)
            self._help_body.get_style_context().add_class("tour-body")
            ok = Gtk.Button(label="Got it")
            ok.get_style_context().add_class("tour-btn")
            ok.get_style_context().add_class("tour-next")
            ok.set_halign(Gtk.Align.END)
            ok.connect("clicked", lambda _b: self._help_pop.popdown())
            for w in (self._help_title, self._help_body, ok):
                body.pack_start(w, False, False, 0)
            body.show_all()
            pop.add(body)
            self._help_pop = pop
        if pop.get_visible() and pop.get_relative_to() is anchor:
            pop.popdown()
            return
        self._help_title.set_label(title)
        self._help_body.set_label(text)
        pop.set_relative_to(anchor)
        pop.set_position(Gtk.PositionType.RIGHT)
        pop.popup()

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
        scroll_outer.set_overlay_scrolling(False)   # a real gutter, never on top of rows
        scroll_outer.set_name("settings-scroll")
        self._settings_scroll = scroll_outer

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_name("settings-inner")   # CSS padding: gap before the scrollbar

        inner.pack_start(self._settings_section_header("Appearance", is_first=True), False, False, 0)

        # ── 1. Theme row ───────────────────────────────────────────────────
        theme_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        theme_row.pack_start(self._settings_row_label("Theme"), False, False, 0)

        for key in THEMES:
            btn = Gtk.Button(label=THEME_LABELS[key])
            btn.get_style_context().add_class("settings-choice")
            btn.connect("clicked", self._on_theme_clicked, key)
            theme_row.pack_start(btn, True, True, 0)
            self._theme_btns[key] = btn

        inner.pack_start(theme_row, False, False, 0)

        # ── 2. Layout row ──────────────────────────────────────────────────
        layout_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        layout_row.pack_start(self._settings_row_label("Layout"), False, False, 0)

        for key in ("qwerty", "azerty", "qwertz"):
            btn = Gtk.Button(label=key.upper())
            btn.get_style_context().add_class("settings-choice")
            btn.connect("clicked", lambda _b, k=key: self._switch_layout(k))
            layout_row.pack_start(btn, True, True, 0)
            self._layout_btns[key] = btn

        inner.pack_start(layout_row, False, False, 0)

        # ── 3. Font family row ─────────────────────────────────────────────
        font_fam_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_fam_row.pack_start(self._settings_row_label("Font"), False, False, 0)

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
        font_row.pack_start(self._settings_row_label("Font size"), False, False, 0)

        font_dec = Gtk.Button(label="−")
        font_dec.get_style_context().add_class("settings-choice")
        font_dec.set_size_request(_px(36), -1)
        font_dec.connect("clicked", self._on_font_size_change, -1)
        font_row.pack_start(font_dec, False, False, 0)

        cur_size = self.settings.get("font_size", DEFAULT_FONT_SIZE)
        self._font_size_label = Gtk.Label(label=f"{cur_size}px")
        self._font_size_label.set_name("settings-label")
        self._font_size_label.set_size_request(_px(42), -1)
        font_row.pack_start(self._font_size_label, False, False, 0)

        font_inc = Gtk.Button(label="+")
        font_inc.get_style_context().add_class("settings-choice")
        font_inc.set_size_request(_px(36), -1)
        font_inc.connect("clicked", self._on_font_size_change, +1)
        font_row.pack_start(font_inc, False, False, 0)

        inner.pack_start(font_row, False, False, 0)

        # ── 5. Key size row ────────────────────────────────────────────────
        key_scale_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        key_scale_row.pack_start(self._settings_row_label("Key size"), False, False, 0)

        ks_dec = Gtk.Button(label="−")
        ks_dec.get_style_context().add_class("settings-choice")
        ks_dec.set_size_request(_px(36), -1)
        ks_dec.connect("clicked", self._on_key_scale_change, -0.05)
        key_scale_row.pack_start(ks_dec, False, False, 0)

        cur_scale = self.settings.get("key_scale", 1.0)
        self._key_scale_label = Gtk.Label(label=f"{cur_scale:.2f}×")
        self._key_scale_label.set_name("settings-label")
        self._key_scale_label.set_size_request(_px(46), -1)
        key_scale_row.pack_start(self._key_scale_label, False, False, 0)

        ks_inc = Gtk.Button(label="+")
        ks_inc.get_style_context().add_class("settings-choice")
        ks_inc.set_size_request(_px(36), -1)
        ks_inc.connect("clicked", self._on_key_scale_change, +0.05)
        key_scale_row.pack_start(ks_inc, False, False, 0)

        inner.pack_start(key_scale_row, False, False, 0)

        # ── 6. Opacity row ─────────────────────────────────────────────────
        opacity_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        opacity_row.pack_start(self._settings_row_label("Opacity"), False, False, 0)

        cur_opacity = self.settings.get("opacity", 1.0)
        opacity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                  0.3, 1.0, 0.05)
        opacity_scale.set_value(cur_opacity)
        opacity_scale.set_draw_value(False)
        opacity_scale.set_hexpand(True)
        opacity_scale.set_size_request(_px(120), -1)

        self._opacity_label = Gtk.Label(label=f"{int(cur_opacity * 100)}%")
        self._opacity_label.set_name("settings-label")
        self._opacity_label.set_size_request(_px(40), -1)

        opacity_scale.connect("value-changed", self._on_opacity_changed)
        opacity_row.pack_start(opacity_scale, True, True, 0)
        opacity_row.pack_start(self._opacity_label, False, False, 0)

        inner.pack_start(opacity_row, False, False, 0)

        inner.pack_start(self._settings_section_header("Input"), False, False, 0)

        # ── Language (word suggestions); English variants on the first line ─
        self._language_btns = {}
        for i, codes in enumerate((ENGLISH_CODES, EUROPEAN_CODES, ASIAN_CODES)):
            lang_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if i == 0:
                lang_row.pack_start(self._settings_row_label("Language"), False, False, 0)
            else:
                pad = Gtk.Box()
                pad.set_size_request(SETTINGS_LABEL_W, -1)
                lang_row.pack_start(pad, False, False, 0)
            for code in codes:
                btn = Gtk.Button(label=LANGUAGE_LABELS[code])
                btn.get_style_context().add_class("settings-choice")
                btn.connect("clicked", lambda _b, c=code: self._on_language_clicked(c))
                lang_row.pack_start(btn, True, True, 0)
                self._language_btns[code] = btn
            inner.pack_start(lang_row, False, False, 0)
        self._refresh_language_buttons()

        # ── Space after a suggestion ───────────────────────────────────────
        space_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        space_row.pack_start(self._settings_row_label("Auto space"), False, False, 0)
        space_on = bool(self.settings.get("space_after_suggestion", True))
        space_toggle = Gtk.ToggleButton(label="On" if space_on else "Off")
        space_toggle.set_name("settings-toggle")
        space_toggle.set_size_request(_px(100), -1)
        space_toggle.set_active(space_on)
        space_toggle.connect("toggled", self._on_space_after_toggled)
        space_toggle.get_accessible().set_description(
            "Type a space after a suggested word is picked")
        space_row.pack_start(space_toggle, False, False, 0)
        space_hint = Gtk.Label(label="adds a space after a picked suggestion")
        space_hint.set_name("settings-hint")
        space_hint.set_xalign(0.0)
        space_row.pack_start(space_hint, False, False, 4)
        inner.pack_start(space_row, False, False, 0)

        # ── 7. Dwell row ───────────────────────────────────────────────────
        dwell_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dwell_row.pack_start(self._settings_row_label("Dwell click"), False, False, 0)

        dwell_toggle = Gtk.ToggleButton(label="Off")
        dwell_toggle.set_name("settings-toggle")
        dwell_toggle.set_size_request(_px(60), -1)
        dwell_toggle.set_active(self.settings["dwell_enabled"])
        dwell_toggle.set_label("On" if self.settings["dwell_enabled"] else "Off")
        dwell_toggle.connect("toggled", self._on_dwell_toggled, dwell_toggle)
        dwell_toggle.get_accessible().set_description("Hover over a key to activate it without clicking")
        dwell_row.pack_start(dwell_toggle, False, False, 0)

        lbl3 = Gtk.Label(label="Delay:")
        lbl3.set_name("settings-label")
        dwell_row.pack_start(lbl3, False, False, 6)

        dec_btn = Gtk.Button(label="−")
        dec_btn.get_style_context().add_class("settings-choice")
        dec_btn.set_size_request(_px(44), -1)
        dec_btn.connect("clicked", self._on_dwell_delay, -0.1)
        dec_btn.get_accessible().set_name("Decrease dwell delay")
        dwell_row.pack_start(dec_btn, False, False, 0)

        self._dwell_label = Gtk.Label(
            label=f"{self.settings['dwell_delay']:.1f}s")
        self._dwell_label.set_name("settings-label")
        self._dwell_label.set_size_request(_px(42), -1)
        dwell_row.pack_start(self._dwell_label, False, False, 0)

        inc_btn = Gtk.Button(label="+")
        inc_btn.get_style_context().add_class("settings-choice")
        inc_btn.set_size_request(_px(44), -1)
        inc_btn.connect("clicked", self._on_dwell_delay, +0.1)
        inc_btn.get_accessible().set_name("Increase dwell delay")
        dwell_row.pack_start(inc_btn, False, False, 0)

        inner.pack_start(dwell_row, False, False, 0)

        # ── 8. Sound row ───────────────────────────────────────────────────
        sound_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sound_row.pack_start(self._settings_row_label("Key sound"), False, False, 0)

        self._sound_btns: dict[str, Gtk.Button] = {}
        for key, desc in (("off",   "No sound on key press"),
                          ("click", "Soft click on each key press"),
                          ("beep",  "Beep tone on each key press")):
            btn = Gtk.Button(label=key.capitalize())
            btn.get_style_context().add_class("settings-choice")
            btn.connect("clicked", lambda _b, k=key: self._set_key_sound(k))
            btn.get_accessible().set_description(desc)
            sound_row.pack_start(btn, True, True, 0)
            self._sound_btns[key] = btn
        self._refresh_sound_buttons()

        inner.pack_start(sound_row, False, False, 0)

        # ── Key sound volume ───────────────────────────────────────────────
        vol_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vol_row.pack_start(self._settings_row_label("Sound volume"), False, False, 0)
        cur_vol = float(self.settings.get("sound_volume", 0.7))
        vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.05, 1.0, 0.05)
        vol_scale.set_value(cur_vol)
        vol_scale.set_draw_value(False)
        vol_scale.set_hexpand(True)
        vol_scale.set_size_request(_px(120), -1)
        vol_scale.get_accessible().set_name("Key sound volume")
        self._volume_label = Gtk.Label(label=f"{int(round(cur_vol * 100))}%")
        self._volume_label.set_name("settings-label")
        self._volume_label.set_size_request(_px(40), -1)
        vol_scale.connect("value-changed", self._on_volume_changed)
        vol_row.pack_start(vol_scale, True, True, 0)
        vol_row.pack_start(self._volume_label, False, False, 0)
        inner.pack_start(vol_row, False, False, 0)

        # ── 9. Modifier auto-release row ───────────────────────────────────
        mod_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mod_row.pack_start(self._settings_row_label("Modifier keys"), False, False, 0)

        auto_release = self.settings.get("modifier_auto_release", True)
        mod_toggle = Gtk.ToggleButton(label="Auto-release" if auto_release else "Sticky")
        mod_toggle.set_name("settings-toggle")
        mod_toggle.set_size_request(_px(100), -1)
        mod_toggle.set_active(auto_release)
        mod_toggle.connect("toggled", self._on_mod_release_toggled, mod_toggle)
        mod_toggle.get_accessible().set_description("Ctrl and Alt release automatically after the next keypress")
        mod_row.pack_start(mod_toggle, False, False, 0)

        mod_hint = Gtk.Label(
            label="releases after keypress" if auto_release else "stays until clicked again")
        mod_hint.set_name("settings-hint")
        mod_hint.set_xalign(0.0)
        self._mod_hint_label = mod_hint
        mod_row.pack_start(mod_hint, False, False, 4)

        inner.pack_start(mod_row, False, False, 0)

        # ── 9b. Shift behaviour row ────────────────────────────────────────
        shift_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        shift_row.pack_start(self._settings_row_label("Shift key"), False, False, 0)

        shift_sticky = self.settings.get("shift_sticky", False)
        self._shift_toggle = Gtk.ToggleButton(
            label="Sticky" if shift_sticky else "One-shot")
        self._shift_toggle.set_name("settings-toggle")
        self._shift_toggle.set_size_request(_px(100), -1)
        self._shift_toggle.set_active(shift_sticky)
        self._shift_toggle.connect("toggled", self._on_shift_sticky_toggled)
        shift_row.pack_start(self._shift_toggle, False, False, 0)

        self._shift_hint_label = Gtk.Label(
            label="stays until clicked again" if shift_sticky else "releases after keypress")
        self._shift_hint_label.set_name("settings-hint")
        self._shift_hint_label.set_xalign(0.0)
        shift_row.pack_start(self._shift_hint_label, False, False, 4)

        inner.pack_start(shift_row, False, False, 0)

        # ── 10. Panel shortcut row (Linux / Cinnamon only) ─────────────────
        if not IS_WINDOWS:
            shortcut_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            shortcut_row.pack_start(self._settings_row_label("Taskbar"), False, False, 0)

            pin_btn = Gtk.Button(label="Pin to panel")
            pin_btn.get_style_context().add_class("settings-choice")
            pin_btn.set_size_request(_px(100), -1)
            pin_btn.connect("clicked", self._on_pin_to_panel, pin_btn)
            shortcut_row.pack_start(pin_btn, False, False, 0)

            self._pin_status_label = Gtk.Label(label="")
            self._pin_status_label.set_name("settings-label")
            self._pin_status_label.set_xalign(0.0)
            shortcut_row.pack_start(self._pin_status_label, False, False, 4)

            inner.pack_start(shortcut_row, False, False, 0)
        else:
            # Windows has no API for an app to pin itself (Microsoft removed it),
            # so the button explains the two-click way.  The shared
            # AppUserModelID makes the pinned icon and the running keyboard
            # one taskbar button.
            shortcut_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            shortcut_row.pack_start(self._settings_row_label("Taskbar"), False, False, 0)
            pin_btn = Gtk.Button(label="Pin to taskbar")
            pin_btn.get_style_context().add_class("settings-choice")
            pin_btn.set_size_request(_px(190), -1)
            pin_btn.connect("clicked", lambda b: self._toggle_setting_help(
                b, "Pin to taskbar", WINDOWS_PIN_STEPS))
            shortcut_row.pack_start(pin_btn, False, False, 0)
            inner.pack_start(shortcut_row, False, False, 0)

        inner.pack_start(self._settings_section_header("Help"), False, False, 0)

        # ── 10b. Tour row ──────────────────────────────────────────────────
        tour_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tour_row.pack_start(self._settings_row_label("Tutorial"), False, False, 0)

        tour_btn = Gtk.Button(label="Show tutorial")
        tour_btn.get_style_context().add_class("settings-choice")
        tour_btn.set_size_request(_px(140), _px(44))
        tour_btn.connect("clicked", lambda _b: self._start_tour())
        tour_btn.get_accessible().set_description(
            "Replay the four-step introduction to the keyboard")
        tour_row.pack_start(tour_btn, False, False, 0)

        inner.pack_start(tour_row, False, False, 0)

        inner.pack_start(self._settings_section_header("Data"), False, False, 0)

        # -- Learned words ---------------------------------------------------
        learn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        learn_row.pack_start(self._settings_row_label("Learn my words"), False, False, 0)

        learn_on = bool(self.settings.get("learn_words", True))
        learn_toggle = Gtk.ToggleButton(label="On" if learn_on else "Off")
        learn_toggle.set_name("settings-toggle")
        learn_toggle.set_size_request(_px(100), -1)
        learn_toggle.set_active(learn_on)
        learn_toggle.connect("toggled", self._on_learn_toggled)
        learn_toggle.get_accessible().set_description(
            "Remember which words you use, locally, to improve suggestions")
        learn_row.pack_start(learn_toggle, False, False, 0)

        self._forget_btn = Gtk.Button(label="Forget learned words")
        self._forget_btn.get_style_context().add_class("settings-choice")
        self._forget_btn.connect("clicked", self._on_forget_clicked)
        self._forget_btn.get_accessible().set_description(
            "Erase the words and phrases the keyboard has learned from your typing")
        self._forget_btn.set_size_request(_px(190), -1)
        learn_row.pack_start(self._forget_btn, False, False, 0)
        inner.pack_start(learn_row, False, False, 0)

        # ── 11. Custom dictionary section ──────────────────────────────────
        inner.pack_start(self._settings_section_header("Your words and shortcuts"), False, False, 0)
        dict_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dict_header.pack_start(self._settings_row_label("Custom words"), False, False, 0)
        self._custom_words_header = dict_header

        n = len(self._custom_words)
        self._custom_words_count = Gtk.Label(
            label=f"{n} word{'s' if n != 1 else ''}")
        self._custom_words_count.set_name("settings-hint")
        self._custom_words_count.set_xalign(0.0)
        dict_header.pack_start(self._custom_words_count, True, True, 0)

        add_btn = Gtk.Button(label="+ Add word or phrase")
        add_btn.get_style_context().add_class("settings-choice")
        add_btn.set_size_request(_px(190), -1)
        add_btn.connect("clicked", lambda _: self._open_custom_input_mode())
        dict_header.pack_end(add_btn, False, False, 0)
        inner.pack_start(dict_header, False, False, 0)

        self._custom_words_listbox = Gtk.ListBox()
        self._custom_words_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._custom_words_listbox.get_style_context().add_class("settings-list")
        self._custom_words_listbox.set_placeholder(self._empty_list_label("No custom words yet"))

        for word in self._custom_words:
            self._custom_words_listbox.add(self._make_custom_word_row(word))

        # No inner scroller: the list grows and the settings page scrolls.
        inner.pack_start(self._custom_words_listbox, False, False, 0)

        # ── 12. Macros section ─────────────────────────────────────────────
        macros_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        macros_header.set_margin_top(8)
        self._macros_header = macros_header
        macros_header.pack_start(self._settings_row_label("Macros"), False, False, 0)

        nm = len(self._macros)
        self._macros_count = Gtk.Label(label=f"{nm} macro{'s' if nm != 1 else ''}")
        self._macros_count.set_name("settings-hint")
        self._macros_count.set_xalign(0.0)
        macros_header.pack_start(self._macros_count, True, True, 0)

        add_macro_btn = Gtk.Button(label="+ Add macro")
        add_macro_btn.get_style_context().add_class("settings-choice")
        add_macro_btn.set_size_request(_px(190), -1)
        add_macro_btn.connect("clicked",
                              lambda _: self._open_custom_input_mode(target="macro_trigger"))
        macros_header.pack_end(add_macro_btn, False, False, 0)
        inner.pack_start(macros_header, False, False, 0)

        self._macros_listbox = Gtk.ListBox()
        self._macros_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._macros_listbox.get_style_context().add_class("settings-list")
        self._macros_listbox.set_placeholder(self._empty_list_label("No macros yet"))

        for macro in self._macros:
            self._macros_listbox.add(self._make_macro_row(macro))

        inner.pack_start(self._macros_listbox, False, False, 0)

        # ── 13. Custom themes section ──────────────────────────────────────
        ctheme_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ctheme_hdr.set_margin_top(8)
        ctheme_hdr.pack_start(self._settings_row_label("Custom themes"), False, False, 0)

        new_theme_btn = Gtk.Button(label="+ New theme")
        new_theme_btn.get_style_context().add_class("settings-choice")
        new_theme_btn.set_size_request(_px(190), -1)
        new_theme_btn.connect("clicked", lambda _: self._open_theme_wizard())
        ctheme_hdr.pack_end(new_theme_btn, False, False, 0)
        inner.pack_start(ctheme_hdr, False, False, 0)

        self._custom_theme_btns_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.pack_start(self._custom_theme_btns_box, False, False, 0)
        self._rebuild_custom_theme_buttons()

        # ── 14. Updates ────────────────────────────────────────────────────────
        # ── 13b. Feedback (GitHub issue forms; no account data leaves the app)
        inner.pack_start(self._settings_section_header("Feedback"), False, False, 0)
        fb_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fb_row.pack_start(self._settings_row_label("Rate or comment"), False, False, 0)
        for text, url, desc in (
            ("★ Leave a review", FEEDBACK_REVIEW_URL, "Rate the keyboard on GitHub"),
            ("✎ Leave a note",   FEEDBACK_NOTE_URL,   "Send a note, idea or bug report on GitHub"),
        ):
            b = Gtk.Button(label=text)
            b.get_style_context().add_class("settings-choice")
            b.set_size_request(_px(190), -1)
            b.set_tooltip_text(desc + " (opens your browser)")
            b.get_accessible().set_description(desc)
            b.connect("clicked", lambda _b, u=url: self._open_url(u))
            fb_row.pack_start(b, False, False, 0)
        fb_hint = Gtk.Label(label="opens GitHub in your browser")
        fb_hint.set_name("settings-hint")
        fb_hint.set_xalign(0.0)
        fb_row.pack_start(fb_hint, False, False, 4)
        inner.pack_start(fb_row, False, False, 0)

        inner.pack_start(self._settings_section_header("Updates"), False, False, 0)
        update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        update_row.pack_start(self._settings_row_label(f"Version {__version__}"), False, False, 0)

        self._update_status_label = Gtk.Label(label="")
        self._update_status_label.set_name("settings-label")
        self._update_status_label.set_xalign(0.0)
        self._update_status_label.set_line_wrap(True)

        self._update_btn = Gtk.Button(label="Check for updates")
        self._update_btn.get_style_context().add_class("settings-choice")
        self._update_btn.set_size_request(_px(190), -1)
        self._update_btn.connect("clicked", self._on_check_for_updates)
        update_row.pack_start(self._update_btn, False, False, 0)
        update_row.pack_start(self._update_status_label, True, True, 4)
        inner.pack_start(update_row, False, False, 0)

        scroll_outer.add(inner)
        panel.pack_start(scroll_outer, True, True, 0)

        self._refresh_theme_buttons()
        self._refresh_layout_buttons()
        self._refresh_font_family_buttons()
        return panel

    def _open_url(self, url: str):
        """Open a web page in the user's default browser."""
        try:
            if IS_WINDOWS:
                os.startfile(url)
            else:
                import webbrowser
                webbrowser.open(url)
        except Exception as exc:
            print(f"[feedback] could not open {url}: {exc}")

    def _build_emoji_panel(self) -> Gtk.Box:
        """Browsable + searchable emoji grid (shown when emoji mode is active)."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.set_name("emoji-panel")

        # Search label at top
        self._emoji_label = Gtk.Label(label="Type to search, click an emoji to insert")
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

    def _on_space_after_toggled(self, toggle: Gtk.ToggleButton):
        on = toggle.get_active()
        toggle.set_label("On" if on else "Off")
        self.settings["space_after_suggestion"] = on
        self._save_settings()

    def _on_learn_toggled(self, toggle: Gtk.ToggleButton):
        on = toggle.get_active()
        toggle.set_label("On" if on else "Off")
        self.settings["learn_words"] = on
        self._apply_learn_setting()
        self._ctx_reset()
        self._save_settings()
        self._refresh_suggestions()

    def _on_forget_clicked(self, btn: Gtk.Button):
        """Two-step: first click arms (3 s window), second click erases."""
        if self._forget_timer is not None:
            GLib.source_remove(self._forget_timer)
        if not self._forget_armed:
            self._forget_armed = True
            btn.set_label("Click again to confirm")
        else:
            self._forget_armed = False
            self._user_model.forget()
            # ...and what was learned in the other languages
            for code in OTHER_LANGUAGE_CODES + ("en_US",):  # every language's file
                path = learned_words_file(code)
                if path != self._user_model.path:
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        print(f"[learn] Could not erase {path}: {exc}")
            if self._learn_save_timer is not None:
                GLib.source_remove(self._learn_save_timer)
                self._learn_save_timer = None
            btn.set_label("Learned words erased")
            self._refresh_suggestions()
        self._forget_timer = GLib.timeout_add_seconds(3, self._disarm_forget)

    def _disarm_forget(self) -> bool:
        self._forget_timer = None
        self._forget_armed = False
        btn = getattr(self, "_forget_btn", None)
        if btn is not None:
            btn.set_label("Forget learned words")
        return False

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

    def _set_key_sound(self, choice: str):
        self.settings["click_sound"] = choice != "off"
        if choice != "off":
            self.settings["click_style"] = choice
        self._refresh_sound_buttons()
        self._save_settings()
        self._play_click_sound()   # preview the new choice

    def _on_volume_changed(self, scale_widget: Gtk.Scale):
        value = round(scale_widget.get_value(), 2)
        self.settings["sound_volume"] = value
        if self._volume_label:
            self._volume_label.set_label(f"{int(round(value * 100))}%")
        # Regenerate + preview once the slider settles, not on every pixel.
        if self._volume_timer is not None:
            GLib.source_remove(self._volume_timer)
        self._volume_timer = GLib.timeout_add(180, self._apply_volume)

    def _apply_volume(self) -> bool:
        self._volume_timer = None
        self._init_click_sound()
        self._save_settings()
        self._play_click_sound(force=True)   # preview, even if sounds are Off
        return False

    def _refresh_sound_buttons(self):
        current = (self.settings.get("click_style", "click")
                   if self.settings.get("click_sound") else "off")
        for key, btn in self._sound_btns.items():
            ctx = btn.get_style_context()
            if key == current:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

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
        size = max(10, min(FONT_SIZE_MAX, self.settings.get("font_size", DEFAULT_FONT_SIZE) + delta))
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
                        f"  cd {shlex.quote(install_dir)} && git pull"
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

    _RELEASES_URL = "https://api.github.com/repos/Gitties67/Onscreen-keyboard/releases/latest"
    _ASSET_URL_PREFIX = "https://github.com/Gitties67/Onscreen-keyboard/releases/download/"

    def _do_update_check_exe_mode(self):
        import fnmatch
        import urllib.request, urllib.error, json as _json

        version_file = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "VERSION")
        local_ver = "unknown"
        if os.path.exists(version_file):
            with open(version_file) as f:
                local_ver = f.read().strip()

        info = None  # dict(url, name, sums_url) when an installer update is available
        new_etag = self._update_etag
        if _parse_version(local_ver) is None:
            status = "Cannot determine installed version — VERSION file missing."
        else:
            headers = {"User-Agent": "OSK-updater/1.0"}
            if self._update_etag:
                headers["If-None-Match"] = self._update_etag
            try:
                req = urllib.request.Request(self._RELEASES_URL, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    new_etag = resp.headers.get("ETag", self._update_etag)
                    data = _json.loads(resp.read())
                latest_tag = data.get("tag_name", "unknown")

                setup = sums = None
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if fnmatch.fnmatch(name.lower(), "onscreenkeyboard-setup-*.exe"):
                        setup = asset
                    elif name.lower() == "sha256sums.txt":
                        sums = asset

                if not _version_newer(latest_tag, local_ver):
                    status = "You're on the latest version."
                elif setup and sums:
                    info = {"url": setup.get("browser_download_url", ""),
                            "name": setup.get("name", ""),
                            "sums_url": sums.get("browser_download_url", "")}
                    status = (f"Update available! You have {local_ver}, latest is {latest_tag}.\n"
                              "Click below to download and install automatically.")
                else:
                    status = (f"Update available ({latest_tag}), but the release has no installer "
                              "with a checksum file. Download it from the GitHub Releases page.")
            except urllib.error.HTTPError as e:
                if e.code == 304:
                    # Not Modified — our cached ETag matched, no API quota used
                    status = "You're on the latest version."
                elif e.code == 404:
                    status = "No releases published yet — this build is the latest."
                elif e.code in (403, 429):
                    status = ("GitHub rate limit reached — you can only check a few times per hour.\n"
                              "Try again later.")
                else:
                    status = f"Update server returned HTTP {e.code} {e.reason}."
            except urllib.error.URLError as e:
                status = f"Network error — check your internet connection.\n({e.reason})"
            except Exception as e:
                status = f"Update check failed: {e}"

        def _apply(status=status, info=info, etag=new_etag):
            self._update_etag = etag
            if self._update_status_label:
                self._update_status_label.set_text(status)
            if self._update_btn:
                self._update_btn.set_sensitive(True)
                if info and not self._is_installed_build():
                    # Portable zip: running Setup would create a second, installed copy.
                    if self._update_status_label:
                        self._update_status_label.set_text(
                            status + "\nPortable copy — download the new zip from the "
                            "Releases page on GitHub.")
                    self._update_btn.set_label("Check for updates")
                elif info:
                    self._update_btn.set_label("Download & install update")
                    try:
                        self._update_btn.disconnect_by_func(self._on_check_for_updates)
                    except Exception:
                        pass
                    self._update_btn.connect(
                        "clicked", lambda _, i=info: self._on_download_update(i))
                else:
                    self._update_btn.set_label("Check for updates")
        GLib.idle_add(_apply)

    @staticmethod
    def _is_installed_build() -> bool:
        """True when running from the Inno Setup install (uninstaller sits beside the exe)."""
        return os.path.exists(os.path.join(os.path.dirname(sys.executable), "unins000.exe"))

    def _on_download_update(self, info: dict):
        if self._update_status_label:
            self._update_status_label.set_text("Starting download…")
        if self._update_btn:
            self._update_btn.set_sensitive(False)
        import threading
        threading.Thread(target=self._do_download_update, args=(info,), daemon=True).start()

    def _do_download_update(self, info: dict):
        """Download the Setup exe, verify it against SHA256SUMS.txt, run it silently."""
        import hashlib
        import tempfile
        import urllib.request, urllib.error
        tmp_dir = None
        try:
            for u in (info["url"], info["sums_url"]):
                if not u.startswith(self._ASSET_URL_PREFIX):
                    raise RuntimeError("unexpected download URL; refusing to continue")

            def _set_status(text):
                GLib.idle_add(lambda: (self._update_status_label and
                                       self._update_status_label.set_text(text)) and False)

            # 1. checksum list (small) — required; no checksum, no install
            req = urllib.request.Request(info["sums_url"], headers={"User-Agent": "OSK-updater/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                sums_text = resp.read(256 * 1024).decode("utf-8", "replace")
            expected = None
            for line in sums_text.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[1].lstrip("*").strip() == info["name"]:
                    expected = parts[0].lower()
                    break
            if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise RuntimeError(f"{info['name']} not listed in SHA256SUMS.txt")

            # 2. installer
            tmp_dir = tempfile.mkdtemp(prefix=UPDATE_TMP_PREFIX)
            tmp_path = os.path.join(tmp_dir, os.path.basename(info["name"]))
            digest = hashlib.sha256()
            req = urllib.request.Request(info["url"], headers={"User-Agent": "OSK-updater/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_pct = -1
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(downloaded * 100 / total)
                            if pct != last_pct:
                                last_pct = pct
                                _set_status(f"Downloading… {pct}%")

            if digest.hexdigest().lower() != expected:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise RuntimeError("checksum mismatch — download discarded")

            def _finish():
                if self._update_status_label:
                    self._update_status_label.set_text("Verified — installing and restarting…")
                try:
                    # /RELAUNCH=1 is read by installer/OnScreenKeyboard.iss to start the
                    # new version once files are replaced (silent mode skips postinstall).
                    subprocess.Popen(
                        [tmp_path, "/SP-", "/SILENT", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"],
                        env=_child_env(),
                        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                                      | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                        close_fds=True,
                    )
                except Exception as exc:
                    if self._update_status_label:
                        self._update_status_label.set_text(f"Could not start installer: {exc}")
                    if self._update_btn:
                        self._update_btn.set_sensitive(True)
                    return False
                Gtk.main_quit()
                return False
            GLib.idle_add(_finish)

        except Exception as exc:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            def _err(msg=str(exc)):
                if self._update_status_label:
                    self._update_status_label.set_text(f"Download failed: {msg}")
                if self._update_btn:
                    self._update_btn.set_sensitive(True)
                    self._update_btn.set_label("Download & install update")
                return False
            GLib.idle_add(_err)

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
            subprocess.Popen([sys.executable], env=_child_env())
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
        font_size   = self.settings.get("font_size", DEFAULT_FONT_SIZE)
        font_family = self.settings.get("font_family", DEFAULT_FONT)
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
        self._dwell_start(widget, lambda: self._on_key_clicked(widget, action))

    def _dwell_start(self, widget, fire_cb) -> None:
        """Arm a dwell timer on `widget`; after dwell_delay call fire_cb().

        Shared by the key buttons and the tour buttons.  Shows the
        .dwell-pending class while armed; _on_dwell_leave / _dwell_cancel
        disarm it.
        """
        if not self.settings.get("dwell_enabled"):
            return
        # Cancel any existing timer for this widget
        self._dwell_cancel(widget)
        delay_ms = int(self.settings.get("dwell_delay", 0.8) * 1000)
        widget.get_style_context().add_class("dwell-pending")
        wid = id(widget)

        def _fire():
            widget.get_style_context().remove_class("dwell-pending")
            self._dwell_timers.pop(wid, None)
            fire_cb()
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

    # ── First-run tour ───────────────────────────────────────────────────────

    def _maybe_start_tour(self) -> bool:
        """GLib timeout: start the tour once, on first run (never in --selftest).

        Retries while the window is not yet mapped (e.g. started minimised).
        """
        if "--selftest" in sys.argv[1:] or self.settings.get("tutorial_done", False):
            return False
        if not self.get_mapped() or self._minimized:
            self._tour_start_tries = getattr(self, "_tour_start_tries", 0) + 1
            return self._tour_start_tries < 30   # retry ~24 s, then wait for next launch
        self._start_tour()
        return False

    def _start_tour(self):
        """Show step 1 of the tour (also used by Settings → Show tutorial)."""
        self._close_other_panels("tour")
        if self._collapsed:
            self._expand()
        if self._tour_popover is None:
            self._build_tour_popover()
        self._tour_step = 0
        self._tour_show_step()

    def _build_tour_popover(self):
        pop = Gtk.Popover()
        # Non-modal: a modal popover grabs input and tries to take focus, which
        # our non-focusable DOCK / WS_EX_NOACTIVATE window must never do.  In
        # GTK3 a popover is a child GdkWindow of our toplevel, so its buttons
        # take clicks without the window being focusable.
        pop.set_modal(False)
        pop.set_position(Gtk.PositionType.BOTTOM)
        pop.get_style_context().add_class("tour-popover")
        pop.get_accessible().set_name("Keyboard tour")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        self._tour_count_lbl = Gtk.Label()
        self._tour_count_lbl.get_style_context().add_class("tour-count")
        self._tour_count_lbl.set_xalign(0.0)
        box.pack_start(self._tour_count_lbl, False, False, 0)

        self._tour_title_lbl = Gtk.Label()
        self._tour_title_lbl.get_style_context().add_class("tour-title")
        self._tour_title_lbl.set_xalign(0.0)
        box.pack_start(self._tour_title_lbl, False, False, 0)

        self._tour_body_lbl = Gtk.Label()
        self._tour_body_lbl.get_style_context().add_class("tour-body")
        self._tour_body_lbl.set_xalign(0.0)
        self._tour_body_lbl.set_line_wrap(True)
        self._tour_body_lbl.set_max_width_chars(34)
        self._tour_body_lbl.set_width_chars(28)
        box.pack_start(self._tour_body_lbl, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_margin_top(6)
        self._tour_skip_btn = Gtk.Button(label="Skip tour")
        self._tour_next_btn = Gtk.Button(label="Next")
        for btn, cls in ((self._tour_skip_btn, "tour-skip"),
                         (self._tour_next_btn, "tour-next")):
            ctx = btn.get_style_context()
            ctx.add_class("tour-btn")
            ctx.add_class(cls)
            btn.set_size_request(_px(96), _px(44))
            btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK |
                           Gdk.EventMask.LEAVE_NOTIFY_MASK |
                           Gdk.EventMask.POINTER_MOTION_MASK)
            btn.connect("enter-notify-event",  self._on_tour_btn_hover)
            btn.connect("motion-notify-event", self._on_tour_btn_hover)
            # Reuse the key dwell disarm (its action arg is unused).
            btn.connect("leave-notify-event",  self._on_dwell_leave, "")
        self._tour_skip_btn.connect("clicked", lambda _b: self._end_tour())
        self._tour_next_btn.connect("clicked", lambda _b: self._tour_next())
        btn_row.pack_start(self._tour_skip_btn, False, False, 0)
        btn_row.pack_end(self._tour_next_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        box.show_all()
        pop.add(box)
        self._tour_popover = pop

    def _tour_target(self, key: str):
        """(relative_to widget, pointing rect or None) for a tour step."""
        if key == "suggestions" and self._suggestion_btns:
            return self._suggestion_btns[min(2, len(self._suggestion_btns) - 1)], None
        if key == "settings" and self._settings_btn:
            return self._settings_btn, None
        if key == "emoji_clip" and self._suggestion_bar:
            # One arrow pointing between the emoji and clipboard buttons.
            bar, rect = self._suggestion_bar, None
            pts = []
            for btn in (self._emoji_btn, self._clipboard_btn):
                if btn is None or not btn.get_mapped():
                    continue
                # PyGObject's Gtk override returns (x, y) or None here.
                res = btn.translate_coordinates(bar, 0, 0)
                if res:
                    x, y = res[-2], res[-1]
                    a = btn.get_allocation()
                    pts.append((x, y, x + a.width, y + a.height))
            if pts:
                rect = Gdk.Rectangle()
                rect.x = min(p[0] for p in pts)
                rect.y = min(p[1] for p in pts)
                rect.width = max(p[2] for p in pts) - rect.x
                rect.height = max(p[3] for p in pts) - rect.y
            return bar, rect
        if key == "titlebar" and self._titlebar:
            return self._titlebar, None
        return self._suggestion_bar or self, None

    def _tour_show_step(self):
        if self._tour_popover is None or not (0 <= self._tour_step < len(TOUR_STEPS)):
            return
        key, title, body = TOUR_STEPS[self._tour_step]
        last = self._tour_step == len(TOUR_STEPS) - 1
        self._tour_count_lbl.set_label(f"Tip {self._tour_step + 1} of {len(TOUR_STEPS)}")
        self._tour_title_lbl.set_label(title)
        self._tour_body_lbl.set_label(body)
        self._tour_next_btn.set_label("Done" if last else "Next")
        self._tour_next_btn.get_accessible().set_description(
            "Finish the tour" if last else "Show the next tip")
        self._tour_skip_btn.set_visible(not last)

        # Disarm any dwell on the buttons before they move.
        for btn in (self._tour_next_btn, self._tour_skip_btn):
            self._dwell_cancel(btn)
            btn.get_style_context().remove_class("dwell-pending")
        # Dwell only re-arms once the pointer has moved away from where it was
        # when this step appeared, so a resting pointer that happens to land on
        # the new Next/Skip cannot auto-advance (or auto-skip) the tour.
        self._tour_anchor_xy = self._pointer_root_xy()

        widget, rect = self._tour_target(key)
        if not widget.get_mapped():        # e.g. a panel replaced the suggestions
            widget, rect = self._titlebar, None
        pop = self._tour_popover
        pop.hide()                 # re-anchor without animating from the old spot
        pop.set_relative_to(widget)
        if rect is None:
            a = widget.get_allocation()
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = 0, 0, a.width, a.height
        pop.set_pointing_to(rect)
        pop.popup()

    def _tour_next(self):
        if self._tour_step >= len(TOUR_STEPS) - 1:
            self._end_tour()
            return
        self._tour_step += 1
        self._tour_show_step()

    def _end_tour(self):
        """Finish or skip: hide the bubble and never auto-show it again."""
        self._tour_step = -1
        for btn in (self._tour_next_btn, self._tour_skip_btn):
            if btn is not None:
                self._dwell_cancel(btn)
                btn.get_style_context().remove_class("dwell-pending")
        if self._tour_popover is not None:
            self._tour_popover.popdown()
        if not self.settings.get("tutorial_done"):
            self.settings["tutorial_done"] = True
            self._save_settings()

    def _pointer_root_xy(self):
        try:
            seat = self.get_display().get_default_seat()
            _screen, x, y = seat.get_pointer().get_position()
            return (x, y)
        except Exception:
            return None

    def _on_tour_btn_hover(self, btn, event):
        """Enter/motion on Next/Skip: start a dwell click (when dwell is on)."""
        if not self.settings.get("dwell_enabled") or self._tour_step < 0:
            return False
        if (event.type == Gdk.EventType.ENTER_NOTIFY
                and event.detail == Gdk.NotifyType.INFERIOR):
            return False
        if id(btn) in self._dwell_timers:
            return False   # already armed
        if self._tour_anchor_xy is not None:
            ax, ay = self._tour_anchor_xy
            if math.hypot(event.x_root - ax, event.y_root - ay) < TOUR_DWELL_MOVE_PX:
                return False
            self._tour_anchor_xy = None
        self._dwell_start(btn, btn.clicked)
        return False

    # ── Click sound ───────────────────────────────────────────────────────────

    @staticmethod
    def _wav_bytes(rate: int, samples, peak: float) -> bytes:
        """16-bit mono WAV, normalised so the loudest sample is `peak` (0–1),
        with 40 ms of trailing silence: Windows' audio engine can drop a sound
        that's only a few milliseconds long."""
        import io
        samples = list(samples)
        top = max((abs(v) for v in samples), default=0) or 1.0
        gain = 32000 * peak / top
        pcm = [int(v * gain) for v in samples] + [0] * int(rate * 0.04)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"".join(struct.pack("<h", v) for v in pcm))
        return buf.getvalue()

    def _init_click_sound(self):
        """Generate both key sounds at the current volume.  Kept in memory for
        Windows (PlaySound from memory) and written to the config dir for
        paplay/aplay on Linux.  Re-run when the volume slider moves."""
        import random
        vol = float(self.settings.get("sound_volume", 0.7))
        rate = 44100
        # Click: a short bright tick (band-limited noise, ~3 ms decay) on top
        # of a small low "thock" (180 Hz, ~7 ms decay) - like a laptop key.
        rng = random.Random(7)
        lp = hp_prev = 0.0
        click = []
        for i in range(int(rate * 0.025)):
            t = i / rate
            lp += 0.5 * (rng.uniform(-1, 1) - lp)
            hp = lp - hp_prev            # crude high-pass keeps it crisp
            hp_prev = lp
            click.append(hp * math.exp(-t / 0.003)
                         + 0.45 * math.sin(2 * math.pi * 180 * t) * math.exp(-t / 0.007))
        beep = [math.exp(-i / rate * 140) * math.sin(2 * math.pi * 1100 * i / rate)
                for i in range(int(rate * 0.04))]
        # Click peaks a little under the beep so equal volume sounds similar.
        self._sound_data = {
            "click": self._wav_bytes(rate, click, 0.75 * vol),
            "beep":  self._wav_bytes(rate, beep, 0.85 * vol),
        }
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            for path, key in ((CLICK_WAV, "click"), (BEEP_WAV, "beep")):
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(self._sound_data[key])
                os.replace(tmp, path)
        except Exception as exc:
            print(f"[sound] Could not write key sounds: {exc}")

    def _play_click_sound(self, force: bool = False):
        if not (self.settings.get("click_sound") or force):
            return
        style = "beep" if self.settings.get("click_style") == "beep" else "click"
        if IS_WINDOWS:
            data = getattr(self, "_sound_data", {}).get(style)
            if not data:
                return
            try:
                # winmm directly (no dependency on the winsound module being
                # present in the bundled Python).  SND_MEMORY: no file I/O.
                import ctypes
                SND_ASYNC, SND_NODEFAULT, SND_MEMORY = 0x0001, 0x0002, 0x0004
                winmm = ctypes.windll.winmm
                winmm.PlaySoundW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
                winmm.PlaySoundW.restype = ctypes.c_int
                buf = self._sound_bufs.get(style)
                if buf is None or buf[0] is not data:
                    buf = (data, ctypes.create_string_buffer(data, len(data)))
                    self._sound_bufs[style] = buf
                    self._sound_keep.append(buf)
                    del self._sound_keep[:-8]
                ok = winmm.PlaySoundW(ctypes.addressof(buf[1]), None,
                                      SND_MEMORY | SND_ASYNC | SND_NODEFAULT)
                if not ok and not getattr(self, "_sound_fail_logged", False):
                    self._sound_fail_logged = True
                    _win_log("sound: PlaySoundW returned 0")
            except Exception as exc:
                if not getattr(self, "_sound_fail_logged", False):
                    self._sound_fail_logged = True
                    _win_log(f"sound: PlaySoundW error ({exc})")
            return
        wav = BEEP_WAV if style == "beep" else CLICK_WAV
        if not os.path.exists(wav):
            return
        for player in ("paplay", "aplay"):
            try:
                subprocess.Popen(
                    [player, wav],
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
            self._open_emoji_mode()

    def _open_emoji_mode(self):
        self._close_other_panels("emoji")
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
            self._emoji_label.set_text("Type to search, click an emoji to insert")
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
                self._emoji_label.set_text("Type to search, click an emoji to insert")
        if self._emoji_flowbox:
            self._emoji_flowbox.invalidate_filter()

    def _open_app_mode(self):
        self._close_other_panels("app")
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
            # Remember how the word was typed so suggestions keep its case:
            # "A" → "Alex", "AB" → "ABOUT".
            if not self.current_word:
                self._word_title = char.isupper()
                self._word_upper = char.isupper()
            else:
                self._word_upper = self._word_upper and char.isupper()
            self.current_word += char.lower()
        elif mods:
            self.current_word = ""
            self._ctx_reset()      # shortcuts (paste, select-all...) change the text
        elif char == "'" and self.current_word:
            # Mid-contraction (don't, I'm): hold the stem so the committed
            # token is "don't", not "don" + "t".
            self._ctx_stem = self.current_word + "'"
            self.current_word = ""
        else:
            # Punctuation / digits end the word. . ! ? end the sentence too.
            if char in ".!?":
                self._ctx_commit(self.current_word)
                self._ctx_reset()
            elif char in ',;:"()':
                self._ctx_commit(self.current_word)
            else:
                # Digits and symbols (@ / _ - # ...) usually mean an address,
                # username or password: don't learn the word or chain context.
                self._ctx_reset()
            self.current_word = ""

        self._refresh_suggestions()

        if self.shift_active and not self.settings.get("shift_sticky", False):
            self.shift_active = False
            self._update_modifier_visuals()

    # ── Suggestion bar ────────────────────────────────��───────────────────────

    # -- Sentence context + learning -----------------------------------------

    def _ctx_commit(self, word: str):
        """A word was finished (space, punctuation, Enter, suggestion click):
        push it onto the sentence context and let the user model learn it."""
        word = (word or "").lower()
        if self._ctx_stem:
            if self.predictor.language in ELISION_LANGUAGES:
                pass                  # "l'" + "homme": the word is just "homme"
            else:
                word = self._ctx_stem + word if word else ""
            self._ctx_stem = ""
        if not word:
            return
        if self.settings.get("learn_words", True):
            # observe() ignores anything that isn't a known alphabetic word
            if self._user_model.observe(word, self._ctx):
                self._schedule_learn_save()
        self._ctx = (self._ctx + [word])[-2:]
        self._last_word = word

    def _ctx_reset(self):
        """Sentence boundary or unknown caret position: forget the context."""
        self._ctx = []
        self._ctx_stem = ""
        self._last_word = ""

    def _apply_learn_setting(self):
        """Learning off also stops the learned data from influencing suggestions."""
        on = self.settings.get("learn_words", True)
        self.predictor.set_user_model(self._user_model if on else None)

    def _schedule_learn_save(self):
        if self._learn_save_timer is None:
            self._learn_save_timer = GLib.timeout_add_seconds(5, self._flush_learned)

    def _flush_learned(self) -> bool:
        self._learn_save_timer = None
        self._user_model.save()
        return False

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
            exact_sugg = [x for x in self.predictor.predict(
                              w, n=n_word_slots + 2, context=self._ctx)
                          if x not in custom_lower]
        else:
            # After a space: predict from the sentence so far
            exact_sugg = self.predictor.predict_next(self._ctx, n=n_word_slots + 2)

        seen_words = {s[0].lower() for s in slots}
        for v in exact_sugg:
            if len(slots) >= 4:
                break
            if v.lower() not in seen_words:
                slots.append((v, False, False, False, False, "", False))
                seen_words.add(v.lower())

        # Pad to 4 word slots with common fallback words (marked as fallback so
        # the fuzzy timer knows these aren't real predictions for the current prefix)
        # Only when no word is in progress — otherwise a fallback word would
        # complete to something unrelated to the typed prefix.
        fallback = [] if w else self.predictor.predict_padded("", 20)
        for v in fallback:
            if len(slots) >= 4:
                break
            if v.lower() not in seen_words:
                slots.append((v, False, False, False, False, "", True))
                seen_words.add(v.lower())

        # Slot 4: emoji if any of the 4 word slots has an emoji match,
        # otherwise pad with a 5th word
        emoji_slot: tuple[str, bool, bool, bool, bool, str, bool] | None = None
        # word labels only; skip when nothing is typed — an emoji for "the"/"to"
        # is just noise
        check_words = [s[0] for s in slots[:4] if s is not None and not s[1]] if w else []
        for word in check_words:
            hits = emoji_suggest(word, n=1)
            if hits:
                emoji_slot = (hits[0], True, False, False, False, "", False)
                break
        if emoji_slot:
            # Keep emoji at index 4 — pad short word lists with blank slots so
            # the fuzzy updater (which fills 0–3) can never overwrite it.
            slots.extend([None] * (4 - len(slots)))
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
            if i < len(slots) and slots[i] is not None:
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
        n_real = sum(1 for s in slots[:4]
                     if s is not None and not s[1] and not s[4] and not s[6])
        if w and n_real < 4 and len(w) >= 3:
            self._fuzzy_timer = GLib.timeout_add(250, self._run_fuzzy_update)

    def _run_fuzzy_update(self) -> bool:
        """Run fuzzy spell-check and fill remaining suggestion slots (debounced)."""
        self._fuzzy_timer = None
        if (self._settings_mode or self._emoji_mode or self._clipboard_mode
                or self._app_mode or self._custom_input_mode):
            return False
        w = self.current_word
        if not w or len(w) < 3:
            return False

        custom_sugg  = self.predictor.custom_matches(w)
        custom_lower = {c.lower() for c in custom_sugg}
        # Count macro slots first so we can correctly budget the exact-word query
        n_macro      = sum(1 for m in self._suggestion_is_macro[:4] if m)
        # Word slots are 0–3; find which are already filled with exact matches
        exact_sugg   = [x for x in self.predictor.predict(
                            w, n=max(1, 4 - n_macro), context=self._ctx)
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
            self._suggestion_values[idx]    = v
            self._suggestion_is_fuzzy[idx]  = True
            self._suggestion_is_emoji[idx]  = False
            self._suggestion_is_custom[idx] = False
            self._suggestion_is_macro[idx]  = False

        return False  # don't repeat

    # Windows: a posted backspace only becomes a "delete" when the target app's
    # TranslateMessage turns it into WM_CHAR(0x08) — which is queued BEHIND the
    # characters we already posted.  So erase first, then type after a pause.
    BACKSPACE_SETTLE_MS = 90

    def _replace_typed_word(self, text: str, typed: str):
        """Turn what was typed (`typed`) into `text` (+ the optional space).

        Keeps the part that already matches exactly (typed "Ga" → "Gattatron"
        only types "ttatron", no backspace at all) and erases only the rest."""
        keep = 0
        while keep < min(len(typed), len(text)) and typed[keep] == text[keep]:
            keep += 1
        erase = len(typed) - keep
        rest = text[keep:]

        def _type_rest():
            for ch in rest:
                self.typer.type_char(ch)
            self._suggestion_space()
            return False

        for _ in range(erase):
            self.typer.send_special("backspace")
        if erase and IS_WINDOWS:
            GLib.timeout_add(self.BACKSPACE_SETTLE_MS, _type_rest)
        else:
            _type_rest()

    def _suggestion_space(self):
        """Space after an accepted suggestion, unless turned off in Settings."""
        if self.settings.get("space_after_suggestion", True):
            self.typer.send_special("space")

    def _on_suggestion_clicked(self, btn: Gtk.Button, idx: int = 0):
        label = btn.get_label()
        if not label:
            return
        self._play_click_sound()

        if self._custom_input_mode:
            if idx == self.CI_SAVE_SLOT:
                self._confirm_custom_word()
            elif idx == self.CI_CANCEL_SLOT:
                self._close_custom_input_mode()
            return

        if self._app_mode:
            if idx < len(self._app_results):
                self._launch_app(self._app_results[idx])
            self._close_app_mode()
            return

        # Korean: the syllable in the bar isn't in the app yet.  A word
        # replaces it (only the typed part needs erasing); an emoji keeps it.
        pre = self._ko.preedit()
        if pre:
            if idx < len(self._suggestion_is_emoji) and self._suggestion_is_emoji[idx]:
                self._ko_flush()
            else:
                self._ko.reset()
                self.current_word = self.current_word[:len(self.current_word) - len(pre)]
                self._show_preedit()

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
            self._replace_typed_word(expanded, typed=self._typed_prefix())
            self.current_word = ""
            self._ctx_reset()      # a macro can expand to anything
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
            self._replace_typed_word(label, typed=self._typed_prefix())
            self.current_word = ""
            self._ctx_commit(label)
            self._refresh_suggestions()
            return

        # Fuzzy spell-check suggestion — always replace in full (prefix never matches)
        if idx < len(self._suggestion_is_fuzzy) and self._suggestion_is_fuzzy[idx]:
            self._replace_typed_word(self._case_word(label), typed=self._typed_prefix())
            self.current_word = ""
            self._ctx_commit(label)
            self._refresh_suggestions()
            return

        # Normal dictionary word-completion
        cased = self._case_word(label)
        self._replace_typed_word(cased, typed=self._typed_prefix())
        self.current_word = ""
        self._ctx_commit(label)
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

    def _typed_prefix(self) -> str:
        """current_word as the user actually typed it (case restored)."""
        w = self.current_word
        if not w:
            return w
        if self._word_upper:
            return w.upper()
        if self._word_title:
            return w[0].upper() + w[1:]
        return w

    def _case_word(self, word: str) -> str:
        """Casing for a suggestion: the word's natural form ("I'm", "Alex"),
        then whatever the user typed (capital first letter, all caps) or the
        live shift/caps state."""
        if not word:
            return word
        if self.caps_lock:
            return word.upper()
        if word == word.lower():           # custom words keep their own casing
            word = self.predictor.display_form(word)
        typed = len(self.current_word)
        if typed >= 2 and self._word_upper:
            return word.upper()
        if self.shift_active or (typed and self._word_title):
            return word[0].upper() + word[1:]
        return word

    def _update_modifier_visuals(self):
        upper = self.shift_active ^ self.caps_lock
        korean = self._korean_active()
        for letter, btn in self._letter_btns.items():
            if korean:
                btn.set_label(jamo_for(letter, self.shift_active))
            else:
                btn.set_label(letter.upper() if upper else letter.lower())
        if self._ko_btn is not None:
            self._ko_btn.set_label("한" if korean else "A")
            self._ko_btn.set_tooltip_text("Switch to English letters" if korean
                                          else "Switch to Korean (Hangul)")

        # Symbol keys: show shifted label as main when shift is active,
        # otherwise show base char and restore the dim shift-hint sub-label.
        for action, (main_lbl, sub_lbl) in self._symbol_btns.items():
            if self.fn_active and action in FN_MAP:
                main_lbl.set_text(FN_MAP[action].upper())     # 1 → F1
                sub_lbl.set_visible(False)
            elif self.shift_active:
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
        _set_active(self._modifier_btns["fn"],   self.fn_active)
        for action, (btn, label) in self._fn_btns.items():
            btn.set_label(FN_LABELS.get(FN_MAP[action], label) if self.fn_active else label)


# ── Entry point ───────────────────────────────────────────────────────────────

def _selftest_log_paths() -> list:
    """Where --selftest writes its log: next to the exe (if writable), then %TEMP%."""
    import tempfile
    paths = []
    if getattr(sys, "frozen", False):
        paths.append(os.path.join(os.path.dirname(sys.executable), "osk_selftest.log"))
    paths.append(os.path.join(tempfile.gettempdir(), "osk_selftest.log"))
    return paths


def _run_selftest() -> int:
    """Build the full window, spin the GTK main loop ~3 s, return exit code.

    Used by CI to prove the frozen bundle starts (typelibs, DLLs, pixbuf
    loaders, CSS, fonts).  Any exception, including ones raised inside GTK
    callbacks, yields exit code 1 and a traceback in the log file and stderr.
    """
    import traceback
    lines: list = []
    problems: list = []

    def log(msg):
        lines.append(msg)
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass

    def hook(et, ev, tb):
        problems.append("".join(traceback.format_exception(et, ev, tb)))

    sys.excepthook = hook
    code = 1
    try:
        log(f"OSK selftest: version {__version__}, frozen={getattr(sys, 'frozen', False)}, "
            f"python {sys.version.split()[0]}, config dir {CONFIG_DIR}")
        ok, _ = Gtk.init_check([sys.argv[0]])
        if not ok:
            raise RuntimeError("Gtk.init_check failed (no display)")

        # Exercise the GdkPixbuf loader chain (loaders.cache + png loader).
        from gi.repository import GdkPixbuf
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        png = os.path.join(base, "osk.png")
        if os.path.isfile(png):
            pb = GdkPixbuf.Pixbuf.new_from_file(png)
            log(f"pixbuf loaded {pb.get_width()}x{pb.get_height()}")
        else:
            raise RuntimeError(f"osk.png missing at {png}")

        win = OnScreenKeyboard()
        GLib.timeout_add(3000, Gtk.main_quit)
        Gtk.main()
        if not win.get_realized():
            raise RuntimeError("main window was never realized")
        if problems:
            raise RuntimeError("exception(s) raised inside GTK callbacks:\n" + "\n".join(problems))
        log("SELFTEST OK")
        code = 0
    except BaseException:
        log("SELFTEST FAILED")
        log(traceback.format_exc())
        for p in problems:
            log(p)
        code = 1
    for path in _selftest_log_paths():
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            break
        except Exception:
            continue
    return code


def _start_hang_watchdog() -> None:
    """OSK_DEBUG only: if the GTK main loop stalls for >3 s, dump every thread's
    Python stack to osk_debug.log.hang — pinpoints freezes (e.g. a blocked
    user32 call) that leave no other trace."""
    if not _WIN_DEBUG_LOG:
        return
    import faulthandler
    import threading
    try:
        hang_file = open(_WIN_DEBUG_LOG + ".hang", "a", encoding="utf-8")
    except Exception:
        return
    faulthandler.enable(file=hang_file, all_threads=True)   # also catches hard crashes
    beat = [time.monotonic()]

    def _heartbeat():
        beat[0] = time.monotonic()
        return True
    GLib.timeout_add(500, _heartbeat)

    def _watch():
        while True:
            time.sleep(1)
            stalled = time.monotonic() - beat[0]
            if stalled > 3:
                hang_file.write(f"=== main loop stalled {stalled:.1f}s at "
                                f"{time.strftime('%H:%M:%S')} ===\n")
                hang_file.flush()
                faulthandler.dump_traceback(file=hang_file, all_threads=True)
                hang_file.flush()
                beat[0] = time.monotonic() + 30   # one dump per stall, not a flood
    threading.Thread(target=_watch, daemon=True, name="osk-hang-watchdog").start()
    _win_log("watchdog: started")


_instance_keepalive: list = []   # mutex handle / listening socket, process lifetime


# Taskbar / shell windows that briefly take the foreground when the user
# clicks the taskbar; typing should keep going to the app before them.
_WIN_SHELL_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
})


def _win_single_instance_or_exit() -> None:
    """Windows: named mutex; if another instance owns it, broadcast OSK_SHOW_v1
    (its WNDPROC re-shows the keyboard) and exit."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    u32.RegisterWindowMessageW.restype = wintypes.UINT
    u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    u32.PostMessageW.restype = wintypes.BOOL
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 ctypes.c_size_t, ctypes.c_ssize_t]
    msg = u32.RegisterWindowMessageW("OSK_SHOW_v1")
    _win_show_msg[0] = msg
    handle = k32.CreateMutexW(None, False, "Local\\OnScreenKeyboard.Instance")
    err = ctypes.get_last_error()
    if handle and err == 183:   # ERROR_ALREADY_EXISTS
        _win_log("single-instance: already running — asking it to show, exiting")
        u32.PostMessageW(0xFFFF, msg, 0, 0)   # HWND_BROADCAST
        # Fallback that doesn't depend on our WNDPROC subclass: restore the
        # minimized taskbar proxy through Windows' own SC_RESTORE path.
        u32.FindWindowW.restype = wintypes.HWND
        u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u32.IsIconic.argtypes = [wintypes.HWND]
        # GDK's class name, so Windows' own "On-Screen Keyboard" (osk.exe) is never matched
        proxy = u32.FindWindowW("gdkWindowToplevel", "On-Screen Keyboard")
        if proxy and u32.IsIconic(proxy):
            u32.PostMessageW(proxy, 0x0112, 0xF120, 0)   # WM_SYSCOMMAND, SC_RESTORE
        sys.exit(0)
    _instance_keepalive.append(handle)


def _linux_single_instance_or_exit():
    """Linux: UNIX socket in $XDG_RUNTIME_DIR.  A live listener means another
    instance runs: tell it to show itself and exit.  Otherwise (no socket or a
    stale one) become the listener.  Returns the listening socket."""
    import socket
    import tempfile
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
                        f"osk-{os.getuid()}.sock")
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(1.0)
    try:
        c.connect(path)
        c.sendall(b"show\n")
        c.close()
        print("[keyboard] already running — asked it to show itself")
        sys.exit(0)
    except OSError:
        pass          # nobody listening (or stale socket): we take over
    finally:
        c.close()
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    srv.setblocking(False)
    import atexit
    atexit.register(lambda: os.path.exists(path) and os.unlink(path))
    _instance_keepalive.append(srv)
    return srv


def _linux_watch_show_requests(srv, win) -> None:
    def _on_conn(_fd, _cond):
        try:
            conn, _ = srv.accept()
            conn.settimeout(0.2)
            try:
                if conn.recv(16).startswith(b"show"):
                    win._force_restore()
            finally:
                conn.close()
        except OSError:
            pass
        return True
    GLib.io_add_watch(srv.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, _on_conn)


def main():
    if "--selftest" in sys.argv[1:]:
        code = _run_selftest()
        # Skip interpreter teardown: unloading GTK/Win32 hooks can crash on exit
        # and turn a passing selftest into a non-zero exit code.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        os._exit(code)

    srv = None
    if IS_WINDOWS:
        try:
            # Before any window exists: group our windows under the same ID as
            # the Start-menu shortcut (needed for "Pin to taskbar" to stick).
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                ctypes.c_wchar_p(APP_USER_MODEL_ID))
        except Exception as exc:
            _win_log(f"AppUserModelID not set ({exc})")
        try:
            _win_single_instance_or_exit()
        except SystemExit:
            raise
        except Exception as exc:
            _win_log(f"single-instance check failed ({exc}) — continuing")
    else:
        try:
            srv = _linux_single_instance_or_exit()
        except SystemExit:
            raise
        except Exception as exc:
            print(f"[keyboard] single-instance check failed ({exc}) — continuing")

    ok, _ = Gtk.init_check([a for a in sys.argv if a != "--debug"])
    if not ok:
        print("ERROR: Could not connect to a display.")
        print(f"  DISPLAY    = {os.environ.get('DISPLAY', '(not set)')}")
        print(f"  XAUTHORITY = {os.environ.get('XAUTHORITY', '(not set)')}")
        print("Run this from a terminal opened inside your desktop session.")
        sys.exit(1)

    _start_hang_watchdog()
    win = OnScreenKeyboard()
    if getattr(sys, "frozen", False):
        # After a self-update the installer that relaunched us may still be
        # exiting, so wait a minute before clearing old downloads.
        GLib.timeout_add_seconds(60, _cleanup_old_update_downloads)
    if srv is not None:
        _linux_watch_show_requests(srv, win)
    Gtk.main()
    # Every Gtk.main_quit path (quit, update installer, destroy) ends here
    try:
        win._user_model.save()
    except Exception:
        pass


if __name__ == "__main__":
    main()

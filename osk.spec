# osk.spec -- PyInstaller ONEDIR build spec for Windows (MSYS2 MINGW64 Python).
#
# Build from an MSYS2 MINGW64 shell, from the repo root:
#   python3 tools/gen_version_info.py
#   MINGW_PREFIX_WIN="$(cygpath -m "$MINGW_PREFIX")" python3 -m PyInstaller --clean --noconfirm osk.spec
#
# Output: dist/OnScreenKeyboard/OnScreenKeyboard.exe (+ _internal/).
#
# GTK typelibs, DLLs and GdkPixbuf loaders (with a relocatable loaders.cache)
# are collected by PyInstaller's / pyinstaller-hooks-contrib's gi hooks.  This
# spec only adds what those hooks do not: fonts, schemas, icons, the GLib
# spawn helper and the app's own data.  Missing required data fails the build.
import glob
import os
import sys

# MINGW64 python's sys.prefix is the mingw64 root, so it is a safe default.
MINGW = os.path.normpath(os.environ.get("MINGW_PREFIX_WIN") or sys.prefix)
MINGW_BIN = os.path.join(MINGW, "bin")
MINGW_SHARE = os.path.join(MINGW, "share")

if not os.path.isfile(os.path.join(MINGW_BIN, "libgtk-3-0.dll")):
    raise SystemExit(
        f"osk.spec: GTK3 not found under MINGW prefix {MINGW!r} "
        "(expected bin/libgtk-3-0.dll). Set MINGW_PREFIX_WIN to the mingw64 "
        "root, e.g. MINGW_PREFIX_WIN=\"$(cygpath -m \"$MINGW_PREFIX\")\"."
    )


def _need(path):
    if not os.path.exists(path):
        raise SystemExit(f"osk.spec: required build input missing: {path}")
    return path


def _here(name):
    return _need(os.path.join(SPECPATH, name))


datas = [
    (_here("style.css"), "."),
    (_here("words.txt"), "."),
    (_here("VERSION"), "."),
    (_here("osk.png"), "."),   # runtime GTK window icon
    (_here("osk.ico"), "."),
    # Fontconfig config (rthook_gtk.py sets FONTCONFIG_PATH / FONTCONFIG_FILE)
    (_need(os.path.join(MINGW, "etc", "fonts")), "etc/fonts"),
    (_need(os.path.join(MINGW_SHARE, "glib-2.0", "schemas", "gschemas.compiled")),
     "share/glib-2.0/schemas"),
    (_need(os.path.join(MINGW_SHARE, "icons", "hicolor")), "share/icons/hicolor"),
    (_need(os.path.join(MINGW_SHARE, "icons", "Adwaita")), "share/icons/Adwaita"),
]

_themes_default = os.path.join(MINGW_SHARE, "themes", "Default")
if os.path.isdir(_themes_default):
    datas.append((_themes_default, "share/themes/Default"))
else:
    print(f"osk.spec: WARNING optional {_themes_default} not found (GTK key theme)")

# GLib spawns helper processes through this exe; it must sit next to libglib.
_helpers = glob.glob(os.path.join(MINGW_BIN, "gspawn-win64-helper*.exe"))
if not _helpers:
    raise SystemExit(f"osk.spec: gspawn-win64-helper*.exe not found in {MINGW_BIN}")
datas += [(h, ".") for h in _helpers]

hiddenimports = [
    "gi", "gi._gi_cairo", "cairo",
    "gi.repository.Gtk", "gi.repository.Gdk", "gi.repository.GLib",
    "gi.repository.Gio", "gi.repository.GObject", "gi.repository.Atk",
    "gi.repository.Pango", "gi.repository.PangoCairo",
    "gi.repository.GdkPixbuf", "gi.repository.cairo",
    "pynput", "pynput.keyboard", "pynput._util", "pynput._util.win32",
    "pynput.keyboard._win32", "pynput.mouse._win32",
    "predictor", "emojis",
    "wave", "struct", "bisect", "threading",
    "urllib.request", "urllib.error", "webbrowser", "winsound",
]

a = Analysis(
    [os.path.join(SPECPATH, "keyboard.py")],
    pathex=[SPECPATH],
    binaries=[],            # DLLs come from the gi hooks (dependency analysis)
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[os.path.join(SPECPATH, "rthook_gtk.py")],
    excludes=["tkinter", "matplotlib", "numpy", "Xlib", "Atspi"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# ONEDIR: GTK DLLs live next to the exe in _internal/, nothing is extracted to
# %TEMP% at launch, so start-up is fast and AV/SmartScreen friendlier.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OnScreenKeyboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX corrupts GTK DLLs and trips antivirus
    console=False,
    icon=os.path.join(SPECPATH, "osk.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OnScreenKeyboard",
)

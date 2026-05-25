# osk.spec — PyInstaller build spec for Windows
# Build from MSYS2 MINGW64 shell:
#   pyinstaller osk.spec
import os, glob

block_cipher = None

# MINGW_PREFIX_WIN is set by the CI workflow to the Windows-style path.
# Locally, fall back to the standard MSYS2 install location.
MINGW = os.environ.get("MINGW_PREFIX_WIN", r"C:\msys64\mingw64")
MINGW_BIN   = os.path.join(MINGW, "bin")
MINGW_LIB   = os.path.join(MINGW, "lib")
MINGW_SHARE = os.path.join(MINGW, "share")

gtk_dlls = [
    "libgtk-3-0.dll", "libgdk-3-0.dll",
    "libglib-2.0-0.dll", "libgobject-2.0-0.dll", "libgio-2.0-0.dll",
    "libgmodule-2.0-0.dll",
    "libatk-1.0-0.dll",                            # required GTK3 dep, omitting causes load failure
    "libpango-1.0-0.dll", "libpangocairo-1.0-0.dll", "libpangowin32-1.0-0.dll",
    "libcairo-2.dll", "libcairo-gobject-2.dll",
    "libgdk_pixbuf-2.0-0.dll",
    "libharfbuzz-0.dll",
    "libfontconfig-1.dll", "libfreetype-6.dll",
    "libepoxy-0.dll", "libffi-8.dll",
    "libpcre2-8-0.dll", "libintl-8.dll",
    "libpixman-1-0.dll", "libpng16-16.dll",
    "zlib1.dll", "libiconv-2.dll",
]

# GTK DLLs must NOT be UPX-compressed — UPX corrupts their import tables.
_upx_exclude = [
    "libgtk-3-0.dll", "libgdk-3-0.dll", "libglib-2.0-0.dll",
    "libgobject-2.0-0.dll", "libgio-2.0-0.dll", "libgmodule-2.0-0.dll",
    "libatk-1.0-0.dll",
    "libpango-1.0-0.dll", "libpangocairo-1.0-0.dll", "libpangowin32-1.0-0.dll",
    "libcairo-2.dll", "libcairo-gobject-2.dll",
    "libgdk_pixbuf-2.0-0.dll", "libharfbuzz-0.dll", "libepoxy-0.dll",
]
binaries = [(os.path.join(MINGW_BIN, dll), ".")
            for dll in gtk_dlls
            if os.path.exists(os.path.join(MINGW_BIN, dll))]

pixbuf_loaders_dir = os.path.join(MINGW_LIB, "gdk-pixbuf-2.0", "2.10.0", "loaders")
if os.path.isdir(pixbuf_loaders_dir):
    for dll in glob.glob(os.path.join(pixbuf_loaders_dir, "*.dll")):
        binaries.append((dll, "gdk-pixbuf-2.0/2.10.0/loaders"))

datas = [
    ("style.css",  "."),
    ("words.txt",  "."),
    ("VERSION",    "."),
]
for src, dest in [
    (os.path.join(MINGW_SHARE, "glib-2.0", "schemas"), "share/glib-2.0/schemas"),
    (os.path.join(MINGW_SHARE, "icons",  "hicolor"),   "share/icons/hicolor"),
    (os.path.join(MINGW_SHARE, "icons",  "Adwaita"),   "share/icons/Adwaita"),
    (os.path.join(MINGW_SHARE, "themes", "Default"),   "share/themes/Default"),
]:
    if os.path.exists(src):
        datas.append((src, dest))

loaders_cache = os.path.join(MINGW_LIB, "gdk-pixbuf-2.0", "2.10.0", "loaders.cache")
if os.path.exists(loaders_cache):
    datas.append((loaders_cache, "gdk-pixbuf-2.0/2.10.0"))
# IMPORTANT: Before running `pyinstaller osk.spec`, regenerate loaders.cache so
# it contains paths relative to the MSYS2 bin dir (not absolute build-machine paths).
# Run this once from the MSYS2 MINGW64 shell before building:
#
#   gdk-pixbuf-query-loaders > $MINGW_PREFIX/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache
#
# The rthook sets GDK_PIXBUF_MODULE_FILE to point at the bundled copy inside
# sys._MEIPASS at runtime, so GTK finds it correctly on any machine.

hiddenimports = [
    "gi", "gi.repository.Gtk", "gi.repository.Gdk", "gi.repository.GLib",
    "gi.repository.Gio", "gi.repository.GObject",
    "gi.repository.Pango", "gi.repository.PangoCairo", "gi.repository.GdkPixbuf",
    "pynput", "pynput.keyboard", "pynput._util", "pynput._util.win32",
    "predictor", "emojis",
    "wave", "struct", "bisect", "threading",
    "urllib.request", "urllib.error", "webbrowser", "winsound",
]

a = Analysis(
    ["keyboard.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["."],
    runtime_hooks=["rthook_gtk.py"],
    excludes=["tkinter", "matplotlib", "numpy", "Xlib", "Atspi"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --onefile: everything bundled into a single exe.
# On first launch GTK DLLs extract to %TEMP%\MEIxxxxxx — takes ~5-10s.
# Subsequent launches reuse the cached extraction and are faster.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="OnScreenKeyboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=_upx_exclude,
    runtime_tmpdir=None,
    console=False,
    icon=None,
)

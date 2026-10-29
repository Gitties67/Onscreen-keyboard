"""PyInstaller runtime hook: point GTK/GLib/fontconfig at the bundled data.

PyInstaller's built-in gi hooks (pyinstaller-hooks-contrib) already set
GI_TYPELIB_PATH / GDK_PIXBUF_MODULE_FILE / schema paths for the pieces they
collect.  Everything here uses setdefault (or an existence check) so that,
whichever order the hooks run in, we never override theirs -- we only fill in
what they do not provide (fontconfig, GTK prefixes, XDG data dirs).
"""
import glob
import os
import sys

base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))


def _first_existing(*parts_list):
    for parts in parts_list:
        p = os.path.join(base, *parts)
        if os.path.exists(p):
            return p
    return None


# GdkPixbuf: the contrib hook generates a relocatable loaders.cache and sets
# the variable itself.  Only fall back to a bundled cache if it did not.
if "GDK_PIXBUF_MODULE_FILE" not in os.environ:
    _cache = _first_existing(("lib", "gdk-pixbuf-2.0", "2.10.0", "loaders.cache"),
                             ("gdk-pixbuf-2.0", "2.10.0", "loaders.cache"))
    if not _cache:
        _found = glob.glob(os.path.join(base, "**", "loaders.cache"), recursive=True)
        _cache = _found[0] if _found else None
    if _cache:
        os.environ["GDK_PIXBUF_MODULE_FILE"] = _cache

_schemas = _first_existing(("share", "glib-2.0", "schemas"))
if _schemas:
    os.environ.setdefault("GSETTINGS_SCHEMA_DIR", _schemas)

os.environ.setdefault("GTK_DATA_PREFIX", base)
os.environ.setdefault("GTK_EXE_PREFIX", base)
os.environ.setdefault("XDG_DATA_DIRS", os.path.join(base, "share"))

# Fontconfig: without a config GTK on Windows logs errors and may show no text.
_fonts = os.path.join(base, "etc", "fonts")
if os.path.isdir(_fonts):
    os.environ.setdefault("FONTCONFIG_PATH", _fonts)
    _conf = os.path.join(_fonts, "fonts.conf")
    if os.path.isfile(_conf):
        os.environ.setdefault("FONTCONFIG_FILE", _conf)

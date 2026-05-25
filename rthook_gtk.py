import os, sys

base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

os.environ.setdefault("GSETTINGS_SCHEMA_DIR",
                      os.path.join(base, "share", "glib-2.0", "schemas"))
os.environ.setdefault("GDK_PIXBUF_MODULE_FILE",
                      os.path.join(base, "gdk-pixbuf-2.0", "2.10.0", "loaders.cache"))
os.environ.setdefault("GTK_DATA_PREFIX", base)
os.environ.setdefault("GTK_EXE_PREFIX", base)
os.environ.setdefault("GTK_PATH", base)
os.environ.setdefault("XDG_DATA_DIRS", os.path.join(base, "share"))
os.environ.setdefault("FONTCONFIG_PATH", os.path.join(base, "etc", "fonts"))

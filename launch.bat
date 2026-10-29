@echo off
:: On-Screen Keyboard -- run from SOURCE on Windows (developers only).
:: Most users should install OnScreenKeyboard-Setup-<version>.exe from the
:: GitHub Releases page instead; no Python or GTK needed.
:: Requires: GTK3 + PyGObject + pynput, i.e. run this from an MSYS2 MINGW64 Python
:: (see README.md). A plain python.org Python will NOT work.

cd /d "%~dp0"
python keyboard.py %*

@echo off
:: On-Screen Keyboard — Windows launcher
:: Requires: Python 3.10+, GTK3 runtime, pynput
:: See README.md for full installation instructions.

cd /d "%~dp0"
python keyboard.py %*

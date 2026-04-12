#!/bin/bash
# install.sh — One-time setup to make the On-Screen Keyboard a proper desktop app.
# Run this once from a terminal; after that you can launch the keyboard from your
# app menu or system tray without ever opening a terminal again.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_SH="$SCRIPT_DIR/launch.sh"

# ── 1. Make scripts executable ────────────────────────────────────────────────
chmod +x "$LAUNCH_SH"
echo "✓ launch.sh is executable"

# ── 2. App menu entry (.local/share/applications) ─────────────────────────────
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/onscreen-keyboard.desktop" << EOF
[Desktop Entry]
Name=On-Screen Keyboard
Comment=Custom GTK on-screen keyboard
Exec=bash $LAUNCH_SH
Icon=input-keyboard
Type=Application
Categories=Utility;Accessibility;
Terminal=false
StartupNotify=false
NoDisplay=false
EOF
chmod +x "$APPS_DIR/onscreen-keyboard.desktop"
echo "✓ App menu entry created"

# ── 3. Autostart on login ──────────────────────────────────────────────────────
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/onscreen-keyboard.desktop" << EOF
[Desktop Entry]
Type=Application
Name=On-Screen Keyboard
Exec=bash $LAUNCH_SH
Terminal=false
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
echo "✓ Autostart on login configured"

# ── 4. Refresh desktop database ───────────────────────────────────────────────
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APPS_DIR"
    echo "✓ Desktop database refreshed"
fi

echo ""
echo "Installation complete."
echo "  • The keyboard now appears in your app menu as 'On-Screen Keyboard'"
echo "  • It will start automatically when you log in"
echo "  • Log file (when launched without terminal): /tmp/onscreen_keyboard.log"

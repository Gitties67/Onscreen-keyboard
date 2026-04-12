#!/bin/bash
# Launch the on-screen keyboard with a full desktop session environment.

# ── Find active X display ──────────────────────────────────────────────────────
DISPLAY_NUM=""
for lock in /tmp/.X*-lock; do
    [ -f "$lock" ] || continue
    num="${lock#/tmp/.X}"
    num="${num%-lock}"
    DISPLAY_NUM=":${num}"
    break
done
[ -z "$DISPLAY_NUM" ] && DISPLAY_NUM=":0"
export DISPLAY="$DISPLAY_NUM"

# ── Find Xauthority ────────────────────────────────────────────────────────────
for f in "/home/bigboy/.Xauthority" "/run/user/$(id -u)/gdm/Xauthority"; do
    [ -f "$f" ] && export XAUTHORITY="$f" && break
done

# ── Inherit session env vars from the running Cinnamon process ────────────────
for SESSION_PROC in cinnamon-session cinnamon xfce4-session mate-session; do
    SESS_PID=$(pgrep -u "$(id -u)" "$SESSION_PROC" 2>/dev/null | head -1)
    [ -n "$SESS_PID" ] || continue
    while IFS= read -r -d '' entry; do
        key="${entry%%=*}"
        val="${entry#*=}"
        case "$key" in
            DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR|XAUTHORITY)
                [ -z "${!key}" ] && export "$key=$val"
                ;;
        esac
    done < "/proc/$SESS_PID/environ" 2>/dev/null
    break
done

# ── Get the live AT-SPI2 bus address from the X11 root window property ────────
# at-spi2-registryd stores the current socket path here; reading it avoids
# connecting to a stale socket from a previous daemon instance.
if command -v xprop &>/dev/null; then
    ATSPI_BUS=$(xprop -root AT_SPI_BUS 2>/dev/null | grep -o '"[^"]*"' | tr -d '"')
    if [ -n "$ATSPI_BUS" ]; then
        export AT_SPI_BUS_ADDRESS="$ATSPI_BUS"
    fi
fi

# If AT-SPI2 bus still not found, try starting the daemon
if [ -z "$AT_SPI_BUS_ADDRESS" ]; then
    if ! pgrep -u "$(id -u)" at-spi2-registryd &>/dev/null; then
        /usr/lib/at-spi2-core/at-spi-bus-launcher --launch-immediately &
        sleep 0.5
        ATSPI_BUS=$(xprop -root AT_SPI_BUS 2>/dev/null | grep -o '"[^"]*"' | tr -d '"')
        [ -n "$ATSPI_BUS" ] && export AT_SPI_BUS_ADDRESS="$ATSPI_BUS"
    fi
fi

echo "Launching keyboard on DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"
echo "  DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-<not set>}"
echo "  AT_SPI_BUS_ADDRESS=${AT_SPI_BUS_ADDRESS:-<not set>}"
exec python3 /home/bigboy/onscreen_keyboard/keyboard.py

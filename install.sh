#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_DIR/applications"
LIB_DIR="$DATA_DIR/uxplay-kitty-audio"
ICON_DIR="$DATA_DIR/icons/hicolor/1024x1024/apps"
ICON_PATH="$ICON_DIR/uxplay-kitty-audio.png"

if command -v kitty >/dev/null 2>&1; then
    KITTY_BIN="$(command -v kitty)"
elif [[ -x "$HOME/.local/kitty.app/bin/kitty" ]]; then
    KITTY_BIN="$HOME/.local/kitty.app/bin/kitty"
else
    echo "Error: Kitty was not found." >&2
    exit 1
fi

mkdir -p "$BIN_DIR" "$APP_DIR" "$LIB_DIR" "$ICON_DIR"

install -m 755 "$ROOT_DIR/bin/uxplay-kitty-audio" "$BIN_DIR/uxplay-kitty-audio"
install -m 644 \
    "$ROOT_DIR/assets/uxplay-kitty-audio-sound.png" \
    "$ICON_PATH"
sed \
    -e "s|^TryExec=kitty$|TryExec=$KITTY_BIN|" \
    -e "s|^Exec=kitty |Exec=\"$KITTY_BIN\" |" \
    -e "s| uxplay-kitty-audio$| \"$BIN_DIR/uxplay-kitty-audio\"|" \
    -e "s|^Icon=uxplay-kitty-audio$|Icon=$ICON_PATH|" \
    "$ROOT_DIR/desktop/uxplay-kitty-audio.desktop" \
    > "$APP_DIR/uxplay-kitty-audio.desktop"
chmod 644 "$APP_DIR/uxplay-kitty-audio.desktop"
install -m 755 "$ROOT_DIR/lib/discord-rpc.py" "$LIB_DIR/discord-rpc.py"

# Remove launcher files left by versions published under the old name.
rm -f \
    "$BIN_DIR/uxplay-kitty" \
    "$BIN_DIR/uxplay-audio-tui" \
    "$APP_DIR/uxplay-kitty.desktop" \
    "$APP_DIR/airplay-audio.desktop"

update-desktop-database "$APP_DIR" 2>/dev/null || true
gtk-update-icon-cache "$DATA_DIR/icons/hicolor" 2>/dev/null || true

echo "Uka installed."
echo "Run: uxplay-kitty-audio from Kitty"
echo "Or launch 'Uka' from your application menu."

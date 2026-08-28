#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"

mkdir -p "$BIN_DIR" "$APP_DIR"

install -m 755 "$ROOT_DIR/bin/uxplay-kitty-audio" "$BIN_DIR/uxplay-kitty-audio"
install -m 644 "$ROOT_DIR/desktop/uxplay-kitty-audio.desktop" "$APP_DIR/uxplay-kitty-audio.desktop"

# Remove launcher files left by versions published under the old name.
rm -f \
    "$BIN_DIR/uxplay-kitty" \
    "$BIN_DIR/uxplay-audio-tui" \
    "$APP_DIR/uxplay-kitty.desktop" \
    "$APP_DIR/airplay-audio.desktop"

update-desktop-database "$APP_DIR" 2>/dev/null || true

echo "UxPlay Kitty Audio installed."
echo "Run: uxplay-kitty-audio from Kitty"
echo "Or launch 'UxPlay Kitty Audio' from your application menu."

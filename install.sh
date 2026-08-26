#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"

mkdir -p "$BIN_DIR" "$APP_DIR"

install -m 755 "$ROOT_DIR/bin/uxplay-audio-tui" "$BIN_DIR/uxplay-audio-tui"
install -m 644 "$ROOT_DIR/desktop/airplay-audio.desktop" "$APP_DIR/airplay-audio.desktop"

update-desktop-database "$APP_DIR" 2>/dev/null || true

echo "UxPlay Audio TUI installed."
echo "Run: uxplay-audio-tui"
echo "Or launch 'AirPlay Audio' from your application menu."

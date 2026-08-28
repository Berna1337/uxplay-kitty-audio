#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"

rm -f \
    "$HOME/.local/bin/uxplay-kitty-audio" \
    "$HOME/.local/bin/uxplay-kitty" \
    "$HOME/.local/bin/uxplay-audio-tui"

rm -f \
    "$DATA_DIR/applications/uxplay-kitty-audio.desktop" \
    "$DATA_DIR/applications/uxplay-kitty.desktop" \
    "$DATA_DIR/applications/airplay-audio.desktop"

rm -f \
    "$DATA_DIR/uxplay-kitty-audio/discord-rpc.py" \
    "$DATA_DIR/uxplay-kitty-audio/playback-state.py" \
    "$DATA_DIR/icons/hicolor/1024x1024/apps/uxplay-kitty-audio.png"

rmdir "$DATA_DIR/uxplay-kitty-audio" 2>/dev/null || true

rm -rf \
    "${XDG_CACHE_HOME:-$HOME/.cache}/uxplay-kitty-audio" \
    "${XDG_CACHE_HOME:-$HOME/.cache}/uxplay-kitty" \
    "${XDG_CACHE_HOME:-$HOME/.cache}/uxplay-audio-tui" \
    "$CONFIG_DIR/uxplay-kitty-audio"

update-desktop-database "$DATA_DIR/applications" 2>/dev/null || true
gtk-update-icon-cache "$DATA_DIR/icons/hicolor" 2>/dev/null || true

echo "Uka removed."

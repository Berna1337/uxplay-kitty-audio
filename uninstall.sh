#!/usr/bin/env bash
set -euo pipefail

rm -f "$HOME/.local/bin/uxplay-audio-tui"
rm -f "$HOME/.local/share/applications/airplay-audio.desktop"
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/uxplay-audio-tui"

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "UxPlay Audio TUI removed."

# UxPlay Audio TUI

A lightweight terminal UI for **UxPlay's audio receiver**, providing a music-player-style interface for AirPlay audio on Linux.

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/80c846e5-50cc-4d75-9b51-36ba90e0c6e1" />

## Features

- AirPlay audio reception through UxPlay
- Track title, artist, album and genre
- UTF-8 metadata support, including accented and special characters
- Album artwork in terminals supporting the Kitty graphics protocol
- Graceful fallback when inline artwork is unavailable
- Playback progress and duration
- Negotiated stream information such as sample rate, bit depth, codec and channels
- Real-time Cava audio visualizer at up to 60 FPS
- Cached playback values to reduce UI glitches
- Desktop launcher named **AirPlay Audio**

## Requirements

### Required

- Bash
- UxPlay
- `iconv`
- standard GNU/Linux utilities (`awk`, `sed`, `grep`, `stat`, `tail`, `tput`)

### Optional

- **Cava** — real-time audio visualizer
- **Kitty / kitten** — inline album artwork

The TUI itself can run in other terminals. Inline artwork currently requires support for the Kitty graphics protocol.

## Fedora

For the main dependencies:

```bash
sudo dnf install uxplay cava
```

Kitty can be installed separately if you want inline album artwork.

## Install

Clone or download the repository, then:

```bash
chmod +x install.sh uninstall.sh
./install.sh
```

You can then run:

```bash
uxplay-audio-tui
```

or search for **AirPlay Audio** in your desktop application launcher.

## Uninstall

```bash
./uninstall.sh
```

## How it works

The project starts UxPlay in audio mode with debug logging, cover-art output and metadata output. The TUI reads those outputs to render the current track and negotiated audio stream.

UxPlay remains the AirPlay receiver; this project is only a terminal frontend around its audio functionality.

## Notes

The stream-quality line displays what UxPlay actually reports receiving. It does not infer Hi-Res or bit depth from the source track.

Metadata is handled as UTF-8 and each metadata update is treated as a complete snapshot, preventing album/genre values from a previous track remaining on screen.

## License

MIT

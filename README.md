# UxPlay Kitty Audio

A Kitty-native AirPlay audio receiver for Linux, powered by UxPlay—with album artwork, adaptive colors, playback metadata and a real-time audio visualizer.

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/80c846e5-50cc-4d75-9b51-36ba90e0c6e1" />

## Features

- AirPlay audio reception through UxPlay
- Track title, artist, album and genre
- Connected AirPlay device display
- UTF-8 metadata support, including accented and special characters
- Album artwork rendered through Kitty's graphics protocol
- Accent colors derived automatically from the current album artwork
- Centered playback timeline with progress and duration
- Negotiated stream information such as sample rate, bit depth, codec and channels
- Real-time, sub-cell Cava audio visualizer at up to 60 FPS
- Cached playback values to reduce UI glitches
- Dedicated Kitty desktop launcher

## Requirements

- Bash
- UxPlay
- Kitty, including the `kitten` helper
- Cava
- ImageMagick (`magick`)
- PulseAudio tools (`pactl`) and a PulseAudio-compatible audio service
- `iconv`
- standard GNU/Linux utilities (`awk`, `sed`, `grep`, `stat`, `tail`, `tput`)

UxPlay Kitty Audio intentionally runs only inside Kitty. When launched from another
terminal, it exits with a short message explaining the requirement.

## Fedora

Install the complete package dependency set:

```bash
sudo dnf install uxplay kitty cava ImageMagick pulseaudio-utils
```

## Install

Clone or download the repository, then:

```bash
chmod +x install.sh uninstall.sh
./install.sh
```

You can then run:

```bash
uxplay-kitty-audio
```

or search for **UxPlay Kitty Audio** in your desktop application launcher. The
launcher opens the application directly in Kitty.

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

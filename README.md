# Uka

A friendly Kitty-native AirPlay audio visualizer for Linux, powered by UxPlay—with
album artwork, adaptive colors, playback metadata and Discord Rich Presence.

<img width="2560" height="1440" alt="image" src="https://github.com/user-attachments/assets/80c846e5-50cc-4d75-9b51-36ba90e0c6e1" />

## Features

- AirPlay audio reception through UxPlay
- Track title, artist, album and genre
- Connected AirPlay device display
- UTF-8 metadata support, including accented and special characters
- Album artwork rendered through Kitty's graphics protocol
- Accent colors derived automatically from the current album artwork
- Centered playback timeline with progress and duration
- Automatic live-stream detection without a misleading fixed timeline
- Negotiated stream information such as sample rate, bit depth, codec and channels
- Real-time, sub-cell Cava audio visualizer at up to 60 FPS
- Discord Rich Presence with track, artist, album, dynamic cover art and playback time
- Ordered playback-state engine with atomic TUI and Discord updates
- Dedicated Kitty desktop launcher

## Requirements

- Bash
- UxPlay
- Kitty, including the `kitten` helper
- Cava
- ImageMagick (`magick`)
- PulseAudio tools (`pactl`) and a PulseAudio-compatible audio service
- Python 3
- Discord desktop client
- standard GNU/Linux utilities (`awk`, `sed`, `grep`, `stat`, `tail`, `tput`)

Uka intentionally runs only inside Kitty. When launched from another
terminal, it exits with a short message explaining the requirement.

## Fedora

Install the complete package dependency set:

```bash
sudo dnf install uxplay kitty cava ImageMagick pulseaudio-utils python3
```

Discord must also be installed for Rich Presence. It can be started before or
after Uka; the integration reconnects automatically. It uses the public Uka
application ID and does not require a bot token, client secret or account
authorization.

The Discord application uses two uploaded Rich Presence assets:

- [`uxplay-kitty-audio`](assets/uxplay-kitty-audio.png) for music when no
  matched cover is available
- [`uxplay-kitty-audio-sound`](assets/uxplay-kitty-audio-sound.png) for
  broadcasts and other AirPlay audio with sparse metadata

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

or search for **Uka** in your desktop application launcher. The
launcher opens the application directly in Kitty.

Press `M` while Uka is running to open its settings panel. The Discord Rich
Presence switch shows its current state and can be changed with `Enter` or
`Space`; press `M` or `Esc` to return to the visualizer. Uka remembers the
setting between launches and enables Discord Rich Presence by default.

## Uninstall

```bash
./uninstall.sh
```

## How it works

The project starts UxPlay in audio mode with debug logging, cover-art output and
metadata output. A bundled playback-state engine consumes UxPlay's events in
their original order and publishes one atomic snapshot containing the content
identity, metadata, playback kind and timeline. Both the TUI and the bundled
Discord RPC helper consume that same snapshot, so a source change cannot mix new
metadata with timing left over from the previous source.

UxPlay remains the AirPlay receiver; this project is only a terminal frontend
around its audio functionality.

## Notes

The stream-quality line displays what UxPlay actually reports receiving. It does
not infer Hi-Res or bit depth from the source track.

Metadata is handled as UTF-8, terminal control characters are removed, and each
metadata update is treated as a complete snapshot, preventing album or genre
values from a previous track remaining on screen.

Some UxPlay builds omit the decoded `Title` line for Apple Music single
releases even though the title is present in AirPlay's raw `minm` field. Uka
decodes that field directly and, when raw data is unavailable, can recover the
release title from Apple Music's `Title - Single` album convention.

Discord Rich Presence shares the current title, artist, album and playback timing
with Discord while music is playing. Album covers are matched through MusicBrainz
and the community-maintained Cover Art Archive first. Regional and newer releases
are resolved through Deezer, then Apple Music's artist catalog for exact editions;
an exact artist-and-track match may use artwork from an alternate Deezer release
as a final fallback. Provider-sourced artwork links back to the matching release,
successful matches are cached locally and the retro vinyl asset is used when no
confident match exists. The presence is cleared when Uka exits.

While Rich Presence is enabled, the Discord connection is checked every five
seconds. Discord may be opened or restarted after Uka; the latest activity is
published automatically as soon as its local RPC socket becomes available
again.

AirPlay content missing identifying metadata, along with recognized non-music
sources such as television audio and radio programs, skips music-catalog lookup
and uses the dedicated sound asset instead of the music fallback. A finite track
with an artist and either a title or album is treated as music; this preserves
music presentation when UxPlay advertises a title field but omits its decoded
value. For sparse live content, Discord keeps the title in place and rotates its
status every ten seconds between the live indicator and any available artist
and album values; empty and duplicate values are skipped.

Finite media is identified from the duration declared in its AirPlay metadata.
UxPlay does not repeat that duration in every metadata event, so Uka remembers
it by AirPlay's persistent content ID for later updates and replays. For unknown
durationless content, Uka recognizes a live stream when its progress window
rolls or rebases as one unit; a source that supplies no progress also settles to
live instead of leaving the timeline blank. This avoids title, artist and
provider-specific guesses. Uka replaces the progress bar with a `LIVE`
indicator and omits playback timestamps from Discord Rich Presence.

Title, artist, album, persistent content ID, declared duration and timing are
owned by one ordered state machine. Every identity change starts a new content
generation and invalidates the previous timeline before another snapshot can be
published. Atomic file replacement then gives the TUI and Discord either the
old generation or the new one, never a partially updated mixture.

## License

MIT

#!/usr/bin/env python3
"""Ordered UxPlay playback-state engine for Uka."""

from __future__ import annotations

import argparse
import os
import re
import select
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RTP_RATE = 44100
RTP_MODULUS = 1 << 32
RTP_HALF_RANGE = RTP_MODULUS // 2
LIVE_FALLBACK_SECONDS = 1.5
PUBLISH_INTERVAL_SECONDS = 0.2
RAW_METADATA_LIMIT = 64 * 1024

METADATA_HEADER = "Audio Metadata"
PROGRESS_LINE = re.compile(r"^progress:\s*(\d+)/(\d+)/(\d+)")
RTP_TIME = re.compile(r"rtp_time=(\d+)")
STREAM_FORMAT = re.compile(
    r"format\s+(?:ALAC|AAC-ELD|AAC|LPCM|PCM)\s+(\d+)(?:/\d+)?/\d+"
)
HEX_BYTES = re.compile(r"[0-9A-Fa-f]{2}")
METADATA_FIELD = re.compile(r"^(Album|Artist|Genre|Title):\s*(.*)$")
DMAP_TAG = re.compile(r"^\d+:\s+dmap_tag\s+\[[^]]+\]")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
SINGLE_RELEASE_SUFFIX = re.compile(r"\s+[-–—]\s+single\s*$", re.IGNORECASE)

DMAP_TEXT_FIELDS = {
    b"asal": "Album",
    b"asar": "Artist",
    b"asgn": "Genre",
    b"minm": "Title",
}


@dataclass(frozen=True)
class Progress:
    start: int
    current: int
    end: int


def rtp_distance(start: int, end: int) -> int:
    return (end - start) % RTP_MODULUS


def rtp_signed_delta(previous: int, current: int) -> int:
    difference = rtp_distance(previous, current)
    if difference >= RTP_HALF_RANGE:
        difference -= RTP_MODULUS
    return difference


def valid_progress(progress: Progress) -> bool:
    span = rtp_distance(progress.start, progress.end)
    offset = rtp_distance(progress.start, progress.current)
    return 0 < span < RTP_HALF_RANGE and offset <= span


def parse_hex_dump_line(line: str) -> bytes | None:
    tokens = line.strip().split()
    if not tokens or not all(HEX_BYTES.fullmatch(token) for token in tokens):
        return None
    return bytes.fromhex("".join(tokens))


def parse_dmap_listing(
    raw: bytes,
) -> tuple[dict[str, str], str, int | None]:
    best: tuple[dict[str, str], str, int | None] = ({}, "", None)
    best_score = 0
    search_from = 0

    while True:
        listing_start = raw.find(b"mlit", search_from)
        if listing_start < 0 or listing_start + 8 > len(raw):
            break
        listing_length = int.from_bytes(
            raw[listing_start + 4:listing_start + 8], "big"
        )
        payload_start = listing_start + 8
        payload_end = payload_start + listing_length
        search_from = listing_start + 1
        if payload_end > len(raw):
            continue

        fields: dict[str, str] = {}
        persistent_id = ""
        duration_ms: int | None = None
        offset = payload_start
        valid = True
        while offset < payload_end:
            if offset + 8 > payload_end:
                valid = False
                break
            tag = raw[offset:offset + 4]
            value_length = int.from_bytes(raw[offset + 4:offset + 8], "big")
            value_start = offset + 8
            value_end = value_start + value_length
            if value_end > payload_end:
                valid = False
                break
            value = raw[value_start:value_end]
            if tag in DMAP_TEXT_FIELDS:
                fields[DMAP_TEXT_FIELDS[tag]] = value.decode(
                    "utf-8", errors="replace"
                )
            elif tag == b"mper" and value:
                persistent_id = value.hex()
            elif tag == b"astm" and value:
                duration_ms = int.from_bytes(value, "big")
            offset = value_end

        if not valid:
            continue
        score = len(fields) + bool(persistent_id) + bool(duration_ms)
        if score > best_score:
            best = fields, persistent_id, duration_ms
            best_score = score

    return best


class PlaybackState:
    def __init__(self) -> None:
        self.title = ""
        self.artist = ""
        self.album = ""
        self.genre = ""
        self.persistent_id = ""
        self.known_durations: dict[str, int] = {}
        self.declared_duration_ms: int | None = None
        self.progress: Progress | None = None
        self.rtp_time = 0
        self.stream_rate = DEFAULT_RTP_RATE
        self.kind = "pending"
        self.generation = 0
        self.timeline_revision = 0
        self.content_changed_at = 0.0
        self.initialized = False
        self.dirty = True

        self.in_metadata = False
        self.section_fields: dict[str, str] = {}
        self.section_persistent_id = ""
        self.section_duration_ms: int | None = None
        self.expect_persistent_id = False
        self.expect_duration = False
        self.pending_hex_dump = bytearray()

    def start_metadata(
        self,
        now: float,
        raw_fields: dict[str, str] | None = None,
        raw_persistent_id: str = "",
        raw_duration_ms: int | None = None,
    ) -> None:
        if self.in_metadata:
            self.finish_metadata(now)
        self.in_metadata = True
        self.section_fields = {
            key: self.sanitize_text(value)
            for key, value in (raw_fields or {}).items()
        }
        self.section_persistent_id = raw_persistent_id
        self.section_duration_ms = raw_duration_ms
        self.expect_persistent_id = False
        self.expect_duration = False

    @staticmethod
    def parse_hex_value(line: str) -> str:
        tokens = line.strip().split()
        if not tokens or not all(HEX_BYTES.fullmatch(token) for token in tokens):
            return ""
        return "".join(tokens)

    @staticmethod
    def sanitize_text(value: str) -> str:
        return CONTROL_CHARACTERS.sub("", value)

    def feed_line(self, line: str, now: float) -> None:
        line = line.rstrip("\n")
        stripped = line.strip("\r")

        if METADATA_HEADER in stripped:
            raw_fields, raw_persistent_id, raw_duration_ms = parse_dmap_listing(
                bytes(self.pending_hex_dump)
            )
            self.pending_hex_dump.clear()
            self.start_metadata(
                now,
                raw_fields,
                raw_persistent_id,
                raw_duration_ms,
            )
            return

        if not self.in_metadata:
            raw_bytes = parse_hex_dump_line(stripped)
            if raw_bytes is not None:
                self.pending_hex_dump.extend(raw_bytes)
                if len(self.pending_hex_dump) > RAW_METADATA_LIMIT:
                    del self.pending_hex_dump[:-RAW_METADATA_LIMIT]
                return

        if self.in_metadata:
            if not stripped.strip():
                self.finish_metadata(now)
                return
            if PROGRESS_LINE.match(stripped):
                self.finish_metadata(now)
            else:
                if self.expect_persistent_id:
                    self.section_persistent_id = self.parse_hex_value(stripped)
                    self.expect_persistent_id = False
                    return
                if self.expect_duration:
                    value = self.parse_hex_value(stripped)
                    if value:
                        self.section_duration_ms = int(value, 16)
                    self.expect_duration = False
                    return
                if "dmap_tag [mper]" in stripped:
                    self.expect_persistent_id = True
                    return
                if "dmap_tag [astm]" in stripped:
                    self.expect_duration = True
                    return
                field = METADATA_FIELD.match(stripped)
                if field:
                    self.section_fields[field.group(1)] = self.sanitize_text(
                        field.group(2)
                    )
                    return
                if DMAP_TAG.match(stripped):
                    return
                if self.parse_hex_value(stripped):
                    return

                # UxPlay normally ends this block with a blank line. If its
                # diagnostics are interleaved, close the partial block at the
                # first unrelated line and process that event normally.
                self.finish_metadata(now)

        progress = PROGRESS_LINE.match(stripped)
        if progress:
            self.apply_progress(
                Progress(*(int(progress.group(index)) for index in range(1, 4)))
            )

        rtp = RTP_TIME.search(stripped)
        if rtp:
            self.apply_rtp_time(int(rtp.group(1)))

        stream_format = STREAM_FORMAT.search(stripped)
        if stream_format:
            rate = int(stream_format.group(1))
            if rate > 0 and rate != self.stream_rate:
                self.stream_rate = rate
                self.dirty = True

    def finish_metadata(self, now: float) -> None:
        if not self.in_metadata:
            return
        self.in_metadata = False

        title = self.section_fields.get("Title", "")
        artist = self.section_fields.get("Artist", "")
        album = self.section_fields.get("Album", "")
        genre = self.section_fields.get("Genre", "")
        if not title.strip() and artist.strip():
            inferred_title = SINGLE_RELEASE_SUFFIX.sub("", album).strip()
            if inferred_title != album.strip():
                title = inferred_title
        persistent_id = self.section_persistent_id
        declared_duration_ms = self.section_duration_ms
        if declared_duration_ms is not None and persistent_id.strip("0"):
            self.known_durations[persistent_id] = declared_duration_ms
        elif persistent_id in self.known_durations:
            declared_duration_ms = self.known_durations[persistent_id]

        visible_changed = (title, artist, album) != (
            self.title,
            self.artist,
            self.album,
        )
        id_changed = bool(
            persistent_id
            and self.persistent_id
            and persistent_id != self.persistent_id
        )
        content_changed = not self.initialized or visible_changed or id_changed

        self.title = title
        self.artist = artist
        self.album = album
        self.genre = genre
        if persistent_id:
            self.persistent_id = persistent_id

        if content_changed:
            self.initialized = True
            self.generation += 1
            self.timeline_revision += 1
            self.declared_duration_ms = declared_duration_ms
            self.progress = None
            self.rtp_time = 0
            self.kind = "pending"
            self.content_changed_at = now
        elif declared_duration_ms is not None:
            self.declared_duration_ms = declared_duration_ms
            if self.progress is not None and self.kind != "track":
                self.kind = "track"
                self.timeline_revision += 1

        self.dirty = True

    def rolling_window(self, previous: Progress, current: Progress) -> bool:
        old_span = rtp_distance(previous.start, previous.end)
        new_span = rtp_distance(current.start, current.end)
        start_delta = rtp_signed_delta(previous.start, current.start)
        end_delta = rtp_signed_delta(previous.end, current.end)
        return (
            start_delta != 0
            and end_delta != 0
            and abs(new_span - old_span) <= self.stream_rate
            and abs(start_delta - end_delta) <= self.stream_rate
        )

    def apply_progress(self, progress: Progress) -> None:
        if not self.initialized:
            return
        if not valid_progress(progress):
            return

        previous = self.progress
        self.progress = progress
        self.rtp_time = progress.current
        self.timeline_revision += 1

        if self.declared_duration_ms is not None:
            self.kind = "track"
        elif self.kind != "live" and previous is not None:
            self.kind = "live" if self.rolling_window(previous, progress) else "track"

        self.dirty = True

    def apply_rtp_time(self, rtp_time: int) -> None:
        progress = self.progress
        if (
            progress is not None
            and rtp_distance(progress.start, rtp_time)
            <= rtp_distance(progress.start, progress.end)
            and rtp_time != self.rtp_time
        ):
            self.rtp_time = rtp_time
            self.dirty = True

    def tick(self, now: float) -> None:
        if self.kind != "pending" or not self.initialized:
            return
        if now - self.content_changed_at < LIVE_FALLBACK_SECONDS:
            return

        normalized_title = self.title.strip().casefold()
        has_identity = bool(self.title or self.artist or self.album)
        is_loading = normalized_title in {"loading", "loading...", "loading…"}
        if not has_identity or is_loading:
            return

        if self.declared_duration_ms is not None or self.progress is not None:
            self.kind = "track"
        else:
            self.kind = "live"
        self.timeline_revision += 1
        self.dirty = True

    def position_seconds(self) -> int:
        progress = self.progress
        if self.kind != "track" or progress is None:
            return 0
        current = self.rtp_time
        span = rtp_distance(progress.start, progress.end)
        offset = rtp_distance(progress.start, current)
        if offset > span:
            current = progress.current
            offset = rtp_distance(progress.start, current)
        return max(0, min(offset, span) // self.stream_rate)

    def duration_seconds(self) -> int:
        if self.kind != "track":
            return 0
        if self.declared_duration_ms is not None:
            return max(0, self.declared_duration_ms // 1000)
        if self.progress is None:
            return 0
        return max(
            0,
            rtp_distance(self.progress.start, self.progress.end)
            // self.stream_rate,
        )

    def snapshot_fields(self) -> tuple[str, ...]:
        duration = self.duration_seconds()
        position = min(self.position_seconds(), duration) if duration else 0
        return (
            self.title,
            self.artist,
            self.album,
            self.genre,
            str(position),
            str(duration),
            self.kind,
            str(self.generation),
            str(self.timeline_revision),
        )

    def snapshot_bytes(self) -> bytes:
        return ("\0".join(self.snapshot_fields()) + "\0").encode(
            "utf-8", errors="replace"
        )


def write_snapshot(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def keep_diagnostic_line(line: str, metadata_context: bool) -> bool:
    stripped = line.strip()
    lowered = stripped.casefold()
    if metadata_context or METADATA_HEADER in line:
        return True
    if stripped.startswith("progress:"):
        return True
    if "connection request from " in line or " format " in f" {line} ":
        return True
    return any(word in lowered for word in ("error", "warning", "failed", "uxplay"))


def run(input_fifo: Path, state_file: Path, log_file: Path) -> int:
    state = PlaybackState()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    write_snapshot(state_file, state.snapshot_bytes())
    last_payload = state.snapshot_bytes()
    last_publish = time.monotonic()

    with log_file.open("w", encoding="utf-8", buffering=1) as diagnostic:
        input_fd = os.open(input_fifo, os.O_RDONLY)
        buffer = bytearray()
        try:
            while True:
                readable, _, _ = select.select([input_fd], [], [], 0.1)
                if readable:
                    chunk = os.read(input_fd, 65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        raw, remainder = buffer.split(b"\n", 1)
                        buffer[:] = remainder
                        line = raw.decode("utf-8", errors="replace") + "\n"
                        metadata_context = state.in_metadata
                        state.feed_line(line, time.monotonic())
                        if keep_diagnostic_line(
                            line, metadata_context or state.in_metadata
                        ):
                            diagnostic.write(line)

                now = time.monotonic()
                state.tick(now)
                if state.dirty and now - last_publish >= PUBLISH_INTERVAL_SECONDS:
                    payload = state.snapshot_bytes()
                    if payload != last_payload:
                        write_snapshot(state_file, payload)
                        last_payload = payload
                    state.dirty = False
                    last_publish = now

            if buffer:
                state.feed_line(
                    buffer.decode("utf-8", errors="replace"), time.monotonic()
                )
            state.finish_metadata(time.monotonic())
            state.tick(time.monotonic())
            payload = state.snapshot_bytes()
            if payload != last_payload:
                write_snapshot(state_file, payload)
        finally:
            os.close(input_fd)
    return 0


def feed_metadata(
    state: PlaybackState,
    now: float,
    *,
    persistent_id: str,
    title: str,
    artist: str = "",
    album: str = "",
    genre: str = "",
    duration_ms: int | None = None,
) -> None:
    state.feed_line("====================Audio Metadata==================\n", now)
    state.feed_line("1: dmap_tag [mper], 8\n", now)
    state.feed_line(" ".join(re.findall("..", persistent_id)) + "\n", now)
    if duration_ms is not None:
        state.feed_line("8: dmap_tag [astm], 4\n", now)
        duration_hex = " ".join(
            f"{duration_ms:08x}"[index:index + 2]
            for index in range(0, 8, 2)
        )
        state.feed_line(duration_hex + "\n", now)
    for key, value in (
        ("Album", album),
        ("Artist", artist),
        ("Genre", genre),
        ("Title", title),
    ):
        if value:
            state.feed_line(f"{key}: {value}\n", now)
    state.feed_line("\n", now)


def self_test() -> int:
    def dmap_tag(tag: bytes, value: bytes) -> bytes:
        return tag + len(value).to_bytes(4, "big") + value

    state = PlaybackState()
    feed_metadata(
        state,
        0.0,
        persistent_id="0102030405060708",
        title="Netflix - Episode",
        album="Netflix - S1:E1",
        duration_ms=1_400_000,
    )
    state.feed_line("progress: 100000/200000/61840000\n", 0.1)
    assert state.kind == "track"
    assert state.duration_seconds() == 1400

    # A progress event before the Twitch metadata boundary belongs to Netflix
    # and must be discarded with the old content generation.
    state.feed_line("progress: 110000/210000/61850000\n", 0.2)
    feed_metadata(
        state,
        0.3,
        persistent_id="1112131415161718",
        title="Live Channel",
        artist="Streamer",
        album="Twitch",
    )
    assert state.kind == "pending"
    assert state.progress is None
    state.feed_line("progress: 300000/400000/63000000\n", 0.4)
    state.feed_line("progress: 250000/400000/62950000\n", 0.5)
    assert state.kind == "live"
    assert state.snapshot_fields()[4:7] == ("0", "0", "live")

    # Finite music may rebase both RTP boundaries and must remain a track.
    feed_metadata(
        state,
        1.0,
        persistent_id="2122232425262728",
        title="Topical Solution",
        artist="Duster",
        album="Stratosphere",
        genre="Rock",
        duration_ms=300_893,
    )
    state.feed_line("progress: 1000000/1001000/14269000\n", 1.1)
    state.feed_line("progress: 1042000/1043000/14311000\n", 1.2)
    assert state.kind == "track"
    assert state.duration_seconds() == 300

    # Repeated metadata without astm retains the duration for the same ID.
    feed_metadata(
        state,
        1.3,
        persistent_id="2122232425262728",
        title="Topical Solution",
        artist="Duster",
        album="Stratosphere",
        genre="Rock",
    )
    assert state.declared_duration_ms == 300_893

    # The sender may clear metadata between apps, then replay an item without
    # repeating astm. Restore duration by persistent ID instead of treating
    # the item's rolling AirPlay buffer as a live stream.
    feed_metadata(
        state,
        1.4,
        persistent_id="0000000000000000",
        title="",
    )
    feed_metadata(
        state,
        1.5,
        persistent_id="2122232425262728",
        title="Topical Solution",
        artist="Duster",
        album="Stratosphere",
        genre="Rock",
    )
    state.feed_line("progress: 2000000/2100000/15269000\n", 1.6)
    state.feed_line("progress: 2034000/2134000/15303000\n", 1.7)
    assert state.kind == "track"
    assert state.duration_seconds() == 300

    no_progress = PlaybackState()
    feed_metadata(
        no_progress,
        5.0,
        persistent_id="3132333435363738",
        title="Radio Broadcast",
        album="Station",
    )
    no_progress.tick(6.6)
    assert no_progress.kind == "live"

    loading = PlaybackState()
    feed_metadata(
        loading,
        7.0,
        persistent_id="4142434445464748",
        title="Loading…",
    )
    loading.tick(20.0)
    assert loading.kind == "pending"

    fixed_durationless = PlaybackState()
    feed_metadata(
        fixed_durationless,
        30.0,
        persistent_id="5152535455565758",
        title="Unknown finite item",
        artist="Creator",
    )
    fixed_durationless.feed_line("progress: 500/600/5000\n", 30.1)
    fixed_durationless.tick(31.6)
    assert fixed_durationless.kind == "track"

    interleaved = PlaybackState()
    interleaved.feed_line("====================Audio Metadata==================\n", 40.0)
    interleaved.feed_line("1: dmap_tag [mper], 8\n", 40.0)
    interleaved.feed_line("61 62 63 64 65 66 67 68\n", 40.0)
    interleaved.feed_line("Title: Safe\x1b[2J title\n", 40.0)
    # A progress event also terminates a metadata block if the usual blank
    # separator is missing, without dropping either event.
    interleaved.feed_line("progress: 1000/1500/5000\n", 40.1)
    assert interleaved.title == "Safe[2J title"
    assert interleaved.progress == Progress(1000, 1500, 5000)

    wrapped = PlaybackState()
    feed_metadata(
        wrapped,
        50.0,
        persistent_id="7172737475767778",
        title="Long recording",
    )
    wrapped.apply_progress(
        Progress(RTP_MODULUS - DEFAULT_RTP_RATE, 0, DEFAULT_RTP_RATE)
    )
    wrapped.tick(51.6)
    assert wrapped.kind == "track"
    assert wrapped.position_seconds() == 1
    assert wrapped.duration_seconds() == 2

    raw_single = PlaybackState()
    raw_payload = b"".join(
        (
            dmap_tag(b"mper", bytes.fromhex("00000000000d3f9f")),
            dmap_tag(b"asal", b"I Wanna Be Sedated - Single"),
            dmap_tag(b"asar", b"The Offspring"),
            dmap_tag(b"asgn", b"Rock"),
            dmap_tag(b"minm", b"I Wanna Be Sedated"),
            dmap_tag(b"caps", b"\x01"),
            dmap_tag(b"astm", (145_843).to_bytes(4, "big")),
        )
    )
    raw_listing = dmap_tag(b"mlit", raw_payload)
    for offset in range(0, len(raw_listing), 16):
        raw_single.feed_line(
            raw_listing[offset:offset + 16].hex(" ") + "\n", 60.0
        )
    raw_single.feed_line("metadata packet decoded\n", 60.0)
    raw_single.feed_line(
        "====================Audio Metadata==================\n", 60.0
    )
    # Reproduce UxPlay omitting its decoded Title line even though minm is in
    # the raw Apple Music metadata.
    raw_single.feed_line("Album: I Wanna Be Sedated - Single\n", 60.0)
    raw_single.feed_line("Artist: The Offspring\n", 60.0)
    raw_single.feed_line("\n", 60.0)
    assert raw_single.title == "I Wanna Be Sedated"
    assert raw_single.artist == "The Offspring"
    assert raw_single.persistent_id == "00000000000d3f9f"
    assert raw_single.declared_duration_ms == 145_843

    single_fallback = PlaybackState()
    feed_metadata(
        single_fallback,
        70.0,
        persistent_id="8182838485868788",
        title="",
        artist="Artist",
        album="Release Name - Single",
    )
    assert single_fallback.title == "Release Name"

    assert len(state.snapshot_fields()) == 9
    assert state.snapshot_bytes().count(b"\0") == 9
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-fifo", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.input_fifo is None or args.state_file is None or args.log_file is None:
        parser.error("--input-fifo, --state-file, and --log-file are required")
    return run(args.input_fifo, args.state_file, args.log_file)


if __name__ == "__main__":
    raise SystemExit(main())

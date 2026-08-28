#!/usr/bin/env python3
"""Minimal Discord Rich Presence client for Uka."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import select
import socket
import struct
import sys
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4
FIELD_COUNT = 5
USER_AGENT = "Uka/1.0 (https://github.com/Berna1337/uxplay-kitty-audio)"
ART_CACHE_VERSION = "v2"


def encode_frame(opcode: int, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return struct.pack("<II", opcode, len(body)) + body


def receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("Discord closed the IPC connection")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_frame(connection: socket.socket) -> tuple[int, dict[str, Any]]:
    opcode, length = struct.unpack("<II", receive_exact(connection, 8))
    payload = json.loads(receive_exact(connection, length))
    return opcode, payload


def ipc_paths() -> list[Path]:
    roots: list[str] = []
    for name in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(name)
        if value and value not in roots:
            roots.append(value)
    if "/tmp" not in roots:
        roots.append("/tmp")
    return [Path(root) / f"discord-ipc-{index}" for root in roots for index in range(10)]


def clean_text(value: str, fallback: str) -> str:
    text = " ".join(value.split()) or fallback
    if len(text) == 1:
        text += " "
    return text[:128]


def normalized_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w]+", " ", without_marks).split())


def similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(
        None, normalized_match_text(left), normalized_match_text(right)
    ).ratio()


def release_base_text(value: str) -> str:
    text = normalized_match_text(re.sub(r"\s*[\[(].*?[\])]\s*$", "", value))
    words = text.split()
    while words and words[-1] in {"single", "ep", "deluxe", "edition", "remaster"}:
        words.pop()
    return " ".join(words)


def lucene_phrase(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def is_generic_audio(title: str, artist: str, album: str) -> bool:
    if not any(value.strip() for value in (title, artist, album)):
        return False
    if not title.strip() or not artist.strip():
        return True
    normalized_artist = normalized_match_text(artist)
    normalized_album = normalized_match_text(album)
    if not normalized_artist:
        return True
    if normalized_album and normalized_artist == normalized_album:
        return True
    return normalized_artist in {"radio", "vodafone tv"}


class ArtworkLookup:
    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.cache: dict[str, str] = {}
        self.misses: set[str] = set()
        self.last_musicbrainz_request = 0.0
        self.last_apple_request = 0.0
        self.apple_artist_ids: dict[str, str] = {}
        self.last_message = ""
        self.last_retryable = False
        self.last_link = ""
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.cache = {
                    str(key): str(value)
                    for key, value in loaded.items()
                    if isinstance(value, str)
                }
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    @staticmethod
    def key(artist: str, album: str, title: str = "") -> str:
        # One accurate result can be reused for every track on the same album.
        # Use the title only when the source sends no album metadata.
        release_identity = album if album.strip() else title
        return "\0".join(
            (ART_CACHE_VERSION, normalized_match_text(artist),
             normalized_match_text(release_identity))
        )

    def cached(self, artist: str, album: str, title: str) -> str | None:
        artwork = self.cache.get(self.key(artist, album, title), "")
        return artwork if artwork.startswith("https://") else None

    def cached_link(self, artist: str, album: str, title: str) -> str:
        return self.cache.get("@link\0" + self.key(artist, album, title), "")

    def save(self) -> None:
        temporary = self.cache_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(self.cache, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.cache_path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass

    def musicbrainz_json(self, endpoint: str, query: str) -> dict[str, Any]:
        request = Request(
            f"https://musicbrainz.org/ws/2/{endpoint}/?"
            + urlencode({"query": query, "fmt": "json", "limit": 5}),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        for attempt in range(3):
            delay = 1.05 - (time.monotonic() - self.last_musicbrainz_request)
            if delay > 0:
                time.sleep(delay)
            try:
                self.last_musicbrainz_request = time.monotonic()
                with urlopen(request, timeout=8) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code not in (429, 502, 503, 504) or attempt == 2:
                    raise
            except OSError:
                if attempt == 2:
                    raise
        return {}

    def cover_art(self, release_group_id: str) -> str | None:
        request = Request(
            f"https://coverartarchive.org/release-group/{release_group_id}",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=8) as response:
                    images = json.load(response).get("images", [])
                break
            except HTTPError as error:
                if error.code == 404:
                    return None
                if error.code not in (429, 502, 503, 504) or attempt == 2:
                    raise
            except OSError:
                if attempt == 2:
                    raise
            time.sleep(0.75 * (attempt + 1))

        front = next((image for image in images if image.get("front")), None)
        if not front:
            return None
        thumbnails = front.get("thumbnails", {})
        artwork_url = str(
            thumbnails.get("500") or thumbnails.get("large") or front.get("image", "")
        )
        if artwork_url.startswith("http://coverartarchive.org/"):
            artwork_url = "https://" + artwork_url.removeprefix("http://")
        return artwork_url if artwork_url.startswith("https://") else None

    def deezer_art(
        self, artist: str, album: str, title: str, allow_alternative: bool = False
    ) -> tuple[str, str] | None:
        query = f'artist:"{artist}" track:"{title}"'
        request = Request(
            "https://api.deezer.com/search?"
            + urlencode({"q": query, "limit": 10}),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        with urlopen(request, timeout=8) as response:
            results = json.load(response).get("data", [])

        ranked: list[tuple[float, str, str]] = []
        requested_album = release_base_text(album)
        for result in results:
            result_artist = str(result.get("artist", {}).get("name", ""))
            result_title = str(result.get("title_short") or result.get("title", ""))
            result_album = str(result.get("album", {}).get("title", ""))
            artist_score = similarity(artist, result_artist)
            title_score = similarity(title, result_title)
            album_score = similarity(requested_album, release_base_text(result_album))
            if (
                artist_score < 0.72
                or title_score < 0.78
                or (
                    album_score < 0.68
                    and not (
                        allow_alternative
                        and artist_score >= 0.90
                        and title_score >= 0.94
                    )
                )
            ):
                continue

            album_data = result.get("album", {})
            artwork_url = str(
                album_data.get("cover_xl") or album_data.get("cover_big") or ""
            )
            track_link = str(result.get("link", ""))
            if not artwork_url.startswith("https://"):
                continue
            if not track_link.startswith("https://"):
                track_link = ""
            match = title_score * 0.4 + artist_score * 0.35 + album_score * 0.25
            ranked.append((match, artwork_url, track_link))

        if not ranked:
            return None
        _, artwork_url, track_link = max(ranked)
        return artwork_url, track_link

    def apple_json(self, endpoint: str, parameters: dict[str, str | int]) -> dict[str, Any]:
        delay = 3.1 - (time.monotonic() - self.last_apple_request)
        if delay > 0:
            time.sleep(delay)
        request = Request(
            f"https://itunes.apple.com/{endpoint}?" + urlencode(parameters),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        self.last_apple_request = time.monotonic()
        with urlopen(request, timeout=8) as response:
            return json.load(response)

    def apple_art(
        self, artist: str, album: str
    ) -> tuple[str, str] | None:
        primary_artist = re.split(
            r"\s+(?:&|and|feat\.?|featuring)\s+|,", artist, maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip() or artist
        artist_key = normalized_match_text(primary_artist)
        artist_id = self.apple_artist_ids.get(artist_key, "")

        if not artist_id:
            results = self.apple_json(
                "search",
                {
                    "term": primary_artist,
                    "country": "PT",
                    "media": "music",
                    "entity": "musicArtist",
                    "limit": 10,
                },
            ).get("results", [])
            ranked_artists: list[tuple[float, str]] = []
            for result in results:
                result_artist = str(result.get("artistName", ""))
                result_artist_id = str(result.get("artistId", ""))
                artist_score = similarity(primary_artist, result_artist)
                if result_artist_id and artist_score >= 0.78:
                    ranked_artists.append((artist_score, result_artist_id))
            if not ranked_artists:
                return None
            _, artist_id = max(ranked_artists)
            self.apple_artist_ids[artist_key] = artist_id

        releases = self.apple_json(
            "lookup",
            {
                "id": artist_id,
                "country": "PT",
                "entity": "album",
                "limit": 200,
            },
        ).get("results", [])
        ranked_releases: list[tuple[float, str, str]] = []
        for release in releases:
            if release.get("wrapperType") != "collection":
                continue
            result_artist = str(release.get("artistName", ""))
            result_album = str(release.get("collectionName", ""))
            artist_score = max(
                similarity(artist, result_artist),
                similarity(primary_artist, result_artist),
            )
            album_score = max(
                similarity(album, result_album),
                similarity(release_base_text(album), release_base_text(result_album)),
            )
            if artist_score < 0.72 or album_score < 0.82:
                continue
            artwork_url = str(release.get("artworkUrl100", ""))
            artwork_url = re.sub(
                r"/\d+x\d+(?:bb)?(?:-\d+)?\.",
                "/1000x1000bb.",
                artwork_url,
            )
            release_link = str(release.get("collectionViewUrl", ""))
            if artwork_url.startswith("https://"):
                ranked_releases.append(
                    (album_score * 0.65 + artist_score * 0.35,
                     artwork_url,
                     release_link if release_link.startswith("https://") else "")
                )
        if not ranked_releases:
            return None
        _, artwork_url, release_link = max(ranked_releases)
        return artwork_url, release_link

    def album_release_groups(self, artist: str, album: str) -> list[tuple[str, str]]:
        # Search on the reported album first. Requiring the full artist-credit
        # string here loses collaborations whose join phrase differs by source.
        query = f'releasegroup:"{lucene_phrase(album)}"'
        results = self.musicbrainz_json("release-group", query).get(
            "release-groups", []
        )
        ranked: list[tuple[float, str, str]] = []
        for result in results:
            score = int(result.get("score", 0)) / 100
            title_score = similarity(album, str(result.get("title", "")))
            credited_artist = "".join(
                str(credit.get("name", ""))
                for credit in result.get("artist-credit", [])
                if isinstance(credit, dict)
            )
            artist_score = similarity(artist, credited_artist)
            match = score * 0.25 + title_score * 0.5 + artist_score * 0.25
            release_group_id = str(result.get("id", ""))
            release_group_title = str(result.get("title", ""))
            if (
                release_group_id
                and title_score >= 0.72
                and artist_score >= 0.55
            ):
                ranked.append((match, release_group_id, release_group_title))
        return [
            (release_group_id, release_group_title)
            for _, release_group_id, release_group_title in sorted(
                ranked, reverse=True
            )
        ]

    def track_release_groups(
        self, artist: str, album: str, title: str
    ) -> list[tuple[str, str]]:
        # AirPlay sources commonly decorate live/remastered tracks with a
        # parenthetical label that is spelled differently in MusicBrainz.
        plain_title = re.sub(r"\s*[\[(].*?[\])]\s*$", "", title).strip() or title
        query = (
            f'recording:"{lucene_phrase(plain_title)}" '
            f'AND artist:"{lucene_phrase(artist)}"'
        )
        results = self.musicbrainz_json("recording", query).get("recordings", [])
        ranked_by_release_group: dict[str, tuple[float, str, str]] = {}
        for result in results:
            credited_artist = "".join(
                str(credit.get("name", ""))
                for credit in result.get("artist-credit", [])
                if isinstance(credit, dict)
            )
            title_score = similarity(plain_title, str(result.get("title", "")))
            artist_score = similarity(artist, credited_artist)
            score = int(result.get("score", 0)) / 100
            if title_score < 0.55 or artist_score < 0.55:
                continue
            for release in result.get("releases", []):
                release_group_id = str(release.get("release-group", {}).get("id", ""))
                release_title = str(release.get("title", ""))
                if not release_group_id:
                    continue
                album_score = similarity(album, release_title) if album.strip() else 0
                if album.strip() and album_score < 0.62:
                    # Recording results include compilations containing the
                    # same song. Never substitute unrelated compilation art.
                    continue
                candidate = (
                    score * 0.25
                    + title_score * 0.25
                    + artist_score * 0.2
                    + album_score * 0.3,
                    release_group_id,
                    release_title,
                )
                previous = ranked_by_release_group.get(release_group_id)
                if previous is None or candidate[0] > previous[0]:
                    ranked_by_release_group[release_group_id] = candidate
        return [
            (release_group_id, release_title)
            for _, release_group_id, release_title in sorted(
                ranked_by_release_group.values(), reverse=True
            )
        ]

    def resolve(self, artist: str, album: str, title: str) -> str | None:
        self.last_retryable = False
        self.last_link = ""
        if not artist.strip() or not (album.strip() or title.strip()):
            self.last_message = "missing artist, album, or track metadata"
            return None

        key = self.key(artist, album, title)
        if key in self.cache:
            self.last_link = self.cache.get("@link\0" + key, "")
            self.last_message = "loaded from cache"
            artwork_url = self.cache[key]
            return artwork_url if artwork_url.startswith("https://") else None
        if key in self.misses:
            self.last_message = "no confident match found earlier in this session"
            return None

        errors: list[str] = []
        artwork_url: str | None = None
        matched_release = ""
        lookup_method = "album"
        try:
            release_groups = self.album_release_groups(artist, album) if album.strip() else []
            for release_group_id, release_group_title in release_groups:
                artwork_url = self.cover_art(release_group_id)
                if artwork_url:
                    matched_release = f"{release_group_title} ({release_group_id})"
                    break
            else:
                artwork_url = None

            if not artwork_url and title.strip():
                lookup_method = "track fallback"
                for release_group_id, release_group_title in self.track_release_groups(
                    artist, album, title
                ):
                    artwork_url = self.cover_art(release_group_id)
                    if artwork_url:
                        matched_release = f"{release_group_title} ({release_group_id})"
                        break

        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            errors.append(f"MusicBrainz: {error}")

        if artwork_url:
            self.cache[key] = artwork_url
            self.save()
            self.last_message = (
                f"matched {matched_release} via {lookup_method}: {artwork_url}"
            )
            return artwork_url

        try:
            deezer_result = self.deezer_art(
                artist, album, title, allow_alternative=False
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            deezer_result = None
            errors.append(f"Deezer: {error}")

        if deezer_result:
            artwork_url, self.last_link = deezer_result
            self.cache[key] = artwork_url
            self.cache["@link\0" + key] = self.last_link
            self.save()
            self.last_message = f"matched via Deezer: {artwork_url}"
            return artwork_url

        try:
            apple_result = self.apple_art(artist, album) if album.strip() else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            apple_result = None
            errors.append(f"Apple Music: {error}")

        if apple_result:
            artwork_url, self.last_link = apple_result
            self.cache[key] = artwork_url
            self.cache["@link\0" + key] = self.last_link
            self.save()
            self.last_message = f"matched exact edition via Apple Music: {artwork_url}"
            return artwork_url

        try:
            alternative_result = self.deezer_art(
                artist, album, title, allow_alternative=True
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            alternative_result = None
            errors.append(f"Deezer alternative: {error}")

        if alternative_result:
            artwork_url, self.last_link = alternative_result
            self.cache[key] = artwork_url
            self.cache["@link\0" + key] = self.last_link
            self.save()
            self.last_message = f"matched alternate release via Deezer: {artwork_url}"
            return artwork_url

        if errors:
            self.last_message = "lookup failed: " + "; ".join(errors)
            self.last_retryable = True
            return None

        self.last_message = "no confident match from MusicBrainz, Deezer, or Apple Music"
        self.misses.add(key)
        return None


def build_activity(
    title: str,
    artist: str,
    album: str,
    position: str,
    duration: str,
    artwork: str,
    artwork_link: str = "",
    generic_audio: bool = False,
) -> dict[str, Any] | None:
    if not title.strip() and not generic_audio:
        return None

    album_text = clean_text(album, "Uka")
    state = (
        "AirPlay audio"
        if generic_audio
        else clean_text(f"by {artist}" if artist.strip() else album, "AirPlay audio")
    )
    activity: dict[str, Any] = {
        "type": 2,
        "details": clean_text(title, "AirPlay audio"),
        "state": state,
        "assets": {
            "large_image": artwork,
        },
    }
    if not generic_audio:
        activity["assets"]["large_text"] = album_text
    if artwork_link.startswith("https://"):
        activity["assets"]["large_url"] = artwork_link

    try:
        position_seconds = max(0, int(position))
        duration_seconds = max(0, int(duration))
    except ValueError:
        position_seconds = 0
        duration_seconds = 0

    if duration_seconds > 0:
        position_seconds = min(position_seconds, duration_seconds)
        now = int(time.time())
        activity["timestamps"] = {
            "start": now - position_seconds,
            "end": now + duration_seconds - position_seconds,
        }

    return activity


class DiscordRPC:
    def __init__(self, client_id: str, pid: int) -> None:
        self.client_id = client_id
        self.pid = pid
        self.connection: socket.socket | None = None
        self.last_error = ""

    def disconnect(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
        self.connection = None

    def connect(self) -> bool:
        self.disconnect()
        last_error = "Discord IPC socket not found"
        for path in ipc_paths():
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(2)
            try:
                connection.connect(str(path))
                connection.sendall(
                    encode_frame(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
                )
                opcode, payload = receive_frame(connection)
                if opcode == OP_FRAME and payload.get("evt") == "READY":
                    self.connection = connection
                    return True
            except (OSError, ValueError, json.JSONDecodeError) as error:
                last_error = str(error)
            connection.close()
        self.last_error = last_error
        return False

    def connection_active(self) -> bool:
        if self.connection is None:
            return False
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return True
            opcode, payload = receive_frame(self.connection)
            if opcode == OP_PING:
                self.connection.sendall(encode_frame(OP_PONG, payload))
                return True
            if opcode == OP_CLOSE:
                raise ConnectionError("Discord closed the IPC connection")
            return True
        except (OSError, ValueError, json.JSONDecodeError, ConnectionError):
            self.disconnect()
            return False

    def request(self, activity: dict[str, Any] | None) -> bool:
        if self.connection is None and not self.connect():
            return False

        nonce = str(uuid.uuid4())
        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": self.pid, "activity": activity},
            "nonce": nonce,
        }
        try:
            assert self.connection is not None
            self.connection.sendall(encode_frame(OP_FRAME, payload))
            while True:
                opcode, response = receive_frame(self.connection)
                if opcode == OP_PING:
                    self.connection.sendall(encode_frame(OP_PONG, response))
                elif opcode == OP_CLOSE:
                    raise ConnectionError("Discord closed the IPC connection")
                elif response.get("nonce") == nonce:
                    if response.get("evt") == "ERROR":
                        raise ConnectionError(str(response.get("data", "RPC error")))
                    self.last_error = ""
                    return True
        except (OSError, ValueError, json.JSONDecodeError, ConnectionError) as error:
            self.last_error = str(error)
            self.disconnect()
            return False


def run(
    client_id: str,
    asset_key: str,
    generic_asset_key: str,
    reconnect_interval: float,
    art_cache: Path,
    pid: int,
) -> int:
    rpc = DiscordRPC(client_id, pid)
    artwork_lookup = ArtworkLookup(art_cache)
    input_buffer = bytearray()
    pending_fields: list[str] = []
    last_activity: dict[str, Any] | None = None
    last_fields: tuple[str, str, str, str, str] | None = None
    next_artwork_retry = 0.0
    next_connection_check = 0.0
    has_activity = False
    last_reported_error = ""

    def publish(activity: dict[str, Any] | None) -> None:
        nonlocal last_reported_error, next_connection_check
        was_disconnected = rpc.connection is None
        if rpc.request(activity):
            if was_disconnected:
                print("Discord Rich Presence: connected", file=sys.stderr)
            last_reported_error = ""
        elif rpc.last_error and rpc.last_error != last_reported_error:
            print(f"Discord Rich Presence: {rpc.last_error}", file=sys.stderr)
            last_reported_error = rpc.last_error
        next_connection_check = time.monotonic() + reconnect_interval

    def resolve_and_publish(fields: tuple[str, str, str, str, str]) -> None:
        nonlocal last_activity, next_artwork_retry
        title, artist, album, position, duration = fields
        resolved_artwork = artwork_lookup.resolve(artist, album, title)
        print(
            f"Artwork for {artist} — {album} — {title}: "
            f"{artwork_lookup.last_message}",
            file=sys.stderr,
        )
        if resolved_artwork:
            last_activity = build_activity(
                title,
                artist,
                album,
                position,
                duration,
                resolved_artwork,
                artwork_lookup.last_link,
            )
            publish(last_activity)
            next_artwork_retry = 0.0
        elif artwork_lookup.last_retryable:
            next_artwork_retry = time.monotonic() + 15
        else:
            next_artwork_retry = 0.0

    try:
        while True:
            readable, _, _ = select.select(
                [sys.stdin.buffer], [], [], min(5.0, reconnect_interval)
            )
            if not readable:
                now = time.monotonic()
                if has_activity and now >= next_connection_check:
                    if not rpc.connection_active():
                        publish(last_activity)
                    else:
                        next_connection_check = now + reconnect_interval
                if (
                    last_fields is not None
                    and next_artwork_retry
                    and now >= next_artwork_retry
                ):
                    resolve_and_publish(last_fields)
                continue

            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                if rpc.connection is not None:
                    publish(None)
                break

            input_buffer.extend(chunk)
            while b"\0" in input_buffer:
                raw, remainder = input_buffer.split(b"\0", 1)
                input_buffer[:] = remainder
                pending_fields.append(raw.decode("utf-8", errors="replace"))
                if len(pending_fields) != FIELD_COUNT:
                    continue

                title, artist, album, position, duration = pending_fields
                pending_fields = []
                last_fields = (title, artist, album, position, duration)
                next_artwork_retry = 0.0
                generic_audio = is_generic_audio(title, artist, album)
                if generic_audio:
                    artwork = generic_asset_key
                    artwork_link = ""
                else:
                    artwork = artwork_lookup.cached(artist, album, title) or asset_key
                    artwork_link = artwork_lookup.cached_link(artist, album, title)
                last_activity = build_activity(
                    title,
                    artist,
                    album,
                    position,
                    duration,
                    artwork,
                    artwork_link,
                    generic_audio,
                )
                has_activity = last_activity is not None
                publish(last_activity)

                if generic_audio:
                    print(
                        f"Generic AirPlay audio for {title or '(untitled)'}: "
                        f"using {generic_asset_key}",
                        file=sys.stderr,
                    )

                if has_activity and artwork == asset_key and not generic_audio:
                    resolve_and_publish(last_fields)
    finally:
        rpc.disconnect()
    return 0


def self_test(asset_key: str) -> int:
    frame = encode_frame(OP_HANDSHAKE, {"v": 1, "client_id": "123"})
    opcode, length = struct.unpack("<II", frame[:8])
    assert opcode == OP_HANDSHAKE
    assert length == len(frame[8:])
    activity = build_activity("Track", "Artist", "Album", "10", "120", asset_key)
    assert activity is not None
    assert activity["type"] == 2
    assert activity["assets"]["large_image"] == asset_key
    assert activity["timestamps"]["end"] - activity["timestamps"]["start"] == 120
    assert is_generic_audio("Jornal da Meia-Noite S1", "", "Vodafone TV")
    assert is_generic_audio("Program", "Vodafone TV", "Vodafone TV")
    assert is_generic_audio("", "", "Vodafone TV")
    assert not is_generic_audio("Track", "Artist", "Album")
    generic_activity = build_activity(
        "Jornal da Meia-Noite S1",
        "",
        "Vodafone TV",
        "10",
        "120",
        "uxplay-kitty-audio-sound",
        generic_audio=True,
    )
    assert generic_activity is not None
    assert generic_activity["state"] == "AirPlay audio"
    assert generic_activity["assets"] == {
        "large_image": "uxplay-kitty-audio-sound"
    }

    client_socket, server_socket = socket.socketpair()

    def respond() -> None:
        opcode, request = receive_frame(server_socket)
        assert opcode == OP_FRAME
        assert request["cmd"] == "SET_ACTIVITY"
        response = {
            "cmd": "SET_ACTIVITY",
            "data": None,
            "evt": None,
            "nonce": request["nonce"],
        }
        server_socket.sendall(encode_frame(OP_FRAME, response))
        server_socket.close()

    responder = threading.Thread(target=respond)
    responder.start()
    rpc = DiscordRPC("123", 999)
    rpc.connection = client_socket
    assert rpc.request(activity)
    rpc.disconnect()
    responder.join()

    health_client, health_server = socket.socketpair()
    health_rpc = DiscordRPC("123", 999)
    health_rpc.connection = health_client
    assert health_rpc.connection_active()
    health_server.close()
    assert not health_rpc.connection_active()
    return 0


def clear_presence(client_id: str, pid: int) -> int:
    rpc = DiscordRPC(client_id, pid)
    rpc.request(None)
    rpc.disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--asset-key", required=True)
    parser.add_argument("--generic-asset-key", default="uxplay-kitty-audio-sound")
    parser.add_argument("--reconnect-interval", type=float, default=5.0)
    parser.add_argument("--art-cache", type=Path, default=Path("discord-art-cache.json"))
    parser.add_argument("--pid", type=int, default=os.getppid())
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test(args.asset_key)
    if args.clear:
        return clear_presence(args.client_id, args.pid)
    return run(
        args.client_id,
        args.asset_key,
        args.generic_asset_key,
        max(1.0, args.reconnect_interval),
        args.art_cache,
        args.pid,
    )


if __name__ == "__main__":
    raise SystemExit(main())

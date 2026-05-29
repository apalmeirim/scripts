#!/usr/bin/env python3
"""
merge tracks from all of your spotify playlists into one destination playlist.
+ deduplicates tracks by spotify track id.

setup:
1. create a spotify app at https://developer.spotify.com/dashboard
2. set redirect uri in app settings (recommended example: http://127.0.0.1:8888/callback)
3. create a local `.env` file in this folder with:
   spotipy_client_id=...
   spotipy_client_secret=...
   spotipy_redirect_uri=http://127.0.0.1:8888/callback
4. install dependency (required!): pip install spotipy

ex.:
python mp.py --target-name "mp_result"

flags:
--target-name "name"       destination playlist name (overrides user_config target).
--public                   create target as public if it does not already exist.
--include-target-source    include target when scanning source playlists (merge mode).
--skip-dedup-target        skip deduplication pass on target playlist.
--dedup-only               skip merge/add; only deduplicate the target playlist.
--shuffle                  shuffle the final target playlist order.
--cover-image "file.jpg"   set target playlist cover image from a JPEG in this script directory.
"""

from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from datetime import date
import os
from pathlib import Path
import random
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException


SCOPES = (
    "ugc-image-upload "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-private "
    "playlist-modify-public"
)

# mandatory: fill these values with your spotify app credentials.
USER_CONFIG = {
    "target_name": "",  # optional override; defaults to today's date if left empty
    "public_target_if_created": False,
}


def default_target_name() -> str:
    return date.today().isoformat()


def resolve_cover_image_path(image_name: str) -> Path:
    script_dir = Path(__file__).resolve().parent
    image_path = (script_dir / image_name).resolve()
    if image_path.parent != script_dir:
        raise ValueError("Cover image must be a file in this script directory.")
    if not image_path.exists() or not image_path.is_file():
        raise ValueError(f"Cover image not found in script directory: {image_path.name}")
    if image_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("Cover image must be a .jpg or .jpeg file.")
    if image_path.stat().st_size > 256 * 1024:
        raise ValueError("Cover image must be 256KB or smaller.")
    data = image_path.read_bytes()
    # Basic JPEG signature check: SOI marker and EOI marker.
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("Cover image bytes are not a valid JPEG. Re-export as baseline JPEG.")
    return image_path


def set_playlist_cover_image(sp: spotipy.Spotify, playlist_id: str, image_path: Path) -> None:
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    try:
        sp.playlist_upload_cover_image(playlist_id, image_b64)
    except SpotifyException as ex:
        raise ValueError(
            "Spotify rejected the cover image (HTTP 400). "
            "Use a real JPEG (not PNG renamed to .jpg), keep it <=256KB, and re-export as baseline JPEG."
        ) from ex

def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def resolve_config_value(env_key: str, config_key: str) -> str:
    value = os.getenv(env_key, "").strip()
    if value:
        return value
    return str(USER_CONFIG.get(config_key, "")).strip()


def get_spotify_client() -> spotipy.Spotify:
    client_id = os.getenv("SPOTIPY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "").strip()

    missing = []
    if not client_id:
        missing.append("client_id")
    if not client_secret:
        missing.append("client_secret")
    if not redirect_uri:
        missing.append("redirect_uri")
    if missing:
        raise ValueError(
            "Missing Spotify auth config: "
            + ", ".join(missing)
            + ". Fill .env (or OS env vars) with SPOTIPY_* values."
        )

    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def get_all_user_playlists(sp: spotipy.Spotify) -> List[dict]:
    playlists: List[dict] = []
    results = sp.current_user_playlists(limit=50)
    playlists.extend(results.get("items", []))
    while results.get("next"):
        results = sp.next(results)
        playlists.extend(results.get("items", []))
    return playlists


def find_playlist_by_name(playlists: List[dict], name: str) -> Optional[dict]:
    lname = name.strip().lower()
    for playlist in playlists:
        if playlist.get("name", "").strip().lower() == lname:
            return playlist
    return None


def get_or_create_target_playlist(sp: spotipy.Spotify, target_name: str, public: bool) -> dict:
    playlists = get_all_user_playlists(sp)
    existing = find_playlist_by_name(playlists, target_name)
    if existing:
        return existing

    user = sp.current_user()
    return sp.user_playlist_create(
        user=user["id"],
        name=target_name,
        public=public,
        description="generated playlist that merges all playlists and deduplicates tracks.",
    )


def extract_track_uri_and_id(item: dict) -> Tuple[Optional[str], Optional[str]]:
    track = item.get("track")
    if not track:
        return None, None

    track_id = track.get("id")
    uri = track.get("uri")
    if not track_id and uri and uri.startswith("spotify:track:"):
        track_id = uri.split(":")[-1]

    if not uri and track_id:
        uri = f"spotify:track:{track_id}"

    return uri, track_id


def normalize_text(value: str) -> str:
    return " ".join(value.lower().strip().split())


def normalize_title(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_semantic_key(track: dict) -> Optional[Tuple[str, str, int]]:
    name = track.get("name") or ""
    artists = track.get("artists") or []
    duration_ms = track.get("duration_ms")
    if not name or not artists or not isinstance(duration_ms, int):
        return None
    artist_names = [normalize_text(a.get("name", "")) for a in artists if a.get("name")]
    if not artist_names:
        return None
    title_key = normalize_title(name)
    artists_key = "|".join(artist_names)
    duration_bucket = int(round(duration_ms / 2000.0))
    return (title_key, artists_key, duration_bucket)


def get_playlist_track_rows(sp: spotipy.Spotify, playlist_id: str) -> List[dict]:
    rows: List[dict] = []
    offset = 0
    while True:
        results = sp.playlist_items(
            playlist_id,
            offset=offset,
            limit=100,
            fields="items(track(id,uri,name,duration_ms,artists(name))),next",
        )
        items = results.get("items", [])
        for idx, item in enumerate(items):
            track = item.get("track") or {}
            uri, track_id = extract_track_uri_and_id(item)
            if not uri or not track_id:
                continue
            rows.append(
                {
                    "position": offset + idx,
                    "uri": uri,
                    "track_id": track_id,
                    "semantic_key": build_semantic_key(track),
                }
            )

        if not results.get("next"):
            break
        offset += len(items)
    return rows


def get_playlist_tracks_with_positions(sp: spotipy.Spotify, playlist_id: str) -> List[Tuple[int, str, str]]:
    """returns (position, uri, track_id) for tracks in a playlist."""
    rows = get_playlist_track_rows(sp, playlist_id)
    return [(r["position"], r["uri"], r["track_id"]) for r in rows]


def get_unique_source_track_uris(
    sp: spotipy.Spotify,
    playlists: List[dict],
    skip_playlist_ids: Set[str],
) -> List[str]:
    seen_ids: Set[str] = set()
    unique_uris: List[str] = []

    for playlist in playlists:
        pid = playlist.get("id")
        if not pid or pid in skip_playlist_ids:
            continue

        offset = 0
        while True:
            results = sp.playlist_items(
                pid,
                offset=offset,
                limit=100,
                fields="items(track(id,uri,is_local)),next",
            )
            items = results.get("items", [])
            for item in items:
                track = item.get("track")
                if not track or track.get("is_local"):
                    continue
                uri, track_id = extract_track_uri_and_id(item)
                if not uri or not track_id:
                    continue
                if track_id in seen_ids:
                    continue
                seen_ids.add(track_id)
                unique_uris.append(uri)

            if not results.get("next"):
                break
            offset += len(items)

    return unique_uris


def add_missing_tracks_to_target(
    sp: spotipy.Spotify, target_playlist_id: str, source_unique_uris: List[str]
) -> int:
    target_rows = get_playlist_tracks_with_positions(sp, target_playlist_id)
    target_ids = {track_id for _, _, track_id in target_rows}

    to_add: List[str] = []
    for uri in source_unique_uris:
        track_id = uri.split(":")[-1]
        if track_id not in target_ids:
            to_add.append(uri)

    for batch in chunked(to_add, 100):
        sp.playlist_add_items(target_playlist_id, batch)

    return len(to_add)


def dedup_playlist_in_place(sp: spotipy.Spotify, playlist_id: str) -> int:
    total_removed = 0
    while True:
        rows = get_playlist_track_rows(sp, playlist_id)
        seen_track_ids: Set[str] = set()
        seen_semantic_keys: Set[Tuple[str, str, int]] = set()
        removals: List[dict] = []

        for row in rows:
            position = row["position"]
            uri = row["uri"]
            track_id = row["track_id"]
            semantic_key = row["semantic_key"]
            duplicate_by_id = track_id in seen_track_ids
            duplicate_by_semantic = bool(semantic_key and semantic_key in seen_semantic_keys)
            if duplicate_by_id or duplicate_by_semantic:
                removals.append({"uri": uri, "positions": [position]})
            else:
                seen_track_ids.add(track_id)
                if semantic_key:
                    seen_semantic_keys.add(semantic_key)

        if not removals:
            break

        # Positions are snapshot-sensitive; remove at most 100, then recompute.
        batch = removals[:100]
        sp.playlist_remove_specific_occurrences_of_items(playlist_id, batch)
        total_removed += len(batch)

    return total_removed


def shuffle_playlist_in_place(sp: spotipy.Spotify, playlist_id: str) -> int:
    rows = get_playlist_tracks_with_positions(sp, playlist_id)
    uris = [uri for _, uri, _ in rows]
    if len(uris) < 2:
        return 0

    random.shuffle(uris)
    sp.playlist_replace_items(playlist_id, uris[:100])
    for batch in chunked(uris[100:], 100):
        sp.playlist_add_items(playlist_id, batch)
    return len(uris)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge all Spotify playlists into a target playlist and deduplicate tracks."
    )
    parser.add_argument(
        "--target-name",
        help="Destination playlist name (created if it does not exist).",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Create target playlist as public if it does not already exist.",
    )
    parser.add_argument(
        "--include-target-source",
        action="store_true",
        help="Include target playlist while scanning sources (normally skipped).",
    )
    parser.add_argument(
        "--skip-dedup-target",
        action="store_true",
        help="Skip deduplicating duplicate tracks already in target.",
    )
    parser.add_argument(
        "--dedup-only",
        action="store_true",
        help="Only deduplicate the target playlist (skip merge/add step).",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle the final target playlist order.",
    )
    parser.add_argument(
        "--cover-image",
        help="JPEG filename in this script directory to set as playlist cover (e.g. cover.jpg).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file()
    sp = get_spotify_client()
    target_name = args.target_name or str(USER_CONFIG.get("target_name", "")).strip() or default_target_name()
    public_if_created = args.public or bool(USER_CONFIG.get("public_target_if_created", False))

    all_playlists = get_all_user_playlists(sp)
    target = get_or_create_target_playlist(sp, target_name, public=public_if_created)
    target_id = target["id"]
    if args.cover_image:
        cover_image_path = resolve_cover_image_path(args.cover_image.strip())
        set_playlist_cover_image(sp, target_id, cover_image_path)

    source_uris: List[str] = []
    added_count = 0
    if not args.dedup_only:
        skip_ids = set()
        if not args.include_target_source:
            skip_ids.add(target_id)
        source_uris = get_unique_source_track_uris(sp, all_playlists, skip_ids)
        added_count = add_missing_tracks_to_target(sp, target_id, source_uris)

    removed_count = 0
    if not args.skip_dedup_target:
        removed_count = dedup_playlist_in_place(sp, target_id)
    shuffled_count = 0
    if args.shuffle:
        shuffled_count = shuffle_playlist_in_place(sp, target_id)

    print(f"Target playlist: {target.get('name')} ({target_id})")
    print(f"Unique tracks found across source playlists: {len(source_uris)}")
    print(f"Tracks added to target: {added_count}")
    print(f"Duplicate entries removed from target: {removed_count}")
    print(f"Tracks shuffled in target: {shuffled_count}")
    if args.cover_image:
        print(f"Cover image uploaded from script directory: {args.cover_image}")


if __name__ == "__main__":
    main()

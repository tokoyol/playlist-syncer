#!/usr/bin/env python3
"""Polls a Spotify playlist and mirrors new songs to a YouTube Music playlist.

Setup:
  1. Run the web app and go to /watch to configure which playlists to watch.
     This saves watcher_config.json with your Spotify token and playlist IDs.
  2. Run this script:  python sync_watcher.py
  3. Optionally set POLL_INTERVAL_SECONDS in watcher_config.json (default: 300).

The script tracks which Spotify track IDs it has already synced in
watcher_state.json, so it only processes songs added after setup.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from services import spotify_service as sp_svc
from services import ytmusic_service as yt_svc
from services.matcher import _build_query, _best_match  # noqa: PLC2701

CONFIG_FILE = Path(__file__).parent / "watcher_config.json"
STATE_FILE = Path(__file__).parent / "watcher_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config / state helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"{CONFIG_FILE} not found.\n"
            "Open the web app and visit /watch to choose your playlists."
        )
    return json.loads(CONFIG_FILE.read_text())


def _save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"synced_spotify_track_ids": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def _sync_once(cfg: dict, state: dict) -> dict:
    sp_playlist_id: str = cfg["spotify_playlist_id"]
    yt_playlist_id: str = cfg["youtube_playlist_id"]
    synced_ids: set[str] = set(state.get("synced_spotify_track_ids", []))

    # --- fetch Spotify tracks (refreshes token automatically) ---
    sp, updated_token = sp_svc.client_from_token(cfg["spotify_token"])
    cfg["spotify_token"] = updated_token  # keep refreshed token in memory

    sp_tracks = sp_svc.get_playlist_tracks(sp, sp_playlist_id)
    new_tracks = [t for t in sp_tracks if t["id"] not in synced_ids]

    if not new_tracks:
        log.info("No new tracks.")
        return state

    log.info("Found %d new track(s) to sync.", len(new_tracks))

    # --- match each new Spotify track to a YT Music video ---
    yt = yt_svc.client_from_session("")  # uses ytmusic_browser.json if available
    video_ids: list[str] = []

    for track in new_tracks:
        query = _build_query(track)
        candidates = yt_svc.search_track(yt, query)
        match = _best_match(track, candidates)
        if match:
            log.info(
                "  + %-40s  →  %s",
                f"{', '.join(track.get('artists', []))} – {track['name']}"[:40],
                match["name"],
            )
            video_ids.append(match["id"])
        else:
            log.warning(
                "  ? No YT Music match for: %s – %s",
                ", ".join(track.get("artists", [])),
                track["name"],
            )
        # Mark as synced regardless so we don't retry unmatched songs every poll
        synced_ids.add(track["id"])

    if video_ids:
        yt_svc.add_tracks_to_playlist(yt, yt_playlist_id, video_ids)
        log.info("Added %d track(s) to YouTube Music playlist.", len(video_ids))

    state["synced_spotify_track_ids"] = list(synced_ids)
    return state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = _load_config()
    interval: int = cfg.get("poll_interval_seconds", 300)

    log.info(
        "Watching  Spotify:%s  →  YouTube:%s",
        cfg["spotify_playlist_id"],
        cfg["youtube_playlist_id"],
    )
    log.info("Polling every %d seconds. Press Ctrl+C to stop.", interval)

    while True:
        try:
            state = _load_state()
            state = _sync_once(cfg, state)
            _save_state(state)
            _save_config(cfg)  # persist refreshed Spotify token
        except Exception as exc:
            log.error("Sync error: %s", exc, exc_info=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()

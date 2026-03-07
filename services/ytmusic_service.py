"""YouTube Music authentication and playlist operations via ytmusicapi."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from ytmusicapi import YTMusic, OAuthCredentials
from ytmusicapi.exceptions import YTMusicServerError

from config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKEN_DIR = PROJECT_ROOT / "ytmusic_tokens"
BROWSER_AUTH_FILE = PROJECT_ROOT / "ytmusic_browser.json"
TOKEN_DIR.mkdir(exist_ok=True)

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _oauth_credentials() -> OAuthCredentials:
    return OAuthCredentials(
        client_id=Config.YTMUSIC_CLIENT_ID,
        client_secret=Config.YTMUSIC_CLIENT_SECRET,
    )


def start_device_flow() -> dict:
    """Initiate the OAuth device-code flow. Returns device flow payload."""
    resp = requests.post(DEVICE_CODE_URL, data={
        "client_id": Config.YTMUSIC_CLIENT_ID,
        "scope": "https://www.googleapis.com/auth/youtube",
    })
    resp.raise_for_status()
    return resp.json()


def poll_for_token(device_code: str, interval: int = 5, timeout: int = 300) -> dict | None:
    """Poll Google's token endpoint until the user authorizes or timeout.

    Returns the token dict on success, None on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.post(TOKEN_URL, data={
            "client_id": Config.YTMUSIC_CLIENT_ID,
            "client_secret": Config.YTMUSIC_CLIENT_SECRET,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        data = resp.json()
        if "access_token" in data:
            data["expires_at"] = int(time.time()) + data.get("expires_in", 3600)
            return data
        if data.get("error") == "slow_down":
            interval += 1
        elif data.get("error") not in ("authorization_pending",):
            return None
        time.sleep(interval)
    return None


def save_token(session_id: str, token: dict) -> Path:
    path = TOKEN_DIR / f"{session_id}.json"
    # Strip fields ytmusicapi doesn't expect (e.g. refresh_token_expires_in from Google)
    allowed = {"access_token", "refresh_token", "expires_in", "expires_at", "scope", "token_type"}
    clean = {k: v for k, v in token.items() if k in allowed}
    path.write_text(json.dumps(clean))
    return path


def load_token(session_id: str) -> dict | None:
    path = TOKEN_DIR / f"{session_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def refresh_if_needed(session_id: str) -> dict | None:
    """Return a (possibly refreshed) token dict, or None if not logged in."""
    token = load_token(session_id)
    if token is None:
        return None
    if token.get("expires_at", 0) - int(time.time()) < 60:
        resp = requests.post(TOKEN_URL, data={
            "client_id": Config.YTMUSIC_CLIENT_ID,
            "client_secret": Config.YTMUSIC_CLIENT_SECRET,
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        })
        if resp.status_code == 200:
            new = resp.json()
            token["access_token"] = new["access_token"]
            token["expires_at"] = int(time.time()) + new.get("expires_in", 3600)
            save_token(session_id, token)
    return token


def has_browser_auth() -> bool:
    """True if browser auth file exists (workaround when OAuth returns 400)."""
    return BROWSER_AUTH_FILE.is_file()


def client_from_browser() -> YTMusic | None:
    """Build YTMusic from browser auth file. Use when OAuth fails with 400."""
    if not has_browser_auth():
        return None
    return YTMusic(str(BROWSER_AUTH_FILE))


def client_from_session(session_id: str) -> YTMusic | None:
    """Build a YTMusic client. Prefers browser auth if available (OAuth often 400)."""
    if has_browser_auth():
        return client_from_browser()
    token = refresh_if_needed(session_id)
    if token is None:
        return None
    token_path = TOKEN_DIR / f"{session_id}.json"
    return YTMusic(str(token_path), oauth_credentials=_oauth_credentials())


# ---------------------------------------------------------------------------
# Playlist operations
# ---------------------------------------------------------------------------

def get_user_playlists(yt: YTMusic) -> list[dict[str, Any]]:
    def _format(pl: dict) -> dict[str, Any]:
        author = pl.get("author")
        if isinstance(author, list) and author:
            owner = author[0].get("name", "You")
        else:
            owner = author if isinstance(author, str) else "You"
        return {
            "id": pl["playlistId"],
            "name": pl["title"],
            "image": pl["thumbnails"][-1]["url"] if pl.get("thumbnails") else None,
            "track_count": pl.get("count", "?"),
            "owner": owner,
        }

    last_err: Exception | None = None
    try:
        raw = yt.get_library_playlists(limit=25)
        return [_format(item) for item in raw]
    except YTMusicServerError as e:
        last_err = e

    # Fallback: extract playlists from home page (when library returns 400)
    try:
        home = yt.get_home(limit=50)
        playlists: list[dict[str, Any]] = []
        seen: set[str] = set()
        for section in home:
            for item in section.get("contents", []):
                pid = item.get("playlistId")
                if pid and pid not in seen:
                    seen.add(pid)
                    playlists.append(_format(item))
        if playlists:
            return playlists
    except YTMusicServerError:
        pass

    if last_err:
        raise last_err
    return []


def get_playlist_tracks(yt: YTMusic, playlist_id: str) -> list[dict[str, Any]]:
    raw = yt.get_playlist(playlist_id, limit=5000)
    tracks: list[dict[str, Any]] = []
    for t in raw.get("tracks", []):
        if not t:
            continue
        video_id = t.get("videoId")
        if not video_id:
            continue
        tracks.append({
            "id": video_id,
            "name": t.get("title", ""),
            "artists": [a["name"] for a in t.get("artists", []) if a.get("name")],
            "album": (t.get("album") or {}).get("name", ""),
            "image": (
                t["thumbnails"][-1]["url"]
                if t.get("thumbnails")
                else None
            ),
            "duration_ms": _duration_to_ms(t.get("duration", "0:00")),
            "isrc": None,
            "platform": "youtube",
        })
    return tracks


def search_track(yt: YTMusic, query: str, limit: int = 5) -> list[dict[str, Any]]:
    results = yt.search(query, filter="songs", limit=limit)
    out: list[dict[str, Any]] = []
    for t in results:
        vid = t.get("videoId")
        if not vid:
            continue
        out.append({
            "id": vid,
            "name": t.get("title", ""),
            "artists": [a["name"] for a in t.get("artists", []) if a.get("name")],
            "album": (t.get("album") or {}).get("name", ""),
            "duration_ms": _duration_to_ms(t.get("duration", "0:00")),
            "isrc": None,
            "platform": "youtube",
        })
    return out


def add_tracks_to_playlist(yt: YTMusic, playlist_id: str, video_ids: list[str]) -> int:
    if not video_ids:
        return 0
    # YouTube Music may reject large batches; add in chunks of 25
    added = 0
    for i in range(0, len(video_ids), 25):
        batch = video_ids[i : i + 25]
        yt.add_playlist_items(playlist_id, batch, duplicates=True)
        added += len(batch)
    return added


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _duration_to_ms(dur: str) -> int:
    """Convert 'M:SS' or 'H:MM:SS' string to milliseconds."""
    try:
        parts = dur.split(":")
        parts = [int(p) for p in parts]
        if len(parts) == 2:
            return (parts[0] * 60 + parts[1]) * 1000
        if len(parts) == 3:
            return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    except (ValueError, AttributeError):
        pass
    return 0

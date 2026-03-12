"""Spotify authentication and playlist operations via spotipy."""

from __future__ import annotations

import time
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import MemoryCacheHandler

from config import Config


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def build_oauth() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=Config.SPOTIPY_CLIENT_ID,
        client_secret=Config.SPOTIPY_CLIENT_SECRET,
        redirect_uri=Config.SPOTIPY_REDIRECT_URI,
        scope=Config.SPOTIFY_SCOPES,
        show_dialog=True,
        cache_handler=MemoryCacheHandler(),
        open_browser=False,
    )


def get_authorize_url() -> str:
    return build_oauth().get_authorize_url()


def exchange_code(code: str) -> dict:
    """Exchange the authorization code for token info dict."""
    return build_oauth().get_access_token(code, as_dict=True)


_REQUIRED_SCOPES = {
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
}


def token_has_required_scopes(token_info: dict) -> bool:
    """Return True if the token was granted all required scopes."""
    granted = set(token_info.get("scope", "").split())
    return _REQUIRED_SCOPES.issubset(granted)


def refresh_if_needed(token_info: dict) -> dict:
    """Return a (possibly refreshed) token_info dict."""
    if token_info.get("expires_at", 0) - int(time.time()) < 60:
        oauth = build_oauth()
        token_info = oauth.refresh_access_token(token_info["refresh_token"])
    return token_info


def client_from_token(token_info: dict) -> tuple[spotipy.Spotify, dict]:
    if not token_has_required_scopes(token_info):
        raise PermissionError(
            "Spotify token is missing required permissions. "
            "Please disconnect and reconnect your Spotify account."
        )
    token_info = refresh_if_needed(token_info)
    return spotipy.Spotify(auth=token_info["access_token"]), token_info


# ---------------------------------------------------------------------------
# Playlist operations
# ---------------------------------------------------------------------------

def get_user_playlists(sp: spotipy.Spotify) -> list[dict[str, Any]]:
    """Return lightweight list of the current user's playlists."""
    playlists: list[dict[str, Any]] = []
    result = sp.current_user_playlists(limit=50)
    while result:
        for item in result["items"]:
            # Spotify uses "items" for track count; "tracks" is deprecated
            track_info = item.get("items") or item.get("tracks") or {}
            track_count = track_info.get("total", 0)
            playlists.append({
                "id": item["id"],
                "name": item["name"],
                "image": (item["images"][0]["url"] if item.get("images") else None),
                "track_count": track_count,
                "owner": item["owner"]["display_name"],
            })
        if result["next"]:
            result = sp.next(result)
        else:
            break
    return playlists


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict[str, Any]]:
    """Fetch every track in a Spotify playlist with ISRC codes."""
    tracks: list[dict[str, Any]] = []
    result = sp.playlist_items(
        playlist_id,
        additional_types=["track"],
    )
    while result:
        for item in result["items"]:
            t = item.get("track")
            if not t or t.get("id") is None:
                continue
            isrc = (t.get("external_ids") or {}).get("isrc")
            tracks.append({
                "id": t["id"],
                "name": t["name"],
                "artists": [a["name"] for a in t.get("artists", [])],
                "album": t.get("album", {}).get("name", ""),
                "image": (
                    t["album"]["images"][0]["url"]
                    if t.get("album", {}).get("images")
                    else None
                ),
                "duration_ms": t.get("duration_ms", 0),
                "isrc": isrc,
                "platform": "spotify",
            })
        if result.get("next"):
            result = sp.next(result)
        else:
            break
    return tracks


def search_track(sp: spotipy.Spotify, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Spotify for tracks matching *query*."""
    results = sp.search(q=query, type="track", limit=limit)
    out: list[dict[str, Any]] = []
    for t in results.get("tracks", {}).get("items", []):
        isrc = (t.get("external_ids") or {}).get("isrc")
        out.append({
            "id": t["id"],
            "name": t["name"],
            "artists": [a["name"] for a in t.get("artists", [])],
            "album": t.get("album", {}).get("name", ""),
            "duration_ms": t.get("duration_ms", 0),
            "isrc": isrc,
            "platform": "spotify",
        })
    return out


def add_tracks_to_playlist(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> int:
    """Add tracks to a Spotify playlist in batches of 100. Returns count added."""
    added = 0
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i : i + 100]
        sp.playlist_add_items(playlist_id, batch)
        added += len(batch)
    return added

"""Cross-platform song matching between Spotify and YouTube Music.

Matching strategy (in priority order):
1. ISRC exact match  (Spotify tracks carry ISRCs)
2. Name + artist text search on the target platform, scored by
   artist-name similarity and duration proximity.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

MatchResult = dict[str, Any]
# Keys: source_track, target_track (or None), status ("matched" | "unmatched")


def diff_playlists(
    spotify_tracks: list[dict],
    youtube_tracks: list[dict],
    search_youtube: Callable[[str], list[dict]],
    search_spotify: Callable[[str], list[dict]],
) -> dict[str, list[MatchResult]]:
    """Compare two playlists and find missing songs on each side.

    Returns dict with keys:
        matched           – songs found on both sides
        only_in_spotify   – songs to add to YouTube Music (with best match or None)
        only_in_youtube   – songs to add to Spotify (with best match or None)
    """
    yt_by_norm = _index_by_norm(youtube_tracks)
    sp_by_norm = _index_by_norm(spotify_tracks)

    matched: list[MatchResult] = []
    only_in_spotify: list[MatchResult] = []
    only_in_youtube: list[MatchResult] = []

    matched_yt_norms: set[str] = set()

    # --- Spotify tracks not in YouTube ---
    sp_to_search: list[tuple[dict, str, str | None]] = []
    for sp_track in spotify_tracks:
        norm = _norm_key(sp_track)
        if norm in yt_by_norm:
            matched.append({
                "source_track": sp_track,
                "target_track": yt_by_norm[norm],
                "status": "matched",
            })
            matched_yt_norms.add(norm)
        else:
            query = _build_query(sp_track)
            isrc = sp_track.get("isrc")
            sp_to_search.append((sp_track, query, isrc))

    # Dedupe: one search per unique (norm, isrc)
    sp_search_keys: dict[tuple[str, str | None], list[dict]] = {}
    for sp_track, query, isrc in sp_to_search:
        norm = _norm_key(sp_track)
        key = (norm, isrc)
        sp_search_keys.setdefault(key, []).append(sp_track)

    def _search_yt(key: tuple[str, str | None]) -> tuple[tuple[str, str | None], dict | None]:
        tracks = sp_search_keys[key]
        sp_track = tracks[0]
        target = None
        if sp_track.get("isrc"):
            target = _best_match(sp_track, search_youtube(sp_track["isrc"]))
        if target is None:
            target = _best_match(sp_track, search_youtube(_build_query(sp_track)))
        return key, target

    with ThreadPoolExecutor(max_workers=3) as ex:
        sp_futures = {ex.submit(_search_yt, k): k for k in sp_search_keys}
        sp_results: dict[tuple[str, str | None], dict | None] = {}
        for fut in as_completed(sp_futures):
            key, target = fut.result()
            sp_results[key] = target

    for sp_track, query, isrc in sp_to_search:
        key = (_norm_key(sp_track), isrc)
        target = sp_results.get(key)
        if target is not None:
            yt_norm = _norm_key(target)
            if yt_norm in yt_by_norm:
                matched_yt_norms.add(yt_norm)
        only_in_spotify.append({
            "source_track": sp_track,
            "target_track": target,
            "status": "matched" if target else "unmatched",
        })

    # --- YouTube tracks not in Spotify ---
    yt_to_search: list[tuple[dict, str]] = []
    for yt_track in youtube_tracks:
        norm = _norm_key(yt_track)
        if norm in matched_yt_norms or norm in sp_by_norm:
            continue
        yt_to_search.append((yt_track, _build_query(yt_track)))

    yt_search_keys: dict[str, list[dict]] = {}
    for yt_track, query in yt_to_search:
        norm = _norm_key(yt_track)
        yt_search_keys.setdefault(norm, []).append(yt_track)

    def _search_sp(norm: str) -> tuple[str, dict | None]:
        yt_track = yt_search_keys[norm][0]
        target = _best_match(yt_track, search_spotify(_build_query(yt_track)))
        return norm, target

    with ThreadPoolExecutor(max_workers=3) as ex:
        yt_futures = {ex.submit(_search_sp, k): k for k in yt_search_keys}
        yt_results: dict[str, dict | None] = {}
        for fut in as_completed(yt_futures):
            norm, target = fut.result()
            yt_results[norm] = target

    for yt_track, query in yt_to_search:
        norm = _norm_key(yt_track)
        target = yt_results.get(norm)
        only_in_youtube.append({
            "source_track": yt_track,
            "target_track": target,
            "status": "matched" if target else "unmatched",
        })

    return {
        "matched": matched,
        "only_in_spotify": only_in_spotify,
        "only_in_youtube": only_in_youtube,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def _normalize(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower()).strip()


def _norm_key(track: dict) -> str:
    """Produce a normalised 'artist - title' key for quick dedup."""
    artists = " ".join(sorted(track.get("artists", [])))
    return _normalize(f"{artists} {track.get('name', '')}")


def _index_by_norm(tracks: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for t in tracks:
        idx[_norm_key(t)] = t
    return idx


def _build_query(track: dict) -> str:
    artists = ", ".join(track.get("artists", []))
    return f"{artists} - {track.get('name', '')}"


def _best_match(source: dict, candidates: list[dict]) -> dict | None:
    """Pick the candidate that best matches *source* by artist + duration."""
    if not candidates:
        return None

    src_artists = _normalize(" ".join(source.get("artists", [])))
    src_name = _normalize(source.get("name", ""))
    src_dur = source.get("duration_ms", 0)

    best, best_score = None, -1.0
    for c in candidates:
        c_artists = _normalize(" ".join(c.get("artists", [])))
        c_name = _normalize(c.get("name", ""))

        artist_sim = SequenceMatcher(None, src_artists, c_artists).ratio()
        name_sim = SequenceMatcher(None, src_name, c_name).ratio()

        dur_diff = abs(src_dur - c.get("duration_ms", 0))
        dur_score = max(0.0, 1.0 - dur_diff / 30_000)  # 30s window

        score = 0.35 * artist_sim + 0.40 * name_sim + 0.25 * dur_score
        if score > best_score:
            best_score = score
            best = c

    # Require a minimum confidence to avoid false positives
    if best_score < 0.45:
        return None
    return best

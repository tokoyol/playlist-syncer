"""Cross-platform song matching between Spotify and YouTube Music.

Matching strategy (in priority order):
1. Name + artist text search on the target platform, scored by
   artist-name similarity and duration proximity.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Callable

MatchResult = dict[str, Any]


def diff_playlists(
    spotify_tracks: list[dict],
    youtube_tracks: list[dict],
    search_youtube: Callable[[str], list[dict]],
    search_spotify: Callable[[str], list[dict]],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, list[MatchResult]]:
    """Compare two playlists and return matched / only_in_spotify / only_in_youtube."""

    yt_by_norm = _index_by_norm(youtube_tracks)
    sp_by_norm = _index_by_norm(spotify_tracks)

    matched: list[MatchResult] = []
    only_in_spotify: list[MatchResult] = []
    only_in_youtube: list[MatchResult] = []
    matched_yt_norms: set[str] = set()

    # ----- bucket Spotify tracks -----
    sp_direct: list[dict] = []
    sp_to_search: list[dict] = []
    for sp_track in spotify_tracks:
        norm = _norm_key(sp_track)
        if norm in yt_by_norm:
            matched.append({"source_track": sp_track, "target_track": yt_by_norm[norm], "status": "matched"})
            matched_yt_norms.add(norm)
            sp_direct.append(sp_track)
        else:
            sp_to_search.append(sp_track)

    # Dedupe searches by normalised key
    sp_keys: dict[str, dict] = {_norm_key(t): t for t in sp_to_search}

    # ----- bucket YouTube tracks -----
    yt_to_search: list[dict] = []
    for yt_track in youtube_tracks:
        norm = _norm_key(yt_track)
        if norm not in matched_yt_norms and norm not in sp_by_norm:
            yt_to_search.append(yt_track)
    yt_keys: dict[str, dict] = {_norm_key(t): t for t in yt_to_search}

    total = max(len(sp_keys) + len(yt_keys), 1)
    completed = [0]

    def _report(phase: str) -> None:
        if on_progress:
            on_progress(completed[0], total, phase)

    # ----- search YouTube for Spotify-only tracks -----
    sp_results: dict[str, dict | None] = {}

    def _search_yt(norm: str) -> tuple[str, dict | None]:
        sp_track = sp_keys[norm]
        candidates = search_youtube(_build_query(sp_track))
        return norm, _best_match(sp_track, candidates)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_search_yt, k): k for k in sp_keys}
        for fut in as_completed(futures):
            norm, target = fut.result()
            sp_results[norm] = target
            completed[0] += 1
            _report("Searching YouTube Music")

    for sp_track in sp_to_search:
        norm = _norm_key(sp_track)
        target = sp_results.get(norm)
        if target is not None:
            yt_norm = _norm_key(target)
            if yt_norm in yt_by_norm:
                matched_yt_norms.add(yt_norm)
        only_in_spotify.append({
            "source_track": sp_track,
            "target_track": target,
            "status": "matched" if target else "unmatched",
        })

    # ----- search Spotify for YouTube-only tracks -----
    yt_results: dict[str, dict | None] = {}

    def _search_sp(norm: str) -> tuple[str, dict | None]:
        yt_track = yt_keys[norm]
        candidates = search_spotify(_build_query(yt_track))
        return norm, _best_match(yt_track, candidates)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_search_sp, k): k for k in yt_keys}
        for fut in as_completed(futures):
            norm, target = fut.result()
            yt_results[norm] = target
            completed[0] += 1
            _report("Searching Spotify")

    for yt_track in yt_to_search:
        norm = _norm_key(yt_track)
        target = yt_results.get(norm)
        only_in_youtube.append({
            "source_track": yt_track,
            "target_track": target,
            "status": "matched" if target else "unmatched",
        })

    if on_progress:
        on_progress(total, total, "Done")

    return {"matched": matched, "only_in_spotify": only_in_spotify, "only_in_youtube": only_in_youtube}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def _normalize(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower()).strip()


def _norm_key(track: dict) -> str:
    artists = " ".join(sorted(track.get("artists", [])))
    return _normalize(f"{artists} {track.get('name', '')}")


def _index_by_norm(tracks: list[dict]) -> dict[str, dict]:
    return {_norm_key(t): t for t in tracks}


def _build_query(track: dict) -> str:
    artists = ", ".join(track.get("artists", []))
    return f"{artists} - {track.get('name', '')}"


def _best_match(source: dict, candidates: list[dict]) -> dict | None:
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
        dur_score = max(0.0, 1.0 - dur_diff / 30_000)
        score = 0.35 * artist_sim + 0.40 * name_sim + 0.25 * dur_score
        if score > best_score:
            best_score = score
            best = c

    return best if best_score >= 0.45 else None

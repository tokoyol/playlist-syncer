"""Flask routes – authentication, playlist selection, comparison, and sync."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_WATCHER_CONFIG = Path(__file__).parent / "watcher_config.json"
_WATCHER_STATE = Path(__file__).parent / "watcher_state.json"

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from services import spotify_service as sp_svc
from services import ytmusic_service as yt_svc
from services.matcher import diff_playlists

main_bp = Blueprint("main", __name__)

# In-memory job store  {job_id -> {status, current, total, phase, error, result}}
_compare_jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_session_id() -> str:
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def _spotify_connected() -> bool:
    return "spotify_token" in session


def _youtube_connected() -> bool:
    # Cache in session to avoid hitting disk on every page load
    if session.get("yt_connected"):
        return True
    if yt_svc.has_browser_auth():
        session["yt_connected"] = True
        return True
    sid = session.get("sid", "")
    if sid and yt_svc.load_token(sid) is not None:
        session["yt_connected"] = True
        return True
    return False


def _template_ctx() -> dict:
    return {
        "spotify_connected": _spotify_connected(),
        "youtube_connected": _youtube_connected(),
    }


def _parse_yt_playlist_id(val: str) -> str | None:
    if not val or len(val) < 5:
        return None
    if "list=" in val:
        qs = parse_qs(urlparse(val).query)
        return qs.get("list", [None])[0]
    return val if val.startswith(("PL", "OLAK", "VL")) else None


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

@main_bp.route("/")
def index():
    _ensure_session_id()
    return render_template("index.html", **_template_ctx())


# ---------------------------------------------------------------------------
# Spotify auth
# ---------------------------------------------------------------------------

@main_bp.route("/login/spotify")
def login_spotify():
    return redirect(sp_svc.get_authorize_url())


@main_bp.route("/callback/spotify")
def callback_spotify():
    code = request.args.get("code")
    if not code:
        flash("Spotify authorization was cancelled.", "error")
        return redirect(url_for("main.index"))
    try:
        session["spotify_token"] = sp_svc.exchange_code(code)
        flash("Spotify connected!", "success")
    except Exception as exc:
        flash(f"Spotify auth failed: {exc}", "error")
    return redirect(url_for("main.index"))


@main_bp.route("/logout/spotify")
def logout_spotify():
    session.pop("spotify_token", None)
    flash("Spotify disconnected.", "info")
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# YouTube Music auth (device-code flow)
# ---------------------------------------------------------------------------

@main_bp.route("/login/youtube")
def login_youtube():
    _ensure_session_id()
    try:
        flow = yt_svc.start_device_flow()
        return render_template(
            "youtube_auth.html",
            verification_url=flow.get("verification_url", ""),
            user_code=flow.get("user_code", ""),
            device_code=flow["device_code"],
            interval=flow.get("interval", 5),
            **_template_ctx(),
        )
    except Exception as exc:
        flash(f"Could not start YouTube auth: {exc}", "error")
        return redirect(url_for("main.index"))


@main_bp.route("/login/youtube/poll", methods=["POST"])
def poll_youtube():
    device_code = request.form.get("device_code", "")
    interval = int(request.form.get("interval", 5))
    sid = _ensure_session_id()
    token = yt_svc.poll_for_token(device_code, interval=interval, timeout=interval + 2)
    if token and "access_token" in token:
        yt_svc.save_token(sid, token)
        session["yt_connected"] = True
        return jsonify({"status": "ok"})
    return jsonify({"status": "pending"})


@main_bp.route("/logout/youtube")
def logout_youtube():
    sid = session.get("sid", "")
    path = yt_svc.TOKEN_DIR / f"{sid}.json"
    if path.exists():
        path.unlink()
    if yt_svc.BROWSER_AUTH_FILE.exists():
        yt_svc.BROWSER_AUTH_FILE.unlink()
    session.pop("yt_connected", None)
    flash("YouTube Music disconnected.", "info")
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Playlist selection
# ---------------------------------------------------------------------------

@main_bp.route("/select")
def select():
    if not _spotify_connected() or not _youtube_connected():
        flash("Please connect both accounts first.", "error")
        return redirect(url_for("main.index"))

    try:
        sp, session["spotify_token"] = sp_svc.client_from_token(session["spotify_token"])
        sp_playlists = sp_svc.get_user_playlists(sp)
    except PermissionError:
        session.pop("spotify_token", None)
        flash(
            "Your Spotify session is missing required permissions. Please reconnect your account.",
            "error",
        )
        return redirect(url_for("main.login_spotify"))
    except Exception as exc:
        flash(f"Error loading Spotify playlists: {exc}", "error")
        return redirect(url_for("main.index"))

    yt_playlists: list = []
    yt_error: str | None = None
    try:
        yt = yt_svc.client_from_session(session["sid"])
        yt_playlists = yt_svc.get_user_playlists(yt)
    except Exception as exc:
        yt_error = str(exc)

    return render_template(
        "select.html",
        spotify_playlists=sp_playlists,
        youtube_playlists=yt_playlists,
        yt_error=yt_error,
        **_template_ctx(),
    )


# ---------------------------------------------------------------------------
# Compare – background job
# ---------------------------------------------------------------------------

def _run_compare_job(job_id: str, sp_playlist_id: str, yt_playlist_id: str,
                     sp_token: dict, sid: str) -> None:
    import logging
    log = logging.getLogger(__name__)
    job = _compare_jobs[job_id]
    try:
        # Phase 1: fetch playlists
        job["phase"] = "Fetching playlists"
        sp, _ = sp_svc.client_from_token(sp_token)  # raises PermissionError if scopes missing
        yt = yt_svc.client_from_session(sid)
        sp_tracks = sp_svc.get_playlist_tracks(sp, sp_playlist_id)
        yt_tracks = yt_svc.get_playlist_tracks(yt, yt_playlist_id)
        log.warning("COMPARE DEBUG: sp=%d tracks, yt=%d tracks", len(sp_tracks), len(yt_tracks))

        # Phase 2: match songs
        def on_progress(current: int, total: int, phase: str) -> None:
            job["current"] = current
            job["total"] = total
            job["phase"] = phase

        diff = diff_playlists(
            sp_tracks, yt_tracks,
            lambda q: yt_svc.search_track(yt, q),
            lambda q: sp_svc.search_track(sp, q),
            on_progress=on_progress,
        )
        job["status"] = "done"
        job["result"] = {
            "diff": diff,
            "spotify_playlist": sp_playlist_id,
            "youtube_playlist": yt_playlist_id,
            "sp_track_count": len(sp_tracks),
            "yt_track_count": len(yt_tracks),
        }
        log.warning(
            "COMPARE DEBUG: sp_tracks=%d, yt_tracks=%d, matched=%d, only_in_spotify=%d, only_in_youtube=%d",
            len(sp_tracks), len(yt_tracks),
            len(diff["matched"]), len(diff["only_in_spotify"]), len(diff["only_in_youtube"]),
        )
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)


@main_bp.route("/compare", methods=["POST"])
def compare():
    sp_playlist_id = request.form.get("spotify_playlist", "")
    yt_playlist_id = request.form.get("youtube_playlist", "")
    yt_url = request.form.get("youtube_playlist_url", "")

    if yt_url:
        yt_playlist_id = _parse_yt_playlist_id(yt_url)
    if not sp_playlist_id or not yt_playlist_id:
        flash("Please select a Spotify playlist and a YouTube Music playlist (or paste a URL).", "error")
        return redirect(url_for("main.select"))

    job_id = uuid.uuid4().hex
    _compare_jobs[job_id] = {
        "status": "running", "current": 0, "total": 1,
        "phase": "Starting…", "error": None, "result": None,
    }
    t = threading.Thread(
        target=_run_compare_job,
        args=(job_id, sp_playlist_id, yt_playlist_id,
              session["spotify_token"].copy(), session["sid"]),
        daemon=True,
    )
    t.start()
    return redirect(url_for("main.compare_progress", job_id=job_id))


@main_bp.route("/compare/progress/<job_id>")
def compare_progress(job_id: str):
    return render_template("compare_progress.html", job_id=job_id, **_template_ctx())


@main_bp.route("/compare/status/<job_id>")
def compare_status(job_id: str):
    job = _compare_jobs.get(job_id, {})
    status = job.get("status", "error")
    if status == "done":
        return jsonify({"status": "done", "redirect": url_for("main.compare_result", job_id=job_id)})
    if status == "error":
        return jsonify({"status": "error", "error": job.get("error", "Unknown error")})
    return jsonify({
        "status": "running",
        "current": job.get("current", 0),
        "total": job.get("total", 1),
        "phase": job.get("phase", "Working…"),
    })


@main_bp.route("/compare/result/<job_id>")
def compare_result(job_id: str):
    job = _compare_jobs.get(job_id, {})
    if job.get("status") != "done" or not job.get("result"):
        flash("Comparison not found or still in progress.", "error")
        return redirect(url_for("main.select"))
    result = job["result"]
    session["last_diff"] = result["diff"]
    _compare_jobs.pop(job_id, None)
    return render_template(
        "diff.html",
        diff=result["diff"],
        spotify_playlist=result["spotify_playlist"],
        youtube_playlist=result["youtube_playlist"],
        sp_track_count=result.get("sp_track_count", 0),
        yt_track_count=result.get("yt_track_count", 0),
        **_template_ctx(),
    )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@main_bp.route("/sync", methods=["POST"])
def sync():
    sp_playlist_id = request.form.get("spotify_playlist", "")
    yt_playlist_id = request.form.get("youtube_playlist", "")
    sp2yt_ids = request.form.getlist("sp2yt")
    yt2sp_ids = request.form.getlist("yt2sp")

    errors: list[str] = []
    added_to_youtube = 0
    added_to_spotify = 0

    if sp2yt_ids:
        try:
            yt = yt_svc.client_from_session(session["sid"])
            added_to_youtube = yt_svc.add_tracks_to_playlist(yt, yt_playlist_id, sp2yt_ids)
        except Exception as exc:
            errors.append(f"YouTube Music: {exc}")

    if yt2sp_ids:
        try:
            sp, session["spotify_token"] = sp_svc.client_from_token(session["spotify_token"])
            added_to_spotify = sp_svc.add_tracks_to_playlist(sp, sp_playlist_id, yt2sp_ids)
        except Exception as exc:
            errors.append(f"Spotify: {exc}")

    return render_template(
        "result.html",
        added_to_youtube=added_to_youtube,
        added_to_spotify=added_to_spotify,
        errors=errors,
        **_template_ctx(),
    )


# ---------------------------------------------------------------------------
# Watch – one-way Spotify → YouTube Music auto-sync setup
# ---------------------------------------------------------------------------

@main_bp.route("/watch", methods=["GET", "POST"])
def watch():
    if not _spotify_connected() or not _youtube_connected():
        flash("Please connect both accounts first.", "error")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        sp_playlist_id = request.form.get("spotify_playlist", "")
        yt_playlist_id = request.form.get("youtube_playlist", "")
        yt_url = request.form.get("youtube_playlist_url", "")
        interval = int(request.form.get("interval", 300))

        if yt_url:
            yt_playlist_id = _parse_yt_playlist_id(yt_url)

        if not sp_playlist_id or not yt_playlist_id:
            flash("Please select both a Spotify and a YouTube Music playlist.", "error")
            return redirect(url_for("main.watch"))

        # Fetch current Spotify track IDs so only future additions are synced
        try:
            sp, session["spotify_token"] = sp_svc.client_from_token(session["spotify_token"])
            existing_tracks = sp_svc.get_playlist_tracks(sp, sp_playlist_id)
            existing_ids = [t["id"] for t in existing_tracks]
        except Exception as exc:
            flash(f"Could not fetch Spotify tracks: {exc}", "error")
            return redirect(url_for("main.watch"))

        config = {
            "spotify_playlist_id": sp_playlist_id,
            "youtube_playlist_id": yt_playlist_id,
            "spotify_token": session["spotify_token"],
            "poll_interval_seconds": interval,
        }
        _WATCHER_CONFIG.write_text(json.dumps(config, indent=2))

        # Seed state so the watcher skips songs already in the playlist
        state = {"synced_spotify_track_ids": existing_ids}
        _WATCHER_STATE.write_text(json.dumps(state, indent=2))

        flash(
            f"Watch configured! {len(existing_ids)} existing tracks noted (won't be re-added). "
            "Run  python sync_watcher.py  to start polling.",
            "success",
        )
        return redirect(url_for("main.watch"))

    # GET – load playlists for the form
    sp_playlists: list = []
    try:
        sp, session["spotify_token"] = sp_svc.client_from_token(session["spotify_token"])
        sp_playlists = sp_svc.get_user_playlists(sp)
    except Exception as exc:
        flash(f"Error loading Spotify playlists: {exc}", "error")

    yt_playlists: list = []
    yt_error: str | None = None
    try:
        yt = yt_svc.client_from_session(session["sid"])
        yt_playlists = yt_svc.get_user_playlists(yt)
    except Exception as exc:
        yt_error = str(exc)

    watcher_config = None
    if _WATCHER_CONFIG.exists():
        watcher_config = json.loads(_WATCHER_CONFIG.read_text())

    synced_count = 0
    if _WATCHER_STATE.exists():
        synced_count = len(json.loads(_WATCHER_STATE.read_text()).get("synced_spotify_track_ids", []))

    return render_template(
        "watch.html",
        spotify_playlists=sp_playlists,
        youtube_playlists=yt_playlists,
        yt_error=yt_error,
        watcher_config=watcher_config,
        synced_count=synced_count,
        **_template_ctx(),
    )

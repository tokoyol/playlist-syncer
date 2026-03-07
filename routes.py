"""Flask routes – authentication, playlist selection, comparison, and sync."""

from __future__ import annotations

import uuid

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
    if yt_svc.has_browser_auth():
        return True
    sid = session.get("sid", "")
    return yt_svc.load_token(sid) is not None


def _template_ctx() -> dict:
    return {
        "spotify_connected": _spotify_connected(),
        "youtube_connected": _youtube_connected(),
    }


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
        token_info = sp_svc.exchange_code(code)
        session["spotify_token"] = token_info
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
        session["yt_device_code"] = flow["device_code"]
        session["yt_interval"] = flow.get("interval", 5)
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
    """AJAX endpoint polled by the auth page."""
    device_code = request.form.get("device_code", "")
    interval = int(request.form.get("interval", 5))
    sid = _ensure_session_id()

    token = yt_svc.poll_for_token(device_code, interval=interval, timeout=interval + 2)
    if token and "access_token" in token:
        yt_svc.save_token(sid, token)
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
# Compare playlists (diff)
# ---------------------------------------------------------------------------

def _parse_yt_playlist_id(val: str) -> str | None:
    """Extract playlist ID from URL or return as-is if already an ID."""
    if not val or len(val) < 5:
        return None
    if "list=" in val:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(val)
        qs = parse_qs(parsed.query)
        return qs.get("list", [None])[0]
    return val if val.startswith(("PL", "OLAK", "VL")) else None


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

    try:
        sp, session["spotify_token"] = sp_svc.client_from_token(session["spotify_token"])
        sp_tracks = sp_svc.get_playlist_tracks(sp, sp_playlist_id)
    except Exception as exc:
        flash(f"Error reading Spotify playlist: {exc}", "error")
        return redirect(url_for("main.select"))

    try:
        yt = yt_svc.client_from_session(session["sid"])
        yt_tracks = yt_svc.get_playlist_tracks(yt, yt_playlist_id)
    except Exception as exc:
        flash(f"Error reading YouTube playlist: {exc}", "error")
        return redirect(url_for("main.select"))

    def search_yt(q: str):
        return yt_svc.search_track(yt, q)

    def search_sp(q: str):
        return sp_svc.search_track(sp, q)

    diff = diff_playlists(sp_tracks, yt_tracks, search_yt, search_sp)

    session["last_diff"] = diff

    return render_template(
        "diff.html",
        diff=diff,
        spotify_playlist=sp_playlist_id,
        youtube_playlist=yt_playlist_id,
        **_template_ctx(),
    )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@main_bp.route("/sync", methods=["POST"])
def sync():
    sp_playlist_id = request.form.get("spotify_playlist", "")
    yt_playlist_id = request.form.get("youtube_playlist", "")

    sp2yt_ids = request.form.getlist("sp2yt")   # YouTube video IDs to add
    yt2sp_ids = request.form.getlist("yt2sp")    # Spotify track IDs to add

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

import os
from dotenv import load_dotenv

load_dotenv(override=True)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-fallback-secret")

    SESSION_TYPE = "filesystem"
    SESSION_FILE_DIR = os.path.join(os.path.dirname(__file__), ".sessions")
    SESSION_PERMANENT = False

    SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID", "")
    SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET", "")
    SPOTIPY_REDIRECT_URI = os.getenv(
        "SPOTIPY_REDIRECT_URI", "http://127.0.0.1:5000/callback/spotify"
    )
    SPOTIFY_SCOPES = (
        "playlist-read-private "
        "playlist-read-collaborative "
        "playlist-modify-public "
        "playlist-modify-private"
    )

    YTMUSIC_CLIENT_ID = os.getenv("YTMUSIC_CLIENT_ID", "")
    YTMUSIC_CLIENT_SECRET = os.getenv("YTMUSIC_CLIENT_SECRET", "")

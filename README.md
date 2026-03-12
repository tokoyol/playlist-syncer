# Playlist Syncer – Spotify / YouTube Music

Sync playlists between Spotify and YouTube Music. Select one playlist from each platform and the app will find missing songs and add them to the other side.

## Setup

1. **Clone / download** this project.

2. **Create a virtual environment** and install dependencies:

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

3. **Copy `.env.example` to `.env`** and fill in your credentials:

   ```bash
   cp .env.example .env
   ```

   - **Spotify**: Create an app at <https://developer.spotify.com/dashboard>, add
     `http://127.0.0.1:5000/callback/spotify` as a Redirect URI, and copy the Client ID / Secret.
     (Spotify requires `127.0.0.1` instead of `localhost` for local development.)
   - **YouTube Music**: In the Google Cloud Console, create an OAuth 2.0 client
     of type *TVs and Limited Input devices* and copy the Client ID / Secret.

4. **YouTube Music 400 errors?** If you get "Request contains an invalid argument" when loading playlists, use browser auth instead:

   ```bash
   python setup_ytmusic_browser.py
   ```
   Copy request headers from music.youtube.com (DevTools > Network > filter by `/browse`), paste them, type `done`, then Enter. The script creates `ytmusic_browser.json`; the app will use it automatically.

5. **Run the app**:

   ```bash
   python app.py
   ```

   Open <http://127.0.0.1:5000> in your browser.

## Usage

### Manual sync (one-off)

1. Connect both your Spotify and YouTube Music accounts.
2. Pick a playlist from each platform.
3. Review the diff showing which songs are missing on each side.
4. Click **Sync** to add the missing songs.

### Auto-sync (Spotify → YouTube Music)

Automatically mirror new Spotify additions to a YouTube Music playlist in the background.

1. With the web app running, go to <http://127.0.0.1:5000/watch>.
2. Select the Spotify playlist to watch and the YouTube Music playlist to mirror to.
3. Choose a poll interval and click **Save Watch Config**.
   This writes `watcher_config.json` and seeds `watcher_state.json` with existing tracks so nothing already in the playlist gets re-added.
4. In a terminal (venv active), run the watcher:

   ```bash
   python sync_watcher.py
   ```

   Every poll cycle it fetches the Spotify playlist, finds any songs added since the last check, searches YouTube Music for the best match, and adds them.

   To run it silently in the background on Windows:

   ```bash
   pythonw sync_watcher.py
   ```

**Files created by the watcher**

| File | Purpose |
|---|---|
| `watcher_config.json` | Playlist IDs, Spotify token, poll interval |
| `watcher_state.json` | Track IDs already synced (prevents duplicates) |

> **Note:** The watcher needs an active internet connection to reach Spotify and YouTube Music. If it fails to refresh the Spotify token, re-visit `/watch` in the web app and re-save the config to write a fresh token.

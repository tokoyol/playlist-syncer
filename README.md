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

1. Connect both your Spotify and YouTube Music accounts.
2. Pick a playlist from each platform.
3. Review the diff showing which songs are missing on each side.
4. Click **Sync** to add the missing songs.

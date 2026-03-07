#!/usr/bin/env python
"""Create ytmusic_browser.json for browser auth (workaround when OAuth returns 400)."""

from pathlib import Path

import ytmusicapi

OUTPUT = Path(__file__).resolve().parent / "ytmusic_browser.json"

print("Paste your request headers from music.youtube.com (DevTools > Network > /browse).")
print("Paste, then type 'done' on a new line and press Enter:")
print()

lines = []
while True:
    line = input()
    if line.strip().lower() == "done":
        break
    lines.append(line)
headers_raw = "\n".join(lines)
if not headers_raw.strip():
    print("No headers provided. Exiting.")
    exit(1)

result = ytmusicapi.setup(filepath=str(OUTPUT), headers_raw=headers_raw)
print(f"\nSaved to {OUTPUT}")
print("Restart the app and YouTube Music will use browser auth.")

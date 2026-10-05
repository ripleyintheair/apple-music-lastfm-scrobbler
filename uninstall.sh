#!/bin/bash

# ─── Apple Music → Last.fm Scrobbler Uninstaller ────────────────────────────

PLIST_NAME="com.apple-music-lastfm-scrobbler"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

echo "Stopping scrobbler service..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true

if [ -f "$PLIST_PATH" ]; then
    rm "$PLIST_PATH"
    echo "Removed launchd plist."
else
    echo "No plist found (service may not have been installed)."
fi

echo ""
echo "Service stopped and removed."
echo "Your config.ini, scrobbles.db, and logs are still in place."
echo "Delete this folder to remove everything."

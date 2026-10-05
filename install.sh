#!/bin/bash
set -e

# ─── Apple Music → Last.fm Scrobbler Installer ──────────────────────────────
#
# This script sets up the scrobbler as a background daemon on macOS using
# launchd, so it starts automatically when you log in and runs continuously.
#
# Run this AFTER you've completed setup:
#   1. cp config.example.ini config.ini
#   2. (fill in your Last.fm API key and secret)
#   3. python3 scrobbler.py setup
#   4. python3 scrobbler.py test
#   5. ./install.sh
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.apple-music-lastfm-scrobbler"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="$HOME/Library/Logs"

# Check that Python 3 is available
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3 from python.org or via Homebrew."
    exit 1
fi

# Check that config exists
if [ ! -f "$SCRIPT_DIR/config.ini" ]; then
    echo "Error: config.ini not found in $SCRIPT_DIR"
    echo "Run these steps first:"
    echo "  cp config.example.ini config.ini"
    echo "  (edit config.ini with your Last.fm credentials)"
    echo "  python3 scrobbler.py setup"
    exit 1
fi

# Unload existing service if present
if launchctl list | grep -q "$PLIST_NAME" 2>/dev/null; then
    echo "Stopping existing service..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# Create the launchd plist
echo "Creating launchd service..."
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>${SCRIPT_DIR}/scrobbler.py</string>
        <string>run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/apple-music-scrobbler.out.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/apple-music-scrobbler.err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF

# Load the service
echo "Starting service..."
launchctl load "$PLIST_PATH"

echo ""
echo "Done! The scrobbler is now running in the background."
echo ""
echo "Useful commands:"
echo "  View logs:       tail -f $SCRIPT_DIR/scrobbler.log"
echo "  Check status:    launchctl list | grep $PLIST_NAME"
echo "  Stop service:    launchctl unload $PLIST_PATH"
echo "  Restart service: launchctl unload $PLIST_PATH && launchctl load $PLIST_PATH"
echo "  Uninstall:       ./uninstall.sh"
echo ""
echo "NOTE: The first time the daemon runs, macOS will prompt you to grant"
echo "      automation permission for Terminal/python3 to control Music."
echo "      Go to System Settings → Privacy & Security → Automation and"
echo "      make sure python3 (or Terminal) is allowed to control Music."

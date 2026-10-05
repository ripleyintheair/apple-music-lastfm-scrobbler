#!/usr/bin/env python3
"""
Apple Music → Last.fm Scrobbler

Scrobbles your Apple Music listening history — including plays from
HomePods, iPhones, and any device on your Apple ID — to Last.fm.

The macOS Music app syncs play history across all your devices.
This script polls that history and submits new plays to Last.fm.

Usage:
    python3 scrobbler.py setup    # Authenticate with Last.fm (one-time)
    python3 scrobbler.py test     # Verify Music app connection
    python3 scrobbler.py run      # Start scrobbling (runs forever)
"""

import subprocess
import sqlite3
import hashlib
import urllib.request
import urllib.parse
import json
import time
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.ini"
DB_FILE = SCRIPT_DIR / "scrobbles.db"
LOG_FILE = SCRIPT_DIR / "scrobbler.log"

# ─── Constants ───────────────────────────────────────────────────────────────

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_AUTH_URL = "https://www.last.fm/api/auth/"
DEFAULT_POLL_INTERVAL = 300  # 5 minutes
DEFAULT_LOOKBACK_DAYS = 7

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scrobbler")


# ═════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═════════════════════════════════════════════════════════════════════════════

def load_config():
    """Load settings from config.ini. Returns a dict."""
    import configparser
    cfg = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        log.error(f"Config file not found: {CONFIG_FILE}")
        log.error("Copy config.example.ini to config.ini and fill in your Last.fm API credentials.")
        sys.exit(1)
    cfg.read(CONFIG_FILE)
    return {
        "api_key": cfg.get("lastfm", "api_key", fallback="").strip(),
        "api_secret": cfg.get("lastfm", "api_secret", fallback="").strip(),
        "session_key": cfg.get("lastfm", "session_key", fallback="").strip(),
        "poll_interval": cfg.getint("scrobbler", "poll_interval", fallback=DEFAULT_POLL_INTERVAL),
        "lookback_days": cfg.getint("scrobbler", "lookback_days", fallback=DEFAULT_LOOKBACK_DAYS),
    }


def save_session_key(session_key):
    """Write the session key back into config.ini."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    if not cfg.has_section("lastfm"):
        cfg.add_section("lastfm")
    cfg.set("lastfm", "session_key", session_key)
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)
    log.info("Session key saved to config.ini.")


# ═════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ═════════════════════════════════════════════════════════════════════════════

def init_db():
    """Create the scrobbles table if it doesn't exist. Returns a connection."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrobbles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            artist      TEXT    NOT NULL,
            track       TEXT    NOT NULL,
            album       TEXT,
            duration    INTEGER,
            played_at   TEXT    NOT NULL,
            scrobbled_at TEXT   NOT NULL,
            UNIQUE(artist, track, played_at)
        )
    """)
    # Last-seen play count per track, used to detect repeat plays between polls
    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_state (
            persistent_id TEXT    PRIMARY KEY,
            play_count    INTEGER NOT NULL,
            played_at     TEXT    NOT NULL
        )
    """)
    conn.commit()
    migrate_db(conn)
    return conn


def migrate_db(conn):
    """Apply one-time schema/data fixes, tracked with PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        # Versions before this one stored played_at shifted by the UTC offset:
        # AppleScript yields local wall-clock seconds since 1970, which were
        # then passed to fromtimestamp() as if they were Unix time. Undo that
        # so existing rows match the corrected times and aren't re-scrobbled.
        rows = conn.execute("SELECT id, played_at FROM scrobbles").fetchall()
        for row_id, played_at in rows:
            wall_seconds = datetime.fromisoformat(played_at).timestamp()
            fixed = datetime(1970, 1, 1) + timedelta(seconds=wall_seconds)
            conn.execute(
                "UPDATE OR IGNORE scrobbles SET played_at = ? WHERE id = ?",
                (fixed.isoformat(timespec="seconds"), row_id),
            )
        conn.execute("ALTER TABLE scrobbles ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        log.info(f"Database migrated: corrected played_at on {len(rows)} existing row(s).")


def get_track_state(conn, persistent_id):
    """Return (play_count, played_at) last seen for a track, or None."""
    return conn.execute(
        "SELECT play_count, played_at FROM track_state WHERE persistent_id = ?",
        (persistent_id,),
    ).fetchone()


def save_track_state(conn, persistent_id, play_count, played_at):
    conn.execute(
        "INSERT OR REPLACE INTO track_state (persistent_id, play_count, played_at) VALUES (?, ?, ?)",
        (persistent_id, play_count, played_at),
    )
    conn.commit()


def already_scrobbled(conn, artist, track, played_at):
    """Check if we've already scrobbled this specific play."""
    row = conn.execute(
        "SELECT 1 FROM scrobbles WHERE artist = ? AND track = ? AND played_at = ?",
        (artist, track, played_at),
    ).fetchone()
    return row is not None


def record_scrobble(conn, artist, track, album, duration, played_at, status="accepted"):
    """Record a submitted play in the database ('accepted' or 'rejected' by Last.fm)."""
    conn.execute(
        "INSERT OR IGNORE INTO scrobbles (artist, track, album, duration, played_at, scrobbled_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (artist, track, album, duration, played_at, datetime.now().isoformat(), status),
    )
    conn.commit()


# ═════════════════════════════════════════════════════════════════════════════
#  LAST.FM API
# ═════════════════════════════════════════════════════════════════════════════

def lastfm_sign(params, api_secret):
    """
    Generate a Last.fm API signature.
    Sort params alphabetically, concatenate key+value pairs, append secret, MD5.
    """
    sig_string = ""
    for key in sorted(params):
        if key == "format":
            continue  # 'format' is excluded from signature
        sig_string += key + str(params[key])
    sig_string += api_secret
    return hashlib.md5(sig_string.encode("utf-8")).hexdigest()


def lastfm_request(params, api_secret, post=False):
    """Make a request to the Last.fm API. Returns parsed JSON."""
    params["format"] = "json"
    params["api_sig"] = lastfm_sign(params, api_secret)

    encoded = urllib.parse.urlencode(params).encode("utf-8")

    if post:
        req = urllib.request.Request(LASTFM_API_URL, data=encoded)
    else:
        req = urllib.request.Request(f"{LASTFM_API_URL}?{encoded.decode()}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error(f"Last.fm API error {e.code}: {body}")
        return {"error": e.code, "message": body}
    except Exception as e:
        log.error(f"Last.fm request failed: {e}")
        return {"error": -1, "message": str(e)}


def lastfm_get_token(api_key, api_secret):
    """Step 1 of auth: get a request token."""
    result = lastfm_request({"method": "auth.getToken", "api_key": api_key}, api_secret)
    return result.get("token")


def lastfm_get_session(api_key, api_secret, token):
    """Step 3 of auth: exchange authorized token for a permanent session key."""
    result = lastfm_request(
        {"method": "auth.getSession", "api_key": api_key, "token": token},
        api_secret,
    )
    session = result.get("session", {})
    return session.get("key")


LASTFM_IGNORED_REASONS = {
    "1": "artist ignored",
    "2": "track ignored",
    "3": "timestamp too old",
    "4": "timestamp too new",
    "5": "daily scrobble limit exceeded",
}
LASTFM_RETRYABLE_IGNORED = {"5"}


def lastfm_scrobble(api_key, api_secret, session_key, artist, track, album, duration, timestamp):
    """
    Submit a scrobble to Last.fm.
    Returns "accepted", "rejected" (Last.fm ignored it and retrying won't help),
    or None (failed; try again next poll).
    """
    params = {
        "method": "track.scrobble",
        "api_key": api_key,
        "sk": session_key,
        "artist": artist,
        "track": track,
        "timestamp": timestamp,
    }
    if album:
        params["album"] = album
    if duration and duration > 0:
        params["duration"] = duration

    result = lastfm_request(params, api_secret, post=True)

    if "error" in result:
        log.error(f"  Scrobble failed: {result.get('message', 'unknown error')}")
        return None

    # Last.fm answers 200 even when it drops a scrobble; the details are in the body.
    scrobbles = result.get("scrobbles", {})
    if int(scrobbles.get("@attr", {}).get("accepted", 0)) == 1:
        log.info(f"  Scrobbled: {artist} — {track}")
        return "accepted"

    ignored = scrobbles.get("scrobble", {}).get("ignoredMessage", {})
    code = str(ignored.get("code", ""))
    reason = LASTFM_IGNORED_REASONS.get(code, f"unknown reason {code!r}: {result}")
    if code in LASTFM_RETRYABLE_IGNORED:
        log.warning(f"  Last.fm ignored {artist} — {track} ({reason}); will retry.")
        return None
    log.warning(f"  Last.fm rejected {artist} — {track} ({reason}); not retrying.")
    return "rejected"


# ═════════════════════════════════════════════════════════════════════════════
#  APPLE MUSIC (via AppleScript)
# ═════════════════════════════════════════════════════════════════════════════

def build_applescript(lookback_days):
    """
    Build the AppleScript that queries the Music app for recently played tracks.
    Returns tab-separated lines:
        name, artist, album, duration (sec), play count, persistent ID, played date
    where played date is "Y-M-D-S" (S = seconds since local midnight), built from
    date components so it doesn't depend on the system locale.

    Each property is fetched for all matching tracks in one Apple event, which is
    far faster than asking for every property of every track individually.
    """
    return f'''
set cutoffDate to (current date) - ({lookback_days} * days)

tell application "Music"
    if not running then return ""
    try
        set recent to a reference to (every track of library playlist 1 whose played date > cutoffDate)
        set nms to name of recent
        set arts to artist of recent
        set albs to album of recent
        set durs to duration of recent
        set cnts to played count of recent
        set pids to persistent ID of recent
        set pds to played date of recent
    on error
        return ""
    end try
end tell

set n to count of nms
repeat with lst in {{arts, albs, durs, cnts, pids, pds}}
    if (count of lst) is not n then error "Track list changed during query" number 9001
end repeat

set out to {{}}
repeat with i from 1 to n
    try
        set pd to item i of pds
        set dateStr to ((year of pd) as text) & "-" & ((month of pd) as integer) & "-" & (day of pd) & "-" & (time of pd)
        set end of out to (item i of nms) & tab & (item i of arts) & tab & ((item i of albs) as text) & tab & ((item i of durs) as integer) & tab & (item i of cnts) & tab & (item i of pids) & tab & dateStr
    end try
end repeat

set AppleScript's text item delimiters to linefeed
return out as text
'''


def parse_played_date(value):
    """Turn the script's "Y-M-D-S" played date into a local ISO timestamp string."""
    year, month, day, seconds = (int(p) for p in value.split("-"))
    dt = datetime(year, month, day) + timedelta(seconds=seconds)
    return dt.isoformat(timespec="seconds")


def get_recent_tracks(lookback_days):
    """
    Run the AppleScript and parse the results.
    Returns a list of dicts:
        {name, artist, album, duration, play_count, persistent_id, played_at}.
    """
    script = build_applescript(lookback_days)

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        log.warning("AppleScript timed out — Music app may be unresponsive.")
        return []
    except FileNotFoundError:
        log.error("osascript not found. This script must run on macOS.")
        return []

    if result.returncode != 0:
        log.warning(f"AppleScript error: {result.stderr.strip()}")
        return []

    tracks = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            log.debug(f"Skipping malformed line: {line!r}")
            continue

        name, artist, album, duration_str, count_str, persistent_id, date_str = parts

        # Skip tracks with missing essential fields
        if not name or not artist:
            continue

        try:
            played_at = parse_played_date(date_str)
            play_count = int(count_str)
        except ValueError:
            log.debug(f"Skipping line with bad date/count: {line!r}")
            continue

        try:
            duration = int(duration_str)
        except ValueError:
            duration = 0

        album = album.strip()
        if album == "missing value":
            album = ""

        tracks.append({
            "name": name.strip(),
            "artist": artist.strip(),
            "album": album,
            "duration": duration,
            "play_count": play_count,
            "persistent_id": persistent_id.strip(),
            "played_at": played_at,
        })

    return tracks


def plays_to_submit(track, prev_state):
    """
    Work out which plays of a track are new since the last poll.

    Music only remembers the most recent play time, but its play count tells us
    how many plays happened. Earlier repeat plays get estimated times, spaced
    one track-length apart before the latest play (a play is logged when the
    track finishes). Estimates never go back past the previous known play, so
    a play count that jumps (e.g. via iCloud sync) can't invent impossible plays.

    Returns a list of ISO timestamp strings, newest first.
    """
    latest = datetime.fromisoformat(track["played_at"])
    if prev_state is None:
        return [track["played_at"]]

    prev_count, prev_played_at = prev_state
    new_plays = track["play_count"] - prev_count
    if new_plays <= 0:
        # Count didn't rise, but the date moved — still count the latest play.
        return [track["played_at"]] if track["played_at"] != prev_played_at else []

    plays = [track["played_at"]]
    step = max(track["duration"], 30)
    earliest_allowed = datetime.fromisoformat(prev_played_at)
    for k in range(1, new_plays):
        estimated = latest - timedelta(seconds=k * step)
        if estimated <= earliest_allowed:
            log.info(f"  {track['artist']} — {track['name']}: play count rose by {new_plays}, "
                     f"but only {k} play(s) fit since the last known play.")
            break
        plays.append(estimated.isoformat(timespec="seconds"))
    return plays


# ═════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

def cmd_setup():
    """Interactive Last.fm authentication flow."""
    config = load_config()

    if not config["api_key"] or not config["api_secret"]:
        print("\n╔══════════════════════════════════════════════════════════╗")
        print("║  You need a Last.fm API account (it's free).            ║")
        print("║                                                          ║")
        print("║  1. Go to: https://www.last.fm/api/account/create       ║")
        print("║  2. Fill in any app name (e.g. 'My Scrobbler')          ║")
        print("║  3. Copy the API Key and Shared Secret into config.ini  ║")
        print("║  4. Run this command again.                              ║")
        print("╚══════════════════════════════════════════════════════════╝\n")
        return

    if config["session_key"]:
        print("You already have a session key in config.ini.")
        answer = input("Re-authenticate? (y/N): ").strip().lower()
        if answer != "y":
            return

    print("\nRequesting authorization token from Last.fm...")
    token = lastfm_get_token(config["api_key"], config["api_secret"])
    if not token:
        print("Failed to get a token. Check your API key and secret.")
        return

    auth_url = f"{LASTFM_AUTH_URL}?api_key={config['api_key']}&token={token}"
    print(f"\nOpen this URL in your browser and click 'Yes, allow access':\n")
    print(f"  {auth_url}\n")
    input("Press Enter after you've authorized the app...")

    print("Exchanging token for session key...")
    session_key = lastfm_get_session(config["api_key"], config["api_secret"], token)
    if not session_key:
        print("Failed to get session key. Did you authorize the app in the browser?")
        return

    save_session_key(session_key)
    print("\nDone! You're authenticated with Last.fm.")
    print("Run 'python3 scrobbler.py test' to verify your Music app connection,")
    print("then 'python3 scrobbler.py run' to start scrobbling.")


def cmd_test():
    """Test the connection to the Music app and show recent tracks."""
    print("Querying Music app for tracks played in the last 7 days...")
    print("(The Music app must be running. This may take a moment for large libraries.)\n")

    tracks = get_recent_tracks(7)

    if not tracks:
        print("No recently played tracks found.")
        print("\nTroubleshooting:")
        print("  - Is the Music app open?")
        print("  - Have you played anything recently?")
        print("  - You may need to grant Terminal (or iTerm) automation")
        print("    permission in System Settings → Privacy & Security →")
        print("    Automation → allow access to 'Music'.")
        return

    print(f"Found {len(tracks)} recently played track(s):\n")
    for t in tracks[:20]:  # Show first 20
        print(f"  {t['artist']} — {t['name']}")
        print(f"    Album: {t['album']}  |  Last played: {t['played_at']}  |  Plays: {t['play_count']}")
    if len(tracks) > 20:
        print(f"\n  ... and {len(tracks) - 20} more.")

    print(f"\nLooks good! These tracks will be scrobbled when you run the daemon.")


def cmd_run():
    """Main scrobbling loop. Runs forever, polling at the configured interval."""
    config = load_config()

    # Validate config
    for key in ("api_key", "api_secret", "session_key"):
        if not config[key]:
            log.error(f"Missing '{key}' in config.ini. Run 'python3 scrobbler.py setup' first.")
            sys.exit(1)

    conn = init_db()
    poll_interval = config["poll_interval"]
    lookback_days = config["lookback_days"]

    log.info("=" * 60)
    log.info("Apple Music → Last.fm Scrobbler started")
    log.info(f"  Poll interval:  {poll_interval}s")
    log.info(f"  Lookback:       {lookback_days} days")
    log.info(f"  Database:       {DB_FILE}")
    log.info("=" * 60)

    while True:
        try:
            tracks = get_recent_tracks(lookback_days)
            new_count = 0

            for t in tracks:
                prev_state = get_track_state(conn, t["persistent_id"])
                all_done = True

                for played_at in reversed(plays_to_submit(t, prev_state)):  # oldest first
                    if already_scrobbled(conn, t["artist"], t["name"], played_at):
                        continue

                    # played_at is local time; .timestamp() converts it to Unix time
                    timestamp = int(datetime.fromisoformat(played_at).timestamp())

                    status = lastfm_scrobble(
                        config["api_key"],
                        config["api_secret"],
                        config["session_key"],
                        t["artist"],
                        t["name"],
                        t["album"],
                        t["duration"],
                        timestamp,
                    )

                    if status is None:
                        all_done = False
                    else:
                        record_scrobble(conn, t["artist"], t["name"], t["album"], t["duration"],
                                        played_at, status)
                        if status == "accepted":
                            new_count += 1

                    # Small delay between scrobbles to be polite to Last.fm
                    time.sleep(0.5)

                # Only advance the play-count baseline once every play is submitted,
                # so failed plays are recomputed and retried on the next poll.
                if all_done:
                    save_track_state(conn, t["persistent_id"], t["play_count"], t["played_at"])

            if new_count > 0:
                log.info(f"Scrobbled {new_count} new track(s).")
            else:
                log.debug("No new tracks to scrobble.")

        except KeyboardInterrupt:
            log.info("Shutting down.")
            conn.close()
            sys.exit(0)
        except Exception as e:
            log.error(f"Error in poll loop: {e}", exc_info=True)

        try:
            time.sleep(poll_interval)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            conn.close()
            sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

USAGE = """
Apple Music → Last.fm Scrobbler

Commands:
    python3 scrobbler.py setup    Authenticate with Last.fm (one-time)
    python3 scrobbler.py test     Show recent tracks from Music app
    python3 scrobbler.py run      Start the scrobbler daemon
""".strip()

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    if command == "setup":
        cmd_setup()
    elif command == "test":
        cmd_test()
    elif command == "run":
        cmd_run()
    elif command in ("-h", "--help", "help"):
        print(USAGE)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)

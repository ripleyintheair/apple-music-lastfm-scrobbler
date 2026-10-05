# Apple Music Last.fm Scrobbler for macOS — Scrobble HomePod, iPhone & Mac Plays

**Scrobble your Apple Music listening to Last.fm — including songs played on HomePod, iPhone, iPad, and Apple TV — using the play history that Apple Music already syncs to your Mac.**

Apple Music has no built-in Last.fm scrobbling, and most Apple Music scrobblers only see what's playing on the Mac in front of them. Music you play on a HomePod (or on your phone with no scrobbler app open) never reaches Last.fm. This scrobbler takes a different approach: Apple syncs play history from every device on your Apple ID into your Mac's Music library, so it reads that history and submits the new plays to Last.fm.

- 🏠 **HomePod scrobbling**: plays from HomePod and HomePod mini get scrobbled, with no AirPlay tricks and no network sniffing
- 📱 **All your Apple devices**: iPhone, iPad, Apple TV, and Mac, as long as the plays sync to your Mac's library
- 🔁 **Counts repeat plays**: uses play counts to catch songs played more than once between checks
- 🪶 **Lightweight**: one Python file, standard library only, no dependencies, no app to keep open
- 🔒 **Private**: talks only to the Music app on your Mac and to Last.fm, nothing else
- 🚀 **Runs in the background**: installs as a launchd agent that starts at login

## How it works

1. Every 5 minutes (configurable), the scrobbler uses AppleScript to ask the macOS **Music** app for tracks played in the last 7 days.
2. Music only remembers the *last* time each song was played, but it also keeps a **play count**. The scrobbler stores both, so if a song's count goes up by 3, it scrobbles 3 plays (earlier repeats get estimated times, spaced one song-length apart).
3. New plays go to the Last.fm API with their real play times. A local SQLite database keeps track of what's been sent, so nothing is scrobbled twice.

Because it reads plays after the fact, it is a **history-sync scrobbler**, not a "now playing" scrobbler. Last.fm accepts plays up to 14 days old, so late-syncing plays still count.

## Requirements

- A Mac running macOS with the **Music** app (tested on macOS 26 Tahoe)
- Python 3 (included with Xcode Command Line Tools; also available from python.org or Homebrew)
- An Apple Music subscription or iTunes Match with **Sync Library** turned on, on the Mac *and* on your other devices
- A free [Last.fm API account](https://www.last.fm/api/account/create)

## Installation

```bash
git clone https://github.com/ripleyintheair/apple-music-scrobbler.git
cd apple-music-scrobbler
cp config.example.ini config.ini
```

1. **Get Last.fm API keys.** Create an API account at <https://www.last.fm/api/account/create> (any app name works, no callback URL needed). Paste the **API key** and **shared secret** into `config.ini`.
2. **Connect your Last.fm account:**
   ```bash
   python3 scrobbler.py setup
   ```
   Open the link it prints, click **Yes, allow access**, then press Enter.
3. **Check that it can read Music:**
   ```bash
   python3 scrobbler.py test
   ```
   macOS will ask to let Terminal control Music. Allow it. You should see your recently played tracks with their play counts.
4. **Run it in the background:**
   ```bash
   ./install.sh
   ```
   This installs a launchd agent that starts at login and restarts if it stops. The first time it runs, allow **python3** to control Music in **System Settings → Privacy & Security → Automation**.
5. **Keep the Music app open.** The scrobbler only reads history while Music is running. It won't launch Music itself, so if Music is closed, nothing gets scrobbled. To have Music open at login, add it under **System Settings → General → Login Items**.

To run it in the foreground instead, use `python3 scrobbler.py run`.

## Configuration

`config.ini`:

| Setting | Default | What it does |
|---|---|---|
| `poll_interval` | `300` | Seconds between checks |
| `lookback_days` | `7` | How many days of history to read. Plays in this window that haven't been scrobbled yet are submitted, so the first run backfills them. Keep it under 14, Last.fm's limit. |

## Useful commands

```bash
tail -f scrobbler.log                                   # watch activity
launchctl list | grep apple-music-lastfm-scrobbler      # is it running?
./uninstall.sh                                          # stop and remove the background service
```

## Limitations

Honest notes so you know what to expect:

- **Plays from other devices arrive late.** HomePod and iPhone plays only reach Last.fm after Apple syncs them to your Mac. That's often within hours, sometimes a day or more. Apple's play-count syncing is occasionally unreliable, and the scrobbler can't scrobble a play your Mac never receives.
- **Repeat-play times are estimates.** Music stores only the most recent play time, so when a song is played several times between checks, the earlier plays are timed by working backwards from the last one.
- **Library tracks only.** Songs streamed without being added to your library aren't in the library's play history, so they can't be scrobbled.
- **Your Mac needs to be on, with Music open.** It scrobbles only while the Mac is awake and logged in *and* the Music app is running. Missed plays are picked up later, as long as it's within the 7-day lookback.
- **macOS permission prompts can stall it.** Controlling Music needs your approval under Automation. Until that prompt is answered, every check times out. The approval is tied to the specific python3 program, so a Homebrew or Xcode update that replaces python3 can make macOS ask again.
- **Very large libraries can be slow.** Each check asks Music to search the whole library for recent plays. On a very large library, or while Music is busy (for example, mid-sync), a check can run past the 2-minute limit and is retried next time.
- **macOS only**, since it relies on the Music app's AppleScript support.

## Troubleshooting

**`test` finds no tracks.** Make sure Music is open, you've played something from your library recently, and Terminal is allowed under **System Settings → Privacy & Security → Automation → Music**.

**HomePod plays never show up.** Check that **Sync Library** is on for your Mac (Music → Settings → General) and your iPhone (Settings → Apps → Music), and that the HomePod uses the same Apple ID. Then look for the song's play count to go up in Music on the Mac. If it doesn't, Apple hasn't synced the play yet.

**Nothing is being scrobbled, and the log is quiet.** Check that the Music app is open. When Music is closed, the scrobbler silently skips each check.

**"AppleScript timed out" in the log.** If it happens on every check, macOS is probably waiting for you to answer a permission prompt. Look for a dialog, or open **System Settings → Privacy & Security → Automation** and make sure **python3** is allowed to control Music. This can come back after python3 is updated. If timeouts are only occasional, Music was busy, and the scrobbler tries again on the next check.

**A scrobble was "rejected".** Last.fm refused it permanently (for example, the timestamp was more than 14 days old). The reason is in `scrobbler.log`.

## FAQ

**Does Apple Music support Last.fm?**
Not natively. Apple Music has no built-in scrobbling, so you need a third-party scrobbler like this one.

**Can you scrobble HomePod to Last.fm?**
Yes. HomePod doesn't expose what it's playing to other apps, but its plays sync to your Mac's Music library through Sync Library, and this scrobbler picks them up from there.

**Does it scrobble my iPhone?**
Yes, if your iPhone syncs your library. iPhone plays reach Last.fm through your Mac, with no app needed on the phone.

**Is my data sent anywhere?**
Only to Last.fm. Your API keys and listening history stay in `config.ini` and `scrobbles.db` on your Mac.

## Uninstall

```bash
./uninstall.sh
```

This stops and removes the background service. Delete the folder to remove everything else.

## Contributing

Improvements are welcome! If you've found a bug, have an idea, or got it working with a setup not covered here, please [open an issue](https://github.com/ripleyintheair/apple-music-scrobbler/issues) or send a pull request. Reports from other macOS versions and device setups are especially helpful.

## Credits

Developed with help from [Claude](https://claude.ai), Anthropic's AI assistant.

## License

[MIT](LICENSE)

---

*Keywords: Apple Music scrobbler, Last.fm scrobbler for Mac, scrobble HomePod to Last.fm, HomePod Last.fm, Apple Music Last.fm integration, macOS Music app scrobbler, iTunes scrobbler alternative, scrobble iPhone Apple Music.*

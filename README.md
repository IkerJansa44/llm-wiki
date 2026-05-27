# LLM Wiki

Obsidian vault plus repo-level tooling for an LLM-maintained technical wiki.

Create and open this folder in Obsidian:

```text
vault/
```

## YouTube Transcripts

The YouTube helper lives outside the vault so tooling does not appear in the graph. It uses the repo virtual environment.

Fetch a full transcript into `vault/raw/`:

```bash
.venv/bin/python scripts/youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Fetch only a range:

```bash
.venv/bin/python scripts/youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --start 12:30 --end 18:05
```

Accepted time formats include seconds, `MM:SS`, and `HH:MM:SS`.

If the video has YouTube chapters, the raw Markdown includes a `## Chapters`
section and groups transcript lines under matching `###` chapter headings. Videos
without chapters fall back to the flat timestamped `## Transcript` format.

YouTube `t=` URL parameters are preserved in the source URL but are not treated as
range extraction. Use `--start` and `--end` when you want only part of a video.

## Telegram to Codex

`scripts/telegram_codex.py` polls a Telegram bot and forwards text messages from one
allowed Telegram user into Codex:

```bash
TELEGRAM_BOT_TOKEN=123:abc
TELEGRAM_ALLOWED_USER_ID=123456789
.venv/bin/python scripts/telegram_codex.py
```

You can put those variables in `.env`; that file is gitignored. The script runs:

```bash
codex --ask-for-approval never exec --sandbox workspace-write --cd /Users/ikerjansa/Documents/llm-wiki --add-dir "/Users/ikerjansa/Library/Mobile Documents/iCloud~md~obsidian/Documents/llm-wiki-vault" -
```

Codex receives the Telegram text on stdin and writes its final answer back to the
same Telegram chat. Runtime state and Codex output files go under
`var/telegram-codex/`, which is also gitignored.

To keep it running on macOS, install a LaunchAgent at
`~/Library/LaunchAgents/com.llm-wiki.telegram-codex.plist`, then load it with:

```bash
mkdir -p var/telegram-codex
launchctl load ~/Library/LaunchAgents/com.llm-wiki.telegram-codex.plist
```

Notes:

- A bot can only receive messages sent to the bot directly, or messages visible to
  it in a group.
- Prefer `TELEGRAM_ALLOWED_USER_ID` over usernames because usernames can change.
- The installed Codex CLI uses the global option `--ask-for-approval never`; the
  `codex exec --ask-for-permisions never` spelling is not accepted by this CLI.

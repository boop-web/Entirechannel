# YouTube Channel Downloader (Docker)

Download **every** video from a YouTube channel — oldest first, with nothing
missed — using [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) inside Docker.

## What it does

- Grabs a channel's **entire upload history** (`/videos`), in **chronological
  order** (first video first), thanks to `--playlist-reverse`.
- Keeps a **download archive** (`downloads/downloaded.txt`). Every finished
  video is recorded, so you can stop and re-run anytime — it picks up exactly
  where it left off and **never re-downloads or skips** a video.
- Saves **best quality** video+audio merged to `.mp4`, and embeds metadata,
  thumbnail, and English subtitles.
- Organizes files as: `downloads/<Channel>/<YYYY-MM-DD> - <Title> [<id>].mp4`.

## Requirements

- Docker (and optionally Docker Compose). That's it — `ffmpeg` and `yt-dlp`
  live inside the image.
- Plenty of disk space. A large channel (like PewDiePie) is **terabytes** at
  full quality — see the tip below to cap quality.

## Quick start (Docker Compose)

```bash
# Build the image
docker compose build

# Download the default channel (PewDiePie), oldest video first
docker compose run --rm downloader

# Or pick any channel
CHANNEL_URL="https://www.youtube.com/@MrBeast" docker compose run --rm downloader
```

Videos appear in the `./downloads` folder next to these files.

## Quick start (plain Docker)

```bash
docker build -t yt-channel-downloader .

docker run --rm -v "$(pwd)/downloads:/downloads" \
  yt-channel-downloader "https://www.youtube.com/@PewDiePie"
```

## Resuming / making sure nothing is missed

Just run the **same command again**. The archive file makes the job
*idempotent*: finished videos are skipped instantly, and any that failed or
are newly uploaded get downloaded. Run it on a schedule (e.g. weekly) to keep
a channel mirrored.

To verify coverage, the number of IDs in `downloads/downloaded.txt` equals the
number of videos successfully downloaded:

```bash
wc -l downloads/downloaded.txt
```

## Common tweaks

Pass extra `yt-dlp` flags after the URL, or use env vars.

**Cap quality (saves huge amounts of space):**
```bash
docker run --rm -v "$(pwd)/downloads:/downloads" \
  -e FORMAT="bestvideo[height<=720]+bestaudio/best" \
  yt-channel-downloader "https://www.youtube.com/@PewDiePie"
```

**Audio only (MP3):**
```bash
docker run --rm -v "$(pwd)/downloads:/downloads" \
  yt-channel-downloader "https://www.youtube.com/@PewDiePie" \
  --extract-audio --audio-format mp3
```

**Also get Shorts or Livestreams** — point the URL at that tab:
```bash
docker run --rm -v "$(pwd)/downloads:/downloads" \
  yt-channel-downloader "https://www.youtube.com/@PewDiePie/shorts"
```

## Age-restricted / sign-in-required videos (cookies)

Some videos need a logged-in account. Export your browser cookies to
`downloads/cookies.txt` (use a "Get cookies.txt" browser extension) and add:

```bash
docker run --rm -v "$(pwd)/downloads:/downloads" \
  yt-channel-downloader "https://www.youtube.com/@PewDiePie" \
  --cookies /downloads/cookies.txt
```

## Notes & limits

- **Use responsibly.** Download only content you have the right to, and respect
  YouTube's Terms of Service and copyright. This tool is for personal/archival
  use.
- YouTube changes often. If downloads start failing, rebuild the image to get
  the latest `yt-dlp`: `docker compose build --no-cache`.
- The script sleeps a few seconds between requests to avoid hammering YouTube
  and reduce the chance of throttling/blocks. Don't set this to zero for large
  channels.

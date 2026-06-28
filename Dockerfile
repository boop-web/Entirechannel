FROM python:3.12-slim

# ffmpeg is needed to merge separate video/audio streams and to embed
# metadata, thumbnails and subtitles into the final files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp is the actively-maintained YouTube downloader. We install the latest
# release at build time; rebuild the image periodically to stay current with
# YouTube changes.
RUN pip install --no-cache-dir --upgrade yt-dlp

WORKDIR /app
COPY download.sh /app/download.sh
RUN chmod +x /app/download.sh

# Downloads land here; mount a host volume to keep them.
VOLUME ["/downloads"]

ENTRYPOINT ["/app/download.sh"]

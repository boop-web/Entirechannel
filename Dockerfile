FROM python:3.12-slim

# ffmpeg merges separate video/audio streams and embeds metadata, thumbnails
# and subtitles into the final files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY app.py index.html download.sh /app/
RUN chmod +x /app/download.sh

# Downloads land here; mount a host volume to keep them.
VOLUME ["/downloads"]
EXPOSE 8000

# Start the web UI. (The old CLI still works: override the entrypoint with
# /app/download.sh <channel-url> if you prefer the command line.)
CMD ["python", "/app/app.py"]

#!/usr/bin/env bash
#
# Download EVERY video from a YouTube channel, oldest first, missing none.
#
# Usage:
#   ./download.sh <CHANNEL_URL> [extra yt-dlp args...]
#
# Examples:
#   ./download.sh https://www.youtube.com/@PewDiePie
#   ./download.sh https://www.youtube.com/@PewDiePie --format "bestvideo[height<=1080]+bestaudio/best"
#
set -euo pipefail

CHANNEL_URL="${1:-${CHANNEL_URL:-}}"
if [[ -z "${CHANNEL_URL}" ]]; then
    echo "ERROR: no channel URL provided." >&2
    echo "Usage: $0 <CHANNEL_URL> [extra yt-dlp args...]" >&2
    exit 1
fi
# Drop the URL from "$@" so any remaining args are passed straight to yt-dlp.
[[ $# -gt 0 ]] && shift || true

# Where everything is written. /downloads is the container's mounted volume.
OUT_DIR="${OUT_DIR:-/downloads}"
mkdir -p "${OUT_DIR}"

# The archive file is the key to "never miss anyone". yt-dlp records the ID of
# every successfully-downloaded video here, so re-running skips finished ones
# and only fetches what is new or what failed last time. Safe to re-run anytime.
ARCHIVE_FILE="${ARCHIVE_FILE:-${OUT_DIR}/downloaded.txt}"

# Quality. Default to best video+audio merged into mp4. Override via FORMAT env
# or by passing --format on the command line.
FORMAT="${FORMAT:-bestvideo*+bestaudio/best}"

echo "=================================================================="
echo " Channel : ${CHANNEL_URL}"
echo " Output  : ${OUT_DIR}"
echo " Archive : ${ARCHIVE_FILE}"
echo " Format  : ${FORMAT}"
echo "=================================================================="

# Append /videos so we grab the channel's full upload history. yt-dlp resolves
# this to the channel's complete video list regardless of the URL form given.
case "${CHANNEL_URL}" in
    *"/videos"|*"/streams"|*"/shorts"|*"playlist?list="*) TARGET="${CHANNEL_URL}" ;;
    *) TARGET="${CHANNEL_URL}/videos" ;;
esac

exec yt-dlp \
    --download-archive "${ARCHIVE_FILE}" \
    --playlist-reverse \
    --format "${FORMAT}" \
    --merge-output-format mp4 \
    --output "${OUT_DIR}/%(uploader)s/%(upload_date>%Y-%m-%d)s - %(title)s [%(id)s].%(ext)s" \
    --restrict-filenames \
    --ignore-errors \
    --no-overwrites \
    --continue \
    --retries 10 \
    --fragment-retries 10 \
    --file-access-retries 5 \
    --concurrent-fragments 4 \
    --embed-metadata \
    --embed-thumbnail \
    --embed-subs \
    --sub-langs "en.*,live_chat" \
    --write-info-json \
    --write-description \
    --sleep-requests 1 \
    --sleep-interval 3 \
    --max-sleep-interval 8 \
    "$@" \
    "${TARGET}"

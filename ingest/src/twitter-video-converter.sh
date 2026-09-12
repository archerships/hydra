#!/usr/bin/env bash
set -euo pipefail

# twitter-video-converter.sh -- Convert video to Twitter-compatible MP4.
# Follows Twitter's recommended specs: H.264, AAC, 40fps max, 1:1 pixel aspect ratio.

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <input_video> [output_name.mp4]"
    exit 1
fi

INPUT="$1"
OUTPUT="${2:-${INPUT%.*}-twitter.mp4}"

echo "Converting '$INPUT' to Twitter-compatible format..."

# Recommended Specs:
# - Container: MP4
# - Video codec: H.264 (High Profile preferred)
# - Audio codec: AAC-LC
# - Frame rate: 40 fps or less
# - Max bitrate: 25 Mbps
# - Pixel Aspect Ratio: 1:1

ffmpeg -y -i "$INPUT" \
    -c:v libx264 \
    -pix_fmt yuv420p \
    -profile:v high \
    -level:v 4.2 \
    -movflags +faststart \
    -c:a aac \
    -b:a 128k \
    -r 30 \
    -vf "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))',format=yuv420p" \
    "$OUTPUT"

echo "Done! Output saved to: $OUTPUT"

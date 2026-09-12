#!/usr/bin/env bash
# Compress videos >= 500MB in ~/Downloads/jav/20060910_xx/ to mobile-friendly format

set -uo pipefail

SRC_DIR="$HOME/Downloads/jav/20060910_xx"
LOG_FILE="$SRC_DIR/_compress_log.txt"
MIN_SIZE_MB=500
STATE_FILE="$SRC_DIR/_compress_done.txt"

MIN_BYTES=$((MIN_SIZE_MB * 1024 * 1024))

if [ ! -f "$STATE_FILE" ]; then
    echo "=========================================" > "$LOG_FILE"
    echo "JAV compression started: $(date)" >> "$LOG_FILE"
    echo "Target: all files >= ${MIN_SIZE_MB}MB in $SRC_DIR" >> "$LOG_FILE"
    echo "=========================================" >> "$LOG_FILE"
fi

declare -A DONE_SET
if [ -f "$STATE_FILE" ]; then
    while IFS= read -r line; do
        DONE_SET["$line"]=1
    done < "$STATE_FILE"
fi

mapfile -t ALL_FILES < <(find "$SRC_DIR" -maxdepth 1 -type f -size +${MIN_SIZE_MB}M \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.avi' -o -iname '*.mkv' \) -printf '%s\t%p\n' | sort -rn | cut -f2-)

FILES=()
for f in "${ALL_FILES[@]}"; do
    BASENAME=$(basename "$f")
    if [ -z "${DONE_SET["$BASENAME"]:-}" ]; then
        FILES+=("$f")
    fi
done

if [ ${#FILES[@]} -eq 0 ]; then
    echo "All ${#ALL_FILES[@]} candidate files already processed. Nothing to do." | tee -a "$LOG_FILE"
    exit 0
fi

echo "$(date): Found ${#FILES[@]} files to compress (${#ALL_FILES[@]} candidates, ${#DONE_SET[@]} already done)" >> "$LOG_FILE"

TOTAL_ORIG=0
TOTAL_COMPRESSED=0
COUNT=0
TOTAL_PENDING=${#FILES[@]}

for FILE in "${FILES[@]}"; do
    COUNT=$((COUNT + 1))
    BASENAME=$(basename "$FILE")
    DIRNAME=$(dirname "$FILE")
    TEMP_FILE="${FILE}.compressing.mp4"
    ORIG_SIZE=$(stat -c%s "$FILE")

    echo "[$COUNT/$TOTAL_PENDING] Processing: $BASENAME ($(numfmt --to=iec $ORIG_SIZE))" | tee -a "$LOG_FILE"

    RES=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$FILE" 2>/dev/null || echo "0,0")
    WIDTH=$(echo "$RES" | cut -d, -f1)
    HEIGHT=$(echo "$RES" | cut -d, -f2)

    echo "       Resolution: ${WIDTH}x${HEIGHT}" | tee -a "$LOG_FILE"
    
    ORIG_BITRATE=$(ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate -of default=nokey=1:noprint_wrappers=1 "$FILE" 2>/dev/null || echo "0")
    ORIG_BITRATE=$((ORIG_BITRATE / 1000))
    
    DURATION=$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$FILE" 2>/dev/null || echo "0")
    DURATION=${DURATION%.*}
    if [ "$DURATION" -gt 0 ]; then
        AUDIO_BITS=$((128 * DURATION))
        TARGET_VIDEO_BITS=$((2457600 - AUDIO_BITS))
        TARGET_BITRATE=$((TARGET_VIDEO_BITS / DURATION))
        if [ "$TARGET_BITRATE" -lt 400 ]; then
            TARGET_BITRATE=400
        elif [ "$TARGET_BITRATE" -gt 2500 ]; then
            TARGET_BITRATE=2500
        fi
    else
        TARGET_BITRATE=800
    fi
    
    echo "       Target video bitrate: ${TARGET_BITRATE}kbps (for ~300MB)" | tee -a "$LOG_FILE"

    if [ "$WIDTH" -gt 1280 ] 2>/dev/null; then
        SCALE_FILTER="scale='min(1280,iw)':-2"
    elif [ "$WIDTH" -gt 852 ] 2>/dev/null; then
        SCALE_FILTER="scale='min(1280,iw)':-2"
    else
        SCALE_FILTER="scale='iw':-2"
    fi

    START_TIME=$(date +%s)

    if ffmpeg -y -i "$FILE" \
        -c:v libx264 \
        -b:v ${TARGET_BITRATE}k \
        -maxrate ${TARGET_BITRATE}k \
        -bufsize $((TARGET_BITRATE * 2))k \
        -preset medium \
        -vf "$SCALE_FILTER" \
        -c:a aac \
        -b:a 128k \
        -movflags +faststart \
        -threads 0 \
        "$TEMP_FILE" 2>> "$LOG_FILE"; then

        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))

        if [ -f "$TEMP_FILE" ]; then
            NEW_SIZE=$(stat -c%s "$TEMP_FILE")
            PCT=$(echo "scale=1; 100 * $NEW_SIZE / $ORIG_SIZE" | bc)
            SAVED_PCT=$(echo "scale=1; 100 - $PCT" | bc)

            mv "$TEMP_FILE" "$FILE"

            echo "       Done in ${DURATION}s: $(numfmt --to=iec $ORIG_SIZE) -> $(numfmt --to=iec $NEW_SIZE) (${SAVED_PCT}% saved)" | tee -a "$LOG_FILE"

            echo "$BASENAME" >> "$STATE_FILE"

            TOTAL_ORIG=$((TOTAL_ORIG + ORIG_SIZE))
            TOTAL_COMPRESSED=$((TOTAL_COMPRESSED + NEW_SIZE))
        else
            echo "       ERROR: temp file not found after ffmpeg" | tee -a "$LOG_FILE"
        fi
    else
        echo "       FAILED: ffmpeg returned non-zero for $BASENAME" | tee -a "$LOG_FILE"
        rm -f "$TEMP_FILE"
    fi
    echo "" | tee -a "$LOG_FILE"
done

TOTAL_SAVED=$((TOTAL_ORIG - TOTAL_COMPRESSED))
echo "=========================================" | tee -a "$LOG_FILE"
echo "Compression complete: $(date)" | tee -a "$LOG_FILE"
echo "Total processed: $TOTAL_PENDING files" | tee -a "$LOG_FILE"
echo "Total original: $(numfmt --to=iec $TOTAL_ORIG)" | tee -a "$LOG_FILE"
echo "Total compressed: $(numfmt --to=iec $TOTAL_COMPRESSED)" | tee -a "$LOG_FILE"
echo "Total saved: $(numfmt --to=iec $TOTAL_SAVED)" | tee -a "$LOG_FILE"
echo "=========================================" | tee -a "$LOG_FILE"
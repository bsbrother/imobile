#!/usr/bin/env bash
# Second pass compression for files still >=500MB, target ~400MB each
set -euo pipefail

SRC_DIR="$HOME/Downloads/jav"
TARGET_MB=400
LOGFILE="$SRC_DIR/_compress2_log.txt"

echo "=== Second pass: target ${TARGET_MB}MB per file ===" >> "$LOGFILE"
date >> "$LOGFILE"

# Find files >=500MB
mapfile -t FILES < <(find "$SRC_DIR" -maxdepth 1 -type f -size +500M -name '*.mp4' -print0 | xargs -0 ls -S)
echo "Found ${#FILES[@]} files >=500MB" >> "$LOGFILE"

for FILE in "${FILES[@]}"; do
    BASENAME=$(basename "$FILE")
    ORIG_SIZE=$(stat -c%s "$FILE")
    echo "Processing: $BASENAME ($(numfmt --to=iec $ORIG_SIZE))" | tee -a "$LOGFILE"
    
    # Get duration
    DURATION=$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$FILE" 2>/dev/null || echo "0")
    DURATION=${DURATION%.*}
    if [ "$DURATION" -eq 0 ]; then
        echo "  ERROR: could not get duration" | tee -a "$LOGFILE"
        continue
    fi
    
    # Calculate target video bitrate for ~TARGET_MB MB
    # Total bits = TARGET_MB * 8 * 1024 * 1024
    # Audio bits = 128 * DURATION * 1000 (kbps to bps) -> but we work in kbits for simplicity
    # Let's work in kbits: total_target_kbits = TARGET_MB * 8 * 1024
    TOTAL_TARGET_KBITS=$((TARGET_MB * 8 * 1024))
    AUDIO_KBITS=$((128 * DURATION))  # 128 kbps * duration seconds
    VIDEO_TARGET_KBITS=$((TOTAL_TARGET_KBITS - AUDIO_KBITS))
    if [ "$VIDEO_TARGET_KBITS" -le 0 ]; then
        VIDEO_TARGET_KBITS=100  # fallback
    fi
    TARGET_BITRATE=$((VIDEO_TARGET_KBITS / DURATION))
    
    # Clamp bitrate between 100 and 2000 kbps
    if [ "$TARGET_BITRATE" -lt 100 ]; then
        TARGET_BITRATE=100
    elif [ "$TARGET_BITRATE" -gt 2000 ]; then
        TARGET_BITRATE=2000
    fi
    
    echo "  Duration: ${DURATION}s, Target video bitrate: ${TARGET_BITRATE} kbps" | tee -a "$LOGFILE"
    
    # Determine scale filter - never upscale, max 720p
    RES=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$FILE" 2>/dev/null || echo "0,0")
    WIDTH=$(echo "$RES" | cut -d, -f1)
    HEIGHT=$(echo "$RES" | cut -d, -f2)
    if [ "$WIDTH" -gt 1280 ]; then
        SCALE_FILTER="scale='min(1280,iw)':-2"
    elif [ "$WIDTH" -gt 852 ]; then
        SCALE_FILTER="scale='min(1280,iw)':-2"
    else
        SCALE_FILTER="scale='iw':-2"
    fi
    
    TEMP_FILE="${FILE}.compressing2.mp4"
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
        "$TEMP_FILE" 2>> "$LOGFILE"; then
        
        END_TIME=$(date +%s)
        NEW_SIZE=$(stat -c%s "$TEMP_FILE")
        PCT=$(echo "scale=1; 100 * $NEW_SIZE / $ORIG_SIZE" | bc)
        SAVED_PCT=$(echo "scale=1; 100 - $PCT" | bc)
        
        mv "$TEMP_FILE" "$FILE"
        
        echo "  => $(numfmt --to=iec $NEW_SIZE) (${SAVED_PCT}% saved) in $((END_TIME-START_TIME))s" | tee -a "$LOGFILE"
    else
        echo "  => FAILED" | tee -a "$LOGFILE"
        rm -f "$TEMP_FILE"
    fi
    echo "" | tee -a "$LOGFILE"
done

echo "=== Second pass complete ===" >> "$LOGFILE"
date >> "$LOGFILE"
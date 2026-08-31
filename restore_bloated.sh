#!/usr/bin/env bash
# Restore ALL bloated files from the failed compression batch
# Files that got upscaled+bloated by the old scale=1280:-2 filter
set -euo pipefail

cd ~/Downloads/jav

declare -A ORIG_RES
ORIG_RES[jav_mfyd_009.mp4]="640:360"
ORIG_RES[jav_mfcd_001.mp4]="640:360"
ORIG_RES[jav_fns_211.mp4]="640:360"
ORIG_RES[jav_fns_091.mp4]="640:360"
ORIG_RES[jav_dldss_276.mp4]="640:360"
ORIG_RES[jav_best_EBWH-233.mp4]="852:480"

STATE_FILE="_compress_done.txt"

echo "Restoring ${#ORIG_RES[@]} bloated files..."
COUNT=0
for f in "${!ORIG_RES[@]}"; do
    COUNT=$((COUNT + 1))
    res="${ORIG_RES[$f]}"
    w="${res%%:*}"
    h="${res##*:}"
    orig_size=$(stat -c%s "$f")
    echo ""
    echo "[$COUNT/${#ORIG_RES[@]}] $f -> ${w}x${h} (currently $(numfmt --to=iec $orig_size))"
    
    temp="${f}.restoring.mp4"
    
    crf=30
    if [ "$w" -le 640 ]; then
        crf=32
    fi
    
    ffmpeg -y -i "$f" \
        -c:v libx264 -crf $crf -preset medium \
        -vf "scale=${w}:${h}" \
        -c:a aac -b:a 96k \
        -movflags +faststart -threads 0 \
        "$temp" 2>/dev/null
    
    new_size=$(stat -c%s "$temp")
    pct=$(echo "scale=1; 100 * $new_size / $orig_size" | bc)
    echo "  $(numfmt --to=iec $orig_size) -> $(numfmt --to=iec $new_size) (${pct}%)"
    mv "$temp" "$f"
    
    # Remove from done.txt so fixed script can re-evaluate if still >500MB
    if [ -f "$STATE_FILE" ]; then
        sed -i "\|^${f}$|d" "$STATE_FILE"
    fi
done

echo ""
echo "All restored. Checking sizes..."
for f in "${!ORIG_RES[@]}"; do
    sz=$(stat -c%s "$f")
    if [ "$sz" -gt 524288000 ]; then  # >500MB
        echo "  $f: $(numfmt --to=iec $sz) — STILL >500MB, will be re-evaluated"
    else
        echo "  $f: $(numfmt --to=iec $sz) — under 500MB, done"
    fi
done
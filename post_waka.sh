#!/usr/bin/env bash
# Post-download: compress waka_misono_02 then run fixed compression on remaining files
set -euo pipefail
cd ~/Downloads/jav

FILE="jav_best_waka_misono_02.mp4"
if [ -f "$FILE" ]; then
    sz=$(stat -c%s "$FILE")
    echo "Compressing $FILE ($(numfmt --to=iec $sz))..."
    ffmpeg -y -i "$FILE" \
        -c:v libx264 -crf 27 -preset medium \
        -vf "scale='min(1280,iw)':-2" \
        -c:a aac -b:a 128k \
        -movflags +faststart -threads 0 \
        "${FILE}.compressing.mp4" 2>/dev/null
    
    new_sz=$(stat -c%s "${FILE}.compressing.mp4")
    echo "  $(numfmt --to=iec $sz) -> $(numfmt --to=iec $new_sz)"
    mv "${FILE}.compressing.mp4" "$FILE"
fi

# Now run the fixed compression on any remaining >500MB files not in done.txt
echo ""
echo "Running fixed compression on remaining files..."
bash /home/kasm-user/apps/imobile/compress_jav.sh
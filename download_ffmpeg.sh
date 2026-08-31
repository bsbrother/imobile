#!/usr/bin/env bash
# Download HLS m3u8 streams via ffmpeg and compress
set -euo pipefail
cd ~/Downloads/jav

declare -A URLS
URLS["jav_best_waka_misono_03_temp.mp4"]="https://cache-xx23.wowstream2.cloud/blah4/XEn0ezMAjwPK3U_oUqJFa8_9hVd3z8pTAqHXoRCPgxcHxtRnxoYEDmvhJvrU/video.m3u8?v=a2"
URLS["jav_best_waka_misono_04_temp.mp4"]="https://cache-xx19.wowstream2.cloud/blah4/XEn0ezAfkFvTgxXzG7ZKZZ79mg130MJQB6HA6BGUhUwA2dRrxdVZ/video.m3u8?v=a2"
URLS["jav_best_waka_misono_05_temp.mp4"]="https://8dff869d.soft-rain-111.site/blah4/XEn0ezcAjwPK3U_oUqJFYdigm0w_zYUHAemU_R+a1E4Kz9JwwtlTGGk/video.m3u8?v=a2"
URLS["jav_best_waka_misono_06_temp.mp4"]="https://cache-xx3.wowstream.cloud/blah4/XEn0ezMAjwPK3U_oUqJFapP9hVF33opaAKbIpBCPgxcBxoVwwtlTGGw/video.m3u8?v=a2"
URLS["jav_best_waka_misono_07_temp.mp4"]="https://cache-xx29.wowstream2.cloud/blah4/XEn0ezgAjwPK3U_oUqJFNNi1lkwz0sZTXKGCpQSajU5TysZiy9dSEQ/video.m3u8?v=a2"
URLS["jav_best_waka_misono_08_temp.mp4"]="https://faf8b60b.cool-breeze-109.space/blah4/XEn0ezAfkFvTgxXzG7ZKZZH9iw133sNOGrTfo0mPgxcGzNgw0NBdG2_r/video.m3u8?v=a2"
URLS["jav_best_waka_misono_09_temp.mp4"]="https://cache-xx15.wowstream.cloud/blah4/XEn0ezAckFvTgxXzG7ZKZY39jhV33sNOAOnD_0mPgxcLy9RwwtlTGWo/video.m3u8?v=a2"
URLS["jav_best_waka_misono_10_temp.mp4"]="https://6eeefed5.bright-light-107.store/blah4/XEn0ezkAjwPK3U_oUqJFbdi8i0xgxZZICeXf9gSajRlQytdwwtlTGmo/video.m3u8?v=a2"

echo "Starting parallel downloads..."
COUNT=0
PIDS=()

for outfile in "${!URLS[@]}"; do
    COUNT=$((COUNT + 1))
    url="${URLS[$outfile]}"
    final="${outfile/_temp/}"
    
    (
        echo "[$COUNT] Downloading $final..."
        ffmpeg -y -headers "Referer: https://javplayer.cc/\r\nOrigin: https://javplayer.cc" \
            -i "$url" -c copy -bsf:a aac_adtstoasc "$outfile" 2>/dev/null
        if [ -f "$outfile" ]; then
            sz=$(stat -c%s "$outfile")
            echo "[$COUNT] $final: $(numfmt --to=iec $sz) — compressing..."
            # Compress
            ffmpeg -y -i "$outfile" \
                -c:v libx264 -crf 27 -preset medium \
                -vf "scale='min(1280,iw)':-2" \
                -c:a aac -b:a 128k \
                -movflags +faststart -threads 0 \
                "$final" 2>/dev/null
            rm -f "$outfile"
            new_sz=$(stat -c%s "$final")
            echo "[$COUNT] $final: $(numfmt --to=iec $new_sz) DONE"
        else
            echo "[$COUNT] $final: FAILED"
        fi
    ) &
    PIDS+=($!)
done

echo "Waiting for ${#PIDS[@]} jobs..."
for pid in "${PIDS[@]}"; do
    wait $pid 2>/dev/null || true
done

echo ""
echo "=== All downloads complete ==="
ls -lh jav_best_waka_misono_0[3-9].mp4 jav_best_waka_misono_10.mp4 2>/dev/null || true
#!/usr/bin/env bash
# Download all videos from m3u8 URLs, compress, save as jav_best_waka_misono_XX.mp4
set -euo pipefail
cd ~/Downloads/jav

# Map: filename -> m3u8 URL
declare -A URLS
URLS["jav_best_waka_misono_03.mp4"]="https://cache-xx23.wowstream2.cloud/blah4/XEn0ezMAjwPK3U_oUqJFa8_9hVd3z8pTAqHXoRCPgxcHxtRnxoYEDmvhJvrU/video.m3u8?v=a2"  # s-cute-k33
URLS["jav_best_waka_misono_04.mp4"]="https://cache-xx19.wowstream2.cloud/blah4/XEn0ezAfkFvTgxXzG7ZKZZ79mg130MJQB6HA6BGUhUwA2dRrxdVZ/video.m3u8?v=a2"     # atid-685
URLS["jav_best_waka_misono_05.mp4"]="https://8dff869d.soft-rain-111.site/blah4/XEn0ezcAjwPK3U_oUqJFYdigm0w_zYUHAemU_R+a1E4Kz9JwwtlTGGk/video.m3u8?v=a2"  # mngs-064
URLS["jav_best_waka_misono_06.mp4"]="https://cache-xx3.wowstream.cloud/blah4/XEn0ezMAjwPK3U_oUqJFapP9hVF33opaAKbIpBCPgxcBxoVwwtlTGGw/video.m3u8?v=a2"     # seven-003
URLS["jav_best_waka_misono_07.mp4"]="https://cache-xx29.wowstream2.cloud/blah4/XEn0ezgAjwPK3U_oUqJFNNi1lkwz0sZTXKGCpQSajU5TysZiy9dSEQ/video.m3u8?v=a2"  # ckck-022
URLS["jav_best_waka_misono_08.mp4"]="https://faf8b60b.cool-breeze-109.space/blah4/XEn0ezAfkFvTgxXzG7ZKZZH9iw133sNOGrTfo0mPgxcGzNgw0NBdG2_r/video.m3u8?v=a2"  # hmn-411
URLS["jav_best_waka_misono_09.mp4"]="https://cache-xx15.wowstream.cloud/blah4/XEn0ezAckFvTgxXzG7ZKZY39jhV33sNOAOnD_0mPgxcLy9RwwtlTGWo/video.m3u8?v=a2"     # dass-363
URLS["jav_best_waka_misono_10.mp4"]="https://6eeefed5.bright-light-107.store/blah4/XEn0ezkAjwPK3U_oUqJFbdi8i0xgxZZICeXf9gSajRlQytdwwtlTGmo/video.m3u8?v=a2"  # same-193

echo "Downloading ${#URLS[@]} videos..."
COUNT=0
for outfile in "${!URLS[@]}"; do
    COUNT=$((COUNT + 1))
    url="${URLS[$outfile]}"
    echo ""
    echo "[$COUNT/${#URLS[@]}] $outfile"
    echo "  URL: ${url:0:80}..."
    
    # Download via yt-dlp (handles m3u8 well)
    yt-dlp -o "$outfile" --no-warnings --merge-output-format mp4 --remux-video mp4 \
        --referer "https://javplayer.cc/" \
        --add-header "Origin:https://javplayer.cc" \
        "$url" 2>&1 | tail -3 || {
        # Fallback: ffmpeg
        echo "  yt-dlp failed, trying ffmpeg..."
        ffmpeg -y -i "$url" -c copy -bsf:a aac_adtstoasc "${outfile}.tmp.mp4" 2>/dev/null && \
            mv "${outfile}.tmp.mp4" "$outfile" || echo "  BOTH FAILED"
    }
    
    if [ -f "$outfile" ]; then
        sz=$(stat -c%s "$outfile")
        echo "  Done: $(numfmt --to=iec $sz)"
    fi
done

echo ""
echo "=== Download complete ==="
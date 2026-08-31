#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="$HOME/Downloads/jav"
STATE_FILE="$OUTPUT_DIR/_compress_done.txt"
LOG_FILE="$OUTPUT_DIR/_123av_compress_log.txt"

echo "=== Starting 123av video compression wait ===" >> "$LOG_FILE"
echo "Output directory: $OUTPUT_DIR" >> "$LOG_FILE"
echo "State file: $STATE_FILE" >> "$LOG_FILE"
echo "Log file: $LOG_FILE" >> "$LOG_FILE"

# Wait for the first raw file to appear (timeout: 10 minutes)
timeout=600
start_time=$(date +%s)
while [ ! -f "$OUTPUT_DIR/jav_waka_misono_292my-588.mp4" ]; do
    now=$(date +%s)
    if [ $((now - start_time)) -gt $timeout ]; then
        echo "Timeout: Raw file not found after 10 minutes." | tee -a "$LOG_FILE"
        exit 1
    fi
    sleep 30
done

echo "Raw file found: jav_waka_misono_292my-588.mp4" | tee -a "$LOG_FILE"

# Clear the state file so that we process all files >=500MB
> "$STATE_FILE"
echo "State file cleared." | tee -a "$LOG_FILE"

# Run the compression script
echo "Running compression script..." | tee -a "$LOG_FILE"
bash /home/kasm-user/apps/imobile/compress_jav.sh 2>>"$LOG_FILE"

echo "Compression completed at $(date)" | tee -a "$LOG_FILE"
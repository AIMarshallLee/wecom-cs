#!/bin/bash
# 日志轮转脚本
# 单个日志文件超过 10MB 时轮转，保留最近 3 份
# 可通过 launchd 定时执行，或手动运行 ./deploy/logrotate.sh

LOG_DIR="$(cd "$(dirname "$0")" && pwd)/logs"
MAX_SIZE=10485760  # 10MB (10 * 1024 * 1024)
KEEP=3             # 保留份数

for logfile in "$LOG_DIR"/*.log; do
    [ -f "$logfile" ] || continue

    size=$(stat -f%z "$logfile" 2>/dev/null || echo 0)
    if [ "$size" -lt "$MAX_SIZE" ]; then
        continue
    fi

    # 删除最旧的
    for i in $(seq $((KEEP - 1)) -1 1); do
        [ -f "${logfile}.$i.gz" ] && mv "${logfile}.$i.gz" "${logfile}.$((i + 1)).gz"
    done
    # 删除超出保留数的
    [ -f "${logfile}.$((KEEP + 1)).gz" ] && rm -f "${logfile}.$((KEEP + 1)).gz"

    # 轮转当前日志
    mv "$logfile" "${logfile}.1"
    gzip "${logfile}.1" &

    echo "[rotate] $logfile ($(echo $size | awk '{printf "%.1fMB", $1/1048576}')) → ${logfile}.1.gz"
done

wait
echo "[rotate] 完成"

#!/usr/bin/env bash
# logs.sh —— 打印某个 tmux 窗口里最近的转发/丢弃/接收日志
#
# 用法: bash scripts/logs.sh n1 n2 h0x0A01      (可给多个窗口)
#       bash scripts/logs.sh n1 20              (只看 n1 的最后 20 行也可以直接 tail)
for w in "$@"; do
    echo "--- $w ---"
    tmux capture-pane -pJ -t "$w" -S -400 2>/dev/null \
        | grep -E '^\[(FWD|DROP|RECV)' || echo "    (暂无日志)"
done

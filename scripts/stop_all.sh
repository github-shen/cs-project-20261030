#!/usr/bin/env bash
# stop_all.sh —— 停掉所有节点/主机进程与 tmux 会话
set -u
cd "$(dirname "$0")/.."
tmux kill-server 2>/dev/null
pkill -f './bin/switch' 2>/dev/null
pkill -f './bin/host' 2>/dev/null
pkill -f 'bin/switch config' 2>/dev/null
pkill -f 'bin/host 0x' 2>/dev/null
sleep 1
if pgrep -f 'bin/switch config|bin/host 0x' >/dev/null 2>&1; then
    echo "仍有残留进程："
    pgrep -af 'bin/switch config|bin/host 0x'
else
    echo "已全部停止。"
fi

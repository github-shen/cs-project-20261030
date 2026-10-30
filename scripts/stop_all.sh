#!/usr/bin/env bash
# stop_all.sh —— 停掉所有节点/主机进程与 tmux 会话
set -u
cd "$(dirname "$0")/.." || exit 1

tmux kill-server 2>/dev/null
pkill -f './bin/switch' 2>/dev/null
pkill -f './bin/host' 2>/dev/null
pkill -f 'bin/switch config' 2>/dev/null
pkill -f 'bin/host 0x' 2>/dev/null
sleep 1

# 双介质模式下节点可能以 root 启动（SO_BINDTODEVICE），普通用户杀不掉
if pgrep -f 'bin/switch config|bin/host 0x' >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
        sudo -n pkill -f 'bin/switch config' 2>/dev/null
        sudo -n pkill -f 'bin/host 0x' 2>/dev/null
        sleep 0.6
    else
        echo "提示：还有 root 身份启动的进程，需执行  sudo bash scripts/stop_all.sh" >&2
    fi
fi

if pgrep -f 'bin/switch config|bin/host 0x' >/dev/null 2>&1; then
    echo "仍有残留进程："
    pgrep -af 'bin/switch config|bin/host 0x'
    echo "  用 root 再停一次:  sudo bash scripts/stop_all.sh"
else
    echo "已全部停止。"
fi

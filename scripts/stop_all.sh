#!/usr/bin/env bash
# 只关闭本项目的 cnet tmux 服务器；不按进程名终止其他实验。
set -u
tmux -L cnet kill-server 2>/dev/null || true
echo '项目 cnet 会话已关闭。'

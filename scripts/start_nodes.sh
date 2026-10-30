#!/usr/bin/env bash
# start_nodes.sh —— 旧入口的兼容包装：等价于 bash scripts/run_tmux.sh [配置目录]
#
# 早期版本会在没有 tmux 时退回 gnome-terminal，逻辑与 run_tmux.sh 重复，
# 现在统一委托给 run_tmux.sh（它要求 tmux：apt install -y tmux）。
set -u
cd "$(dirname "$0")/.." || exit 1
exec bash scripts/run_tmux.sh "${1:-config}"

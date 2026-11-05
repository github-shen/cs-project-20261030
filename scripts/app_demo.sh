#!/usr/bin/env bash
# 单机业务演示复用题图拓扑；额外站点不增加外部网络或虚构链路。
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/run_tmux.sh config-app
sleep 1
bash scripts/demo.sh A
bash scripts/demo.sh B
tmux -L cnet send-keys -t h0x0A01 'sendbad 0x0C01 app-bad-crc' Enter
tmux -L cnet send-keys -t h0x0A01 'send 0x0E01 app-no-route' Enter
sleep 1
bash scripts/logs.sh ch0A n1
echo '会话保留供观察；停止: bash scripts/stop_all.sh'

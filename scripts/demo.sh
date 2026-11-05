#!/usr/bin/env bash
# 三个外部逻辑信道的双向业务演示。先启动 run_tmux.sh 或 run_lan.sh。
set -euo pipefail
cd "$(dirname "$0")/.."
S() { tmux -L cnet send-keys -t "$1" "$2" Enter; }
case "${1:-A}" in
A)
    S h0x0A01 'send 0x0B01 upper-to-down3'
    S h0x0A01 'send 0x0C01 upper-to-down5'
    S h0x0A02 'send 0x0C01 second-source-path'
    S h0x0B01 'send 0x0A01 down3-to-upper'
    ;;
B)
    S h0x0C01 'send 0x0A01 down5-to-upper'
    S h0x0C01 'send 0x0B01 down5-to-down3'
    ;;
*) echo '用法: demo.sh A|B' >&2; exit 1;;
esac
sleep 1
bash scripts/logs.sh n1 n2 n3 n4 h0x0A01 h0x0B01 h0x0C01

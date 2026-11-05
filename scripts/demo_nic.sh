#!/usr/bin/env bash
# 双接口演示：1—4 为 Wi-Fi 路径，2—3/2—4 为有线路径。
set -euo pipefail
cd "$(dirname "$0")/.."
S() { tmux -L cnet send-keys -t "$1" "$2" Enter; }
if [ "${1:-A}" = B ]; then
    bash scripts/logs.sh n3 n4 ch0B h0x0B01
    exit 0
fi
S h0x0A02 'send 0x0C01 nic-eth-1'
sleep 1
S h0x0A01 'send 0x0C01 nic-wifi-1'
sleep 1
S h0x0A01 'send 0x0B01 nic-wifi-2'
sleep 1
bash scripts/logs.sh n1 n2 h0x0C01
echo '前两条消息分别从节点1端口1/2发出；下行3经过节点2外部端口1。'
echo '实际 Wi-Fi/有线介质由所绑定网卡和抓包证明。'

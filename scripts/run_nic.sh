#!/usr/bin/env bash
# A: 节点1/2，上行及下行5；B: 节点3/4，下行3。
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f config-nic/ips.env ] || { echo '先运行 scripts/nic.sh gen' >&2; exit 1; }
. config-nic/ips.env
for dev in "$ETH_DEV" "$WIFI_DEV"; do ip link show dev "$dev" >/dev/null; done
sudo -v
exec python3 scripts/run_lab.py "${2:-config-nic}" --role "${1:?用法: run_nic.sh A|B [配置目录]}" --sudo

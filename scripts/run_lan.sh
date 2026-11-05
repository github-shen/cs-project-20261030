#!/usr/bin/env bash
# A: 节点1/2，上行及下行3；B: 节点3/4，下行5。
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/run_lab.py "${2:-config-lan}" --role "${1:?用法: run_lan.sh A|B [配置目录]}"

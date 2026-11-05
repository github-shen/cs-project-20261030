#!/usr/bin/env bash
# 单机 / OVS 启动：四节点、三共享信道、四终端（上行有两个站点）。
set -euo pipefail
cd "$(dirname "$0")/.."
CFG="${1:-config-hier}"
if [ "${CFG%/}" = config-ovs ] || [ "${CFG%/}" = config-hier-ovs ]; then
    sudo -v
    exec python3 scripts/run_lab.py "$CFG" --namespaces
fi
exec python3 scripts/run_lab.py "$CFG"

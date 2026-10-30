#!/usr/bin/env bash
# ovs_teardown.sh —— 拆除 br-int（internal 端口 ovs1~ovs4 会随之消失）
# 用法：sudo bash scripts/ovs_teardown.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限，请用: sudo bash $0" >&2
    exit 1
fi

ovs-vsctl --if-exists del-br br-int
echo "br-int 已删除。"
ovs-vsctl show

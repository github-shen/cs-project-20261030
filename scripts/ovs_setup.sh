#!/usr/bin/env bash
# ovs_setup.sh —— 搭建“节点域”的二层互连（题目要求：调试 Open vSwitch）
#
# 做的事：
#   1) 确保 openvswitch-switch 已安装并运行；
#   2) 建网桥 br-int；
#   3) 建 4 个 internal 端口 ovs1..ovs4，分别配 10.1.0.1~4/24；
#   4) 装一条 NORMAL 流表，让 br-int 做二层自学习转发；
#   5) 顺手关掉 ARP flux（arp_ignore/arp_announce），避免 4 个端口在同一台机器上互相抢答 ARP。
#
# 用法：sudo bash scripts/ovs_setup.sh
# 之后：bash scripts/run_tmux.sh config-ovs
set -euo pipefail

BR=br-int
PREFIX=10.1.0

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限，请用: sudo bash $0" >&2
    exit 1
fi

if ! command -v ovs-vsctl >/dev/null 2>&1; then
    echo "== 未检测到 Open vSwitch，开始安装 =="
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y openvswitch-switch
fi

systemctl enable --now openvswitch-switch 2>/dev/null || service openvswitch-switch start || true

if ! ovs-vsctl br-exists "$BR"; then
    ovs-vsctl add-br "$BR"
    ovs-vsctl set bridge "$BR" other-config:datapath-id=0000000000000001
    echo "== 已创建网桥 $BR =="
else
    echo "== 网桥 $BR 已存在，复用 =="
fi

for k in 1 2 3 4; do
    ovs-vsctl --may-exist add-port "$BR" "ovs$k" -- set Interface "ovs$k" type=internal
    ip link set "ovs$k" up
    ip addr replace "${PREFIX}.${k}/24" dev "ovs$k"
    sysctl -qw "net.ipv4.conf.ovs$k.arp_ignore=1"
    sysctl -qw "net.ipv4.conf.ovs$k.arp_announce=2"
    echo "== ovs$k 就绪: ${PREFIX}.${k}/24 =="
done

# 没有控制器时用 NORMAL 动作做二层自学习转发（也是“调试 openvswitch”的可观察点）
ovs-ofctl add-flow "$BR" "priority=0,actions=NORMAL"

echo
echo "================ ovs-vsctl show ================"
ovs-vsctl show
echo "================ 流表 ================"
ovs-ofctl dump-flows "$BR"
echo "================ 接口地址 ================"
ip -4 -br addr show | grep -E 'ovs[1-4]' || true
echo
echo "搭建完成。下一步:"
echo "  bash scripts/run_tmux.sh config-ovs"

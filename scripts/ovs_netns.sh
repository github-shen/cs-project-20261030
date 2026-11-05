#!/usr/bin/env bash
# ovs_netns.sh —— 让域内二层流量"真的"经过 Open vSwitch 的 br-int（题目 2(1) 取证）
#
# 为什么需要它：config-ovs 把节点 k 绑到 10.1.0.k，但四张 ovs 网卡在同一台机器、
#   同一个 netns 里，内核把 10.1.0.1~4 全当成本地地址（ip route get 显示 dev lo），
#   报文走本机回环、不经过 br-int —— 功能能通，但 ovs-ofctl 的 n_packets 不增长。
#   把每张 ovsX 放进独立 netns 后，跨节点报文必须经网桥二层转发，n_packets 真实增长。
#
# 用法（都要 root）：
#   sudo bash scripts/ovs_netns.sh setup     建 ns1~4、ns11~13，分别配置 10.1.0.k/24
#   sudo bash scripts/ovs_netns.sh status    查看每个 ns 的网卡与 IP
#   sudo bash scripts/ovs_netns.sh teardown  还原：网卡移回、IP 复位、删 ns
#   sudo bash scripts/ovs_netns.sh verify    独立拓扑自动验收（保存结果，失败非零退出）
#   sudo bash scripts/ovs_netns.sh demo      隔离验收：4 节点、3 外部网络及 OVS 计数
#
# 手动观察：先 ovs_setup.sh，再 setup，最后 run_tmux.sh config-ovs。
# ns1～4 为交换节点；ns11～13 为三个共享外部网络。
set -u
cd "$(dirname "$0")/.." || exit 1

NETS="1 2 3 4 11 12 13"

need_root() {
    [ "$(id -u)" -eq 0 ] || { echo "需要 root: sudo bash scripts/ovs_netns.sh $*" >&2; exit 1; }
}

ns_exists() { ip netns list 2>/dev/null | awk '{print $1}' | grep -qx "ns$1"; }

setup_one() {
    local i="$1"
    ns_exists "$i" || ip netns add "ns$i"
    ip netns exec "ns$i" ip link set lo up
    if ip link show "ovs$i" >/dev/null 2>&1; then
        ip link set "ovs$i" netns "ns$i"
    fi
    if ! ip netns exec "ns$i" ip link show "ovs$i" >/dev/null 2>&1; then
        echo "  x ns$i 里没有 ovs$i，先执行 sudo bash scripts/ovs_setup.sh" >&2
        return 1
    fi
    ip netns exec "ns$i" ip addr replace "10.1.0.$i/24" dev "ovs$i"
    ip netns exec "ns$i" ip link set "ovs$i" up
    echo "  ns$i: ovs$i = 10.1.0.$i/24"
}

do_setup() {
    echo "== 把 ovs1~4、ovs11~13 放进独立 netns =="
    for i in $NETS; do setup_one "$i" || return 1; done
    echo
    echo "== 网桥端口 =="
    ovs-vsctl list-ports br-int 2>/dev/null | sed 's/^/  /'
    echo
    echo "接下来可以: sudo bash scripts/ovs_netns.sh demo   或按脚本头部注释手动开终端"
}

do_teardown() {
    echo "== 还原 netns =="
    for i in $NETS; do
        ns_exists "$i" || continue
        if ip netns exec "ns$i" ip link show "ovs$i" >/dev/null 2>&1; then
            ip netns exec "ns$i" ip link set "ovs$i" netns 1
            ip addr replace "10.1.0.$i/24" dev "ovs$i"
            ip link set "ovs$i" up
        fi
        ip netns del "ns$i"
        echo "  ns$i 已删除，ovs$i 回到宿主 ns（10.1.0.$i）"
    done
}

do_status() {
    for i in $NETS; do
        echo "--- ns$i ---"
        if ns_exists "$i"; then
            ip netns exec "ns$i" ip -4 -br addr | sed 's/^/  /'
        else
            echo "  (不存在)"
        fi
    done
    echo "--- 宿主 ns 里的 10.1.0.x ---"
    ip -4 -br addr | grep -E 'ovs[1-4]' | sed 's/^/  /' || echo "  (无，说明已全部放进 netns)"
}

# demo 与 verify 共用隔离验收，保留所有节点、信道、接收和流计数日志。
do_demo() { python3 scripts/ovs_acceptance.py; }

# ---------------- 入口 ----------------
if [ "${1:-}" = verify ]; then
    shift
    exec python3 scripts/ovs_acceptance.py "$@"
fi
need_root "$@"
case "${1:-}" in
    setup)    do_setup ;;
    teardown) do_teardown ;;
    status)   do_status ;;
    demo)     do_demo ;;
    *)        sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac


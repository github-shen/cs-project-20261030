#!/usr/bin/env bash
# ovs_netns.sh —— 让域内二层流量"真的"经过 Open vSwitch 的 br-int（题目 2(1) 取证）
#
# 为什么需要它：config-ovs 把节点 k 绑到 10.1.0.k，但四张 ovs 网卡在同一台机器、
#   同一个 netns 里，内核把 10.1.0.1~4 全当成本地地址（ip route get 显示 dev lo），
#   报文走本机回环、不经过 br-int —— 功能能通，但 ovs-ofctl 的 n_packets 不增长。
#   把每张 ovsX 放进独立 netns 后，跨节点报文必须经网桥二层转发，n_packets 真实增长。
#
# 用法（都要 root）：
#   sudo bash scripts/ovs_netns.sh setup     建 ns1~4，把 ovs1~4 移进去并配 10.1.0.k/24
#   sudo bash scripts/ovs_netns.sh status    查看每个 ns 的网卡与 IP
#   sudo bash scripts/ovs_netns.sh teardown  还原：网卡移回、IP 复位、删 ns
#   sudo bash scripts/ovs_netns.sh demo      一键取证：起 4 节点 + 2 主机、发帧、打印 n_packets 增量
#
# 手动跑（8 个终端，节点和它的外部主机必须同一个 ns）：
#   sudo bash scripts/ovs_netns.sh setup
#   sudo ip netns exec ns1 ./bin/switch config-ovs/node1.conf
#   sudo ip netns exec ns2 ./bin/switch config-ovs/node2.conf
#   sudo ip netns exec ns3 ./bin/switch config-ovs/node3.conf
#   sudo ip netns exec ns4 ./bin/switch config-ovs/node4.conf
#   sudo ip netns exec ns1 ./bin/host 0x0A01 9001 127.0.0.1 1103
#   sudo ip netns exec ns2 ./bin/host 0x0B01 9002 127.0.0.1 3104
#   sudo ip netns exec ns3 ./bin/host 0x0C01 9003 127.0.0.1 2104
#   sudo ip netns exec ns4 ./bin/host 0x0D01 9004 127.0.0.1 4103
#   sudo ovs-ofctl dump-flows br-int
set -u
cd "$(dirname "$0")/.." || exit 1

NETS="1 2 3 4"

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
    echo "== 把 ovs1~ovs4 放进独立 netns =="
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

# ---------------- 一键取证 ----------------
W=""
pids=()

do_demo() {
    local i
    W="$(mktemp -d)"
    do_setup >/dev/null || { echo "setup 失败"; exit 1; }

    cleanup() {
        local p
        for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null; done
        pkill -f 'bin/switch config-ovs' 2>/dev/null
        pkill -f 'bin/host 0x' 2>/dev/null
        [ -n "$W" ] && rm -rf "$W"
    }
    trap cleanup EXIT

    # 后台进程的三条标准流都要重定向，否则会持有调用方的管道，脚本永不返回
    start_node() {   # $1 = 节点号
        mkfifo "$W/n$1.in"
        ( exec sleep 3600 > "$W/n$1.in" 2>/dev/null </dev/null ) & pids+=($!)
        ip netns exec "ns$1" ./bin/switch "config-ovs/node$1.conf" \
            < "$W/n$1.in" > "$W/n$1.log" 2>&1 & pids+=($!)
    }
    start_host() {   # $1=ns  $2=地址  $3=本地端口  $4=节点外部端口
        mkfifo "$W/h$2.in"
        ( exec sleep 3600 > "$W/h$2.in" 2>/dev/null </dev/null ) & pids+=($!)
        ip netns exec "ns$1" ./bin/host "$2" "$3" 127.0.0.1 "$4" \
            < "$W/h$2.in" > "$W/h$2.log" 2>&1 & pids+=($!)
    }
    send() { timeout 3 bash -c "echo \"$2\" > \"$W/$1.in\"" || echo "  (命令写入超时：对应主机没起来)"; }

    for i in $NETS; do start_node "$i"; done
    sleep 0.8
    start_host 1 0x0A01 9001 1103
    start_host 3 0x0C01 9003 2104
    sleep 1.5

    echo "== 运行中的进程 =="
    pgrep -af 'bin/switch config-ovs|bin/host 0x' | sed 's/^/  /'

    echo "== 基线流表 =="
    ovs-ofctl dump-flows br-int 2>/dev/null | grep 'actions=NORMAL' | sed 's/^/  /'

    echo "== ns1 的主机 0x0A01 发帧给 0x0C01（域内逐跳应全部经过 br-int）=="
    send "h0x0A01" "send 0x0C01 ovs-netns-test"
    sleep 1.5

    echo "== 之后流表（n_packets 应增长）=="
    ovs-ofctl dump-flows br-int 2>/dev/null | grep 'actions=NORMAL' | sed 's/^/  /'

    echo "== 外部主机 0x0C01 是否收到 =="
    grep -E '^\[RECV' "$W/h0x0C01.log" 2>/dev/null | tail -3 | sed 's/^/  /'

    echo "== 节点1 / 节点2 转发日志 =="
    grep -hE '^\[(FWD|DROP)' "$W/n1.log" 2>/dev/null | tail -2 | sed 's/^/  n1: /'
    grep -hE '^\[(FWD|DROP)' "$W/n2.log" 2>/dev/null | tail -2 | sed 's/^/  n2: /'

    echo "== 判据：n_packets 增长 + 主机收到，说明流量真的过了 br-int =="
}

# ---------------- 入口 ----------------
need_root "$@"
case "${1:-}" in
    setup)    do_setup ;;
    teardown) do_teardown ;;
    status)   do_status ;;
    demo)     do_demo ;;
    *)        sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac


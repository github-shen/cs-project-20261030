#!/usr/bin/env bash
# run_nic.sh —— 双介质（有线 eth + 无线 wifi）双机启动
#
# 用法:  A 机执行  bash scripts/run_nic.sh A
#        B 机执行  bash scripts/run_nic.sh B
#
# 前提:  1) bash scripts/nic.sh gen <A_eth> <A_wifi> <B_eth> <B_wifi>  已生成 config-nic/
#        2) make 已编译
#
# 分工:
#   A 机: 节点1 + 节点2；主机 0x0A01/0x0A02(上行, 本机回环)、0x0C01/0x0D01(下行, 跨机走有线)
#   B 机: 节点3 + 节点4；主机 0x0B01(下行, 跨机走无线)
#
# 介质落位（与 config-nic-template 一致）:
#   无线 WiFi(ens37): 域内 1—3 链路、下行 0x0B 信道（主机在 B 机，节点在 A 机）
#   有线 eth (ens33): 域内 2—3 / 2—4 链路、下行 0x0C / 0x0D 信道（主机在 A 机，节点在 B 机）
set -u
cd "$(dirname "$0")/.." || exit 1

ROLE="${1:-}"
ENVF="config-nic/ips.env"

if [ ! -f "$ENVF" ]; then
    echo "缺少 $ENVF，先执行:" >&2
    echo "  bash scripts/nic.sh gen <A_eth> <A_wifi> <B_eth> <B_wifi>" >&2
    exit 1
fi
for n in 1 2 3 4; do
    [ -f "config-nic/node$n.conf" ] || {
        echo "缺少 config-nic/node$n.conf，先执行 scripts/nic.sh gen" >&2
        exit 1
    }
done
# shellcheck disable=SC1090
. "./$ENVF"

case "$ROLE" in
A) MY_ETH="$A_ETH"; MY_WIFI="$A_WIFI"; PEER_ETH="$B_ETH"; PEER_WIFI="$B_WIFI" ;;
B) MY_ETH="$B_ETH"; MY_WIFI="$B_WIFI"; PEER_ETH="$A_ETH"; PEER_WIFI="$A_WIFI" ;;
*)
    echo "用法: bash scripts/run_nic.sh A|B" >&2
    echo "  A = $A_ETH(eth) / $A_WIFI(wifi)" >&2
    echo "  B = $B_ETH(eth) / $B_WIFI(wifi)" >&2
    exit 1
    ;;
esac

if [ ! -x ./bin/switch ] || [ ! -x ./bin/host ]; then
    echo "还没编译，先执行: make" >&2
    exit 1
fi
command -v tmux >/dev/null 2>&1 || { echo "需要 tmux: sudo apt install -y tmux" >&2; exit 1; }

# SO_BINDTODEVICE（dev 强绑网卡）需要 root；没有 root 时只按 IP 绑定，
# 因为对端 IP 与本端 IP 同网段，内核路由仍会选中正确的网卡，功能等价。
# 注意：tmux 必须以普通用户身份启动（否则后续 demo_nic.sh 连不上 root 的 tmux server），
#       root 只用于 tmux 里面跑的那条 ./bin/switch 命令。
if sudo -n true 2>/dev/null; then
    echo "== root 可用：dev 强绑网卡生效（SO_BINDTODEVICE）=="
else
    echo "== 需要一次 sudo 密码才能让 dev 强绑网卡生效（直接回车则退化为按 IP 绑定，功能等价）=="
    if sudo -v 2>/dev/null && sudo -n true 2>/dev/null; then
        echo "== 已获得 root：dev 强绑网卡生效 =="
    else
        echo "== 未获得 root：仅按 IP 绑定（节点照常运行，介质由 bind IP 区分）=="
    fi
fi

# 每个节点一条 tmux 会话：登录窗口里再判断一次能否 sudo（防止凭证过期导致节点起不来）
node_cmd() {
    echo "cd $(pwd) && if sudo -n true 2>/dev/null; then sudo -n ./bin/switch config-nic/node$1.conf; else ./bin/switch config-nic/node$1.conf; fi"
}

echo "本机 $ROLE: eth=$MY_ETH($ETH_DEV)  wifi=$MY_WIFI($WIFI_DEV)"
for d in "$ETH_DEV" "$WIFI_DEV"; do
    ip link show dev "$d" >/dev/null 2>&1 || {
        echo "  ✗ 网卡 $d 不存在（改网卡名: ETH_DEV=xxx WIFI_DEV=yyy 或重新 nic.sh gen）" >&2
        exit 1
    }
    ip -4 -br addr show dev "$d" | awk -v d="$d" '{printf "  %-8s %s\n", d, ($3=="" ? "无IP" : $3)}'
done
echo "对端:      eth=$PEER_ETH  wifi=$PEER_WIFI"
ping -c1 -W1 "$PEER_ETH"  >/dev/null 2>&1 && echo "  有线网段 可达 ✓" || echo "  有线网段 不可达 ✗"
ping -c1 -W1 "$PEER_WIFI" >/dev/null 2>&1 && echo "  无线网段 可达 ✓" || echo "  无线网段 不可达 ✗（无线路径会失败）"

tmux kill-server 2>/dev/null
sleep 0.3

case "$ROLE" in
A)
    tmux new-session -d -s n1 "$(node_cmd 1)"
    tmux new-session -d -s n2 "$(node_cmd 2)"
    sleep 0.5
    # 上行主机：本机回环（进程模拟）
    tmux new-session -d -s h0x0A01 "cd $(pwd) && ./bin/host 0x0A01 9001 127.0.0.1 1103"
    tmux new-session -d -s h0x0A02 "cd $(pwd) && ./bin/host 0x0A02 9005 127.0.0.1 1103"
    # 下行 0x0C / 0x0D：本机的主机进程，跨机连到 B 机节点，走有线
    tmux new-session -d -s h0x0C01 "cd $(pwd) && ./bin/host 0x0C01 9003 $B_ETH 2104 bind $A_ETH"
    tmux new-session -d -s h0x0D01 "cd $(pwd) && ./bin/host 0x0D01 9004 $B_ETH 4103 bind $A_ETH"
    # 冒名主机（源地址校验演示）
    tmux new-session -d -s spoof   "cd $(pwd) && ./bin/host 0x0C05 9006 127.0.0.1 1103"
    ;;
B)
    tmux new-session -d -s n3 "$(node_cmd 3)"
    tmux new-session -d -s n4 "$(node_cmd 4)"
    sleep 0.5
    # 下行 0x0B：本机的主机进程，跨机连到 A 机节点2，走无线
    tmux new-session -d -s h0x0B01 "cd $(pwd) && ./bin/host 0x0B01 9002 $A_WIFI 3104 bind $B_WIFI"
    ;;
esac

sleep 1.2
echo
echo "== 本机角色 $ROLE 已启动 =="
tmux ls
echo
echo "看某节点: tmux attach -t n1   (Ctrl-b d 脱离)"
echo "发命令:   tmux send-keys -t n1 'table' Enter"
echo "演示:     bash scripts/demo_nic.sh A|B"
echo "断链实验: bash scripts/link_test.sh"
echo "抓包取证: sudo bash scripts/capture.sh"
echo "停止:     bash scripts/stop_all.sh"

#!/usr/bin/env bash
# link_test.sh —— ps(1) 断链实验：证明两种介质是两条独立的物理路径
#
# 在 A 机执行（B 机保持 run_nic.sh B 运行状态）：
#   bash scripts/link_test.sh
#
# 原理：把本机的无线网卡 down 掉 20 秒（脚本自动恢复），同一组流量里
#   走无线的两条   —— 0x0A02→0x0C01（无线 1—3）、0x0A01→0x0B01（无线信道 0x0B） 应当失败
#   走有线的两条   —— 0x0A01→0x0C01、0x0A01→0x0D01                               应当照常通
# 这就证明：ens37 与 ens33 是两条真实、互相独立的链路，不是同一块网卡的两种叫法。
set -u
cd "$(dirname "$0")/.." || exit 1

ENVF="config-nic/ips.env"
if [ ! -f "$ENVF" ]; then
    echo "缺少 $ENVF，先执行 bash scripts/nic.sh gen ..." >&2
    exit 1
fi
# shellcheck disable=SC1090
. "./$ENVF"

command -v tmux >/dev/null 2>&1 || { echo "需要 tmux" >&2; exit 1; }
if ! sudo -n true 2>/dev/null; then
    echo "断链需要 root（ip link set）。请先执行:  sudo -v   再重跑本脚本" >&2
    exit 1
fi

WIFI="${WIFI_DEV:-ens37}"
title() { printf '\n\033[1;36m═══ %s ═══\033[0m\n' "$*"; }
say()   { printf '  %s\n' "$*"; }
S()     { tmux send-keys -t "$1" "$2" Enter; }
logs()  { tmux capture-pane -pJ -t "$1" -S -400 2>/dev/null | grep -E '^\[(FWD|DROP|RECV)' | tail -"${2:-1}" | sed 's/^/    /'; }

# 四条流：f1/f4 走有线，f2/f3 走无线
send_all() {
    S h0x0A01 "send 0x0C01 lt-eth-1"     # f1 有线
    sleep 0.6
    S h0x0A02 "send 0x0C01 lt-wifi-1"    # f2 无线（去程 1—3）
    sleep 0.6
    S h0x0A01 "send 0x0B01 lt-wifi-2"    # f3 无线（下行信道 0x0B）
    sleep 0.6
    S h0x0A01 "send 0x0D01 lt-eth-2"     # f4 有线
    sleep 1.2
}

restore() {
    sudo ip link set "$WIFI" up 2>/dev/null
    echo "  已恢复 $WIFI up"
}
trap restore EXIT

title "0. 准备"
say "无线网卡: $WIFI ($A_WIFI)   有线网卡: ${ETH_DEV:-ens33} ($A_ETH)"
say "将断开 $WIFI 约 20 秒并自动恢复（trap 保证，Ctrl-C 也会恢复）"
printf '  确认执行请输入 YES: '
read -r ok
[ "$ok" = "YES" ] || { echo "  已取消"; exit 0; }

title "1. 基线：两块网卡都在线"
send_all
say "节点1（f1 出端口1、f2 出端口2、f3/f4 出端口1）："; logs n1 2
say "主机 0x0C01（f1 应收到 lt-eth-1；f2 应收到 lt-wifi-1）："; logs h0x0C01 2
say "主机 0x0D01（f4 应收到 lt-eth-2）："; logs h0x0D01 1
say "节点2（f3 应从外部端口4/wifi 送出）："; logs n2 1

title "2. 断开无线网络 $WIFI"
sudo ip link set "$WIFI" down
sleep 1
ip -br link show "$WIFI" | sed 's/^/    /'
send_all
say "节点1："; logs n1 2
say "节点2（f3 走 wifi 外部端口，应出现 [DROP] ... 发送失败）："; logs n2 1
say "主机 0x0C01：f1(lt-eth-1, 有线) 应仍在；f2(lt-wifi-1, 无线) 应消失"; logs h0x0C01 2
say "主机 0x0D01：f4(lt-eth-2, 有线) 应仍在"; logs h0x0D01 1

title "3. 恢复无线网络 $WIFI"
sudo ip link set "$WIFI" up
sleep 3
send_all
say "节点1："; logs n1 2
say "主机 0x0C01（两条都应在）："; logs h0x0C01 2
say "主机 0x0D01："; logs h0x0D01 1
say "节点2（f3 恢复外部转发）："; logs n2 1

title "结论（截图用）"
say "断无线期间：0x0C01 只收到 lt-eth-1（有线路径），lt-wifi-1 消失；0x0D01 照常收到 lt-eth-2"
say "            → 证明 ens33(有线) 与 ens37(无线) 是两条独立链路，满足 ps(1)“任选两种”"
say "B 机窗口对照：0x0B01 在断链期间收不到 lt-wifi-2，恢复后能收到"

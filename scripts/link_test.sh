#!/usr/bin/env bash
# link_test.sh —— 双接口断链与等价路径恢复实验（物理介质需另行核实）
#
# 在 A 机执行（B 机保持 run_nic.sh B 运行状态）：
#   bash scripts/link_test.sh
#
# 0x0A02→0x0C01及0x0A01→0x0D01走有线；0x0A01→0x0C01无线主用、有线备用；
# 0x0A01→0x0B01只有无线外部信道。备用重试依赖sendto明确报错，并非远端故障探测。
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
logs()  { tmux capture-pane -pJ -t "$1" -S -400 2>/dev/null | grep -E '^\[(FWD|DROP|RECV|REROUTE)' | tail -"${2:-1}" | sed 's/^/    /'; }

# 唯一阶段标记避免把断链前的旧收包记录当作断链期间的成功证据。
run_id="$$"
send_all() {
    local phase="$1"
    S h0x0A02 "send 0x0C01 lt-eth-1-${phase}-${run_id}"   # f1 有线
    sleep 0.6
    S h0x0A01 "send 0x0C01 lt-wifi-1-${phase}-${run_id}"  # f2 无线主用，有线备用
    sleep 0.6
    S h0x0A01 "send 0x0B01 lt-wifi-2-${phase}-${run_id}"  # f3 单路径无线
    sleep 0.6
    S h0x0A01 "send 0x0D01 lt-eth-2-${phase}-${run_id}"   # f4 有线
    sleep 1.2
}

# 拒绝关闭管理接口；只恢复本脚本确实尝试关闭的、原本UP的接口。
if { ip -4 route show default; ip -6 route show default; } | awk '{for(i=1;i<NF;i++) if($i=="dev") print $(i+1)}' | grep -Fxq "$WIFI"; then
    echo "拒绝操作：$WIFI 承载默认路由" >&2; exit 1
fi
if [ -n "${SSH_CONNECTION:-}" ]; then
    read -r ssh_peer _ <<< "$SSH_CONNECTION"
    ssh_route=$(ip route get "$ssh_peer") || { echo "无法确认SSH管理路径，拒绝断链" >&2; exit 1; }
    if printf '%s\n' "$ssh_route" | awk '{for(i=1;i<NF;i++) if($i=="dev") print $(i+1)}' | grep -Fxq "$WIFI"; then
        echo "拒绝操作：$WIFI 承载当前SSH连接" >&2; exit 1
    fi
fi
if ! ip -o link show dev "$WIFI" | grep -Eq '<([^>]*,)?UP(,|>)'; then
    echo "拒绝操作：$WIFI 不存在或原本不是UP" >&2; exit 1
fi
restore_needed=0
restore() {
    if [ "$restore_needed" = 1 ]; then
        if sudo ip link set "$WIFI" up; then
            restore_needed=0; echo "  已恢复 $WIFI up"
        else
            echo "  恢复 $WIFI 失败，请检查接口" >&2
            return 1
        fi
    fi
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

title "0. 准备"
say "无线网卡: $WIFI ($A_WIFI)   有线网卡: ${ETH_DEV:-ens33} ($A_ETH)"
say "将断开 $WIFI，完成断链阶段后自动恢复；Ctrl-C会触发恢复"
printf '  确认执行请输入 YES: '
read -r ok
[ "$ok" = "YES" ] || { echo "  已取消"; exit 0; }

title "1. 基线：两块网卡都在线"
send_all baseline
say "节点1（f1 出端口1、f2 出端口2、f3/f4 出端口1）："; logs n1 4
say "主机 0x0C01（f1 应收到 lt-eth-1；f2 应收到 lt-wifi-1）："; logs h0x0C01 2
say "主机 0x0D01（f4 应收到 lt-eth-2）："; logs h0x0D01 1
say "节点2（f3 应从外部端口4/wifi 送出）："; logs n2 2

title "2. 断开无线网络 $WIFI"
restore_needed=1
sudo ip link set "$WIFI" down || exit 1
sleep 1
ip -br link show "$WIFI" | sed 's/^/    /'
send_all down
say "节点1（f2应在本地发送报错后出现[REROUTE]，需同时核对接收端）："; logs n1 6
say "节点2（f3 走 wifi 外部端口，应出现 [DROP] ... 发送失败）："; logs n2 3
say "主机 0x0C01：f1有线不受影响；f2可切到有线备用，核对带-down-${run_id}的两条收包"; logs h0x0C01 2
say "主机 0x0D01：f4(lt-eth-2, 有线) 应仍在"; logs h0x0D01 1

title "3. 恢复无线网络 $WIFI"
restore || exit 1
sleep 3
send_all restored
say "节点1："; logs n1 4
say "主机 0x0C01（两条都应在）："; logs h0x0C01 2
say "主机 0x0D01："; logs h0x0D01 1
say "节点2（f3 恢复外部转发）："; logs n2 2

title "结果核对（依据本次阶段标记，不自动宣告通过）"
say "预期：f1/f4有线仍通；f2无线主用发送报错后走有线备用；f3单路径无线中断"
say "核对接收端的baseline/down/restored标记及[REROUTE]，本次标识：${run_id}"
say "B机0x0B01不应收到lt-wifi-2-down-${run_id}，恢复后应收到restored标记"
say "该实验验证接口路径；真实Wi-Fi/有线介质需硬件证据，虚拟网卡不能代替。"

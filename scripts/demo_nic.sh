#!/usr/bin/env bash
# demo_nic.sh —— 双介质（有线 + 无线）一键演示
#
# 用法:
#   B 机: bash scripts/demo_nic.sh B      # 先开，滚动显示 B 侧收包
#   A 机: bash scripts/demo_nic.sh A      # 再开，跑 7 幕（约 30 秒）
#
# 前提: 两台机器已跑 bash scripts/run_nic.sh A / B
#
# 介质对照（抓包可证，见 scripts/capture.sh）:
#   无线 WiFi(ens37): 域内 1—3（1102→2002）、下行 0x0B 信道（3104→9002）
#   有线 eth (ens33): 域内 2—3（3003→2001）、2—4（3002→4001）、下行 0x0C/0x0D（2104→9003、4103→9004）
set -u
cd "$(dirname "$0")/.." || exit 1
ROLE="${1:-A}"
ENVF="config-nic/ips.env"
[ -f "$ENVF" ] && . "./$ENVF" || { A_ETH=; A_WIFI=; B_ETH=; B_WIFI=; }

command -v tmux >/dev/null 2>&1 || { echo "需要 tmux: sudo apt install -y tmux" >&2; exit 1; }

title() { printf '\n\033[1;36m═══ %s ═══\033[0m\n' "$*"; }
say()   { printf '  %s\n' "$*"; }
S()     { tmux send-keys -t "$1" "$2" Enter; }
has()   { tmux has-session -t "$1" 2>/dev/null; }
logs() {
    tmux capture-pane -pJ -t "$1" -S -400 2>/dev/null \
        | grep -E '^\[(FWD|DROP|RECV)' | tail -"${2:-1}" | sed 's/^/    /'
}
raw() {
    tmux capture-pane -pJ -t "$1" -S -"${2:-400}" 2>/dev/null \
        | sed '/^[[:space:]]*$/d' | sed 's/^/    /'
}

case "$ROLE" in

B)
    ROUNDS="${2:-12}"
    title "B 机（下行侧 / 节点3、节点4）观察窗口"
    say "共刷新 ${ROUNDS} 次，每 3 秒一次；随时 Ctrl-C 结束"
    for i in $(seq 1 "$ROUNDS"); do
        printf '\n----- 第 %s 次刷新 -----\n' "$i"
        say "主机 0x0B01（走无线接入 A 机节点2）："; logs h0x0B01 3
        say "节点3："; logs n3 3
        say "节点4："; logs n4 3
        sleep 3
    done
    title "B 机观察结束"
    ;;

A)
    title "0. 环境检查"
    tmux ls | sed 's/^/    /'
    [ -n "${B_ETH:-}" ] && { ping -c1 -W1 "$B_ETH"  >/dev/null 2>&1 && say "有线网段 $B_ETH 可达 ✓" || say "有线网段 $B_ETH 不可达 ✗"; }
    [ -n "${B_WIFI:-}" ] && { ping -c1 -W1 "$B_WIFI" >/dev/null 2>&1 && say "无线网段 $B_WIFI 可达 ✓" || say "无线网段 $B_WIFI 不可达 ✗（无线场景会失败）"; }

    title "1. 有线路径（题目 2(2)+2(3)：外部转发 + 内部转发）"
    say "0x0A01 → 0x0C01：节点1 --回环--> 节点2 --有线 2—3--> 节点3 --外部端口4(有线)--> 主机"
    say "抓包对照: sudo tcpdump -i ${ETH_DEV:-ens33} -n udp 'port 3003 or port 2104'"
    S h0x0A01 "send 0x0C01 nic-eth-1"
    sleep 1.6
    say "节点1："; logs n1 1
    say "节点2："; logs n2 1
    say "主机 0x0C01（在 A 机，经有线接入 B 机）："; logs h0x0C01 1

    title "2. 无线路径（同一目的、换源 → 换介质，题目 ps(2) 负载均衡）"
    say "0x0A02 → 0x0C01：节点1 --无线 1—3--> 节点3（去程走 ens37）"
    say "抓包对照: sudo tcpdump -i ${WIFI_DEV:-ens37} -n udp 'port 1102'"
    S h0x0A02 "send 0x0C01 nic-wifi-1"
    sleep 1.6
    say "节点1："; logs n1 1
    say "主机 0x0C01："; logs h0x0C01 1

    title "3. 无线外部信道（下行 0x0B，主机在 B 机、节点在 A 机）"
    say "0x0A01 → 0x0B01：节点1 → 节点2 → 外部端口4(wifi) —— 跨机这一段走 ens37"
    say "抓包对照: sudo tcpdump -i ${WIFI_DEV:-ens37} -n udp 'port 3104'"
    S h0x0A01 "send 0x0B01 nic-wifi-2"
    sleep 1.6
    say "节点1："; logs n1 1
    say "节点2："; logs n2 1
    say "（B 机窗口应出现 [RECV] 来自0x0A01: nic-wifi-2）"

    title "4. 有线外部信道（下行 0x0D，主机在 A 机、节点在 B 机）"
    say "0x0A01 → 0x0D01：节点1 → 节点2 --有线 2—4--> 节点4 → 外部端口3(eth)"
    say "抓包对照: sudo tcpdump -i ${ETH_DEV:-ens33} -n udp 'port 3002 or port 4103'"
    S h0x0A01 "send 0x0D01 nic-eth-2"
    sleep 1.6
    say "节点1："; logs n1 1
    say "节点2："; logs n2 1
    say "主机 0x0D01："; logs h0x0D01 1

    title "5. 数据包校验 CRC（坏帧必须被丢）"
    S h0x0A01 "sendbad 0x0C01 bad-crc"
    sleep 1.2
    say "节点1："; logs n1 1

    title "6. 源地址校验（冒名寄件被拒）"
    say "自称 0x0C05 的主机挂在上行端口（该端口只连 0x0A 网络）"
    S spoof "send 0x0B01 spoofed"
    sleep 1.2
    say "节点1："; logs n1 1

    title "7. 无路由兜底 + 两张表"
    S n1 "send 0x0E01 nowhere"; sleep 1.0
    say "节点1："; logs n1 1
    S n1 "reset"; sleep 0.4
    S h0x0A01 "send 0x0C01 stat-1";     sleep 0.8
    S h0x0A02 "send 0x0C01 stat-wifi";  sleep 0.8
    S h0x0A01 "sendbad 0x0C01 stat-bad"; sleep 0.8
    S spoof   "send 0x0B01 stat-spoof"; sleep 0.8
    S n1 "stats"; sleep 0.8
    say "节点1 stats："
    raw n1 400 | grep -E '^ *接收=' | tail -2
    S n1 "table"; sleep 0.9
    say "节点1 table（注意 信道 列的 eth / wifi）："
    raw n1 400 | sed -n '/==== 外部端口表/,$p'

    title "演示结束"
    say "两种介质都跑了真实流量：ens37(无线) 承载 1—3 与 0x0B 信道；ens33(有线) 承载 2—3、2—4 与 0x0C/0x0D 信道"
    say "想做硬证据: sudo bash scripts/capture.sh  然后重跑本演示，看两个 pcap 各自的端口对"
    say "断链实验:   bash scripts/link_test.sh"
    ;;
*)
    echo "用法: bash scripts/demo_nic.sh A|B" >&2
    exit 1
    ;;
esac

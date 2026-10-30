#!/usr/bin/env bash
# demo.sh —— 双机一键演示（答辩时照着跑）
#
# 用法:
#   B 机: bash scripts/demo.sh B          # 先开这个，滚动显示 B 侧收到的消息
#   A 机: bash scripts/demo.sh A          # 再开这个，自动跑 7 个场景并打印 A 侧日志
#
# 前提: 两台机器已经跑过 bash scripts/run_lan.sh A / B
# 说明: 脚本只会往 tmux 窗口里打命令、再抓日志，不会改任何配置。
set -u
cd "$(dirname "$0")/.." || exit 1
ROLE="${1:-A}"

command -v tmux >/dev/null 2>&1 || { echo "需要 tmux: sudo apt install -y tmux" >&2; exit 1; }

title() { printf '\n\033[1;36m═══ %s ═══\033[0m\n' "$*"; }
say()   { printf '  %s\n' "$*"; }
S()     { tmux send-keys -t "$1" "$2" Enter; }
has()   { tmux has-session -t "$1" 2>/dev/null; }

# 只抓转发/丢弃/接收日志（默认抓整段历史，避免窗口高度不同抓不到）
logs() {
    tmux capture-pane -pJ -t "$1" -S -400 2>/dev/null \
        | grep -E '^\[(FWD|DROP|RECV)' | tail -"${2:-1}" | sed 's/^/    /'
}
# 抓原始输出（table / stats 用）：$2 = 抓多少行历史
raw() {
    tmux capture-pane -pJ -t "$1" -S -"${2:-400}" 2>/dev/null \
        | sed '/^[[:space:]]*$/d' | sed 's/^/    /'
}

case "$ROLE" in

B)
    ROUNDS="${2:-10}"
    title "B 机（下行侧）观察窗口"
    say "主机 0x0C01 收到的消息 + 节点3 / 节点4 的转发日志"
    say "共刷新 ${ROUNDS} 次，每 3 秒一次；随时 Ctrl-C 结束"
    for i in $(seq 1 "$ROUNDS"); do
        printf '\n----- 第 %s 次刷新 -----\n' "$i"
        say "主机 0x0C01："; logs h0x0C01 3
        say "节点3：";       logs n3 3
        say "节点4：";       logs n4 3
        sleep 3
    done
    title "B 机观察结束"
    ;;

A)
    title "0. 环境检查"
    tmux ls | sed 's/^/    /'
    say "B 机连通性："
    if ping -c1 -W1 192.168.222.130 >/dev/null 2>&1; then
        say "192.168.222.130 可达"
    else
        say "警告：192.168.222.130 不可达，跨机场景可能收不到"
    fi
    # 负载均衡 / 冒名演示需要这两台"临时主机"
    has h0x0A02 || tmux new-session -d -s h0x0A02 "cd $(pwd) && ./bin/host 0x0A02 9005 127.0.0.1 1103"
    has spoof   || tmux new-session -d -s spoof   "cd $(pwd) && ./bin/host 0x0C05 9006 127.0.0.1 1103"
    sleep 0.8

    title "1. 跨机送达（题目 2(2) 外部转发 + 2(3) 内部转发）"
    say "0x0A01 发一句话给 0x0C01（哈希选中端口2 → 直接跨机到 B 机节点3）"
    S h0x0A01 "send 0x0C01 order-001"
    sleep 1.5
    say "A 机 节点1（外部端口进 → 内部跨域出）："; logs n1 1
    say "（B 机节点3 应出现：外部转发 入端口2 -> 外部端口4）"

    title "2. 域内逐跳（1 → 2 → 4）"
    say "节点1 直接发一帧给节点4（地址 0x0104，唯一路径经节点2）："
    S n1 "send 0x0104 ping-node4"
    sleep 1.6
    say "A 机 节点1："; logs n1 1
    say "A 机 节点2："; logs n2 1

    title "3. 负载均衡（同目的、不同源 → 不同下一跳）"
    say "源 0x0101（节点1 自己）哈希落 0 → 端口1；源 0x0A01（外部主机）落 1 → 端口2"
    S n1 "send 0x0C01 lb-src-0101"
    sleep 1.0
    S h0x0A01 "send 0x0C01 lb-src-0A01"
    sleep 1.5
    say "A 机 节点1 的两条日志："; logs n1 2

    title "4. CRC 校验（坏帧必须被丢）"
    say "0x0A01 发一个 CRC 故意损坏的帧："
    S h0x0A01 "sendbad 0x0C01 bad-crc"
    sleep 1.2
    say "A 机 节点1："; logs n1 1

    title "5. 源地址校验（冒名寄件被拒）"
    say "一台自称 0x0C05 的主机挂在上行端口（只连 0x0A 网络），发帧给 0x0B01："
    S spoof "send 0x0B01 spoofed"
    sleep 1.2
    say "A 机 节点1："; logs n1 1

    title "6. 无路由兜底（地址不存在就丢，不乱转）"
    S n1 "send 0x0E01 nowhere"
    sleep 1.2
    say "A 机 节点1："; logs n1 1

    title "7. 收尾：统计计数 + 两张表"
    S n1 "reset"; sleep 0.4
    # 重新制造一些流量，让统计不为 0
    S h0x0A01 "send 0x0C01 stat-1"; sleep 0.8
    S h0x0A01 "sendbad 0x0C01 stat-bad"; sleep 0.8
    S spoof   "send 0x0B01 stat-spoof"; sleep 0.8
    S n1 "stats"; sleep 0.8
    say "节点1 stats："
    raw n1 400 | grep -E '^ *接收=' | tail -2
    S n1 "table"; sleep 0.9
    say "节点1 table："
    raw n1 400 | sed -n '/==== 外部端口表/,$p'

    title "演示结束"
    say "B 机窗口应能看到：[RECV] 来自0x0101/0x0A02/0x0A01 的多条消息"
    say "B 机节点3 应能看到：外部转发 -> 外部端口4(网络0x0C/wifi)"
    ;;

*)
    echo "用法: bash scripts/demo.sh A|B" >&2
    exit 1
    ;;
esac

#!/usr/bin/env bash
# app_demo.sh —— 应用场景演示：三家门店 ↔ 三台业务服务器，中间是 4 节点交换域
#
# 用法（任意一台机器，单机回环，不用改任何配置）:
#   bash scripts/app_demo.sh              # 8 幕，约 25 秒
#   sudo bash scripts/app_demo.sh         # 多一幕"链路故障自动切换"（要 root 加一条 unreachable 路由）
#
# 业务模型:
#   门店终端 0x0A01/0x0A02/0x0A03 挂在 上行信道 0x0A（节点1 外部端口3）
#   订单库 0x0B01（节点2 外部端口4） 库存库 0x0C01（节点3 外部端口4） 结算库 0x0D01（节点4 外部端口3）
#   节点1 往库存库有两条等价路径：端口2 正常、端口4 是人造的"故障链路"
#
# 用到 config-app/：节点1 多一条不可达链路 + ift 0C00 2,4，用来演示快速重路由
set -u
cd "$(dirname "$0")/.." || exit 1

CFG="config-app"
command -v tmux >/dev/null 2>&1 || { echo "需要 tmux: sudo apt install -y tmux" >&2; exit 1; }
[ -x ./bin/switch ] && [ -x ./bin/host ] || { echo "先编译: make" >&2; exit 1; }
[ -d "$CFG" ] || { echo "缺少 $CFG/ 配置目录" >&2; exit 1; }

title() { printf '\n\033[1;36m═══ %s ═══\033[0m\n' "$*"; }
say()   { printf '  %s\n' "$*"; }
S()     { tmux send-keys -t "$1" "$2" Enter; }
logs()  { tmux capture-pane -pJ -t "$1" -S -400 2>/dev/null | grep -E '^\[(FWD|DROP|RECV|REROUTE)' | tail -"${2:-1}" | sed 's/^/    /'; }
raw()   { tmux capture-pane -pJ -t "$1" -S -"${2:-400}" 2>/dev/null | sed '/^[[:space:]]*$/d' | sed 's/^/    /'; }

# ---- 故障链路：把 10.255.255.0/24 设成"不可达"（有 root 才做）----
ROUTE_ADDED=0
cleanup() { [ "$ROUTE_ADDED" = 1 ] && sudo -n ip route del unreachable 10.255.255.0/24 2>/dev/null; }
trap cleanup EXIT
if sudo -n true 2>/dev/null && sudo -n ip route add unreachable 10.255.255.0/24 2>/dev/null; then
    ROUTE_ADDED=1
fi

title "0. 搭建业务网络"
tmux kill-server 2>/dev/null
sleep 0.3
for i in 1 2 3 4; do
    tmux new-session -d -s "n$i" "cd $(pwd) && ./bin/switch $CFG/node$i.conf"
done
sleep 0.6
for t in "0x0A01 9001 1103" "0x0A02 9005 1103" "0x0A03 9007 1103" \
         "0x0B01 9002 3104" "0x0C01 9003 2104" "0x0D01 9004 4103" "0x0C05 9006 1103"; do
    set -- $t
    tmux new-session -d -s "h$1" "cd $(pwd) && ./bin/host $1 $2 127.0.0.1 $3"
done
sleep 1.2
say "4 个交换节点 + 3 家门店 + 3 台服务器 + 1 台冒名主机 已就绪"
if [ "$ROUTE_ADDED" = 1 ]; then
    say "故障链路已就绪：10.255.255.0/24 = unreachable（节点1 端口4 会发送失败）"
else
    say "提示：未获得 root，第 2/8 幕的自动切换演示会退化为“丢包”；用 sudo 跑可看到 [REROUTE]"
fi

title "1. 门店A 下单 → 订单库 0x0B01（跨域 + 外部转发）"
S h0x0A01 "send 0x0B01 order-A-1001"
sleep 1.2
say "节点1："; logs n1 1
say "订单库 0x0B01："; logs h0x0B01 1

title "2. 门店A 查库存 → 库存库 0x0C01（主用链路故障，自动切换）"
say "节点1 的 0x0C00 有两条等价路径：端口2(正常) / 端口4(断的)；该流的哈希落在端口4"
S h0x0A01 "send 0x0C01 stock-A"
sleep 1.5
say "节点1："; logs n1 3
say "库存库 0x0C01："; logs h0x0C01 1

title "3. 门店B 查库存 → 库存库 0x0C01（另一条流落在正常端口，无需切换）"
S h0x0A02 "send 0x0C01 stock-B"
sleep 1.2
say "节点1："; logs n1 1
say "库存库 0x0C01："; logs h0x0C01 1

title "4. 三家门店同时上报心跳给节点3（同一目的、不同源 → 不同下一跳，负载均衡）"
say "节点1 去节点3 的 0x0103 是双端口等价路径，三家门店的哈希分别落在不同端口"
S h0x0A01 "send 0x0103 heartbeat-A"
sleep 0.5
S h0x0A02 "send 0x0103 heartbeat-B"
sleep 0.5
S h0x0A03 "send 0x0103 heartbeat-C"
sleep 1.5
say "节点1（三次转发的出端口）："; logs n1 3
say "节点3（交付本节点）："; logs n3 3

title "5. 数据包校验：一条坏帧必须被丢（业务不受影响）"
S h0x0A01 "sendbad 0x0B01 broken-order"
sleep 1.0
say "节点1："; logs n1 1

title "6. 源地址校验：冒充门店的帧被拒"
say "一台自称 0x0C05 的主机挂在上行端口（该端口只连 0x0A 网络）"
S h0x0C05 "send 0x0B01 fake-order"
sleep 1.0
say "节点1："; logs n1 1

title "7. 边界：访问一个不存在的库 0x0E01"
S n1 "send 0x0E01 nowhere"
sleep 1.0
say "节点1："; logs n1 1

title "8. 运维视角：计数与两张表"
S n1 "stats"; sleep 0.8
say "节点1 统计（注意 重路由 计数）："
raw n1 400 | grep -E '^ *接收=' | tail -1
S n1 "table"; sleep 0.9
say "节点1 两张表："
raw n1 400 | sed -n '/==== 外部端口表/,$p'

title "演示结束"
say "继续观察: tmux attach -t n1   /  tmux attach -t h0x0C01"
say "停止全部: bash scripts/stop_all.sh"

#!/usr/bin/env bash
# run_lan.sh —— 两台机器分工启动（跨机部署）
#
# 用法:  A 机执行  bash scripts/run_lan.sh A
#        B 机执行  bash scripts/run_lan.sh B
#
# A 机: 节点1 + 节点2 + 主机 0x0A01(上行信道) + 0x0B01(下行信道3)
# B 机: 节点3 + 节点4 + 主机 0x0C01(下行信道5) + 0x0D01(下行信道1)
#
# 前提: 两台机器的 config-lan/*.conf 里 IP 与实际一致（A=192.168.222.128 B=192.168.222.130）
set -u

cd "$(dirname "$0")/.." || exit 1
ROLE="${1:-}"

case "$ROLE" in
A)
    NODES="1 2"
    HOSTS="0x0A01 9001 1103  0x0B01 9002 3104"
    ;;
B)
    NODES="3 4"
    HOSTS="0x0C01 9003 2104  0x0D01 9004 4103"
    ;;
*)
    echo "用法: bash scripts/run_lan.sh A|B   (A=192.168.222.128  B=192.168.222.130)" >&2
    exit 1
    ;;
esac

if [ ! -x ./bin/switch ] || [ ! -x ./bin/host ]; then
    echo "还没编译，先执行: make" >&2
    exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
    echo "需要 tmux: sudo apt install -y tmux" >&2
    exit 1
fi

tmux kill-server 2>/dev/null
sleep 0.3

for n in $NODES; do
    tmux new-session -d -s "n$n" "cd $(pwd) && ./bin/switch config-lan/node$n.conf"
done

# HOSTS 是 "地址 本地端口 节点外部端口" 三连，三三成组
set -- $HOSTS
while [ "$#" -ge 3 ]; do
    addr="$1"; lport="$2"; swport="$3"; shift 3
    tmux new-session -d -s "h$addr" "cd $(pwd) && ./bin/host $addr $lport 127.0.0.1 $swport"
done

sleep 1
echo "== 本机角色 $ROLE 已启动 =="
tmux ls
echo
echo "看某节点: tmux attach -t n$NODES   (Ctrl-b d 脱离)"
echo "发命令:   tmux send-keys -t n1 'table' Enter"
echo "停止:     bash scripts/stop_all.sh"

#!/usr/bin/env bash
# run_tmux.sh —— 在 tmux 里启动 4 个交换节点 + 4 台外部主机（单机运行）
#
# 用法: bash scripts/run_tmux.sh [配置目录]     默认 config
#       bash scripts/run_tmux.sh config-ovs      OVS 模式（先跑 ovs_setup.sh）
#
# 注意：必须给 switch/host 一个真实终端（或 tmux 的 pty）。
#       它们的命令行循环是 while (fgets(...))，stdin 一旦 EOF 就会直接退出，
#       所以不能用 nohup ... & 、也不能 </dev/null。
set -u

cd "$(dirname "$0")/.." || exit 1
CFG="${1:-config}"

if [ ! -x ./bin/switch ] || [ ! -x ./bin/host ]; then
    echo "还没编译，先执行: make" >&2
    exit 1
fi
if [ ! -d "$CFG" ]; then
    echo "配置目录不存在: $CFG" >&2
    exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
    echo "需要 tmux: sudo apt install -y tmux" >&2
    exit 1
fi

tmux kill-server 2>/dev/null
sleep 0.3

for i in 1 2 3 4; do
    tmux new-session -d -s "n$i" "cd $(pwd) && ./bin/switch $CFG/node$i.conf"
done

tmux new-session -d -s h1 "cd $(pwd) && ./bin/host 0x0A01 9001 127.0.0.1 1103"   # 上行信道
tmux new-session -d -s h2 "cd $(pwd) && ./bin/host 0x0B01 9002 127.0.0.1 3104"   # 下行信道3
tmux new-session -d -s h3 "cd $(pwd) && ./bin/host 0x0C01 9003 127.0.0.1 2104"   # 下行信道5
tmux new-session -d -s h4 "cd $(pwd) && ./bin/host 0x0D01 9004 127.0.0.1 4103"   # 下行信道1

sleep 1
tmux ls
echo
echo "查看某个节点的日志:  tmux attach -t n1     (Ctrl-b d 脱离)"
echo "不 attach 直接发命令: tmux send-keys -t n1 'table' Enter"
echo "停止全部:            bash scripts/stop_all.sh"

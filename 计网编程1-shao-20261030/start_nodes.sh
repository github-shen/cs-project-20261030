#!/bin/bash
# start_nodes.sh —— 一键在 4 个 gnome-terminal 窗口中启动 4 个节点
# 用法: bash start_nodes.sh   (如终端不同, 把 gnome-terminal 换成 xterm 等)
cd "$(dirname "$0")"
for i in 1 2 3 4; do
    gnome-terminal --title="节点$i" -- bash -c "./bin/switch config/node$i.conf; echo 已退出; read" &
done
echo "4个节点已启动。另开终端启动主机, 例如:"
echo "  ./bin/host 0x0A01 9001 127.0.0.1 1103   # 上行信道主机"
echo "  ./bin/host 0x0B01 9002 127.0.0.1 3104   # 下行信道3主机"
echo "  ./bin/host 0x0C01 9003 127.0.0.1 2104   # 下行信道5主机"

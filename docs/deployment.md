# 部署与抓包

以下命令在 Ubuntu Bash 中、仓库根目录执行。先按 [README](../README.md) 编译并完成单机收发，再选择本页的一种部署方式。

## 双机 UDP 部署

`config-lan/` 的分工是 A 机运行节点 1、2 和主机 A、B；B 机运行节点 3、4 和主机 C、D。外部主机连接本机交换节点，内部链路跨机器。

两端都安装 `build-essential python3 tmux` 并执行 `make`。用 `ip -4 -br addr` 确认地址、相互 `ping`，然后在两端配置中替换示例地址。下面的值必须换成实际地址：

```bash
A_IP=192.168.10.10
B_IP=192.168.10.11
sed -i -e "s/192\.168\.222\.128/$A_IP/g" \
       -e "s/192\.168\.222\.130/$B_IP/g" config-lan/node*.conf
```

这是对仓库示例地址的一次性替换；如果之前已修改过，应直接编辑实际的 `peer` / `bind` 字段。让防火墙允许实验对端访问配置使用的 UDP 端口，不必关闭整个防火墙。

旧启动脚本会关闭它连接的 tmux 服务器，因此每台机器先在当前终端建立独立的实验目录：

```bash
unset TMUX
export TMUX_TMPDIR="$(mktemp -d /tmp/cnet-lab.XXXXXX)"
printf '本机后续实验终端使用 TMUX_TMPDIR=%s\n' "$TMUX_TMPDIR"
```

在 A 机执行 `bash scripts/run_lan.sh A`，B 机执行 `bash scripts/run_lan.sh B`。后续 `tmux`、演示脚本、停止操作都要使用该机器同一个 `TMUX_TMPDIR`；新终端应先导出刚才打印的实际路径。

A 机发送：

```bash
tmux send-keys -t h0x0A01 'send 0x0C01 lan-check' Enter
tmux send-keys -t n1 'send 0x0104 node-check' Enter
```

B 机检查：

```bash
tmux capture-pane -p -t h0x0C01 -S -30
tmux capture-pane -p -t n4 -S -30
```

应分别出现来自 `0x0A01` 的 `lan-check` 和交付节点 4 的 `node-check`。`scripts/demo.sh` 还包含课程展示场景，但其中连通性提示使用固定的示例 B 地址；更换地址后应自行核对实际对端。

完成后在两端各执行 `tmux kill-server`，关闭各自独立实验服务器。不要用 `stop_all.sh` 清理共享实验机，该脚本按程序名称结束进程。

## 双接口部署

`config-nic-template/` 的主机分工与 LAN 模式不同：

| 机器 | 交换节点 | 外部主机 |
|---|---|---|
| A | 节点 1、2 | A01、A02，以及经对端接入的 C01、D01 |
| B | 节点 3、4 | 经对端接入的 B01 |

节点 1—3 和外部 B 信道走第二接口；节点 2—3、2—4 及外部 C、D 信道走第一接口。两接口应使用不同子网。以下示例假设两端网卡名分别为 `ens33`、`ens37`：

```bash
bash scripts/nic.sh detect
bash scripts/nic.sh gen 192.168.10.10 192.168.20.10 192.168.10.11 192.168.20.11
```

两端都用相同的四个实际 IP 生成配置。若本机网卡名不同，可在生成时指定：

```bash
ETH_DEV=ens160 WIFI_DEV=wlan0 bash scripts/nic.sh gen \
  192.168.10.10 192.168.20.10 192.168.10.11 192.168.20.11
```

目前生成器把这些网卡名写入 `config-nic/ips.env`，**不会替换模板中的 `dev ens33` / `dev ens37`**。还须在每台机器上把本机要运行的节点配置改为实际设备名；例如 A 机：

```bash
sed -i -e 's/dev ens33/dev ens160/g' -e 's/dev ens37/dev wlan0/g' \
  config-nic/node1.conf config-nic/node2.conf
```

B 机对应修改 `node3.conf`、`node4.conf`。两端网卡名不同时分别配置，不能把 A 的 `ips.env` 原样当作 B 的本机配置。用 `ip -4 -br addr` 核对 `bind` 地址，用 `grep 'dev ' config-nic/node*.conf` 核对设备名。

A 机检查 B 的两地址，B 机反向检查 A：

```bash
# A 机，替换为 B 的真实两地址
bash scripts/nic.sh check 192.168.10.11 192.168.20.11
```

按上一节在两端设置各自独立的 `TMUX_TMPDIR`，然后 A 执行 `bash scripts/run_nic.sh A`，B 执行 `bash scripts/run_nic.sh B`。确保节点成功绑定设备；若日志出现 `SO_BINDTODEVICE` 失败，先解决设备名或权限问题，再做断链实验。

B 开启 `bash scripts/demo_nic.sh B` 观察收包，A 执行 `bash scripts/demo_nic.sh A`。预期四条示例消息全部到达：

| 地址流 | 预期路径 |
|---|---|
| A02 → C01 | 第一接口 |
| A01 → C01 | 第二接口主用，第一接口备用 |
| A01 → B01 | 第二接口单路径 |
| A01 → D01 | 第一接口 |

在 A 机执行 `sudo -v` 后运行 `bash scripts/link_test.sh`，阅读它输出的接口名并输入 `YES`。脚本拒绝关闭承载默认路由或当前 SSH 的接口；只适合实验专用接口。断开第二接口时预期 3 条消息到达、B01 路径中断；恢复后重新变为 4 条。核对本次 `baseline/down/restored` 消息标记，以及节点的 `[REROUTE]` / `[DROP]`，不要用旧日志判断。

### 抓包

两端安装 `tcpdump`。在 A 的一个终端执行：

```bash
sudo apt-get install -y tcpdump
sudo bash scripts/capture.sh 60
```

在另一个设置了相同 `TMUX_TMPDIR` 的终端运行 `bash scripts/demo_nic.sh A`。结果写入 `logs/cap-时间-eth.pcap` 和 `logs/cap-时间-wifi.pcap`。使用 `tcpdump -nn -r 文件.pcap` 或 Wireshark 检查端口和实际帧内容，并保留原始文件。

`eth` / `wifi` 是配置标签；在虚拟网卡上抓到流量只能证明接口路径。报告物理介质时，还需记录实际网卡、连接方式和无线设备。完成后在两端使用各自的实验 `TMUX_TMPDIR` 执行 `tmux kill-server`。

## OVS 手动演示与抓包

自动验收优先用 [README 的 verify 命令](../README.md#ovs-自动验收)。需要观察旧版 `br-int` 拓扑时，使用一台没有同名实验资源的专用 Ubuntu 虚拟机；本模式固定使用 `br-int`、`ovs1`～`ovs4`、`ns1`～`ns4`。

```bash
make
sudo apt-get install -y openvswitch-switch iproute2 tcpdump
sudo systemctl start openvswitch-switch
sudo bash scripts/ovs_setup.sh
sudo bash scripts/ovs_netns.sh setup
sudo bash scripts/ovs_netns.sh status
```

在第一个终端抓取节点 1 的跨节点 UDP：

```bash
mkdir -p logs
sudo ip netns exec ns1 tcpdump -i ovs1 -n -U -w logs/node1.pcap udp
```

第二个终端执行：

```bash
sudo bash scripts/ovs_netns.sh demo
echo $?
```

预期接收端包含 `ovs-netns-test`，退出码 0；失败退出码非零。脚本打印并保留临时日志目录。对比前后流表，同时核对 PCAP 中的业务载荷；仅有 NORMAL 总计数增长还可能包含 ARP，不能单独证明业务帧交付。

在抓包终端按 Ctrl+C，再回放与清理：

```bash
sudo tcpdump -nn -XX -r logs/node1.pcap
sudo bash scripts/ovs_netns.sh teardown
sudo bash scripts/ovs_teardown.sh
```

四个 IP 都留在同一个命名空间时，Linux 可能走本机回环，所以不能只执行 `ovs_setup.sh` 就宣称业务经过 OVS。提交实验结果时同时提供运行时间、命令、接口、接收日志和原始 PCAP；文本解码不是原始文件的替代品。

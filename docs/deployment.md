# 部署与抓包

以下命令在 Ubuntu Bash、仓库根目录执行。先完成 [README](../README.md) 的单机演示。本页以 a/b 模式为主，生成目录 `config-hier-*` 已加入忽略规则，不会提交本机地址。转换器要求输出目录为空，以免覆盖已有部署修改。

## 双机 LAN

A 机运行节点 1/2、上行和下行 3 信道及其终端；B 机运行节点 3/4、下行 5 信道及其终端。两个接入节点仍同时连到同一信道进程，第二条连接可以跨机器。

两端编译相同版本并确认 IPv4 可达。先从已核对的模板生成 a/b 配置，再替换真实地址；**节点文件和 channels.json 都要替换**：

```bash
make
python3 scripts/hier_config.py config-lan --output config-hier-lan
A_IP=192.168.10.10
B_IP=192.168.10.11
sed -i -e "s/192\.168\.222\.128/$A_IP/g" \
       -e "s/192\.168\.222\.130/$B_IP/g" \
       config-hier-lan/node*.conf config-hier-lan/channels.json
```

两端使用相同的真实 IP。A 运行 `bash scripts/run_lan.sh A config-hier-lan`，B 运行 `bash scripts/run_lan.sh B config-hier-lan`。所有会话位于各自的 `tmux -L cnet` 服务器。

A 发送、B 观察：

```bash
# A
tmux -L cnet send-keys -t hA1 'send 2001:db8:500::1 lan-forward' Enter
tmux -L cnet send-keys -t n1 'send 2001:db8:ff00::4 node-forward' Enter
# B
tmux -L cnet capture-pane -p -t hC1 -S -30
tmux -L cnet capture-pane -p -t n4 -S -30
tmux -L cnet send-keys -t hC1 'send 2001:db9::1 lan-reverse' Enter
# A
tmux -L cnet capture-pane -p -t hA1 -S -30
```

## 有线与 Wi-Fi 双接口

两条链路使用不同 IPv4 子网。角色分工与 LAN 模式不同：A 运行节点 1/2、上行和下行 5；B 运行节点 3/4、下行 3。这样两条下行外部信道本身也跨机器，便于验证不同介质。

| 链路 | 模板承载 |
|---|---|
| 1p2 ↔ 4p3 | Wi-Fi 接口 |
| 2p3 ↔ 3p1、2p2 ↔ 4p2 | 有线接口 |
| 上行 → 3p2、下行 5 → 3p3/4p5 | 有线接口 |
| 下行 3 → 2p1 | Wi-Fi 接口 |
| 同机节点及下行 3 → 4p1 | 本机地址之间通信 |

在每台机器上分别用**本机网卡名**生成配置。四个 IP 的顺序为 A 有线、A Wi-Fi、B 有线、B Wi-Fi：

```bash
ip -4 -br addr
ETH_DEV=ens160 WIFI_DEV=wlan0 bash scripts/nic.sh gen \
  192.168.10.10 192.168.20.10 192.168.10.11 192.168.20.11
python3 scripts/hier_config.py config-nic --output config-hier-nic
```

生成器会同时替换 `bind` 地址、peer 和 `dev` 网卡名。两端设备名不同时分别生成，不要把 A 的配置当成 B 的本机设备配置。检查 `config-hier-nic/node*.conf` 与 `config-nic/ips.env`，允许实验对端访问所需 UDP 端口。

```bash
# A：检查 B 的实际地址，然后启动
bash scripts/nic.sh check 192.168.10.11 192.168.20.11
sudo bash scripts/run_nic.sh A config-hier-nic
# B：反向检查后启动
bash scripts/nic.sh check 192.168.10.10 192.168.20.10
sudo bash scripts/run_nic.sh B config-hier-nic
```

上述双接口命令以 root 启动整组实验，交换节点用 `SO_BINDTODEVICE` 绑定实际接口；a/b 模式绑定失败会退出。此时观察、发命令和停止也要用 `sudo tmux -L cnet ...`、`sudo bash scripts/stop_all.sh`。具有免密 sudo 或共享 sudo 时间戳的测试账户可省略启动命令外层 sudo，保持普通用户的 tmux 服务器；启动器会验证每个程序就绪，权限不足则回滚。

真实介质验收至少记录：三个外部网络六个方向、每条信道两个接入点、两端地址/网卡、接收日志及接口抓包。用 `via` 分别选择两个入域节点；出域选择若需固定，可在四节点临时停用另一 EPT 出口，测试后恢复。断链实验选择不承载 SSH/默认路由的实验接口，并在本地终端操作。

`tests/test_nic_regressions.py --hierarchical` 在隔离 veth 中验证同一绑定配置和断链恢复；不操作物理网卡。旧 `demo_nic.sh/link_test.sh` 仅用于 16 位兼容演示，不能直接操作 a/b 会话。

## OVS

推荐先使用独立自动验收：

```bash
sudo python3 scripts/ovs_acceptance.py --hierarchical --output logs/ovs-run1
```

需要手工观察时，在专用实验 VM 上准备 `br-int` 和七个命名空间：

```bash
python3 scripts/hier_config.py config-ovs --output config-hier-ovs
sudo bash scripts/ovs_setup.sh
sudo bash scripts/ovs_netns.sh setup
bash scripts/run_tmux.sh config-hier-ovs
sudo ovs-ofctl dump-flows br-int
```

`ns1～4` 各运行一个交换节点，`ns11～13` 各运行一个共享外部信道及其终端，跨域内链路和外部链路的业务均可经过 OVS。结束时按顺序停止程序、还原命名空间、删除实验桥：

```bash
bash scripts/stop_all.sh
sudo bash scripts/ovs_netns.sh teardown
sudo bash scripts/ovs_teardown.sh
```

## 抓包与结果判断

双接口部署可在两机执行 `sudo bash scripts/capture.sh 30`，按本机 `config-nic/ips.env` 同时记录有线、Wi-Fi 接口 30 秒。手动抓某条链路，例如节点 1—4：

```bash
sudo tcpdump -ni wlan0 'udp and (port 1102 or port 4003)' -w logs/node1-node4.pcap
```

结合 `[SELECT] Lport=... EPID=...`、`[FWD]` 和接收端 `[RECV]` 判断路径。仅有发送日志或 ping 成功不能证明业务交付；仅标注 `type wifi/bt` 也不能证明实际介质。蓝牙需两端适配器和 L2CAP 连接，见[蓝牙说明](bluetooth.md)。

没有接收时依次核对：程序是否就绪、三个信道进程是否运行、逻辑 IPv6 与 a/b 是否一致、peer/bind/dev 是否对应本机、对端防火墙、CRC/源地址/无路由丢弃日志。重复实验先在两机各执行 `bash scripts/stop_all.sh`；以 root 启动的实验加 sudo。

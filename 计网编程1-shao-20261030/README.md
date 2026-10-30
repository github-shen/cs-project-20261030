# 计算机网络体系结构实验 1.1 —— 交换网络交换功能实现

4 个逻辑节点组成交换网络，实现外部端口表 + 外部转发、内部转发表 + 内部转发，
支持负载均衡、源地址校验、CRC-16 数据包校验。链路用 UDP 模拟，单机回环即可跑通。

## 目录结构

```
计网编程1/
├── Makefile            # make 编译, 生成 bin/switch 和 bin/host
├── README.md           # 本文件
├── start_nodes.sh      # 一键启动4个节点(图形终端)
├── config/
│   ├── node1.conf      # 节点1: 端口/链路/转发表 (带上行外部端口 0x0A)
│   ├── node2.conf      # 节点2: (带下行外部端口 0x0B)
│   ├── node3.conf      # 节点3: (带下行外部端口 0x0C)
│   └── node4.conf      # 节点4: 纯内部转发节点
└── src/
    ├── frame.h / frame.c   # 帧格式定义 + CRC-16-CCITT 校验
    ├── switch.c            # 交换节点程序(核心)
    └── host.c              # 外部主机程序
```

## 编译

```bash
make            # 生成 bin/switch 和 bin/host
```

## 单机回环运行（调试/演示）

开 7 个终端（或用 start_nodes.sh 起前 4 个）：

```bash
./bin/switch config/node1.conf   # 终端1
./bin/switch config/node2.conf   # 终端2
./bin/switch config/node3.conf   # 终端3
./bin/switch config/node4.conf   # 终端4
./bin/host 0x0A01 9001 127.0.0.1 1103   # 终端5: 上行信道主机
./bin/host 0x0B01 9002 127.0.0.1 3104   # 终端6: 下行信道3主机
./bin/host 0x0C01 9003 127.0.0.1 2104   # 终端7: 下行信道5主机
```

测试（在主机终端输入）：

```
send 0x0B01 hello-from-up        # 上行->下行信道3, 跨域转发
send 0x0C01 test-lb              # 测试负载均衡(多次发送看节点1首跳端口)
send 0x0A02 reply                # 下行->上行
sendbad 0x0B01 corrupted         # 发送CRC损坏帧, 节点会丢弃并打印
```

在节点终端可用命令：`table`（看端口表/转发表）、`stats`（看收发/丢包计数）。

## 实验室多机部署（有线/WiFi 任选两种）

1. 准备 4 台 Linux 机器（或虚拟机），保证网络互通（同一局域网，关闭防火墙或放行 UDP）。
2. 把工程拷贝到每台机器：`scp -r 计网编程1 用户名@机器IP:~/`
3. 每台机器 `make` 编译。
4. 按分工编辑 `config/nodeX.conf`：把 `127.0.0.1` 换成**对端机器的真实 IP**，
   UDP 端口号保持不变即可。例如节点1配置里"连节点2"一行，
   `对端IP` 填节点2那台机器的 IP，`对端UDP` 填节点2该端口绑定的 3001。
5. 每台机器启动对应节点：`./bin/switch config/nodeX.conf`
6. 在任意一台机器启动 host 充当外部主机，host 的"节点IP/节点UDP"指向所连节点的外部端口。

- 有线连接：机器插同一交换机/网线直连，用有线网卡的 IP。
- WiFi 连接：机器连同一 WiFi（或一台开热点），用无线网卡的 IP。
- 蓝牙：可用 pand/蓝牙 PAN 组网后同样分配 IP，再按上述方式填配置。

## 配置文件格式

```
id <节点号>
port <端口号> <int|ext> [外部网络号hex] <本地UDP> <对端IP> <对端UDP>
ift <目的键hex> <端口1[,端口2]>
```

- 目的键 = 完整地址（0x0102 表示域内节点2）或 网络号<<8（0x0C00 表示下行信道5）。
- `ift ... 1,2` 两个端口为等价路径，按 (源^目的)%2 分流，实现负载均衡。

## 关键机制对应实验要求

| 要求 | 实现 |
|---|---|
| 外部端口表+外部转发 | `switch.c` 中 `ept_lookup()`，目的网络直连外部端口则送出 |
| 内部转发表+内部转发 | `ift_lookup()` + `route_frame()`，逐跳转发 |
| 负载均衡 | 转发表项含多条等价端口，按流哈希 `(src^dst)%n` 选择 |
| 源地址校验 | 外部端口收帧时检查 `src>>8 == 端口所连网络号` |
| 数据包校验 | 帧尾 CRC-16-CCITT，损坏帧丢弃计数 |

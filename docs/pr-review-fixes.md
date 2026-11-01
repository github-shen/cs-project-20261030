# PR反馈修复与回归测试

2026年11月1日在本机两台Ubuntu 24.04.5虚拟机中修复并复测PR #1的三项反馈。修复前代码为 `493c573`，本次证据位于 [results/review-fixes](../results/review-fixes/)。三项反馈均已解决；虚拟接口实验和虚拟蓝牙实验不作为物理介质验收结果。

## 修复内容

### 双接口演示的源地址与路径

`config-nic-template/node1.conf` 的 `ift 0C00 1,2` 按FNV-1a选择首选端口，索引从0开始：

| 源 → 目的 | 哈希值 | `% 2` | 实际首选 |
|---|---:|---:|---|
| 0x0A02 → 0x0C01 | 570430422 | 0 | 端口1，经节点2，再沿有线配置路径到节点3 |
| 0x0A01 → 0x0C01 | 3346244523 | 1 | 端口2，沿无线配置路径直达节点3 |

`scripts/demo_nic.sh` 已交换前两幕的源地址，并同步统计示例。`scripts/link_test.sh` 现在使用四条与README一致的流：两条有线、无线主用且有线备用的一条、单路径无线的一条。三个阶段分别携带 `baseline/down/restored` 及本次运行标识，防止把历史收包误当作断链期间送达。

`forward_equiv()` 只有在备用端口的 `sendto()` 成功返回后才打印 `[REROUTE]` 并增加重路由计数。该日志表示本地已提交备用发送，是否送达仍检查目的终端。远端断电、静默丢弃或蓝牙适配器内部断线，不一定让本地UDP发送报错，不能据此声称支持所有远端故障。

断链脚本拒绝关闭承载默认路由或当前SSH连接的接口，也拒绝操作原本已关闭的接口；只恢复本次确实尝试关闭的接口。

### 节点侧坏帧注入与本地校验

原节点CLI先翻转CRC，再调用 `route_frame()`。下一跳发送中的 `frame_seal()` 会重新计算CRC，坏帧因此变成合法帧；发给本节点时则绕过了校验。

现在CLI通过 `corrupt_crc` 参数将故障注入意图传给路由和发送函数。`port_send()` 按顺序执行TTL减一、重封CRC、故意翻转一位、实际发送。备用路径重试先恢复原帧，再执行同样流程，避免TTL重复递减或坏帧被重新修好。正常收包转发始终传0。

本地交付分支也先检查长度与CRC，校验失败增加错误计数并丢弃。主机侧 `sendbad` 的原有语义保持不变。

### 学习表并发保护

每个 `Port` 增加一把 `pthread_mutex_t learn_lock`，在配置解析完成后、接收线程启动前初始化。

- `learn_add()` 在锁内修改地址、UDP端点、表项数和轮转位置。
- `learn_find()` 在锁内复制命中端点到调用者栈上，不返回共享表项指针。
- `learn_snapshot()` 在锁内复制数量与表项；`print_tables()` 打印副本。
- `reset_stats()` 改用原子写，避免与收包线程的原子增减混用普通写。

发送系统调用和表格打印均不占用学习表锁。快照按端口生成，统计计数也分别清零，不承诺所有端口或所有计数在同一瞬间形成全局快照。

## 环境与实际结果

VM A复用现有Ubuntu，Linux为6.8.0-147；VM B使用已有专用测试VM，Linux为6.8.0-142。两端Python为3.12.3、BlueZ为5.72；C回归使用GCC 13.3.0。程序部署在各VM独立的 `network-labs/cs-project-20261030-bluetooth` 子目录，未迁移管理网卡、主机IP或默认路由。

| 检查 | 实际结果 | 状态 |
|---|---|---|
| C编译 | switch/host以 `-Wall -Wextra -Werror -O2 -pthread` 编译通过 | PASS |
| 修复前复现 | 节点1的坏CRC帧在节点2出现 `[RECV]`，预期拒绝测试失败 | 已复现 |
| 节点CLI四种目的 | 远端节点、跨域终端、直连终端、本节点：正常4/4送达，坏帧4/4拒绝 | PASS |
| 真实UDP学习回程 | 8个终端同时刷新学习，期间查询/清零统计，64条回程正确，错投0、CRC错误0，TTL均为6 | PASS |
| 学习表ThreadSanitizer | 写入20000次，2个读取线程合计40000轮查询/快照，CLI快照100轮；退出码0，无数据竞争报告 | PASS |
| 实际双接口演示脚本 | 四条演示流均收到；节点1前两条出端口依次1、2，统计阶段同样1、2 | PASS |
| 接口中断与恢复 | 四流收到数依次4/4、3/4、4/4；唯一中断的是单路径无线流 | PASS |
| 管理接口保护 | 在测试Namespace中模拟默认路由接口，脚本拒绝关闭且接口仍UP | PASS |
| 单VM蓝牙回归 | 12/12方向，64/64地址流，端口1/2各32帧；节点侧及主机侧坏CRC均拒绝 | PASS |
| 双VM蓝牙回归 | 两个Linux内核、两条L2CAP会话；12/12方向，64/64地址流、32/32分布；节点侧跨VM坏CRC拒绝 | PASS |
| 蓝牙故障恢复 | 离线帧丢弃，重连后双向成功，无离线旧帧重放 | PASS |
| 清理 | 测试进程/HCI/Namespace/private tmux清理；原路由、OVS和Bluetooth服务状态保持或恢复 | PASS |
| 真实Wi-Fi、有线和蓝牙射频 | 当前没有对应硬件与实际无线对端 | NOT VERIFIED |

ThreadSanitizer测试使用真实 `switch.c` 的学习、查找和打印实现，不是替代模型；32个逻辑地址超过16项容量，也覆盖轮转替换。用 `setarch x86_64 -R` 只为该测试进程设置地址布局，未修改系统ASLR配置。该工具结果覆盖本次执行，不能证明所有可能调度均不存在缺陷。

双接口实验在两个独立Network Namespace内建立两组veth，地址采用文档保留网段192.0.2.0/30和198.51.100.0/30，运行四个真实交换进程、终端和原演示脚本。`eth/wifi` 是本测试的路径标签，实际介质均为veth。实验使用独有 `TMUX_TMPDIR`，不运行会清空默认tmux服务器的 `run_nic.sh`。

| 流 | baseline | down | restored |
|---|---|---|---|
| 0x0A02 → 0x0C01，有线 | 收到 | 收到 | 收到 |
| 0x0A01 → 0x0C01，无线主用 | 收到 | 收到，节点1端口2→端口1重路由 | 收到 |
| 0x0A01 → 0x0B01，单路径无线 | 收到 | 未收到，节点2端口4发送失败 | 收到 |
| 0x0A01 → 0x0D01，有线 | 收到 | 收到 | 收到 |

## 可复核证据

- [修复前结果](../results/review-fixes/core-before.json)及[节点2错误交付日志](../results/review-fixes/core-before-node2.log)：`review_bad_remote_node` 被交付；这是修复前失败证据，不计为通过。
- [修复后核心结果](../results/review-fixes/core-after.json)、[节点1](../results/review-fixes/core-node1.log)、[直连终端](../results/review-fixes/core-upper.log)；[ThreadSanitizer完整输出](../results/review-fixes/learning-tsan.log)末尾含通过记录，没有sanitizer告警。
- [双接口结构化结果](../results/review-fixes/nic.json)、[演示脚本实际输出](../results/review-fixes/nic-demo_nic.sh.log)、[断链脚本实际输出](../results/review-fixes/nic-link_test.sh.log)、[节点1重路由](../results/review-fixes/nic-n1.log)、[节点2单路径拒绝](../results/review-fixes/nic-n2.log)、[0x0C01接收](../results/review-fixes/nic-h0x0C01.log)、[0x0B01接收](../results/review-fixes/nic-h0x0B01.log)、[0x0D01接收](../results/review-fixes/nic-h0x0D01.log)。
- [单VM结果](../results/review-fixes/single-vm.json)、[单VM接收端拒绝](../results/review-fixes/single-upper.log)、[HCI证据](../results/review-fixes/single-hci.log)。
- [双VM结果](../results/review-fixes/two-vm.json)、[双VM接收端拒绝](../results/review-fixes/two-upper.log)、[A端HCI](../results/review-fixes/two-hci-a.log)、[B端HCI](../results/review-fixes/two-hci-b.log)。

所有计数来自对应运行的日志或结构化结果。虚拟控制器地址是实验生成值；VM实际IP和部署路径已替换为占位符。未提交凭据、完整配对日志或抓包二进制。

## 复现命令

在Ubuntu项目根目录执行；`core/nic/single` 结果目录必须尚不存在，以免混合旧日志。依赖沿用项目的gcc、Python、iproute2、tmux、BlueZ及开发库。

```bash
make CFLAGS='-Wall -Wextra -Werror -O2 -pthread'
make bluetooth
python3 tests/test_switch_regressions.py --output logs/review-core
mkdir -p logs/review-learning
gcc -Wall -Wextra -Werror -O1 -g -fsanitize=thread \
  -fno-omit-frame-pointer -fno-pie -no-pie -pthread \
  tests/test_learning.c src/frame.c -o logs/review-learning/learning-tsan
setarch x86_64 -R logs/review-learning/learning-tsan > logs/review-learning/tsan.log 2>&1
sudo python3 tests/test_nic_regressions.py --output logs/review-nic
sudo python3 tests/test_bluetooth.py --output logs/review-single
```

双VM部署和严格SSH主机密钥配置见 [蓝牙部署说明](bluetooth.md)。两端编译相同修订后，在协调机执行：

```bash
python3 tests/test_two_vm.py /path/to/two-vm.local.json \
  --output logs/review-two-vm --run-name review-two-vm
```

## 本轮发现的测试环境问题

首次直接复制Windows工作树时，Shell文件的CRLF导致bash报错，未进入业务测试。新增 `.gitattributes` 为Shell脚本固定LF，重新传输后通过。

首次双VM回归中，外部监听器报 `Address already in use`，而本机客户端已开始连接。测试worker此前并行启动监听器与客户端，可能使客户端自动分配的本地PSM先占用监听PSM。现已等待本机监听器 `[READY]` 再启动客户端；修改后完整双VM回归通过。保留 [首次失败和清理结果](../results/review-fixes/two-vm-startup-before.json)。这次修改属于测试启动顺序，未改变L2CAP适配器的传输协议。

本次没有新增OVS数据平面验收，也没有将逻辑地址对分流表述成五元组分流。物理无线设备、安全配对和教师原始a/b定义仍保持原报告的验证边界。

## 蓝牙连接边界的离线帧重放

对 `8c342fe` 继续检查时发现一项P2问题：`bt-channel` 先处理连接完成，再读取本地UDP。当连接事件和UDP可读事件同时发生，内核队列中离线期间积压的帧会使用新连接发出。原重连测试先等到离线帧被丢弃，再发起重连，没有覆盖这个时序。

在Linux 6.8、BlueZ虚拟HCI上暂停测试适配器，排入4条业务帧，并在恢复进程前完成L2CAP握手；服务端与客户端两个场景均复现4条离线帧重放。客户端用 `BT_DEFER_SETUP` 控制握手，测试没有替换传输实现。

修复将清理离线UDP队列放在统一的 `ready()` 边界，覆盖 `accept`、非阻塞连接完成和立即连接成功三条路径。清理过程复用正常接收的来源与长度校验；只有合法离线帧计入 `offline`。新连接启用后的帧仍保持原始CRC、TTL和内容。

2026年11月1日复测结果：

| 检查 | 结果 |
|---|---|
| 修复前服务端／客户端离线队列 | 各4条，均被错误重放 |
| 修复后服务端／客户端离线队列 | 各丢弃4条，重放0条，新帧双向正常 |
| 队列中的异常长度／非法来源 | 分别计入 `invalid=1`、`denied=1` |
| 新增时序场景重复验证 | 每端连续3轮通过 |
| 单VM蓝牙完整回归 | 11项全部通过；12个通信方向、64条地址流全部收到，路径分布32/32 |
| 配置拒绝测试 | 3项全部通过 |
| 严格编译 | switch、host、bt-channel通过 `-Wall -Wextra -Werror -O2` |
| 测试清理 | 子进程、虚拟HCI、网络与Bluetooth服务状态检查全部通过 |

新增回归已并入 `tests/test_bluetooth.py`，运行现有单VM命令即可复现。此次修复复测没有重跑双VM实验，也没有新增真实蓝牙射频或配对验证。

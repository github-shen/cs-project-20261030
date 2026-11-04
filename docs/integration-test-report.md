# 2026-11-04 集成验证记录

main（`9a0d6b6`）与 PR #1～#4 的合并版本已通过本页列出的 Ubuntu 测试。测试中发现并修复了双接口回归在深目录下无法启动 tmux 的问题。README 的单机命令也已按文档顺序实际运行。

## 源码与修复

| PR | 集成的提交 | 处理结果 |
|---|---|---|
| [#1 蓝牙](https://github.com/github-shen/cs-project-20261030/pull/1) | `9b05b95` | 保留蓝牙离线队列修复；将 NIC 回归的 tmux 套接字移到独立的短临时目录，避免 Unix 路径长度限制 |
| [#2 OVS 验收](https://github.com/github-shen/cs-project-20261030/pull/2) | `cd3a5fe` | CI 只发现 `test_ovs_acceptance.py`，避免导入需要可选 Paramiko 的双 VM 协调器 |
| [#3 OVS 证据](https://github.com/github-shen/cs-project-20261030/pull/3) | `75c0273` | 更正原始 PCAP 的提交状态，并[请作者提供历史原件](https://github.com/github-shen/cs-project-20261030/pull/3#issuecomment-6631364947) |
| [#4 UDP 长度](https://github.com/github-shen/cs-project-20261030/pull/4) | `a50f65c` | 严格长度检查与现有蓝牙收发兼容，两项专项回归通过，未作多余修改 |

功能源码的集成提交为 `b7a974380bdd19a5fe214e89a73ea10c20367cee`。后续改动是 README、部署说明、结果文件及核心 CI 配置。合并冲突的处理保留了 shell / conf 的 LF 换行，以及 OVS demo 的“只结束自身 PID、wait 回收、保留日志、失败非零退出”行为。

本次新增的核心 CI 使用现有用例检查严格编译、蓝牙配置、UDP 长度、节点转发和学习表并发；OVS CI 继续执行 32 项内核数据面验收。GitHub Actions 的最终运行状态应查看对应 PR 检查页，本页记录的是实际执行的本地 Linux 验证。

## 环境

- Ubuntu 24.04，x86_64，Linux `6.8.0-142-generic`，专用 VMware 测试虚拟机。
- GCC 13.3.0、Python 3.12.3、Open vSwitch 3.3.9、BlueZ 5.72。
- OVS 使用内核数据面；蓝牙使用 BlueZ 虚拟 HCI 和真正的 L2CAP socket；双接口使用隔离命名空间中的两组 veth。
- 所有测试使用同一份合并源码。网络实验串行运行，并检查各自的清理结果。

## 执行命令与结果

以下命令在 Linux 仓库根目录执行。输出目录是本次使用的名称，复现时应改为新目录。

```bash
make bluetooth CFLAGS='-Wall -Wextra -Werror -O2'
python3 -m unittest discover -s tests -p 'test_ovs_acceptance.py' -v
python3 -m unittest discover -s tests -p 'test_bluetooth_config.py' -v
python3 tests/udp_regression.py -v
python3 tests/test_switch_regressions.py --output logs/core
sudo python3 scripts/ovs_acceptance.py --output logs/ovs
sudo python3 tests/test_bluetooth.py --output logs/bluetooth
sudo python3 tests/test_nic_regressions.py --output logs/nic-fixed
```

| 检查 | 实际结果 | 主要证据 |
|---|---|---|
| 严格编译 | switch、host、bt-channel 全部通过 | 编译退出码 0，无警告升级为错误 |
| OVS 单元测试 | 14 项 OK | 覆盖正常、注入故障和资源清理逻辑 |
| 蓝牙配置 | 3 项 OK | 配置校验与不安全级别的显式选择 |
| UDP 长度 | 2 项 OK | switch / host 拒绝尾随和超长数据报 |
| 节点核心 | 9 项 PASS | [core-summary.json](../results/integration-20261104/core-summary.json) |
| OVS | 32 项 PASS，清理无错误 | [ovs-result.json](../results/integration-20261104/ovs-result.json) |
| 蓝牙 | 11 项 PASS | [bluetooth-summary.json](../results/integration-20261104/bluetooth-summary.json) |
| 双接口 | 3 项 PASS | [nic-summary.json](../results/integration-20261104/nic-summary.json) |
| README 操作 | 8 个会话正常启动，主机 / 节点通信、CRC 拒绝、table / stats 通过 | [readme-summary.json](../results/integration-20261104/readme-summary.json) |

蓝牙结果包括 12 个外部通信方向、64 条地址流、正常与坏 CRC、未授权对端、断连重连，以及 listen / connect 两侧离线积压不重放。双接口结果中，四条业务在基线时全部收到，第二接口断开时收到 3 条，恢复后重新收到 4 条；管理接口保护也通过。

首次双接口运行因 tmux 套接字路径过长失败，未把失败当成通过。修复后在同一个深目录中重跑该组，得到 `nic-fixed` 的 PASS；此前已通过的 OVS 和蓝牙代码没有变化。

### 学习表并发

```bash
gcc -Wall -Wextra -Werror -O1 -g -pthread -fsanitize=thread \
  -fno-omit-frame-pointer -fno-pie -no-pie \
  tests/test_learning.c src/frame.c -o bin/learning-tsan
setarch x86_64 -R bin/learning-tsan
```

退出码为 0，ThreadSanitizer 未报告数据竞争。最终输出：

```text
PASS learning concurrency: writes=20000 readers=2 lookups_and_snapshots=40000 cli_snapshots=100
```

同时执行了核心 CI 中不带 sanitizer 的 `-O2 -pthread` 构建与运行，结果一致。`setarch -R` 仅用于此次 sanitizer 运行的地址布局兼容，不是普通运行前提。

### 旧 OVS demo 与原始抓包

先检查实验机没有同名网桥、接口和命名空间，再依次执行 `ovs_setup.sh`、`ovs_netns.sh setup`、`demo`。抓包命令为：

```bash
sudo ip netns exec ns1 tcpdump -Z root -i ovs1 -n -U -w node1.pcap udp
```

本次抓包始于 2026-11-04 13:20 UTC，接口为 `ns1/ovs1`，原始文件为 [node1.pcap](../results/integration-20261104/node1.pcap)。其中一条 UDP 从 `10.1.0.1:1102` 到 `10.1.0.3:2002`，载荷中包含 `ovs-netns-test`；[文本解码](../results/integration-20261104/node1-decoded.txt)可与原件对应。

正常运行时目的主机收到消息，demo 返回 0。随后在本次专用 `br-int` 添加 `priority=100,udp,actions=drop`，demo 明确返回 1，输出“目的主机未收到测试消息”。两次均保留日志并结束自身进程。最后执行命名空间和网桥 teardown，环境恢复。

证据：[正常运行](../results/integration-20261104/legacy-success.txt)、[强制丢包](../results/integration-20261104/legacy-forced-drop.txt)、[汇总](../results/integration-20261104/legacy-ovs-summary.json)。本文件是新的集成实验原件；PR #3 历史实验的原始 `node1.pcap` 仍待作者补充。

## 清理与未验证范围

测试结束后，本次 switch / host / bt-channel / btvirt 进程均已停止，临时 OVS 网桥和命名空间已删除，默认路由保持不变，虚拟 HCI 已释放，Bluetooth 服务恢复到测试前状态。README 专用 tmux 服务器也已关闭。

本轮未重新运行双 VM 协调测试，未验证真实蓝牙配对、射频范围或物理 Wi-Fi。单 VM 的 L2CAP、OVS 和双 veth 测试不能替代这些硬件验证；本次没有发现尚未修复的已复现功能回归。

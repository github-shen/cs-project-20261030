# 拓扑与 a/b 验收记录

2026-11-05 在本机已有 Ubuntu 虚拟机完成验收。课程模式、旧帧兼容模式、OVS、虚拟蓝牙、双接口路径及 README 启动流程均通过。没有验证真实有线、Wi-Fi、蓝牙射频，也没有把历史双 VM 结果当作本次 a/b 证据。

## 修正和实现

对照《第3-4次-层次网络》第 26～36 页，将错误的 1—3 内部连线修正为 1p2—4p3；外部改为上行、下行 3、下行 5 三个共享网络，每个网络接入两个节点。各部署模板使用题图一致的逻辑端口。

新增 128 位 IPv6 a/b 寻址（最高位为 bit 0）、全域 EPT、按 SID 的 IFT、入域 EPID 封装/出域解封装、带宽权重分流及状态过滤。默认使用 `32/8`，另测跨字节 `29/11`。保留旧 16 位模式，并修复蓝牙适配器对以 `HN` 开头的旧地址误判。

## 环境与结果

环境为 Ubuntu 24.04.5 LTS / x86_64、Linux 6.8.0-142、GCC 13.3、Python 3.12.3、OVS 3.3.9、BlueZ 5.72。原始版本输出见 [environment.txt](../results/topology-20261105/environment.txt)。严格 C 编译使用 `-Wall -Wextra -Werror -O2`。

| 验收 | 结果 | 证据 |
|---|---|---|
| 旧核心收发、CRC、多终端回程 | 9 组通过 | [core](../results/topology-20261105/core/summary.json) |
| 修正后旧格式拓扑 | 5 组通过 | [legacy-topology](../results/topology-20261105/legacy-topology/summary.json) |
| a/b 完整交换 | 8 组通过 | [hierarchical](../results/topology-20261105/hierarchical/summary.json) |
| OVS 旧帧 / a/b | 各 32 项通过 | [旧帧](../results/topology-20261105/ovs-legacy/result.json)、[a/b](../results/topology-20261105/ovs-hier/result.json) |
| 真实 L2CAP，虚拟 HCI | 旧帧 12 组、a/b 9 组通过 | [旧帧](../results/topology-20261105/bluetooth-legacy/summary.json)、[a/b](../results/topology-20261105/bluetooth-hier/summary.json) |
| 两条 veth 接口路径 | 两模式各 3 组通过 | [旧帧](../results/topology-20261105/nic-legacy/summary.json)、[a/b](../results/topology-20261105/nic-hier/summary.json) |
| 默认启动器和 README 收发 | 3 组通过 | [launcher](../results/topology-20261105/launcher/summary.json) |

另通过旧配置 6 项、蓝牙参数 3 项、拓扑 2 项、共享信道 4 项、a/b 配置 2 项（含 16 类非法输入）、UDP 长度 2 项、OVS 验收器 14 项单元检查。C 位向量覆盖 `32/8`、`36/12`、`28/16`、`120/8`、`127/1`，以及 IPv6 Traffic Class / Flow Label 保留、EPID、最大长度和 CRC。蓝牙帧门控回归覆盖旧地址 `HN` 前缀、249 字节帧、非法长度及坏 CRC 原样通过。

旧学习表并发测试完成 20,000 次写入、40,000 次查询/快照及 100 次 CLI 快照；本次未重跑 ThreadSanitizer。所有 Shell 脚本通过 `bash -n`。

## 关键观察

- 四节点内部 12 个有向组合可达。六个外部接入端口均验证双向收发，八种入域节点组合完成 48 次跨信道交付，无重复交付。
- 两个动态上行站点由节点 1 入域，再由节点 3 回送；外部收到的帧无 EPID，TTL 和 CRC 正确。
- 64 条 IPv6 地址流各发送两次，均保持出口稳定。下行 5 的两个出口分别承担 18、46 条流；表中权重为 45、100。该有限样本验证两个出口被使用，不代表精确吞吐比例。
- 停用出口后不再选择该出口；内部链路停用时保留目标 EPID `4/5`，节点 1 从端口 2 改走端口 1。
- OVS 的独立业务 UDP 规则计数由 0 增至 2，同时目的节点确认交付，见 [before](../results/topology-20261105/ovs-hier/flows-before.txt) / [after](../results/topology-20261105/ovs-hier/flows-after.txt)。
- L2CAP 双向传输 245 字节外部帧和 249 字节内部帧；内部 1—4 两端 `bt_tx/bt_rx` 均大于 0。断线丢弃并恢复双向传输，无离线帧重放。保存了[虚拟 HCI 原始抓包](../results/topology-20261105/bluetooth-hier/transport.btsnoop)。
- 双接口三条业务在正常、Wi-Fi 标签接口停用、恢复阶段分别交付 3、2、3 条。内部流通过有线路径改道，指定外部单出口流在该接口停用时丢弃，恢复后可达。EPID 保持不变。
- README 默认命令启动 11 个就绪会话，实际消息交付成功；重复启动保留已有会话，第三个节点配置错误时回滚本次部分启动。

测试结束已核对命名空间/OVS 桥、私有 tmux、HCI 和 Bluetooth 服务恢复，相关 JSON 的 cleanup 字段均通过。完整运行日志保留在虚拟机本次隔离工作目录，仓库保存上述报告和关键日志；历史报告保持原内容。

## 后续硬件验收

按 [部署说明](deployment.md) 和 [蓝牙说明](bluetooth.md)，在真实有线/Wi-Fi/BR/EDR 设备上重复三个网络的双向收发、六个接入点、接口抓包与断链恢复。记录设备与实际承载，不以 `type` 标签代替介质证据。

静态 EPT/IFT 不检测远端静默故障；蓝牙断线没有自动传播到交换表。状态更新目前需要在各节点同步操作。业务没有 ACK/重传，本次不宣称无线吞吐、距离或任意多故障收敛能力。

# OVS 实验自动验收方案

本次扩展把原先需要人工阅读日志的 OVS 演示补充为可判定成功或失败的验收。保留原有 C 转发程序、帧格式、静态转发表、配置文件及 setup/status/demo/teardown 命令；新增 verify 入口。既有 demo 的清理仅改为等待自己启动的 PID，移除按名称批量杀进程的操作，并提前注册退出清理。

基线为 main 的 9a0d6b6。本扩展未合入蓝牙 PR，也不依赖该 PR。验收使用主机侧 sendbad，避免把主分支已知的节点侧 sendbad 缺陷当作本扩展造成的回归。

## 使用方法

Ubuntu 环境准备：

```bash
sudo apt-get update
sudo apt-get install -y build-essential python3 iproute2 openvswitch-switch
sudo systemctl start openvswitch-switch
make
python3 -m unittest discover -s tests -p 'test_ovs_acceptance.py' -v
bash -n scripts/ovs_netns.sh
sudo bash scripts/ovs_netns.sh verify
echo $?  # 必须在验收命令后立即查看
```

也可以直接使用 Python 入口，并选择一个不存在的结果目录：

```bash
sudo python3 scripts/ovs_acceptance.py --timeout 10 --output logs/ovs-review-01
```

Python 只依赖标准库，建议 Python 3.8 或以上。verify 不会自动安装软件、修改防火墙、重启 OVS 服务或重新编译二进制。应先 make，避免测试旧版本二进制。

退出码：0 表示全部检查及清理通过；1 表示验收失败、运行中断或清理失败；2 表示缺少 Linux/root/工具/可执行文件等前提条件或参数非法。输出目录已存在时拒绝覆盖。

## 拓扑和资源隔离

每次运行生成一个随机前缀 avxxxxxxxx，建立一个专属 OVS 网桥和四个命名空间。每个命名空间通过一对 veth 接到新网桥。节点程序的域内 UDP 包必须离开所在命名空间，经 OVS 再进入另一个命名空间。

```text
ns1 中 node1 / hostA ─ veth ┐
ns2 中 node2 / hostB ─ veth ├─ 本次专属 OVS bridge
ns3 中 node3 / hostC ─ veth ┤
ns4 中 node4 / hostD ─ veth ┘
```

以现有 config-ovs/node1.conf 至 node4.conf 为输入，在结果目录生成副本，仅将 10.1.0.1～4 替换为 10.203.0.1～4。逻辑地址、端口、路由和候选路径保持原样。原文件不写入。四个独立命名空间可使用相同 UDP 端口而不占用宿主现有实验端口。

不触碰 br-int、ovs1～4、ns1～4、宿主默认路由或物理网卡。随机资源名使并行运行互不占用同名资源；各命名空间中相同的私有地址也互不冲突。OVS 服务本身仍为系统共享服务，应在专用实验机或 CI 中运行。

## 通过判据

| 检查 | 通过条件 | 防止的误判 |
|---|---|---|
| 启动 | 四节点和四主机都完成 socket/接收线程初始化，进程持续存活 | 固定 sleep 后假设启动成功 |
| OVS 业务经过 | A 到 C 的唯一消息收到，专属 UDP 流计数相对基线增加 | ARP 或普通流量造成 NORMAL 总计数增长 |
| 外部转发 | 四个外部主机之间 12 个有向通信全部收到对应消息 | 只测 A 到 B 就宣称全互通 |
| 内部转发 | 四节点之间 12 个有向通信全部交付 | 外部转发正常掩盖节点交付问题 |
| CRC 拒绝 | host sendbad 触发入口 CRC 丢弃，观察窗口内无主机交付 | 单纯接收端没输出就判丢弃成功 |
| 非法源拒绝 | 0x0C05 接入 A 网络入口，出现源校验丢弃 | 只检查消息正文，没有检查真实源地址 |
| 无路由 | 发往 0x0E01 触发无路由丢弃 | 路由查找失败后误转发 |
| 分流及稳定性 | A01、A02 到 C01 各连续四包，全部到达；每个地址流首跳固定，两流覆盖两个候选端口 | 重复发送同流却期待逐包换路 |
| 原有 CLI | 四节点 table 和 stats 均输出 | 新验收只覆盖数据收发 |
| 清理 | 本次子进程结束，创建的网桥、veth、命名空间删除，无清理错误 | 测完遗留进程或误杀其他实验 |

总计 32 个检查记录，采用失败即停止的执行方式。遇到失败时，后续未执行的检查不能视为通过。

专属业务规则使用 cookie=0xaca1、priority=100，匹配源和目的均在测试网段的 UDP，动作仍为 NORMAL；默认 priority=0,NORMAL 保留，用于 ARP 等其他流量。这不改变 C 程序的逻辑转发。计数只证明业务 UDP 经过 OVS，逻辑帧内容由目的端唯一消息接收记录交叉验证。这里没有将自定义地址转发规则下沉到 OVS，也不宣称 OVS 理解自定义帧。

每个消息带运行随机标记及序号，接收检查同时匹配 RECV、源地址及完整消息。负向用例先完成正常通信，发送异常帧后等待入口明确 DROP，并观察 0.5 秒内没有交付，随后再用正常消息验证接收程序仍可用。该有限窗口不是对任意延迟交付的数学证明。

命令有超时，启动和接收采用轮询条件，不依赖单个固定启动延时。任何子进程提前退出、超时、命令失败、计数缺失或清理错误都会使本次验收失败。

## 输出证据

默认保存在 logs/ovs-acceptance-随机编号/：

- result.json：总状态、逐项结果、失败原因、清理错误；明确 physical_media_verified=false。
- commands.log：外部命令参数、输出及错误信息。
- n1.log～n4.log：节点转发、交付、拒绝、table/stats 输出。
- h1.log～h4.log、spoof.log、h1b.log：各主机接收与额外测试主机记录。
- node1.conf～node4.conf：此次实际使用的配置副本。
- flows-before.txt、flows-after.txt、flows-final.txt：业务测试前后的流表。

遇到提前失败，尚未生成的文件不会存在。日志保留供审查，资源清理不删除结果目录；logs/ 已在仓库忽略列表中。结果应选取必要内容作为 PR 附件，不应把每次运行的所有日志都提交。

## 精确清理

Python 入口使用 Popen 对象追踪本次启动的进程，先 terminate，超时再 kill，并 wait 回收。只删除本次成功创建并登记的网桥、veth 和命名空间。清理单个网络资源失败后继续清理其余资源，并使结果为 FAIL。SIGINT/SIGTERM 进入同一清理路径。

原 demo 去除 pkill -f，仅对自己的 PID 列表发信号并 wait。保留 demo 的手工观察行为，自动判定由 verify 提供。

SIGKILL、系统断电、外部同时修改测试资源、命令创建资源后发生通信超时等情况可能绕过正常清理，不能承诺绝不残留。可依据 commands.log 中的随机资源名前缀人工核对并清理，禁止按 bin/host 等程序名称批量结束无关进程。

## 回归和提交方案

1. 首先运行标准库测试和 bash 语法检查。
2. 对照基线确认 src/、Makefile、config*/ 以及其余启动脚本没有变化。
3. 在 Ubuntu make 后运行 verify，检查 result.json 为 PASS、进程及随机网络资源已清理。
4. 开着另一个原有 host 实验再运行 verify，确认原进程持续运行；用原实验命令再次收发。
5. 依次运行旧 setup/status/demo/teardown，确认原 CLI 行为；demo 结束时旁路实验 host 不受影响。
6. 对失败路径做 Linux 实测：停止本次接收进程应失败；使专属 UDP 规则改为 drop 应失败；Ctrl+C 后应保存 FAIL 并清理。本地标准库测试只验证这些路径中的控制逻辑，不能替代系统级注入。
7. 最后再开 PR，附代码、32 项验收结果、旧入口回归记录、环境版本和未验证范围。

新增 GitHub Actions 工作流在 Ubuntu 上构建原 C 程序、运行 OVS 标准库测试和真实 OVS 验收，并在失败时上传日志。单元测试按文件名限定发现范围，避免合入蓝牙扩展后意外导入需要 Paramiko 的双VM协调器。工作流需推送后才能执行；本地存在 YAML 文件不代表 CI 已通过。

## 本次验证范围

本次开发环境为 Windows，未发现可用 Linux/WSL 发行版，且用户选择先做本地检查。因此可执行 Python 标准库测试、真实进程归属清理测试、bash 语法检查和差异核查；不能在此环境证明 OVS 内核数据面、原 C 程序运行和旧 OVS 入口回归全部通过。应将当前代码作为待 Ubuntu 集成验证的 PR 候选，不应描述为已完成完整回归。

本扩展也不验证 PS（1）的真实有线/Wi-Fi/蓝牙介质，不引入 Dijkstra，不修复现有蓝牙 PR 已覆盖的 CRC/学习表问题，不改变 a/b 地址字段实现。

### 2026 年 11 月 2 日本地检查记录

- Windows / Python 3.8.3：`python -m unittest discover -s tests -v`，14 项全部通过。
- Git Bash：`bash -n scripts/ovs_netns.sh`，退出码 0。
- `python -m py_compile scripts/ovs_acceptance.py tests/test_ovs_acceptance.py`，退出码 0。
- Windows 直接运行验收入口时明确拒绝，退出码 2，没有建立网络资源。
- `git diff --check` 通过。
- 对 src、Makefile、config、config-ovs、config-lan、config-app、config-nic-template 与 HEAD 比较无差异。
- Ubuntu OVS 32 项集成检查、旧入口运行回归和 GitHub Actions：尚未执行。

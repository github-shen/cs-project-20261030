# OVS 自动验收

验收运行真实 C 交换程序与共享外部信道，要求接收日志和 OVS 业务 UDP 计数同时成立。默认检查旧帧兼容模式；`--hierarchical` 检查课件 a/b、全域 EPT 和 EPID 模式。

```bash
sudo apt-get install -y build-essential python3 iproute2 openvswitch-switch
sudo systemctl start openvswitch-switch
make
sudo python3 scripts/ovs_acceptance.py --hierarchical --output logs/ovs-run1
```

等价入口是 `sudo bash scripts/ovs_netns.sh verify --hierarchical --output logs/ovs-run1`。输出目录必须不存在；退出码 0 表示全部检查和清理通过，1 表示检查或清理失败，2 表示环境或参数不满足要求。

## 拓扑与计数

每次建立随机命名的独立 OVS 桥、七个网络命名空间和七对 veth。四个空间各放一个交换节点，另外三个空间各放一个共享外部信道及其终端。节点间以及节点与外部信道间的 UDP 必须经过 OVS，避免同一命名空间的本地路由绕过桥。

运行配置从 `config-ovs/` 复制；a/b 模式先通过 `hier_config.py` 转换，再将示例 IPv4 前缀换成测试专用的 `10.203.0.0/24`。不修改仓库配置。

OVS 安装一条带独立 cookie 的 UDP 匹配规则，排除 ARP 和默认 NORMAL 规则的计数。先读取基线，再发送有唯一标记的内部消息，确认目的节点收到且该规则计数增长。

## 32 项检查

| 检查 | 项数 |
|---|---:|
| 环境与程序就绪 | 1 |
| 业务交付、OVS 专属计数增长 | 2 |
| 三外部网络六方向 | 6 |
| 四节点内部十二方向 | 12 |
| 六个外部接入点 | 6 |
| CRC、源字段、无路由拒绝 | 3 |
| 地址流稳定性与两出口使用 | 1 |
| 四节点表项与统计 | 1 |

a/b 模式使用 16 对源/目的地址，每条发送两次，要求相同地址流保持同一 EPID，并同时使用下行 5 的两个出口；完整 64 流、带宽权重及出口状态覆盖在 `test_hier_integration.py`。

## 证据与清理

结果目录包含 `result.json`、节点/信道/主机日志、生成配置、执行命令和 `flows-before/after/final.txt`。保留失败结果便于定位。

脚本只停止自己持有的进程，并清理自己建立的桥、veth 和命名空间；清理失败会使整体失败。不操作现有 `br-int`、默认路由、物理网卡或防火墙。OVS 服务需预先安装启动。

GitHub Actions 对旧帧和 a/b 两种模式分别运行上述验收并上传日志。本机虚拟机的实际结果见[拓扑与 a/b 验收](topology-test-report.md)。`docs/ovs-evidence/` 和 2026-11-04 的报告属于历史版本，不能用作本次拓扑的验收证据。

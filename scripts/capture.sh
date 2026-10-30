#!/usr/bin/env bash
# capture.sh —— ps(1) 取证：同一时刻在两块网卡上抓包，证明帧真的走了不同介质
#
# 用法（两台机器都可以跑，推荐在 A 机跑）:
#   sudo bash scripts/capture.sh            # 默认抓 30 秒
#   sudo bash scripts/capture.sh 60         # 抓 60 秒
#
# 抓包期间在另一个终端跑:  bash scripts/demo_nic.sh A
#
# 预期（这是报告里“两种介质”的硬证据）:
#   无线 pcap（ens37）:  1102 → 2002   域内 1—3 链路（节点1 → 节点3）
#                        3104 → 9002   下行信道 0x0B（A 机节点2 → B 机主机）
#   有线 pcap（ens33）:  3003 → 2001   域内 2—3 链路
#                        3002 → 4001   域内 2—4 链路
#                        2104 → 9003   下行信道 0x0C（B 机节点3 → A 机主机）
#                        4103 → 9004   下行信道 0x0D（B 机节点4 → A 机主机）
set -u
cd "$(dirname "$0")/.." || exit 1

DUR="${1:-30}"
OUTDIR="logs"
mkdir -p "$OUTDIR"
TS="$(date +%m%d-%H%M%S)"

ENVF="config-nic/ips.env"
ETH_DEV="ens33"; WIFI_DEV="ens37"
[ -f "$ENVF" ] && . "./$ENVF" || echo "(未找到 $ENVF，用默认网卡名 ens33 / ens37)"

if [ "$(id -u)" -ne 0 ]; then
    echo "抓包需要 root: sudo bash scripts/capture.sh" >&2
    exit 1
fi
command -v tcpdump >/dev/null 2>&1 || { echo "需要 tcpdump: sudo apt install -y tcpdump" >&2; exit 1; }

# 本项目用到的全部 UDP 端口
PORTS="1101 1102 1103 2001 2002 2003 2104 3001 3002 3003 3104 4001 4002 4103 9001 9002 9003 9004 9005 9006"
FILT=""
for p in $PORTS; do FILT="${FILT}${FILT:+ or }udp port $p"; done

ETH_PCAP="$OUTDIR/cap-$TS-eth.pcap"
WIFI_PCAP="$OUTDIR/cap-$TS-wifi.pcap"

echo "== 开始抓 $DUR 秒 =="
echo "  有线 ${ETH_DEV}$([ -n "${A_ETH:-}" ] && echo " ($A_ETH)")  -> $ETH_PCAP"
echo "  无线 ${WIFI_DEV}$([ -n "${A_WIFI:-}" ] && echo " ($A_WIFI)")  -> $WIFI_PCAP"
echo "  现在去另一个终端跑:  bash scripts/demo_nic.sh A"
echo

tcpdump -i "$ETH_DEV"  -n -U -w "$ETH_PCAP"  "$FILT" >/dev/null 2>&1 &
P1=$!
tcpdump -i "$WIFI_DEV" -n -U -w "$WIFI_PCAP" "$FILT" >/dev/null 2>&1 &
P2=$!
sleep "$DUR"
kill "$P1" "$P2" 2>/dev/null
wait "$P1" "$P2" 2>/dev/null
sleep 0.5

summarize() {
    f="$1"; label="$2"
    n="$(tcpdump -r "$f" -n 2>/dev/null | wc -l)"
    echo "----- $label  $f  （共 $n 个包）-----"
    if [ "$n" -eq 0 ]; then
        echo "  (空：确认这台机器该网卡确实承载了流量、或对端 IP/端口写对了)"
        return
    fi
    tcpdump -r "$f" -n 2>/dev/null \
        | awk '{split($3,a,"."); split($5,b,"."); print "  " a[5] " -> " b[5]}' \
        | sort | uniq -c | sort -rn
}

echo "== 抓包结果：端口对统计 =="
summarize "$ETH_PCAP"  "有线 ${ETH_DEV}"
summarize "$WIFI_PCAP" "无线 ${WIFI_DEV}"

echo
echo "== 帧内容（前一帧的十六进制，可对上 dst/src/ttl/len/crc）=="
echo "有线:"; tcpdump -r "$ETH_PCAP"  -X -n -c 1 2>/dev/null | sed 's/^/  /'
echo "无线:"; tcpdump -r "$WIFI_PCAP" -X -n -c 1 2>/dev/null | sed 's/^/  /'
echo
echo "回放: sudo tcpdump -r $ETH_PCAP -X -n | less"
echo "      sudo tcpdump -r $WIFI_PCAP -X -n | less"

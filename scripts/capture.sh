#!/usr/bin/env bash
# capture.sh —— ps(1) 取证：同一时刻在两块网卡上抓包，证明帧真的走了不同介质
#
# 用法（两台机器都可以跑，推荐在 A 机跑）:
#   sudo bash scripts/capture.sh            # 默认抓 30 秒
#   sudo bash scripts/capture.sh 60         # 抓 60 秒
#
# 抓包期间在另一个终端跑:  bash scripts/demo_nic.sh A
#
# Wi-Fi: 1102↔4003（1—4），3101↔9102（下行3）。
# 有线: 3003↔2001（2—3），3002↔4002（2—4），2103/4105↔9103（下行5）。
# 虚拟网卡抓包只证明接口路径，实际介质还需记录硬件连接。
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
PORTS="1101 1102 1103 2001 2004 2102 2103 3002 3003 3004 3101 4002 4003 4004 4101 4105 9001 9002 9003 9005 9101 9102 9103"
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

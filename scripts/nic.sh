#!/usr/bin/env bash
# nic.sh —— ps(1) 双介质（有线 eth + 无线 wifi）环境准备
#
# 三个子命令：
#   bash scripts/nic.sh detect                             # 看本机两块网卡的 IP
#   bash scripts/nic.sh gen <A_eth> <A_wifi> <B_eth> <B_wifi>   # 生成 config-nic/*.conf
#   bash scripts/nic.sh check <对端eth> <对端wifi>          # 两个网段互通自检
#
# 网卡名不对时用环境变量覆盖：
#   ETH_DEV=ens160 WIFI_DEV=wlan0 bash scripts/nic.sh detect
set -u
cd "$(dirname "$0")/.." || exit 1

ETH_DEV="${ETH_DEV:-ens33}"     # 有线：VMware VMnet8(NAT) 或桥接以太网
WIFI_DEV="${WIFI_DEV:-ens37}"   # 无线：桥接到宿主机 WLAN 的第二块网卡
TPL="config-nic-template"
OUT="config-nic"

ip_of() {                       # 取网卡上的 IPv4
    ip -4 -br addr show dev "$1" 2>/dev/null \
        | awk '{print $3}' | cut -d/ -f1 | head -1
}

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in

detect)
    echo "== 本机网卡 =="
    for d in "$ETH_DEV" "$WIFI_DEV"; do
        a="$(ip_of "$d")"
        if [ -n "$a" ]; then
            printf '  %-8s %-14s %s\n' "$d" "$a" "$(ip link show dev "$d" 2>/dev/null | grep -o 'state [A-Z]*' | head -1)"
        else
            printf '  %-8s %-14s %s\n' "$d" "(无IP)" "未插网线/未桥接/网卡不存在"
        fi
    done
    echo
    echo "其它网卡（挑第二块用）："
    ip -4 -br addr show | awk '$1!="lo"{printf "  %-10s %s\n",$1,$3}'
    echo
    echo "下一步：两台机器都跑 detect，把 4 个 IP 填进 gen"
    echo "  bash scripts/nic.sh gen <A_eth> <A_wifi> <B_eth> <B_wifi>"
    ;;

gen)
    A_ETH="${2:-}"; A_WIFI="${3:-}"; B_ETH="${4:-}"; B_WIFI="${5:-}"
    if [ -z "$A_ETH" ] || [ -z "$A_WIFI" ] || [ -z "$B_ETH" ] || [ -z "$B_WIFI" ]; then
        echo "用法: bash scripts/nic.sh gen <A_eth> <A_wifi> <B_eth> <B_wifi>" >&2
        echo "例:   bash scripts/nic.sh gen 192.168.222.128 192.168.235.101 192.168.222.130 192.168.235.102" >&2
        exit 1
    fi
    if [ ! -d "$TPL" ]; then
        echo "缺少模板目录 $TPL" >&2
        exit 1
    fi
    mkdir -p "$OUT"
    for n in 1 2 3 4; do
        sed -e "s/@A_ETH@/$A_ETH/g" -e "s/@A_WIFI@/$A_WIFI/g" \
            -e "s/@B_ETH@/$B_ETH/g" -e "s/@B_WIFI@/$B_WIFI/g" \
            "$TPL/node$n.conf" > "$OUT/node$n.conf"
    done
    # 供 run_nic.sh / link_test.sh / capture.sh 读取
    cat > "$OUT/ips.env" <<EOF
# 由 scripts/nic.sh gen 生成，勿手改
A_ETH=$A_ETH
A_WIFI=$A_WIFI
B_ETH=$B_ETH
B_WIFI=$B_WIFI
ETH_DEV=$ETH_DEV
WIFI_DEV=$WIFI_DEV
EOF
    echo "== 已生成 $OUT/ =="
    for n in 1 2 3 4; do echo "--- node$n.conf ---"; sed 's/^/  /' "$OUT/node$n.conf"; done
    echo
    echo "把整个工程同步到另一台机器后，两边分别执行："
    echo "  bash scripts/nic.sh check <对端eth> <对端wifi>"
    echo "  bash scripts/run_nic.sh A      # 或 B"
    ;;

check)
    P1="${2:-}"; P2="${3:-}"
    [ -n "$P1" ] || { echo "用法: bash scripts/nic.sh check <对端eth> <对端wifi>" >&2; exit 1; }
    echo "== 与对端两个网段的连通性 =="
    for p in "$P1" "$P2"; do
        [ -n "$p" ] || continue
        if ping -c2 -W2 "$p" >/dev/null 2>&1; then
            echo "  $p  可达 ✓"
        else
            echo "  $p  不可达 ✗  —— 检查：对端 IP 是否写错 / ufw 是否放行 / 无线网是否开了 AP 隔离"
        fi
    done
    echo
    echo "== 本机两块网卡 =="
    for d in "$ETH_DEV" "$WIFI_DEV"; do
        printf '  %-8s %s\n' "$d" "$(ip_of "$d")"
    done
    echo
    echo "== UDP 端口占用（应为空） =="
    ss -uan 2>/dev/null | grep -E ':(1101|1102|1103|2001|2002|2003|2104|3001|3002|3003|3104|4001|4002|4103|900[1-6])\b' || echo "  无占用"
    echo
    echo "== 两个网段不通时的排查 =="
    echo "  防火墙:  sudo ufw allow 1101:9006/udp   （或 sudo ufw disable）"
    echo "  无线不通: 宿主机 WiFi 可能开了 AP 隔离，改用手机热点或以太网桥接"
    echo "  网卡名不对: ETH_DEV=ens160 WIFI_DEV=wlan0 bash scripts/nic.sh detect"
    ;;

*)
    usage
    exit 1
    ;;
esac

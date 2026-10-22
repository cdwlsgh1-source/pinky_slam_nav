#!/bin/bash
# ros2_net_diag.sh
# 관제PC에서 실행: 가벼운 부하 + 실제 bringup 토픽까지 함께 모니터링하며
# 네트워크/디스커버리/시스템 자원 지표를 동시에 로깅합니다.
#
# 사용법:
#   ./ros2_net_diag.sh <핑키IP> [부하_Hz] [지속시간_분] [무선인터페이스명] [감시할_토픽,쉼표구분]
#
# 예시:
#   ./ros2_net_diag.sh 192.168.0.101 0.1 30 wlan0 "/scan,/tf,/map,/odom"
#
# 종료: Ctrl+C 또는 지정한 시간이 지나면 자동 종료됩니다.
#
# 필요 권한: tcpdump 캡처(6번)는 sudo 필요. 없으면 해당 로그만 비워둔 채 나머지는 계속 진행합니다.

set -u

TARGET_IP=${1:?"핑키 IP를 입력하세요. 예: ./ros2_net_diag.sh 192.168.0.101"}
RATE=${2:-0.1}
DURATION_MIN=${3:-30}
IFACE=${4:-}
WATCH_TOPICS=${5:-"/scan,/tf,/map,/odom"}

# 인터페이스 미지정 시 자동 감지: 무선 인터페이스 → 핑키IP로 나가는 경로의 인터페이스 순
if [ -z "$IFACE" ]; then
  IFACE=$(iw dev 2>/dev/null | awk '$1=="Interface" {print $2; exit}')
  [ -z "$IFACE" ] && IFACE=$(ip route get "$TARGET_IP" 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
  IFACE=${IFACE:-wlan0}
  echo "[알림] 인터페이스 자동 감지: $IFACE"
fi
if [ ! -d "/sys/class/net/$IFACE" ]; then
  echo "[경고] 인터페이스 '$IFACE' 가 없습니다 → iface_bw/iface/wifi 로그가 비게 됩니다. (ip link 로 확인)"
fi

LOGDIR="./ros2_net_diag_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOGDIR"

echo "===================================================="
echo " ROS2 네트워크 진단 v2 시작"
echo " 대상 IP     : $TARGET_IP"
echo " 부하 발행율 : ${RATE} Hz"
echo " 지속 시간   : ${DURATION_MIN} 분"
echo " 인터페이스  : $IFACE"
echo " 감시 토픽   : $WATCH_TOPICS"
echo " 로그 폴더   : $LOGDIR"
echo "===================================================="

DURATION_SEC=$((DURATION_MIN * 60))
END_TIME=$((SECONDS + DURATION_SEC))

PIDS=()

cleanup() {
  echo ""
  echo "[종료 중] 백그라운드 프로세스 정리..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  echo "[완료] 로그는 $LOGDIR 에 저장되었습니다."
  echo "  - pub.log         : 발행한 부하 토픽 로그"
  echo "  - topic_hz.log    : 합성 테스트 토픽 수신 빈도 (끊기면 여기서 바로 보임)"
  echo "  - ping.log        : 초당 핑 성공/실패, 지연시간"
  echo "  - iface.log       : 5초마다 인터페이스 RX/TX 에러·드랍 카운트 (원본)"
  echo "  - iface_bw.csv    : 5초 간격 실제 처리량 (KB/s, 델타 계산됨) ← 신규"
  echo "  - topic_bw_*.log  : 감시 대상 실제 토픽별 대역폭 ← 신규"
  echo "  - node_count.csv  : 10초 간격 노드/참가자 수 추이 ← 신규"
  echo "  - discovery.pcap  : discovery 멀티캐스트(239.255.0.1, 7400-7420) 캡처 ← 신규 (sudo 필요)"
  echo "  - wifi_quality.csv: 5초 간격 WiFi 신호세기/링크품질 ← 신규"
  echo "  - cpu_load.csv    : 5초 간격 CPU/메모리 부하 (관제PC 기준) ← 신규"
  echo "  - proto_load.csv  : 5초 간격 UDP/TCP/멀티캐스트/유니캐스트 부하 (호스트 전체 카운터) ← 신규"
  exit 0
}
trap cleanup INT TERM

# 1) 가벼운 부하 발행 (백그라운드) - 기존 기능
ros2 topic pub /diag_test std_msgs/msg/String "data: hello" -r "$RATE" \
  > "$LOGDIR/pub.log" 2>&1 &
PIDS+=($!)

# 2) 합성 테스트 토픽 수신 빈도 - 기존 기능
ros2 topic hz /diag_test > "$LOGDIR/topic_hz.log" 2>&1 &
PIDS+=($!)

# 3) 초당 핑 로그 - 기존 기능
(
  while [ $SECONDS -lt $END_TIME ]; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    result=$(ping -c 1 -W 1 "$TARGET_IP" 2>/dev/null | grep -oP 'time=\K[0-9.]+')
    if [ -z "$result" ]; then
      echo "$ts LOST" >> "$LOGDIR/ping.log"
    else
      echo "$ts OK ${result}ms" >> "$LOGDIR/ping.log"
    fi
    sleep 1
  done
) &
PIDS+=($!)

# 4) 인터페이스 레벨 드랍/에러 카운트 (원본 텍스트) - 기존 기능
(
  while [ $SECONDS -lt $END_TIME ]; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    stats=$(ip -s link show "$IFACE" 2>/dev/null | tr '\n' ' ')
    echo "$ts | $stats" >> "$LOGDIR/iface.log"
    sleep 5
  done
) &
PIDS+=($!)

# 5) [신규] 인터페이스 실제 처리량 (KB/s, 델타 계산 → 바로 그래프 가능한 CSV)
(
  echo "timestamp,rx_KBps,tx_KBps" > "$LOGDIR/iface_bw.csv"
  PREV_RX=$(cat /sys/class/net/"$IFACE"/statistics/rx_bytes 2>/dev/null || echo 0)
  PREV_TX=$(cat /sys/class/net/"$IFACE"/statistics/tx_bytes 2>/dev/null || echo 0)
  while [ $SECONDS -lt $END_TIME ]; do
    sleep 5
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    CUR_RX=$(cat /sys/class/net/"$IFACE"/statistics/rx_bytes 2>/dev/null || echo 0)
    CUR_TX=$(cat /sys/class/net/"$IFACE"/statistics/tx_bytes 2>/dev/null || echo 0)
    RX_KBPS=$(awk -v a="$CUR_RX" -v b="$PREV_RX" 'BEGIN{printf "%.2f", (a-b)/5/1024}')
    TX_KBPS=$(awk -v a="$CUR_TX" -v b="$PREV_TX" 'BEGIN{printf "%.2f", (a-b)/5/1024}')
    echo "$ts,$RX_KBPS,$TX_KBPS" >> "$LOGDIR/iface_bw.csv"
    PREV_RX=$CUR_RX
    PREV_TX=$CUR_TX
  done
) &
PIDS+=($!)

# 6) [신규] 실제 bringup 토픽별 대역폭 (ros2 topic bw) - 감시 토픽마다 병렬 실행
IFS=',' read -ra TOPIC_ARR <<< "$WATCH_TOPICS"
for t in "${TOPIC_ARR[@]}"; do
  safe_name=$(echo "$t" | tr '/' '_')
  (
    while [ $SECONDS -lt $END_TIME ]; do
      ts=$(date '+%Y-%m-%d %H:%M:%S')
      echo "=== $ts ===" >> "$LOGDIR/topic_bw${safe_name}.log"
      timeout 4 ros2 topic bw "$t" >> "$LOGDIR/topic_bw${safe_name}.log" 2>&1
      sleep 6
    done
  ) &
  PIDS+=($!)
done

# 7) [신규] 노드/참가자 수 추이 (discovery 부하와 직결)
(
  echo "timestamp,node_count,topic_count" > "$LOGDIR/node_count.csv"
  while [ $SECONDS -lt $END_TIME ]; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    ncount=$(ros2 node list 2>/dev/null | wc -l)
    tcount=$(ros2 topic list 2>/dev/null | wc -l)
    echo "$ts,$ncount,$tcount" >> "$LOGDIR/node_count.csv"
    sleep 10
  done
) &
PIDS+=($!)

# 8) [신규] Discovery 멀티캐스트 트래픽 캡처 (sudo 필요, 없으면 스킵)
if command -v tcpdump >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    (
      sudo timeout "$DURATION_SEC" tcpdump -i "$IFACE" -n \
        "host 239.255.0.1 or portrange 7400-7420" \
        -w "$LOGDIR/discovery.pcap" 2>/dev/null
    ) &
    PIDS+=($!)
  else
    echo "[알림] sudo 비밀번호 없이 실행 불가 → discovery.pcap 캡처는 건너뜁니다."
    echo "        수동으로 원하시면: sudo tcpdump -i $IFACE -n 'host 239.255.0.1 or portrange 7400-7420' -w discovery.pcap"
  fi
fi

# 9) [신규] WiFi 신호 세기 / 링크 품질 (관제PC 기준)
(
  echo "timestamp,signal_dbm,link_quality" > "$LOGDIR/wifi_quality.csv"
  while [ $SECONDS -lt $END_TIME ]; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    if command -v iw >/dev/null 2>&1; then
      sig=$(iw dev "$IFACE" link 2>/dev/null | grep -oP 'signal:\s*\K-?[0-9]+')
    else
      sig=""
    fi
    if [ -z "$sig" ] && [ -f /proc/net/wireless ]; then
      sig=$(awk -v ifc="$IFACE:" '$1==ifc {print $4}' /proc/net/wireless 2>/dev/null)
    fi
    qual=""
    if [ -f /proc/net/wireless ]; then
      qual=$(awk -v ifc="$IFACE:" '$1==ifc {print $3}' /proc/net/wireless 2>/dev/null)
    fi
    echo "$ts,${sig:-NA},${qual:-NA}" >> "$LOGDIR/wifi_quality.csv"
    sleep 5
  done
) &
PIDS+=($!)

# 10) [신규] CPU / 메모리 부하 (관제PC 기준 - 필요시 Pinky에서 별도 실행 권장)
(
  echo "timestamp,cpu_load1,mem_used_pct" > "$LOGDIR/cpu_load.csv"
  while [ $SECONDS -lt $END_TIME ]; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    load1=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)
    mem_pct=$(free | awk '/Mem:/ {printf "%.1f", $3/$2*100}')
    echo "$ts,${load1:-NA},${mem_pct:-NA}" >> "$LOGDIR/cpu_load.csv"
    sleep 5
  done
) &
PIDS+=($!)

# 11) [신규] 프로토콜별 부하: UDP / TCP / 멀티캐스트 / 유니캐스트 (커널 카운터 델타, sudo 불필요)
#     /proc/net/snmp, /proc/net/netstat(IpExt) 는 호스트 전체(loopback 포함) 기준입니다.
python3 - "$LOGDIR/proto_load.csv" "$DURATION_SEC" <<'PYEOF' &
import sys, time

out_path, duration = sys.argv[1], int(sys.argv[2])
INTERVAL = 5

def read_counters():
    """'Proto: k1 k2 ...' / 'Proto: v1 v2 ...' 두 줄 쌍을 {'Proto.k': v} 로 변환"""
    c = {}
    for path in ("/proc/net/snmp", "/proc/net/netstat"):
        try:
            lines = open(path).read().splitlines()
        except OSError:
            continue
        for head, vals in zip(lines[0::2], lines[1::2]):
            proto, keys = head.split(":", 1)
            for k, v in zip(keys.split(), vals.split(":", 1)[1].split()):
                c[f"{proto}.{k}"] = int(v)
    return c

def delta(cur, prev, key):
    # 카운터 리셋(음수)은 0 으로 처리
    return max(0, cur.get(key, 0) - prev.get(key, 0))

cols = ("timestamp,udp_in_pps,udp_out_pps,udp_err,udp_rcvbuf_err,"
        "tcp_in_pps,tcp_out_pps,tcp_retrans,"
        "mcast_in_pps,mcast_out_pps,mcast_in_KBps,ucast_in_pps,ucast_in_KBps")
with open(out_path, "w", buffering=1) as f:
    f.write(cols + "\n")
    prev, t_prev = read_counters(), time.monotonic()
    t_end = t_prev + duration
    while time.monotonic() < t_end:
        time.sleep(INTERVAL)
        cur, now = read_counters(), time.monotonic()
        dt = now - t_prev
        d = lambda k: delta(cur, prev, k)
        mc_in, bc_in = d("IpExt.InMcastPkts"), d("IpExt.InBcastPkts")
        mc_oct, bc_oct = d("IpExt.InMcastOctets"), d("IpExt.InBcastOctets")
        row = [
            time.strftime("%Y-%m-%d %H:%M:%S"),
            d("Udp.InDatagrams") / dt, d("Udp.OutDatagrams") / dt,
            d("Udp.InErrors"), d("Udp.RcvbufErrors"),
            d("Tcp.InSegs") / dt, d("Tcp.OutSegs") / dt, d("Tcp.RetransSegs"),
            mc_in / dt, d("IpExt.OutMcastPkts") / dt, mc_oct / dt / 1024,
            max(0, d("Ip.InReceives") - mc_in - bc_in) / dt,
            max(0, d("IpExt.InOctets") - mc_oct - bc_oct) / dt / 1024,
        ]
        f.write(row[0] + "," + ",".join(f"{v:.2f}" for v in row[1:]) + "\n")
        prev, t_prev = cur, now
PYEOF
PIDS+=($!)

echo "실행 중... (백그라운드 PID: ${PIDS[*]})"
echo "실시간으로 확인하려면 다른 터미널에서:"
echo "  tail -f $LOGDIR/ping.log"
echo "  tail -f $LOGDIR/topic_hz.log"
echo "  tail -f $LOGDIR/iface_bw.csv"
echo "  tail -f $LOGDIR/node_count.csv"
echo ""
echo "※ 로봇(Pinky) 쪽 CPU/메모리/신호세기도 함께 보고 싶다면,"
echo "  이 스크립트를 Pinky에서도 동시에 실행하시면 됩니다 (TARGET_IP만 PC IP로 바꿔서)."
echo ""
echo "Ctrl+C 로 언제든 중단 가능합니다."

# 지정 시간만큼 대기
while [ $SECONDS -lt $END_TIME ]; do
  sleep 1
done

cleanup

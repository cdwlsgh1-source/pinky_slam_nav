# ros2_network_test

관제 PC와 Pinky 로봇 사이의 ROS 2 네트워크 상태를 진단하고, 이상이 난 시점의 이력을 확인하기 위한 도구 모음입니다.

| 파일 | 역할 |
|------|------|
| `ros2_net_diag.sh` | **수집**: 핑, 처리량, WiFi, 토픽 대역폭, 노드 수, 프로토콜별 부하 등을 로그 폴더에 기록 |
| `net_monitor.py` | **분석 + 화면**: 로그 폴더를 읽어 이상 이벤트를 찾고 웹 대시보드로 표시 (읽기 전용) |
| `net_monitor.json` | 대시보드의 이상 판정 임계값 |

두 도구는 로그 폴더(`ros2_net_diag_YYYYMMDD_HHMMSS/`)로만 연결됩니다. 진단이 끝난 뒤나 다른 날의 로그도 대시보드로 볼 수 있습니다.

```
ros2_net_diag.sh ──쓰기──▶ ros2_net_diag_*/ ──읽기──▶ net_monitor.py ──▶ 브라우저
```

## 목차

1. [빠른 시작](#1-빠른-시작)
2. [사전 준비](#2-사전-준비)
3. [진단 스크립트 `ros2_net_diag.sh`](#3-진단-스크립트-ros2_net_diagsh)
4. [결과 로그](#4-결과-로그)
5. [이상 이력 대시보드 `net_monitor.py`](#5-이상-이력-대시보드-net_monitorpy)
6. [참고 사항과 한계](#6-참고-사항과-한계)

---

## 1. 빠른 시작

터미널 두 개를 `ros2_network_test/` 폴더에서 엽니다.

```bash
# 터미널 1: 진단 시작 (ROS 환경 source 후, 30분)
./ros2_net_diag.sh 192.168.0.101

# 터미널 2: 대시보드 시작 → 브라우저에서 http://localhost:8088/
python3 net_monitor.py
```

> 대시보드는 자기 폴더의 `ros2_net_diag_*` 를 찾으므로 진단 스크립트도 이 폴더에서 실행하세요.

## 2. 사전 준비

- ROS 2 환경이 source 된 터미널 (`ros2` 명령이 동작해야 함)
  ```bash
  source /opt/ros/<distro>/setup.bash
  ```
- 관제 PC와 Pinky가 **같은 `ROS_DOMAIN_ID`** 를 쓰고 같은 네트워크에 있어야 합니다. 스크립트는 도메인을 설정하지 않고 실행한 셸의 값을 그대로 씁니다.
- 실행 권한 (처음 한 번만)
  ```bash
  chmod +x ros2_net_diag.sh
  ```
- (선택) `iw`, `tcpdump` — 없으면 해당 항목만 건너뛰거나 `NA`로 기록됩니다.
  ```bash
  sudo apt install iw tcpdump
  ```
- 대시보드(`python3 net_monitor.py`)는 표준 라이브러리만 쓰므로 추가 설치가 없습니다.

> **시험 실행 시 주의**: 진단 스크립트는 `/diag_test` 토픽을 발행합니다. 실제 관제 도메인과 섞이지 않게 시험할 때는 `ROS_DOMAIN_ID=77` 과 `ROS_LOCALHOST_ONLY=1` 을 지정하고, 로봇 IP 대신 `127.0.0.1`, 인터페이스는 `lo` 를 쓰세요.
> ```bash
> ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1 ./ros2_net_diag.sh 127.0.0.1 1 2 lo
> ```

## 3. 진단 스크립트 `ros2_net_diag.sh`

**관제 PC**에서 실행하며, 가벼운 테스트 토픽(`/diag_test`)을 발행하는 동시에 여러 지표를 로그 파일로 기록합니다. 네트워크가 끊기거나 느려지는 시점에 어떤 지표가 같이 변했는지 비교하는 용도입니다.

```bash
./ros2_net_diag.sh <핑키IP> [부하_Hz] [지속시간_분] [인터페이스명] [감시할_토픽,쉼표구분]
```

| 순서 | 인자 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| 1 | `핑키IP` | 필수 | - | Pinky 로봇의 IP 주소 (핑 대상) |
| 2 | `부하_Hz` | 선택 | `0.1` | `/diag_test` 토픽 발행 주기(Hz) |
| 3 | `지속시간_분` | 선택 | `30` | 진단을 진행할 시간(분) |
| 4 | `인터페이스명` | 선택 | 자동 감지 | 관제 PC의 네트워크 인터페이스. 생략하면 무선 인터페이스(`iw dev`), 없으면 핑키 IP로 나가는 경로의 인터페이스를 사용 |
| 5 | `감시할_토픽` | 선택 | `/scan,/tf,/map,/odom` | 대역폭을 측정할 토픽 (쉼표로 구분, 공백 없이) |

> 뒤쪽 인자를 지정하려면 앞쪽 인자도 모두 채워야 합니다. (예: 인터페이스만 바꾸려면 IP, Hz, 시간도 함께 입력)
>
> 지정한 인터페이스가 시스템에 없으면 시작할 때 경고합니다. 없는 이름이면 `iface_bw.csv`, `iface.log`, `wifi_quality.csv` 가 비거나 `NA` 로 기록되니 `ip link` 로 이름을 확인하세요.

### 예시

```bash
# 최소 실행 (0.1Hz, 30분, 인터페이스 자동 감지)
./ros2_net_diag.sh 192.168.0.101

# 전체 옵션 지정
./ros2_net_diag.sh 192.168.0.101 0.1 30 wlan0 "/scan,/tf,/map,/odom"

# 10분만, 무선 인터페이스가 wlp2s0 인 경우
./ros2_net_diag.sh 192.168.0.101 0.1 10 wlp2s0
```

### discovery.pcap 캡처 (sudo)

tcpdump 캡처는 **비밀번호 없이 sudo가 가능할 때만** 실행됩니다. 실행 전에 sudo 인증을 미리 받아두세요.

```bash
sudo -v                                  # 비밀번호 입력 (인증 캐시)
./ros2_net_diag.sh 192.168.0.101
```

sudo를 쓰지 않으면 이 캡처만 건너뛰고 나머지는 정상 진행됩니다. 수동 캡처가 필요하면:

```bash
sudo tcpdump -i wlan0 -n 'host 239.255.0.1 or portrange 7400-7420' -w discovery.pcap
```

### 실행 중 확인 / 종료

- 지정한 시간이 지나면 **자동 종료**됩니다.
- 중간에 멈추려면 `Ctrl+C` 를 누르세요. 백그라운드 프로세스가 정리되고 로그는 그대로 남습니다.
- 다른 터미널에서 로그를 바로 볼 수도 있습니다.
  ```bash
  tail -f ros2_net_diag_*/ping.log
  tail -f ros2_net_diag_*/topic_hz.log
  tail -f ros2_net_diag_*/iface_bw.csv
  tail -f ros2_net_diag_*/proto_load.csv
  ```

## 4. 결과 로그

스크립트를 실행한 위치에 `ros2_net_diag_YYYYMMDD_HHMMSS/` 폴더가 생성되고 그 안에 다음 파일이 저장됩니다.

| 파일 | 주기 | 내용 |
|------|------|------|
| `pub.log` | - | 발행한 부하 토픽(`/diag_test`) 로그 |
| `topic_hz.log` | 실시간 | `/diag_test` 수신 빈도. 통신이 끊기면 여기서 바로 확인 가능 |
| `ping.log` | 1초 | Pinky IP로의 핑 성공(`OK`, 지연시간) / 실패(`LOST`) |
| `iface.log` | 5초 | 인터페이스 RX/TX 에러·드랍 카운트 (원본 텍스트) |
| `iface_bw.csv` | 5초 | 인터페이스 실제 처리량 (rx/tx KB/s) |
| `proto_load.csv` | 5초 | UDP / TCP / 멀티캐스트 / 유니캐스트 부하 (커널 카운터 델타, sudo 불필요) |
| `topic_bw_*.log` | 약 10초 | 감시 토픽별 대역폭 (`ros2 topic bw`, 4초 측정 후 6초 대기) |
| `node_count.csv` | 10초 | 노드 수 / 토픽 수 추이 |
| `discovery.pcap` | - | discovery 멀티캐스트(239.255.0.1, 포트 7400–7420) 캡처 (sudo 필요) |
| `wifi_quality.csv` | 5초 | WiFi 신호 세기(dBm) / 링크 품질 |
| `cpu_load.csv` | 5초 | CPU load(1분) / 메모리 사용률(%) — 관제 PC 기준 |

`*.csv` 파일은 스프레드시트나 Python(pandas) 등으로 열어 그래프를 그릴 수 있습니다.

### proto_load.csv 컬럼

`/proc/net/snmp`, `/proc/net/netstat`(IpExt)의 5초 변화량으로 계산하며, **호스트 전체(loopback 포함)** 기준입니다. 인터페이스 단위 값은 `iface_bw.csv` 를 보세요.

| 컬럼 | 의미 |
|------|------|
| `udp_in_pps`, `udp_out_pps` | UDP 수신/송신 데이터그램 수 (초당) |
| `udp_err`, `udp_rcvbuf_err` | UDP 오류 / 수신버퍼 오버플로 (5초당 건수). DDS 패킷 유실의 직접 원인 |
| `tcp_in_pps`, `tcp_out_pps`, `tcp_retrans` | TCP 세그먼트 수 (초당), 재전송 (5초당 건수) |
| `mcast_in_pps`, `mcast_out_pps`, `mcast_in_KBps` | 멀티캐스트 수신/송신 패킷, 수신량. DDS discovery 부하 |
| `ucast_in_pps`, `ucast_in_KBps` | 유니캐스트 수신 = 전체 − 멀티캐스트 − 브로드캐스트 (IP 계층 기준) |

## 5. 이상 이력 대시보드 `net_monitor.py`

로그 폴더를 읽어 **이상이 발생한 시점의 이력**을 보여주는 웹 화면입니다. 로그를 읽기만 하고 수정하지 않으며, 진단이 진행 중이어도 끝난 뒤에도 쓸 수 있습니다.

```bash
python3 net_monitor.py                                      # 이 폴더의 가장 최근 ros2_net_diag_* 실행
python3 net_monitor.py ros2_net_diag_20261007_210621_ID20   # 특정 실행부터 열기
python3 net_monitor.py --port 8088 --host 0.0.0.0           # 다른 PC 브라우저에서 보기
```

브라우저에서 `http://localhost:8088/` 를 엽니다.

- **이상 이벤트 이력**: 시각 · 종류 · 값 · 기준. 행을 클릭하면 모든 차트가 해당 시점 ±2분으로 확대되고 기준선이 표시됩니다.
- **차트**: 핑, 처리량, WiFi, 프로토콜별 패킷률/수신량, 오류·재전송, 노드 수, CPU/메모리, 토픽 대역폭. 시간축이 공유되고 마우스를 올리면 같은 시각의 값이 범례에 표시됩니다.
- **상단**: 실행 선택 드롭다운, 상태 배지(정상/주의/이상, 실시간/종료됨), "전체 보기", "최근 10분".
- 연속된 이상 샘플은 `merge_gap_s` 이내면 하나의 구간 이벤트로 합쳐집니다.
- `proto_load.csv` 가 없는 예전 로그도 해당 차트만 빠진 채 열립니다.

### 실시간 모니터링

진단이 진행 중이면 브라우저가 5초마다 새 데이터를 가져옵니다. 마지막 데이터가 30초(`stale_after_s`) 이내면 "실시간", 그보다 오래되면 "종료됨"으로 표시됩니다. 차트를 맨 끝까지 보고 있으면 새 데이터를 따라가고, 이벤트를 눌러 과거 구간을 보는 중에는 시점이 고정됩니다. 수집 주기가 1~10초라 화면에는 보통 5~10초 늦게 반영됩니다.

### 이상 판정 기준 (`net_monitor.json`)

| 키 | 기본값 | 판정 |
|----|--------|------|
| `ping_latency_ms` | 100 | 핑 지연 (핑 `LOST` 는 항상 이상) |
| `wifi_signal_dbm` | -75 | 이보다 약하면 주의 |
| `mcast_in_pps` | 200 | 멀티캐스트 수신이 이를 넘으면 discovery 폭주 의심 |
| `tcp_retrans_per_interval` | 20 | 5초당 TCP 재전송 건수 |
| `node_drop` | 1 | 직전 샘플 대비 노드 수 감소 |
| `cpu_load1` | 8.0 | 관제 PC load(1분) |
| `mem_used_pct` | 90 | 관제 PC 메모리 사용률(%) |
| `merge_gap_s` | 15 | 이벤트 병합 간격(초) |
| `stale_after_s` | 30 | 이 시간 넘게 새 데이터가 없으면 "종료됨" |

임계값 없이 항상 이벤트로 기록되는 항목: 핑 `LOST`, UDP 수신버퍼 오버플로, 인터페이스 에러/드랍 증가, 감시 토픽 미발행(측정값 없음). 설정 파일을 바꾸면 대시보드를 다시 시작해야 반영됩니다.

## 6. 참고 사항과 한계

- CPU/메모리, WiFi 신호는 **스크립트를 실행한 PC 기준**입니다. 로봇 쪽 상태도 보려면 Pinky에서 같은 스크립트를 동시에 실행하세요. 이때 `핑키IP` 자리에는 **관제 PC의 IP**를 넣습니다. 로봇의 로그는 로봇 PC의 폴더에 쌓이므로 대시보드도 그쪽에서 따로 띄웁니다.
- 감시 토픽에 실제로 발행되지 않는 토픽을 넣으면 해당 `topic_bw_*.log` 에는 측정값이 기록되지 않고, 대시보드에는 "미발행" 이벤트로 나타납니다.
- `topic_hz.log` 는 줄마다 시각이 없어서 대시보드의 이상 판정에는 쓰지 않습니다. 직접 확인하세요.
- 대시보드를 켜 둔 채 새 진단을 시작하면 드롭다운에 새 실행이 바로 나타나지 않습니다. 브라우저를 새로고침(F5)하세요.
- 대시보드 서버에는 인증이 없습니다. `--host 0.0.0.0` 은 신뢰할 수 있는 내부망에서만 쓰세요.
- `ping.log` 에서 `LOST` 가 나타난 시각을 기준으로 `wifi_quality.csv`, `iface_bw.csv`, `node_count.csv`, `proto_load.csv` 를 대조하면 원인을 좁히기 쉽습니다. 대시보드는 이 대조를 같은 시간축 차트로 보여줍니다.

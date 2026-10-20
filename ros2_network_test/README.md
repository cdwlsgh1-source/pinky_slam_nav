# ros2_network_test

관제 PC와 Pinky 로봇 사이의 ROS 2 네트워크 상태를 진단하기 위한 스크립트 모음입니다.

| 파일 | 설명 |
|------|------|
| `ros2_net_diag.sh` | 부하 토픽, 핑, 인터페이스 에러/드랍 + 실제 토픽 대역폭, 노드 수, discovery 캡처, WiFi 품질, CPU/메모리 |

이 문서는 **`ros2_net_diag.sh`** 의 실행 방법을 설명합니다.

---

## 1. 무엇을 하는 스크립트인가

**관제 PC**에서 실행하며, 가벼운 테스트 토픽(`/diag_test`)을 발행하는 동시에 아래 지표를 로그 파일로 기록합니다.
네트워크가 끊기거나 느려지는 시점에 어떤 지표가 같이 변했는지 비교하는 용도입니다.

## 2. 사전 준비

- ROS 2 환경이 source 된 터미널 (`ros2` 명령이 동작해야 함)
  ```bash
  source /opt/ros/<distro>/setup.bash
  ```
- 관제 PC와 Pinky가 **같은 `ROS_DOMAIN_ID`** 를 사용하고, 같은 네트워크에 연결되어 있어야 합니다.
- 실행 권한 (처음 한 번만)
  ```bash
  chmod +x ros2_net_diag.sh
  ```
- (선택) `iw`, `tcpdump` 설치 — 없으면 해당 항목만 건너뛰거나 `NA`로 기록됩니다.
  ```bash
  sudo apt install iw tcpdump
  ```

## 3. 사용법

```bash
./ros2_net_diag.sh <핑키IP> [부하_Hz] [지속시간_분] [무선인터페이스명] [감시할_토픽,쉼표구분]
```

| 순서 | 인자 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| 1 | `핑키IP` | 필수 | - | Pinky 로봇의 IP 주소 (핑 대상) |
| 2 | `부하_Hz` | 선택 | `0.1` | `/diag_test` 토픽 발행 주기(Hz) |
| 3 | `지속시간_분` | 선택 | `30` | 진단을 진행할 시간(분) |
| 4 | `무선인터페이스명` | 선택 | `wlan0` | 관제 PC의 무선 인터페이스 이름 |
| 5 | `감시할_토픽` | 선택 | `/scan,/tf,/map,/odom` | 대역폭을 측정할 토픽 (쉼표로 구분, 공백 없이) |

> 뒤쪽 인자를 지정하려면 앞쪽 인자도 모두 채워야 합니다. (예: 인터페이스만 바꾸려면 IP, Hz, 시간도 함께 입력)

### 예시

```bash
# 최소 실행 (기본값 사용: 0.1Hz, 30분, wlan0)
./ros2_net_diag.sh 192.168.0.101

# 전체 옵션 지정
./ros2_net_diag.sh 192.168.0.101 0.1 30 wlan0 "/scan,/tf,/map,/odom"

# 10분만, 무선 인터페이스가 wlp2s0 인 경우
./ros2_net_diag.sh 192.168.0.101 0.1 10 wlp2s0
```

무선 인터페이스 이름은 아래 명령으로 확인할 수 있습니다.
```bash
ip link
```

### discovery.pcap 캡처를 원할 때 (sudo)

tcpdump 캡처는 **비밀번호 없이 sudo가 가능할 때만** 실행됩니다.
스크립트 실행 전에 sudo 인증을 미리 받아두면 됩니다.

```bash
sudo -v                                  # 비밀번호 입력 (인증 캐시)
./ros2_net_diag.sh 192.168.0.101
```

sudo를 쓰지 않으면 이 캡처만 건너뛰고 나머지는 정상 진행됩니다. 수동 캡처가 필요하면:
```bash
sudo tcpdump -i wlan0 -n 'host 239.255.0.1 or portrange 7400-7420' -w discovery.pcap
```

## 4. 실행 중 확인 / 종료

- 지정한 시간이 지나면 **자동 종료**됩니다.
- 중간에 멈추려면 `Ctrl+C` 를 누르세요. 백그라운드 프로세스가 정리되고 로그는 그대로 남습니다.
- 다른 터미널에서 실시간으로 볼 수 있습니다.
  ```bash
  tail -f ros2_net_diag_*/ping.log
  tail -f ros2_net_diag_*/topic_hz.log
  tail -f ros2_net_diag_*/iface_bw.csv
  tail -f ros2_net_diag_*/node_count.csv
  ```

## 5. 결과 로그

스크립트를 실행한 위치에 `ros2_net_diag_YYYYMMDD_HHMMSS/` 폴더가 생성되고, 그 안에 다음 파일이 저장됩니다.

| 파일 | 주기 | 내용 |
|------|------|------|
| `pub.log` | - | 발행한 부하 토픽(`/diag_test`) 로그 |
| `topic_hz.log` | 실시간 | `/diag_test` 수신 빈도. 통신이 끊기면 여기서 바로 확인 가능 |
| `ping.log` | 1초 | Pinky IP로의 핑 성공(`OK`, 지연시간) / 실패(`LOST`) |
| `iface.log` | 5초 | 인터페이스 RX/TX 에러·드랍 카운트 (원본 텍스트) |
| `iface_bw.csv` | 5초 | 인터페이스 실제 처리량 (rx/tx KB/s) |
| `topic_bw_*.log` | 10초 | 감시 토픽별 대역폭 (`ros2 topic bw`) |
| `node_count.csv` | 10초 | 노드 수 / 토픽 수 추이 |
| `discovery.pcap` | - | discovery 멀티캐스트(239.255.0.1, 포트 7400–7420) 캡처 (sudo 필요) |
| `wifi_quality.csv` | 5초 | WiFi 신호 세기(dBm) / 링크 품질 |
| `cpu_load.csv` | 5초 | CPU load(1분) / 메모리 사용률(%) — 관제 PC 기준 |

`*.csv` 파일은 그대로 스프레드시트나 Python(pandas) 등으로 열어 그래프를 그릴 수 있습니다.

## 6. 참고 사항

- CPU/메모리, WiFi 신호는 **관제 PC 기준**입니다. 로봇 쪽 상태도 보려면 Pinky에서 같은 스크립트를 동시에 실행하세요. 이때 `핑키IP` 자리에는 **관제 PC의 IP**를 넣습니다.
- 감시 토픽에 실제로 발행되지 않는 토픽을 넣으면 해당 `topic_bw_*.log` 에는 측정값이 기록되지 않습니다.
- `ping.log` 에서 `LOST` 가 나타난 시각을 기준으로 `wifi_quality.csv`, `iface_bw.csv`, `node_count.csv` 를 대조해 보면 원인을 좁히기 쉽습니다.

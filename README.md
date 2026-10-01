# Pinky_SLAM-NAV

Pinky SLAM & NAV 관련 Code 공유

Pinky 로봇 2대(`pinky1`, `pinky2`)를 관제 PC에서 원격으로 순찰시키고, 공유 구역(위험 구역)에는 **한 번에 한 대만** 들어가도록 제어하는 ROS 2 코드 모음입니다.

## 시스템 구성

로봇과 관제 PC는 각각 다른 `ROS_DOMAIN_ID`를 사용하며, `domain_bridge`로 필요한 토픽만 주고받습니다.

| 장비 | ROS_DOMAIN_ID | 실행하는 것 |
|---|---|---|
| 관제 PC | 50 | `domain_bridge`, `zone_manager_node`, `pinky_control_client_v2.py` |
| pinky1 | 20 | Nav2, `pinky_patrol_node_pinky1.py` |
| pinky2 | 22 | Nav2, `pinky_patrol_node_pinky2.py` |

```
관제 PC(50) ──[domain_bridge]── pinky1(20)
     │        └─[domain_bridge]── pinky2(22)
     ├─ patrol_cmd (start/stop) ───────────▶ 로봇
     ├─ patrol_status ◀─────────────────────  로봇
     └─ zone_manager (request_entry / grant_entry / notify_exit)
```

## 폴더 구조

```
pinky_slam_nav/
├── bridge_ws/              # 도메인 브릿지 (workspace + 로봇별 설정)
├── my_pinky_package/       # 로봇에서 실행하는 순찰 노드
├── pinky_patrol_cmd/       # 관제 PC용 순찰 명령/모니터링 클라이언트
├── zone_traffic_control/   # 구역 상호 배제(mutex) ROS 2 패키지
├── ros2_network_test/      # 무선 네트워크 진단 스크립트
└── map_view/               # 지도 좌표 확인용 웹 뷰어
```

## 각 폴더 설명 및 실행 방법

### 1. `bridge_ws/` — 도메인 브릿지

서로 다른 도메인(50 ↔ 20/22) 사이에서 토픽을 중계합니다. `src/domain_bridge`는 [ros2/domain_bridge](https://github.com/ros2/domain_bridge) 소스이며, 이 프로젝트에서 직접 작성한 것은 `config/`의 YAML입니다.

| 파일 | 기능 |
|---|---|
| `config/pinky_bridge_pinky1.yaml` | 관제 PC(50) ↔ pinky1(20). 관제 쪽 토픽은 `/pinky1/...`로 remap |
| `config/pinky_bridge_pinky2.yaml` | 관제 PC(50) ↔ pinky2(22). 관제 쪽 토픽은 `/pinky2/...`로 remap |

브릿지 대상 토픽: `patrol_cmd`, `patrol_status`, `cmd_vel`, `initialpose`, `goal_pose`, `scan`, `odom`, `tf`, `tf_static`, `amcl_pose`, `battery_state`, `/zone_manager/*`

**실행 (관제 PC)**

```bash
cd bridge_ws
colcon build
source install/setup.bash

# 로봇마다 터미널을 하나씩 열어서 실행
ros2 run domain_bridge domain_bridge config/pinky_bridge_pinky1.yaml
ros2 run domain_bridge domain_bridge config/pinky_bridge_pinky2.yaml
```

### 2. `my_pinky_package/` — 로봇용 순찰 노드

로봇 안에서 실행되는 노드입니다. `patrol_cmd`로 `start`/`stop` 명령을 받으면 Nav2(`BasicNavigator`)로 waypoint를 순서대로 순찰하고, 진행 상태를 JSON으로 `patrol_status`에 발행합니다. 구역 진입 지점에서는 `ZoneGateClient`로 허가를 받을 때까지 대기합니다.

| 파일 | 기능 |
|---|---|
| `pinky_patrol_node_pinky1.py` | pinky1 경로: `P2 → RED1 → P3 → P6 → RED1 → P1` |
| `pinky_patrol_node_pinky2.py` | pinky2 경로: `RED2 → P3 → P6 → RED2 → P7` |

- 두 파일은 waypoint 목록과 구역 진입/이탈 인덱스만 다르고 구조는 같습니다.
- 마지막 위치는 `~/.pinky_last_pose.json`에 저장되어 재시작 후 initial pose로 사용됩니다.
- 필요 조건: Nav2가 실행 중이어야 하고, `zone_traffic_control` 패키지가 로봇에 설치되어 있어야 합니다.

**실행 (각 로봇)**

```bash
# pinky1
python3 pinky_patrol_node_pinky1.py --ros-args -p robot_id:=pinky1

# pinky2
python3 pinky_patrol_node_pinky2.py --ros-args -p robot_id:=pinky2
```

> `robot_id`는 `zone_traffic_control/config/zone_params.yaml`의 `robot_ids`와 같아야 합니다.

### 3. `pinky_patrol_cmd/` — 관제 PC용 순찰 클라이언트

| 파일 | 기능 |
|---|---|
| `pinky_control_client_v2.py` | 로봇에 `start`/`stop` 명령을 보내거나, `patrol_status`를 구독해 화면에 출력 |
| `README.md` | 코드 해석 (초보자용) |

**실행 (관제 PC)**

```bash
python3 pinky_control_client_v2.py <pinky1|pinky2> <start|stop|monitor>

# 예시
python3 pinky_control_client_v2.py pinky1 start     # 순찰 시작
python3 pinky_control_client_v2.py pinky1 monitor   # 상태 계속 출력 (Ctrl+C로 종료)
python3 pinky_control_client_v2.py pinky1 stop      # 순찰 중지
```

### 4. `zone_traffic_control/` — 구역 상호 배제 패키지

공유 구역에 로봇이 동시에 들어가지 않도록 조정합니다. 구역 안에 로봇이 있으면 다른 로봇은 진입 전 waypoint에서 대기하고, 나오면 대기 중인 로봇에게 자동으로 허가가 갑니다.

| 파일 | 실행 위치 | 기능 |
|---|---|---|
| `zone_manager_node.py` | 관제 PC (1대만) | 점유 상태·대기열 관리, `max_hold_sec` 초과 시 강제 해제 |
| `zone_gate_client.py` | 각 로봇 (patrol 노드가 import) | `wait_for_entry()`로 진입 허가 대기, `notify_exit()`로 이탈 통보 |
| `config/zone_params.yaml` | - | `robot_ids`, `max_hold_sec`(기본 120초) |
| `launch/zone_manager.launch.xml` | - | zone_manager_node 실행용 launch |

**빌드 (관제 PC와 두 로봇 모두)**

```bash
cd <ros2_ws>/src            # 이 폴더를 복사해 둠
cd .. && colcon build --packages-select zone_traffic_control
source install/setup.bash
```

**실행 (관제 PC)**

```bash
ros2 launch zone_traffic_control zone_manager.launch.xml

# 상태 확인: free / occupied_by:pinky1
ros2 topic echo /zone_manager/status
```

자세한 내용은 [zone_traffic_control/README.md](zone_traffic_control/README.md)를 참고하세요.

### 5. `ros2_network_test/` — 네트워크 진단

관제 PC에서 가벼운 부하 토픽을 발행하면서 Wi-Fi 연결 품질(ping 지연, 토픽 수신 주기, 인터페이스 에러/드랍 등)을 로그로 남겨, 로봇 통신이 끊기는 원인을 찾을 때 사용합니다.

| 파일 | 기능 |
|---|---|
| `ros2_net_diag.sh` | 기본 진단 (ping, 부하 토픽 수신 주기, 인터페이스 RX/TX 에러) |
| `ros2_net_diag_v2.sh` | v1 + 실제 bringup 토픽(`/scan`, `/tf` 등) 감시, 디스커버리·시스템 자원 로깅, tcpdump(sudo 필요) |

**실행 (관제 PC)**

```bash
# ./ros2_net_diag.sh <핑키IP> [부하_Hz] [지속시간_분] [무선인터페이스명]
./ros2_net_diag.sh 192.168.0.101 0.1 30 wlan0

# ./ros2_net_diag_v2.sh <핑키IP> [부하_Hz] [지속시간_분] [인터페이스] [감시토픽]
./ros2_net_diag_v2.sh 192.168.0.101 0.1 30 wlan0 "/scan,/tf,/map,/odom"
```

로그는 실행한 위치의 `ros2_net_diag_<날짜_시간>/` 폴더에 저장됩니다. 종료는 `Ctrl+C` 또는 지정 시간 경과 시 자동으로 됩니다.

### 6. `map_view/` — 지도 좌표 뷰어

`my_pinky_map10` 지도(해상도 0.01 m/px, origin `(-0.167, -0.907)`)를 웹에서 보여주는 단일 HTML 파일입니다. 마우스를 올리면 map 좌표(m)와 occupancy(free/occupied/unknown)가 표시되고, 클릭하면 좌표가 기록됩니다. 순찰 waypoint 좌표를 정할 때 사용합니다.

| 파일 | 기능 |
|---|---|
| `pinky_map_viewer.html` | 지도 이미지가 내장된 좌표 뷰어 (별도 서버 불필요) |

**실행**

- 웹에서 바로 보기: [🗺️ Pinky Map Viewer 실행하기](https://htmlpreview.github.io/?https://raw.githubusercontent.com/cdwlsgh1-source/pinky_slam_nav/main/map_view_pc/pinky_map_viewer.html)
- 로컬: `pinky_map_viewer.html`을 브라우저로 열기

## 추돌 방지 실행 (관제 PC)

브릿지와 `zone_manager_node`는 터미널을 나눠서 실행합니다. (사전에 `bridge_ws`, `zone_traffic_control` 빌드 후 `source` 필요)

```bash
# 터미널 1: 도메인 브릿지 (pinky1 + pinky2)
ros2 launch launch/bridges.launch.xml                 # pinky2:=false 로 한 대만 가능

# 터미널 2: zone_manager_node
ros2 launch zone_traffic_control zone_manager.launch.xml
```

순찰 노드는 각 로봇에서 `-p robot_id:=pinky1` / `-p robot_id:=pinky2`로 실행합니다.

## 전체 실행 순서 요약

1. **로봇**: Nav2 bringup 실행 (각 로봇의 도메인 ID 설정)
2. **관제 PC**: `domain_bridge`를 로봇별로 실행 (1번 항목)
3. **관제 PC**: `zone_manager_node` 실행 (4번 항목)
4. **로봇**: 순찰 노드 실행 (2번 항목)
5. **관제 PC**: `pinky_control_client_v2.py`로 `start` / `monitor` / `stop` (3번 항목)

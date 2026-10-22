# Pinky SLAM-NAV

Pinky 로봇 2대(`pinky1`, `pinky2`)를 관제 PC에서 원격 순찰시키고, 공유 구역(위험 구역)에는 한 번에 한 대만 들어가도록 제어하는 ROS 2 코드 모음입니다.

## 주요 기능

- **원격 순찰 명령**: 관제 PC에서 `start` / `stop` / `goto` 명령을 보내고 `patrol_status`를 모니터링 (`pinky_patrol_cmd/`)
- **Nav2 순찰 노드**: 로봇에서 waypoint를 순서대로 순찰하고 진행 상태를 JSON으로 발행 (`my_pinky_package/`)
- **구역 상호 배제**: 위험 구역(P3~P6)에 한 대만 진입. v3는 FIFO 대기열과 대기 슬롯 좌표까지 관리 (`zone_traffic_control/`)
- **도메인 브릿지**: 관제 PC(도메인 100)와 로봇(pinky1=10, pinky2=20, pinky3=30) 사이에서 필요한 토픽만 중계하고 `/pinky1/...`, `/pinky2/...`, `/pinky3/...`로 remap (`domain_bridge/`)
- **관제 웹 GUI**: 지도 위 로봇 위치·배터리, 명령 패널, 비상정지, LiDAR 오버레이, 알람바, 프로세스 시작/정지. ROS 없이 확인하는 `--mock` 모드 지원 (`pinky_web_gui/`)
- **카메라 차선 추종**: CSI 카메라 영상 발행, YOLO 기반 차선 검출, 차선 추종 노드 (`pinky_camera/`)
- **지도 좌표 뷰어**와 **Wi-Fi 진단 스크립트** (`map_view_pc/`, `ros2_network_test/`)

## 시스템 구성

| 장비 | ROS_DOMAIN_ID | 실행하는 것 |
|---|---|---|
| 관제 PC | 100 | `domain_bridge`, `zone_manager_node`, 웹 GUI, `pinky_control_client.py` |
| pinky1 | 10 | Nav2, 순찰 노드, (카메라 사용 시) `camera_node`, `lane_detector_node` |
| pinky2 | 20 | Nav2, 순찰 노드 |
| pinky3 | 30 | Nav2, 순찰 노드 (`pinky_patrol_node_pinky3`, 홈 지점은 임시로 pinky2 와 동일) |

```
관제 PC(100) ──[domain_bridge]── pinky1(10)
     │        ├─[domain_bridge]── pinky2(20)
     │        └─[domain_bridge]── pinky3(30)
     ├─ /<robot>/patrol_cmd (start/stop/goto) ──▶ 로봇
     ├─ /<robot>/patrol_status ◀────────────────  로봇
     └─ /zone_manager/* (request_entry / grant_entry / notify_exit)
```
![시스템 구성도](docs/pinky_architecture_adaptive.svg)

## 설치 방법

검증된 환경: Ubuntu 24.04, ROS 2 `jazzy`, Python 3.12 (관제 PC 기준).

**1. ROS 패키지 빌드** (관제 PC와 각 로봇에서 저장소 루트 기준)

```bash
git clone https://github.com/cdwlsgh1-source/pinky_slam_nav.git
cd pinky_slam_nav
source /opt/ros/jazzy/setup.bash

# 관제 PC
colcon build --packages-select domain_bridge zone_traffic_control
source install/setup.bash

# 로봇 (순찰 노드가 zone_gate_client를 import하므로 zone_traffic_control 필요)
colcon build --packages-select zone_traffic_control my_pinky_package
source install/setup.bash
```

- 로봇에는 Nav2(`nav2_simple_commander`)와 `pinky_interfaces`(`SetLed` 서비스)가 이미 설치되어 있어야 합니다. 이 저장소에는 포함되어 있지 않습니다.
- 카메라 패키지는 `colcon build --packages-select pinky_camera`로 빌드합니다. 로봇에는 `python3-picamera2`, `lane_detector_node`를 돌리는 쪽에는 pip 패키지 `ultralytics`가 필요합니다 (`package.xml` 주석).

**2. 웹 GUI 설치** (관제 PC, `pinky_web_gui/` 폴더에서 한 번만)

```bash
cd pinky_web_gui
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

`rclpy`는 pip가 아니라 ROS 2가 제공하므로 venv를 `--system-site-packages`로 만듭니다.

## 실행 방법

아래 순서로 실행합니다 (`robot_id`는 `zone_traffic_control/config/zone_params.yaml`의 `robot_ids`와 같아야 합니다).

**1. 로봇**: 각 로봇에서 Nav2 bringup 실행

**2. 관제 PC — 도메인 브릿지** (한 대만 띄우려면 `pinky2:=false pinky3:=false`)

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
export ROS_DOMAIN_ID=100
ros2 launch domain_bridge bridges.launch.xml
```

**3. 관제 PC — zone_manager** (새 터미널)

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
export ROS_DOMAIN_ID=100
ros2 launch zone_traffic_control zone_manager.launch.xml
```

**4. 로봇 — 순찰 노드** (각 로봇에서)

```bash
# pinky1 (도메인 10)
ros2 run my_pinky_package pinky_patrol_node_pinky1 --ros-args -p robot_id:=pinky1

# pinky2 (도메인 20)
ros2 run my_pinky_package pinky_patrol_node_pinky2 --ros-args -p robot_id:=pinky2
```

**5. 관제 PC — 명령 또는 웹 GUI**

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=100
python3 pinky_patrol_cmd/pinky_control_client.py pinky1 monitor
```

### 웹 GUI

```bash
cd pinky_web_gui

# ROS 없이 (가짜 로봇 2대)
.venv/bin/python -m backend.main --mock

# 실제 로봇 연결 (브릿지가 먼저 떠 있어야 함)
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=100
.venv/bin/python -m backend.main
```

브라우저에서 <http://localhost:8000/> 을 열고 로그인합니다. 계정은 `backend/auth.py`에 고정되어 있습니다 (`mngr`/`mngr`: 명령 가능, `oper`/`oper`: 보기 전용). 신뢰하는 내부망에서만 사용하세요.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--mock` | 꺼짐 | ROS 없이 가짜 데이터로 실행 |
| `--host` | `127.0.0.1` | 다른 PC에서 접속하려면 `0.0.0.0` |
| `--port` | `8000` | 포트 |
| `--config` | `config/robots.yaml` | 설정 파일 경로 |
| `--no-auth` | 꺼짐 | 로그인 끔 (개발용, 프로세스 제어도 꺼짐) |

전체 옵션은 `.venv/bin/python -m backend.main --help` 또는 [`pinky_web_gui/docs/usage.md`](pinky_web_gui/docs/usage.md)를 참고하세요.

### 대기열 구성

RED POINT(RED*) 대신 앵커/대기슬롯을 씁니다. 이전 세대(RED* 문 방식)는 git 태그 `v2-final` 에 보존되어 있습니다.

```bash
# 관제 PC
ros2 launch zone_traffic_control zone_manager.launch.xml

# 로봇
ros2 run my_pinky_package pinky_patrol_node_pinky1 --ros-args -p robot_id:=pinky1
ros2 run my_pinky_package pinky_patrol_node_pinky2 --ros-args -p robot_id:=pinky2
```

### 카메라 / 차선 추종 (선택)

```bash
# 로봇: 카메라와 차선 검출 (namespace는 pinky1 | pinky2)
ros2 launch pinky_camera camera.launch.xml namespace:=pinky1
ros2 launch pinky_camera lane_detector.launch.xml namespace:=pinky1

# 관제 PC: 차선 추종 (브릿지 설정상 관제 PC에서 실행)
ros2 launch pinky_camera lane_follower.launch.xml namespace:=pinky1
```

> `config/lane_detector_params.yaml`의 `model_path`가 `/home/jinho/dev_ws/best.pt`로 하드코딩되어 있습니다. 실행 전에 실제 경로(저장소의 `pinky_camera/lane_seg_best.pt` 등)로 바꾸세요. 어느 모델이 최종인지는 **[TODO]**.

### 지도 뷰어 / 네트워크 진단

```bash
# 지도 좌표 뷰어: 브라우저로 열기 (서버 불필요)
xdg-open map_view_pc/pinky_map_viewer.html

# 네트워크 진단 (관제 PC): <핑키IP> [부하_Hz] [지속시간_분] [인터페이스] [감시_토픽]
# 인터페이스를 생략하면 무선 인터페이스를 자동 감지합니다.
cd ros2_network_test
chmod +x ros2_net_diag.sh
./ros2_net_diag.sh 192.168.0.101 0.1 30 wlan0 "/scan,/tf,/map,/odom"

# 이상 이력 대시보드 (다른 터미널, ros2_network_test 폴더에서): http://localhost:8088/
python3 net_monitor.py
```

진단 스크립트는 핑·처리량·WiFi·토픽 대역폭과 함께 UDP/TCP/멀티캐스트/유니캐스트 부하(`proto_load.csv`)를 기록하고, `net_monitor.py`는 그 로그에서 이상 시점을 찾아 보여줍니다. 임계값은 `net_monitor.json`, 자세한 내용은 [`ros2_network_test/README.md`](ros2_network_test/README.md)를 참고하세요. 시험할 때는 실제 관제 도메인(100)과 섞이지 않도록 `ROS_DOMAIN_ID=77`, `ROS_LOCALHOST_ONLY=1`을 쓰세요.

## 사용 예시

**순찰 명령 (관제 PC)**

```bash
python3 pinky_patrol_cmd/pinky_control_client.py <pinky1|pinky2> <start|stop|monitor|goto 포인트...>

python3 pinky_patrol_cmd/pinky_control_client.py pinky1 start          # 순찰 시작
python3 pinky_patrol_cmd/pinky_control_client.py pinky1 goto P2 P3 P6  # 지정 지점 순서대로 이동 (내부적으로 goto:P2,P3,P6)
python3 pinky_patrol_cmd/pinky_control_client.py pinky1 monitor        # 상태 출력 (Ctrl+C로 종료)
python3 pinky_patrol_cmd/pinky_control_client.py pinky1 stop           # 순찰 중지
```

**구역 점유 상태 확인**

```bash
ros2 topic echo /zone_manager/status    # free | occupied_by:pinky1
```

**웹 GUI REST API (`--mock` 서버를 띄운 상태)**

```bash
curl -s localhost:8000/api/health

curl -s -c /tmp/pinky.cookie -X POST localhost:8000/api/login \
  -H 'Content-Type: application/json' -d '{"id": "mngr", "password": "mngr"}'
curl -s -b /tmp/pinky.cookie -X POST localhost:8000/api/robots/pinky1/command \
  -H 'Content-Type: application/json' -d '{"cmd": "goto", "points": ["P2", "P3", "P6"]}'
```

**웹 GUI 테스트 (ROS 불필요)**

```bash
cd pinky_web_gui && .venv/bin/python -m pytest tests -q
```

## 환경변수

| 변수 | 값 | 사용처 | 설명 |
|---|---|---|---|
| `ROS_DOMAIN_ID` | 관제 PC `100`, pinky1 `10`, pinky2 `20`, pinky3 `30` | 전체 | 장비별 ROS 도메인. 웹 GUI는 100이 아니면 시작 시 경고 |
| `ROS_LOCALHOST_ONLY` | 설정하지 않음 | 웹 GUI | `1`이면 브릿지 토픽을 못 받는다는 경고 |
| `RMW_IMPLEMENTATION` | `rmw_cyclonedds_cpp` | 로봇 | `processes.yaml`의 `env`로 SSH 실행 시 주입. PC의 CycloneDDS와 맞춰야 함 |
| `CYCLONEDDS_URI` | `file:///home/pinky/.ros/cyclonedds_robot.xml` | 로봇 | 위와 같음. 로봇 터미널 값과 같게 둠 |
| `ROS_AUTOMATIC_DISCOVERY_RANGE` | `SUBNET` | 로봇 | 위와 같음 |
| `ssh.password_env`가 가리키는 변수 | 선택 | 웹 GUI | `processes.yaml` 항목에 변수 **이름**을 적으면 SSH 비밀번호로 사용 (`sshpass` 필요). 기본은 SSH 키 로그인 |
| `ROS_DOMAIN_ID=77` + `ROS_LOCALHOST_ONLY=1` | 시험용 | 테스트 | ROS 경로 검증 테스트 전용 격리 값 (`verify_step4_ros.py`, zone/patrol 테스트) |
| `STEP4_SERVER_LOG` | 파일 경로, 선택 | 테스트 | `verify_step4_ros.py` 디버깅용 서버 로그 파일 |

> ROS 파라미터 `robot_id`(순찰 노드, `-p robot_id:=pinky1`)는 환경변수가 아닙니다. 웹 GUI의 나머지 설정은 `pinky_web_gui/config/*.yaml`에 있습니다.

## 프로젝트 구조

```
pinky_slam_nav/
├── domain_bridge/          # ros2/domain_bridge 소스 + 직접 작성한 launch/config
│   ├── config/             #   pinky_bridge_pinky1.yaml, pinky_bridge_pinky2.yaml
│   └── launch/             #   bridges.launch.xml (pinky1+pinky2 브릿지 실행)
├── my_pinky_package/       # 로봇용 순찰 노드 (pinky_patrol_node_pinky{1,2,3})
├── pinky_patrol_cmd/       # 관제 PC용 CLI (pinky_control_client.py)
├── zone_traffic_control/   # 구역 상호 배제 (zone_manager_node, zone_gate_client)
├── pinky_camera/           # camera_node, lane_detector_node, lane_follower_node, YOLO 모델(.pt)
├── pinky_web_gui/          # 관제 웹 GUI
│   ├── backend/            #   FastAPI + rclpy, 인증, 알람, 프로세스 제어, mock
│   ├── frontend/           #   정적 화면 (빌드 없음)
│   ├── config/             #   robots.yaml, processes.yaml, points.yaml, zone.yaml, alarms.yaml
│   ├── docs/               #   usage.md, interfaces.md, 체크리스트, PRD
│   └── tests/              #   pytest, verify_*.py
├── map_view_pc/            # 지도(my_pinky_map10.yaml/.pgm), 좌표 뷰어 HTML
└── ros2_network_test/      # 네트워크 진단 스크립트(ros2_net_diag.sh)와 이상 이력 대시보드(net_monitor.py)
```

## 기여 방법

저장소 이력에서 확인되는 관례입니다. 별도의 기여 가이드 문서는 없으며, 그 밖의 규칙은 **[TODO]**.

1. `main`에서 기능 브랜치를 만듭니다 (예: `feature/patrol-v1.4.0`, `refactor/drop-version-suffix`).
2. 커밋 메시지는 `패키지명: 변경 내용` 형식의 한국어 한 줄로 씁니다 (예: `my_pinky_package: v1.4.0 S1 파라미터 레이어 추가 (pinky1)`).
3. 변경한 부분의 테스트를 실행합니다. 웹 GUI는 ROS 없이 돌아갑니다.
   ```bash
   cd pinky_web_gui && .venv/bin/python -m pytest tests -q
   ```
   ROS 경로를 검증할 때는 실제 도메인을 건드리지 않도록 `ROS_DOMAIN_ID=77`과 `ROS_LOCALHOST_ONLY=1`로 격리해서 실행하세요.
4. GitHub에서 Pull Request를 열어 `main`에 병합합니다.

`domain_bridge/`는 [ros2/domain_bridge](https://github.com/ros2/domain_bridge) 소스를 포함하므로, 이 폴더의 기여 규칙은 `domain_bridge/CONTRIBUTING.md`를 따릅니다.

## 라이선스

저장소 루트에는 `LICENSE` 파일이 없어 전체 라이선스는 **[TODO]** 입니다. 패키지별 `package.xml` 선언은 다음과 같습니다.

| 패키지 | 라이선스 |
|---|---|
| `domain_bridge` | Apache 2.0 (`domain_bridge/LICENSE` 포함) |
| `zone_traffic_control` | Apache-2.0 |
| `pinky_camera` | Apache-2.0 |
| `my_pinky_package` | **[TODO]** (`package.xml`에 `TODO: License declaration`로 남아 있음) |
| `pinky_web_gui`, `pinky_patrol_cmd`, `map_view_pc`, `ros2_network_test` | **[TODO]** (선언 없음) |

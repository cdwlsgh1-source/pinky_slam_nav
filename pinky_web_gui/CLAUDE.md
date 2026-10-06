# CLAUDE.md — pinky_slam_nav

Pinky 로봇 2대(`pinky1`, `pinky2`)를 관제 PC에서 원격 순찰시키고, 공유 구역(위험 구역)에는 한 번에 한 대만 들어가게 하는 ROS 2 코드 모음이다. 관제 PC 웹 GUI(`pinky_web_gui/`)는 이 위에 얹는 새 계층이다 (`Pinky 관제주행 웹 GUI — 단계별 PRD.md` 참고).

> 표기 규칙: 이 파일은 `pinky_web_gui/`에 있지만, 본문의 경로와 `파일:줄`은 **레포 루트 `pinky_slam_nav/` 기준**이다 (예: `README.md:13` = `pinky_slam_nav/README.md`의 13줄). 작성 시점은 커밋 `f2e3e6a` + 미커밋 변경 작업 트리다. 확인하지 못한 것은 **확인 필요**로 적었다.

## 1. 도메인 ID

| 장비 | ROS_DOMAIN_ID | 실행하는 것 | 근거 |
|---|---|---|---|
| 관제 PC | 50 | domain_bridge 2개, `zone_manager_node`, (GUI 백엔드) | `README.md:13`, `launch/bridges.launch.xml:7-10` |
| pinky1 | 20 | Nav2, 순찰 노드, lane_detector | `README.md:14`, `domain_bridge_pc/config/pinky_bridge_pinky1.yaml:9-13` |
| pinky2 | 22 | Nav2, 순찰 노드 | `README.md:15`, `domain_bridge_pc/config/pinky_bridge_pinky2.yaml:5-9` |

- 로봇 쪽 토픽은 원래 이름(`/patrol_cmd`, `/amcl_pose` …)이고, 브리지가 관제 PC(50)에서 `/pinky1/...`, `/pinky2/...`로 remap한다 (`pinky_bridge_pinky1.yaml:3-5` 주석, `:9-19`).
- 브리지 대상이 아닌 토픽은 관제 PC에서 볼 수 없다. 카메라(`/{id}/camera/image_raw/compressed`)는 두 YAML 어디에도 없다.
- `/zone_manager/*`는 로봇별 remap 없이 한 토픽을 두 로봇이 공유한다 (`pinky_bridge_pinky1.yaml:96-120`).

## 2. 폴더별 역할

| 폴더 | 역할 |
|---|---|
| `my_pinky_package/` | 로봇에서 실행하는 순찰 노드. 현재 쓰는 파일은 `pinky_patrol_node_pinky{1,2}_v2.py` (`start`/`stop`/`goto` 지원). 흐름도는 `PinkyPatrolNode.html` |
| `zone_traffic_control/` | 구역 상호 배제 패키지. 관제 PC의 `zone_manager_node`, 로봇이 import하는 `zone_gate_client` |
| `domain_bridge_pc/` | `config/`의 브리지 YAML 2개가 직접 작성한 것. `src/domain_bridge`는 upstream 소스 (수정 금지) |
| `launch/bridges.launch.xml` | 브리지 2개를 한 번에 실행 (`pinky2:=false`로 한 대만 가능) |
| `pinky_patrol_cmd/` | 관제 PC용 CLI 클라이언트 `pinky_control_client_v2.py` (`start`/`stop`/`goto`/`monitor`) |
| `pinky_camera/` | `camera_node`, `lane_detector_node`(로봇), `lane_follower_node`(관제 PC), 모델 `best.pt`, `lane_seg_best.pt` |
| `map_view_pc/` | 지도 `my_pinky_map10.yaml/.pgm`, 좌표 뷰어 `pinky_map_viewer.html` |
| `ros2_network_test/` | Wi-Fi 진단 스크립트 |
| `pinky_web_gui/` | (신규) 웹 GUI. **이 `CLAUDE.md`가 있는 폴더.** 백엔드, 프론트엔드, `docs/interfaces.md` |

주의: 루트 `README.md`는 `bridge_ws/`(`:29,:53`), `map_view/`(`:34`)라고 적혀 있지만 실제 폴더는 `domain_bridge_pc/`, `map_view_pc/`다. 이 파일에서는 실제 이름을 쓴다.

지도: 해상도 0.010 m/px, origin `[-0.167, -0.907, 0]` (`map_view_pc/my_pinky_map10.yaml:3-4`).

## 3. 전체 실행 순서

1. **로봇**: Nav2 bringup (각 로봇의 `ROS_DOMAIN_ID` 설정)
2. **관제 PC**: 브리지 `ros2 launch launch/bridges.launch.xml`
3. **관제 PC**: `ros2 launch zone_traffic_control zone_manager.launch.xml`
4. **로봇**: `ros2 run my_pinky_package pinky_patrol_node_pinky1_v2 --ros-args -p robot_id:=pinky1` (pinky2도 동일, `my_pinky_package/setup.py:33-34`)
5. **관제 PC**: `python3 pinky_patrol_cmd/pinky_control_client_v2.py <pinky1|pinky2> <start|stop|monitor|goto P2 P3 ...>` 또는 웹 GUI

(`README.md:183-189` 기준. `robot_id`는 `zone_traffic_control/config/zone_params.yaml:4`의 `robot_ids`와 같아야 한다.)

**`start` 명령이 로봇까지 가는 경로**: 관제 PC가 `/pinky1/patrol_cmd`(String `"start"`)를 발행(도메인 50) → `domain_bridge`가 50→20으로 중계하며 `/patrol_cmd`로 remap (`pinky_bridge_pinky1.yaml:9-13`) → 로봇의 `PinkyPatrolNode._cmd_cb`(`pinky_patrol_node_pinky1_v2.py:242`)가 작업 스레드를 시작 → 상태는 `/patrol_status`(로봇) → 브리지 → `/pinky1/patrol_status`(관제 PC)로 돌아온다 (`:15-19`).

## 4. zone manager 버전 (결정 기록)

현재 구성은 **접미사 없는 쪽(PRD의 "v1")** 이다.
- 순찰 노드 4개(`pinky_patrol_node_pinky1.py:12`, `pinky2.py:12`, `pinky1_v2.py:47`, `pinky2_v2.py:47`)가 모두 `zone_traffic_control.zone_gate_client`를 import한다. 생성자 시그니처도 `ZoneGateClient(node, robot_id)`로 순찰 노드(`pinky1_v2.py:136`)와 맞다.
- 이 클라이언트가 쓰는 토픽 `/zone_manager/request_entry`, `notify_exit`, `grant_entry` (`zone_gate_client.py:47-52`)는 두 브리지 YAML에 모두 있다 (pinky1 `:104-119`, pinky2 `:80-95`).
- 매니저는 `/zone_manager/status`를 발행한다 (`zone_manager_node.py:64`). 값은 `free` 또는 `occupied_by:<id>` (`:156`). 이 토픽은 관제 PC 안에서만 쓰이므로 브리지에 없다.
- zone manager v2(`zone_manager_node_v2.py`, `zone_gate_client_v2.py`, `zone_manager_v2.launch.xml`)는 시도했으나 삭제하고 접미사 없는 매니저를 쓰기로 **확정**했다 (사용자 결정). 작업 트리에서는 삭제된 상태(미커밋)이고 HEAD에만 남아 있다. v2의 토픽 `/zone_manager_v2/*`는 브리지 YAML에 없다. `zone_params_v2.yaml`은 아직 남아 있다.
- 이름 혼동 주의: `zone_manager_node.py:3`의 docstring은 `(v2 - self-report 방식)`, `zone_gate_client.py:3`은 `(v2)`라고 적혀 있다. 이 문서에서는 접미사 없는 쪽을 "기존 zone manager"라고 부른다. `pinky_patrol_node_*_v2.py`의 `_v2`는 goto 지원 현재 버전이라는 뜻이며 zone v2와 무관하다. 순찰 노드는 `_v2` 파일이 맞다 (사용자 확인).

## 5. 환경 (이 PC에서 확인한 값)

- Ubuntu 24.04.5 LTS, ROS 2 `jazzy` (`$ROS_DISTRO`), Python 3.12.3
- ROS 2는 `/opt/ros/jazzy`에 설치돼 있지만 셸이 자동으로 source하지 않는다 (`$ROS_DISTRO`가 비어 있을 수 있음). `source /opt/ros/jazzy/setup.bash` 후에 `rclpy` import가 된다 (확인함).
- **가상환경**: `pinky_web_gui/.venv` (시스템 `python3` 3.12.3, `--system-site-packages`). Ubuntu 24.04가 시스템 Python에 `pip install`을 막고(PEP 668), `rclpy`는 apt 설치본이라 시스템 패키지를 볼 수 있어야 하기 때문이다. `.venv/`는 루트 `.gitignore`에 있다.
- venv 안 설치 패키지: `fastapi` 0.142.2, `uvicorn[standard]` 0.54.0 (`websockets` 17.2, `uvloop`, `httptools`, `watchfiles` 포함), `pydantic` 2.13.5, `starlette` 1.7.0. `PyYAML` 6.0.1은 시스템 패키지를 쓴다. WebSocket(`/ws`)에 `websockets`가 필요하다. 이 환경에서 `rclpy`, `fastapi`, `uvicorn`, `websockets`가 한 인터프리터에서 import되는 것까지 확인했고, 실제 `/ws` 연결은 **확인 필요**.
- **실행 순서** (순서가 바뀌면 `PYTHONPATH` 때문에 venv 패키지가 가려질 수 있다):
  ```bash
  source /opt/ros/jazzy/setup.bash
  export ROS_DOMAIN_ID=50      # mock 모드에서는 불필요
  source pinky_web_gui/.venv/bin/activate
  ```
- 의존성은 `pinky_web_gui/requirements.txt`에 직접 쓰는 것만 적는다 (`pip freeze`에는 시스템 패키지가 섞이므로). 새 venv는 `pip install -r requirements.txt`로 만든다.
- 로봇 쪽 ROS 배포판: **확인 필요** (이 PC에서는 알 수 없음)

## 6. 코딩 규칙

- 주석과 docstring은 **한국어**로 쓴다. 기존 파일처럼 "왜 이렇게 했는지"를 적는다.
- 주변 코드의 스타일을 따른다. 순찰 노드는 `# ====` 섹션 배너로 구역을 나눈다.
- **기존 파일은 수정하지 않는다.** 대상: `my_pinky_package/`, `zone_traffic_control/`, `domain_bridge_pc/`, `launch/`, `pinky_camera/`, `pinky_patrol_cmd/`, `map_view_pc/`, `ros2_network_test/`, 루트 `README.md`. 새 코드는 `pinky_web_gui/`에만 만든다.
- 이미 있는 미커밋 변경(v2 zone 파일 삭제, `zone_traffic_control/setup.py`)은 건드리지 않는다. 변경 여부를 `git diff`로 볼 때 이 변경을 기준선으로 본다.
- 로봇 이름(`pinky1`, `pinky2`)을 코드에 하드코딩하지 않는다. 설정 파일(`pinky_web_gui/config/robots.yaml`)에서 읽는다.
- 로봇으로 나가는 명령은 허용 목록(`start`, `stop`, `goto:<허용 포인트>`)만 통과시킨다. 허용 포인트는 `docs/interfaces.md` 참고.
- 실제 로봇 연결 테스트는 사용자가 직접 한다. mock 모드로 먼저 확인한다.
- 포인트 좌표, 토픽 이름은 문서에서 베끼지 말고 `docs/interfaces.md`와 실제 파일을 근거로 쓴다.

## 7. 알려진 함정

- **goto 검증 FAILED**: 순찰 노드는 작업 중인지 확인하기 *전에* goto 입력을 검증한다 (`pinky1_v2.py:265-279`). 작업 중에 빈/알 수 없는/금지 포인트를 보내면 `FAILED`가 발행되어 진행 중인 작업의 상태처럼 보일 수 있다. 백엔드에서 사전 검증한다.
- **`start`/`goto` 무시 시 무응답**: 이미 작업 중이면 경고 로그만 남고 `patrol_status`는 발행되지 않는다 (`:253`, `:280`). `stop`도 작업 중이 아니면 무응답 (`:292`).
- **보정 중 stop**: 첫 작업의 초기 위치 보정 회전(4초)은 `stop`을 확인하지 않는다 (`:376-378`, `:398`).
- **늦게 접속한 구독자**: `patrol_status`의 `IDLE`은 시작 시 한 번만 발행(`:155`), `/zone_manager/status`는 상태가 바뀔 때만 발행(`zone_manager_node.py:134,140`). 처음 접속한 GUI는 현재 값을 모를 수 있다. 실제 QoS 동작은 **확인 필요**.
- **`waypoint`/`detail` 의미**: `start`와 `goto`에서 다르다. `docs/interfaces.md` 4절 참고.
- **pinky2 경로**: `start`는 5개 `RED2IN(0) → P3(1) → P6(2) → RED2OUT(3) → P7(4)`, 진입/이탈 인덱스 0/3, `DONE.waypoint`=5, `goto` 홈도 P7이다 (`pinky2_v2.py:84-90,98-99,109`). pinky1은 6개(`P2` 경유)이고 인덱스 1/4, `DONE.waypoint`=6이다. 로봇별 경로를 설정으로 가져야 한다. 이 파일의 주석은 코드(`POINTS`)에 맞게 수정됐다 (사용자 확인).
- **하드코딩 절대 경로**: `pinky_camera/config/lane_detector_params.yaml:4`(`/home/jinho/dev_ws/best.pt`, 이 PC에 파일 없음), `pinky_camera/pinky_camera/lane_detector_node.py:235-236`(`/home/jinho/dev_ws/pinky_slam_nav/pinky_camera/...pt`). 홈 상대 경로 `~/.pinky_last_pose.json`은 순찰 노드 6개 파일에 있다 (`pinky1_v2.py:64`, `pinky2_v2.py:64`, `pinky_patrol_node_pinky1.py:29`, `pinky2.py:29`, `my_pinky_patrol.py:15`, `pinky_patrol.py:15`). 수정은 하지 않는다.
- **setup.py**: `my_pinky_package/setup.py:30`의 `pinky_patrol_node_zone_pinky1`은 가리키는 모듈이 없지만 빌드에 영향이 없다 (사용자 확인). 수정하지 않는다.
- **위험 구역 = P3, P4, P5, P6** (사용자 확인). 영역 좌표는 코드에 없고 `ZONE_POINTS`로만 정의된다 (`docs/interfaces.md` 5-1절).

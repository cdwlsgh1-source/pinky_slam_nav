# CLAUDE.md — pinky_slam_nav

Pinky 로봇 2대(`pinky1`, `pinky2`)를 관제 PC에서 원격 순찰시키고, 공유 구역(위험 구역)에는 한 번에 한 대만 들어가게 하는 ROS 2 코드 모음이다. 관제 PC 웹 GUI(`pinky_web_gui/`)는 이 위에 얹는 새 계층이다 (`docs/prd/Pinky 관제주행 웹 GUI — 단계별 PRD.md` 참고).

> 표기 규칙: `파일:줄`은 작성 시점(2026-10-14, 커밋 `cf3fbcb` 기준) 작업 트리 기준이다. 확인하지 못한 것은 **확인 필요**로 적었다.

## 0. 문서 지도

매 세션에 전부 읽히므로 **안 읽으면 실수하는 규칙만** 둔다. 나머지는 필요할 때 읽는다.

| 알고 싶은 것 | 문서 |
|---|---|
| 알람바 (알람 코드, 이력 `data/alarms/`) | `docs/interfaces.md` 6-7절, 이력 필드·타입은 `docs/reference/alarm-history-format.md` |
| 설치·실행 / 옵션·화면·인증·프로세스 제어 | `README.md` / `docs/usage.md` |
| 토픽, 명령, 상태 JSON, API (사실의 단일 출처) | `docs/interfaces.md` |
| 알려진 함정 (goto 검증, 무응답, pinky2 인덱스 불일치, 하드코딩 경로 …) | `docs/reference/known-pitfalls.md` |
| 결정 기록 (zone manager 버전, 전체 시작 순서) | `docs/reference/decisions.md` |
| 환경 값 (OS, ROS, Python, 패키지) | `docs/reference/environment.md` |
| 실물 확인 절차 (사용자 수행) | `docs/checklists/README.md` (파일별 대상 기능 표) |
| 단계별 계획서(PRD) | `docs/prd/` (네트워크 진단·편집 툴은 미착수), 대기열 v3 는 `../my_pinky_package/docs/prd/` |

문서 작성 규칙: (1) 한 사실은 한 곳에만 쓰고 나머지는 링크한다. (2) 이 파일에는 규칙만 두고 이유와 근거는 `docs/reference/` 로 보낸다. (3) PRD 는 계획이고 README 는 현황이라 섞지 않는다. (4) 문서 맨 위에 역할 한 줄. (5) `파일:줄` 근거는 함수·상수 이름도 함께 적는다. (6) 이 파일은 9 KB 를 넘기지 않는다 (한글이라 바이트가 크다).

## 1. 도메인 ID

| 장비 | ROS_DOMAIN_ID | 실행하는 것 | 근거 |
|---|---|---|---|
| 관제 PC | 100 | domain_bridge 3개, `zone_manager_node`, (GUI 백엔드) | `README.md:13`, `domain_bridge/launch/bridges.launch.xml:7-10` |
| pinky1 | 10 | Nav2, 순찰 노드, lane_detector | `README.md:14`, `domain_bridge/config/pinky_bridge_pinky1.yaml:9-13` |
| pinky2 | 20 | Nav2, 순찰 노드 | `README.md:15`, `domain_bridge/config/pinky_bridge_pinky2.yaml:5-9` |

- 로봇 쪽 토픽은 원래 이름(`/patrol_cmd`, `/amcl_pose` …)이고, 브리지가 관제 PC(100)에서 `/pinky1/...`, `/pinky2/...`로 remap한다 (`pinky_bridge_pinky1.yaml:3-5` 주석, `:9-19`).
- 브리지 대상이 아닌 토픽은 관제 PC에서 볼 수 없다. 카메라(`/{id}/camera/image_raw/compressed`)는 두 YAML 어디에도 없다.
- `/zone_manager/*`는 로봇별 remap 없이 한 토픽을 두 로봇이 공유한다 (`pinky_bridge_pinky1.yaml:96-120`).

## 2. 폴더별 역할

| 폴더 | 역할 |
|---|---|
| `my_pinky_package/` | 로봇에서 실행하는 순찰 노드. GUI 가 맞춘 것은 `pinky_patrol_node_pinky{1,2,3}.py` (`start`/`stop`/`goto`, 대기열 방식) |
| `zone_traffic_control/` | 구역 상호 배제 패키지. 관제 PC의 `zone_manager_node`(대기열), 로봇이 import하는 `zone_gate_client` |
| `domain_bridge/` | upstream 소스에 `launch/bridges.launch.xml`(브리지 3개 실행, `pinky2:=false` 처럼 일부만 가능), `config/pinky_bridge_pinky{1,2,3}.yaml`(직접 작성)을 추가한 패키지. 나머지는 수정 금지 |
| `pinky_patrol_cmd/` | 관제 PC용 CLI 클라이언트 `pinky_control_client.py` (`start`/`stop`/`goto`/`monitor`) |
| `pinky_camera/` | `camera_node`, `lane_detector_node`(로봇), `lane_follower_node`(관제 PC), 모델 `best.pt`, `lane_seg_best.pt` |
| `map_view_pc/` | 지도 `my_pinky_map10.yaml/.pgm`, 좌표 뷰어 `pinky_map_viewer.html` |
| `ros2_network_test/` | Wi-Fi 진단 스크립트 |
| `pinky_web_gui/` | 웹 GUI. `backend/`(FastAPI+rclpy, 인증, 프로세스 제어), `frontend/`, `config/`(`robots.yaml`, `processes.yaml` 등), `docs/`(`interfaces.md`, 실물 확인 체크리스트), `tests/`. 사용법은 `pinky_web_gui/README.md` |

주의: 루트 `README.md`는 `bridge_ws/`(`:29,:53`), `map_view/`(`:34`)라고 적혀 있지만 실제 폴더는 `domain_bridge/`, `map_view_pc/`다. 이 파일에서는 실제 이름을 쓴다.

지도: 해상도 0.010 m/px, origin `[-0.167, -0.907, 0]` (`map_view_pc/my_pinky_map10.yaml:3-4`).

## 3. 전체 실행 순서

1. **로봇**: Nav2 bringup (각 로봇의 `ROS_DOMAIN_ID` 설정)
2. **관제 PC**: 브리지 `ros2 launch domain_bridge bridges.launch.xml`
3. **관제 PC**: `ros2 launch zone_traffic_control zone_manager.launch.xml`
4. **로봇**: `ros2 run my_pinky_package pinky_patrol_node_pinky1 --ros-args -p robot_id:=pinky1` (pinky2도 동일, `my_pinky_package/setup.py:33-34`)
5. **관제 PC**: `python3 pinky_patrol_cmd/pinky_control_client.py <pinky1|pinky2> <start|stop|monitor|goto P2 P3 ...>` 또는 웹 GUI

웹 GUI 시스템 패널의 **전체 시작** 순서는 위와 다르다: 도메인 브릿지 → bringup → map → zone_manager → 순찰 노드 (이유: `docs/reference/decisions.md`).

(`README.md:183-189` 기준. `robot_id`는 `zone_traffic_control/config/zone_params.yaml:4`의 `robot_ids`와 같아야 한다.)

순찰 노드는 문(RED*)이 없는 대기열 방식이다. 이전 세대(문 방식, `_v2`)는 git 태그 `v2-final` 에 있다. GUI 문서 일부(`docs/interfaces.md`, `docs/reference/`)는 아직 문 방식 기준이니 코드와 다르면 코드를 믿는다.

**`start` 명령이 로봇까지 가는 경로**: 관제 PC가 `/pinky1/patrol_cmd`(String `"start"`)를 발행(도메인 100) → `domain_bridge`가 100→10으로 중계하며 `/patrol_cmd`로 remap (`pinky_bridge_pinky1.yaml:9-13`) → 로봇의 `PinkyPatrolNode._cmd_cb`(`pinky_patrol_node_pinky1.py:242`)가 작업 스레드를 시작 → 상태는 `/patrol_status`(로봇) → 브리지 → `/pinky1/patrol_status`(관제 PC)로 돌아온다 (`:15-19`).

## 6. 코딩 규칙

- 주석과 docstring은 **한국어**로 쓴다. 기존 파일처럼 "왜 이렇게 했는지"를 적는다.
- 주변 코드의 스타일을 따른다. 순찰 노드는 `# ====` 섹션 배너로 구역을 나눈다.
- **기존 파일은 수정하지 않는다.** (예외: 브리지 yaml 토픽 블록 추가, `setup.py` entry point.) 대상: `my_pinky_package/`, `zone_traffic_control/`, `domain_bridge/`, `pinky_camera/`, `pinky_patrol_cmd/`, `map_view_pc/`, `ros2_network_test/`, 루트 `README.md`. 새 코드는 `pinky_web_gui/`에만 만든다.
- 파일 이름에 `_v2`, `_v3` 같은 버전을 붙이지 않는다. 버전은 git 태그·브랜치로 관리한다 (이전 세대는 태그 `v2-final`). 미커밋 변경은 사용자 작업이니 되돌리거나 커밋하지 않는다.
- 로봇 이름(`pinky1`, `pinky2`)을 코드에 하드코딩하지 않는다. 설정 파일(`pinky_web_gui/config/robots.yaml`)에서 읽는다.
- 로봇으로 나가는 명령은 허용 목록(`start`, `stop`, `goto:<허용 포인트>`)만 통과시킨다. 허용 포인트는 `docs/interfaces.md` 참고.
- 실제 로봇 연결 테스트는 사용자가 직접 한다. mock 모드로 먼저 확인한다.
- 화면 문구는 한국어, 메뉴·탭·버튼·칩 이름만 영문이다 (알람바 PRD 결정 22).
- PRD 완료 기준: 자동 검증 통과 `[x]`, 실물 확인 대기 `[ ]`. 실로봇에서 안 해 본 것을 `[x]` 로 쓰지 않는다.
- ROS 경로 검증은 `ROS_DOMAIN_ID=77`, `ROS_LOCALHOST_ONLY=1` 로만 한다. 이 PC 의 셸 기본 도메인(100) 은 실제 관제 도메인이다.
- GUI 의 프로세스 제어는 `config/processes.yaml` 에 적힌 목록만 실행한다 (브라우저가 보낸 값은 id 로만 쓰고 명령으로 쓰지 않는다). SSH 는 키 로그인만 쓰고 비밀번호를 설정 파일에 두지 않는다.
- 로그인 계정은 `backend/auth.py` 에 고정이다 (`mngr` manager, `oper` operator). 환경 변수나 설정 파일로 바꾸지 않는다.
- 포인트 좌표, 토픽 이름은 문서에서 베끼지 말고 `docs/interfaces.md`와 실제 파일을 근거로 쓴다.

## 7. 꼭 기억할 함정 (전체는 `docs/reference/known-pitfalls.md`)

- 순찰 노드는 작업 중이어도 goto 입력부터 검증해 `FAILED` 를 낸다. 백엔드에서 먼저 검증한다.
- `start`/`goto`/`stop` 은 무시될 때 **무응답**이다. 응답 여부는 `patrol_status` 로만 판단한다.
- `pinky2_v2.py` 의 `ZONE_ENTRY_INDEX`/`ZONE_EXIT_INDEX` 가 5개 경로와 맞지 않는다 (올바른 값 0/3). 코드는 수정하지 않았고 사용자가 고친다. 로봇별 경로는 설정으로 가진다.
- 하드코딩 절대 경로와 `~/.pinky_last_pose.json` 은 수정하지 않는다. 위험 구역은 P3~P6 이다.
- GUI 서버를 `kill -9` 하면 자식 프로세스가 남는다. SSH 비대화형 셸은 `~/.bashrc` 를 읽지 않으므로 DDS 환경은 `processes.yaml` 의 `env`/`source` 에 적는다.
- GUI 프로세스 제어는 실제 로봇에서 한 번도 실행하지 않았다 (mock 만). 절차: `docs/checklists/step6_process_control_real_checklist.md`.

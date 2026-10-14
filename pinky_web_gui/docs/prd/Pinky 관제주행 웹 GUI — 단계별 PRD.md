# Pinky 관제주행 웹 GUI — 단계별 PRD

Oct 6, 2026 · @JinHo

## 0. 공통 개요

관제 PC의 웹 브라우저에서 pinky1·pinky2의 위치와 상태를 보고, 순찰 명령(start / stop / goto)을 내리는 GUI를 6단계(Step 0\~5)로 나눠 만든다. 기존 ROS 2 코드(순찰 노드, zone manager)는 최대한 건드리지 않고, 이미 있는 토픽 규약 위에 웹 계층만 얹는다.

### 현재 시스템 (레포 `pinky_slam_nav` 기준)

- 기준 코드는 `main` 브랜치의 커밋 `f2e3e6a`이다.
- 관제 PC는 `ROS_DOMAIN_ID=50`, pinky1은 20, pinky2는 22이며 `domain_bridge`가 토픽을 중계한다.
- 관제 PC 쪽 토픽 이름은 `/pinky1/...`, `/pinky2/...` 네임스페이스로 remap되어 있다.
- 순찰 명령은 `std_msgs/String` 문자열 프로토콜이다: `start`, `stop`, `goto:<포인트>[,<포인트>,...]`. `goto`는 여러 지점을 순서대로 방문하고 홈으로 복귀한다 (예: `goto:P2,P3,P6`).
- 로봇 상태는 `patrol_status`에 JSON 문자열로 온다: `{"state", "waypoint", "detail", "time"}`. `goto` 중에는 `waypoint`가 경로 안 순번, `detail`이 지점 이름이다.
- 위험 구역 조정은 v1 구조다. 순찰 노드(`_v2` 파일 포함)가 모두 v1 `zone_gate_client`를 쓰고, 관제 PC의 `zone_manager_node`가 `/zone_manager/status`를 발행한다.
- 지도는 `my_pinky_map10` (해상도 0.01 m/px, origin (-0.167, -0.907)). 좌표 뷰어 `pinky_map_viewer.html`이 이미 있다.

| 토픽 (관제 PC 기준) | 타입 | 방향 | 용도 |
| --- | --- | --- | --- |
| `/{id}/patrol_cmd` | String | PC → 로봇 | `start` / `stop` / `goto:P2,P3,P6` |
| `/{id}/patrol_status` | String(JSON) | 로봇 → PC | 순찰 상태 (IDLE, MOVING, WAITING\_ZONE …) |
| `/{id}/amcl_pose` | PoseWithCovarianceStamped | 로봇 → PC | 지도 위 로봇 위치 |
| `/{id}/battery_state` | BatteryState | 로봇 → PC | 배터리 |
| `/{id}/scan` | LaserScan | 로봇 → PC | LiDAR (Step 4) |
| `/{id}/cmd_vel` | Twist | PC → 로봇 | 속도 명령 (비상정지 0 속도, Step 4) |
| `/zone_manager/status` | String | PC 내부 | `free` / `occupied_by:<id>` |

`{id}`는 `pinky1` 또는 `pinky2`. zone manager는 관제 PC에서 실행되므로 브리지 없이 바로 구독한다. 카메라 토픽(`/{id}/camera/image_raw/compressed`)은 pinky1 브리지 설정에서 확인되지 않아, Step 5에서 브리지 추가가 필요하다.

### 아키텍처 결정

- **백엔드**: 관제 PC(도메인 50)에서 Python FastAPI + `rclpy` 노드 하나로 실행한다. ROS 토픽을 구독해 WebSocket(`/ws`)으로 브라우저에 밀어주고, 브라우저의 명령은 REST(`/api/...`)로 받아 ROS 토픽으로 발행한다.
- **프론트엔드**: 프레임워크 없이 정적 HTML + JavaScript. 기존 `pinky_map_viewer.html`을 재사용한다. 빌드 도구(npm)가 필요 없어야 Ubuntu에서 바로 실행된다.
- **위치**: 레포에 `pinky_web_gui/` 폴더를 새로 만든다 (`backend/`, `frontend/`, `config/`).
- **rosbridge를 쓰지 않는 이유**: 명령이 문자열 프로토콜이고 로봇 2대의 네임스페이스·상태를 합쳐야 해서, 백엔드가 번역과 검증을 맡는 편이 안전하다.

&#91;embedded content: 구조도 · 브라우저, 백엔드, 도메인 브리지, 로봇 2대\]

백엔드는 zone manager의 상태를 직접 구독하고, 로봇과는 기존 `domain_bridge`를 거쳐 통신한다.

### 공통 원칙

1. 기존 순찰 노드와 zone manager 코드는 수정하지 않는다. GUI는 기존 토픽 규약의 클라이언트로만 동작한다.
2. 로봇으로 나가는 명령은 백엔드에서 허용 목록(`start`, `stop`, `goto:<허용 포인트>`)만 통과시킨다.
3. 각 Step은 로봇 없이도 mock 데이터로 테스트할 수 있어야 한다.
4. Step의 완료 기준을 통과한 뒤에 다음 Step으로 넘어간다.

### 로드맵

> 현황 (2026-10-14): Step 0~4, 6 구현 완료, Step 5 는 일부만 구현. 단위·mock 테스트 567개 통과(`.venv/bin/python -m pytest tests`). **실로봇 확인은 아직 하지 않았다** (`../checklists/README.md`). 아래 각 Step 의 완료 기준은 `[x]` = 자동 검증(mock·격리 도메인) 통과, `[ ]` = 사용자 실물 확인 대기이거나 미구현이다.
> 이 PRD 의 구역 전제(v1 zone manager, RED1IN/OUT 문)는 순찰 노드 v3(`../../../my_pinky_package/docs/prd/Pinky 위험구역 대기열 — 단계별 PRD.md`) 이전 기준이다. v3 는 문(RED*)을 없애고 앵커 진입·출구 반납으로 바꿨고 GUI 연동은 그 PRD 의 Step Q6 에서 한다. 그때까지 이 PRD 의 RED* 관련 요구(FR3-12, FR4-1, FR4-4)는 v2 순찰 노드 기준으로만 맞다.


1. **Step 0** 레포 분석, `CLAUDE.md` 작성, v1/v2 zone manager 확정
2. **Step 1** 백엔드 뼈대: 상태 구독과 WebSocket 중계
3. **Step 2** 지도 화면: 맵과 로봇 2대 위치 표시
4. **Step 3** 명령 패널: start / stop / goto
5. **Step 4** 구역 상태, 비상정지, 경로와 LiDAR 시각화
6. **Step 5** 카메라, 로그와 이력, 인증 (선택)
7. **Step 6** 인증과 프로세스 제어: 시스템 패널로 브릿지·zone_manager·로봇 bringup/map/순찰 노드를 켜고 끈다 (하위 Step 6-1 ~ 6-8, 개발 중에 추가됨)

## Step 0. 레포 분석과 CLAUDE.md

코드를 한 줄도 쓰기 전에, Claude Code가 이 레포의 구조와 토픽 규약을 정확히 이해하도록 `CLAUDE.md`와 인터페이스 문서를 만든다. 이후 모든 Step의 품질이 이 단계에 달려 있다.

### 범위

- **포함**: `CLAUDE.md` 작성, 인터페이스 문서 작성, v1/v2 zone manager 결정, 포인트 좌표 목록 추출, 실행 환경 확인.
- **제외**: GUI 코드 구현, 기존 코드 수정.

### 기능 요구사항

| ID | 요구사항 | 산출물 |
| --- | --- | --- |
| FR0-1 | 레포 루트에 `CLAUDE.md`를 만든다. 도메인 ID 표(50/20/22), 폴더별 역할, 전체 실행 순서, 코딩 규칙(한국어 주석, 기존 파일 수정 금지 목록)을 담는다. | `CLAUDE.md` |
| FR0-2 | 토픽 규약을 문서화한다. `patrol_cmd` 명령 문법(`start`, `stop`, `goto:<P>[,<P>,...]`), `patrol_status` JSON 스키마, state 값 11종(IDLE, STARTING, MOVING, WAITING\_ZONE, ARRIVED, LEAVING\_ZONE, RETURNING, DONE, STOPPED, FAILED, RETRY)을 포함한다. `waypoint`와 `detail`의 의미가 `start` 순찰과 `goto` 경로에서 어떻게 다른지(`goto`는 경로 안 순번과 지점 이름) 구분해서 적는다. | `pinky_web_gui/docs/interfaces.md` |
| FR0-3 | zone manager 버전을 확정한다. 순찰 노드 4개 파일(`_v2` 포함)이 모두 v1 `zone_gate_client`를 import하고, v1 매니저가 `/zone_manager/status`를 발행하며, 브리지 설정도 v1 토픽을 쓰므로 현재 구성은 **v1**로 판단한다. v2 파일(`zone_manager_node_v2` 등)은 "미사용"으로 표시하고, 실제 실행 중인 노드는 `ros2 node list`로 확인한다. v2로 전환할 계획이 있는지 사용자에게 묻는다. | 결정 기록 1문단 |
| FR0-4 | 포인트 좌표(P1\~P7, RED1·RED2의 IN/OUT)를 표로 만든다. 두 로봇의 `POINTS`는 같다. 로봇별로 다른 것은 순찰 경로(`WAYPOINTS`: pinky1은 RED1 문, pinky2는 RED2 문)와 홈 복귀 지점(pinky1 P1, pinky2 P7)이다. `goto` 허용 목록은 P1, P2, P7, P3\~P6이고 RED\*는 직접 goto 금지다. 구역 안 지점은 P3\~P6이다. | `docs/interfaces.md`의 포인트 표 |
| FR0-5 | 하드코딩된 절대 경로(예: `lane_detector_node.py`의 `/home/jinho/dev_ws/...`)를 목록으로 만든다. 수정은 하지 않는다. | 목록 |
| FR0-6 | 실행 환경을 확인한다: Ubuntu 버전, ROS 2 배포판, Python 버전, `fastapi`/`uvicorn` 설치 가능 여부. | `CLAUDE.md`의 환경 섹션 |
| FR0-7 | `my_pinky_package/PinkyPatrolNode.html`(순찰 노드 흐름도)과 `my_pinky_package/README.md`를 읽고, `goto` 경로 순회, 구역 처리, 첫 작업의 초기 위치 보정 흐름을 `interfaces.md`에 요약한다. | `docs/interfaces.md`의 순찰 노드 동작 섹션 |

### 완료 기준

- [x] `CLAUDE.md`를 읽은 새 Claude Code 세션이 "로봇에 start 명령을 보내는 경로"를 정확히 설명한다.
- [x] `docs/interfaces.md`의 토픽 이름이 실제 코드와 브리지 YAML에서 확인된 것만 담고 있다.
- [x] v1/v2 결정과 근거가 기록되어 있다.
- [x] 기존 소스 파일이 하나도 수정되지 않았다 (`git diff`로 확인). 단 이후 Step 1 에서 브리지 yaml 에 배터리 토픽을 추가했다 (아래 Step 1 참고).

### Claude Code 프롬프트

```text
이 레포(pinky_slam_nav)의 최신 main 브랜치를 분석해서 아래를 만들어줘. 코드 수정은 하지 마.
1. 루트에 CLAUDE.md: 도메인 ID 표(관제 PC 50 / pinky1 20 / pinky2 22), 폴더별 역할, 전체 실행 순서, 코딩 규칙.
2. pinky_web_gui/docs/interfaces.md: 관제 PC 기준 토픽 표, patrol_cmd 명령 문법(goto:P2,P3,P6처럼 여러 지점 지원), patrol_status JSON 스키마와 state 값(start 순찰과 goto 경로에서 waypoint, detail 의미가 다른 점 포함), 로봇별 포인트 좌표표(홈 복귀 지점과 goto 허용 목록 포함).
3. my_pinky_package/PinkyPatrolNode.html과 pinky_patrol_node_pinky1_v2.py의 _cmd_cb, _run_route, _init_pose_once를 읽고 goto 경로 순회, 구역 처리, 초기 위치 보정 흐름을 요약.
4. zone_manager v1과 v2 중 어느 쪽이 순찰 노드(import하는 zone_gate_client)와 브리지 YAML에 맞는지 결론.
5. 하드코딩된 절대 경로 목록.
근거는 반드시 실제 파일과 줄 번호로 제시하고, 확인 못 한 것은 추측하지 말고 '확인 필요'로 표시해줘.
```

## Step 1. 백엔드 뼈대: 상태 구독과 WebSocket 중계

관제 PC에서 `rclpy` 노드가 두 로봇의 상태 토픽을 구독하고, 이를 하나의 상태 모델로 합쳐 WebSocket으로 내보내는 백엔드를 만든다. 화면이 없어도 터미널과 브라우저 콘솔만으로 동작을 확인할 수 있어야 한다.

### 범위

- **포함**: `pinky_web_gui/backend/`, `config/robots.yaml`, `/ws`, `/api/health`, `/api/state`, mock 모드, 콘솔 확인용 최소 HTML.
- **제외**: 지도 렌더링, 로봇으로 나가는 명령 (Step 2\~3).

### 기능 요구사항

| ID | 요구사항 |
| --- | --- |
| FR1-1 | `config/robots.yaml`에 로봇 ID 목록(`pinky1`, `pinky2`)과 zone status 토픽 이름을 둔다. 코드에 로봇 이름을 하드코딩하지 않는다. |
| FR1-2 | `ROS_DOMAIN_ID=50` 환경의 `rclpy` 노드가 로봇별로 `/{id}/patrol_status`, `/{id}/amcl_pose`, `/{id}/battery_state`와 zone status 토픽을 구독한다. rclpy는 별도 스레드에서 spin하고, FastAPI(asyncio)와는 스레드 안전한 큐로 연결한다. |
| FR1-3 | 로봇별 상태 모델을 유지한다: `patrol`(state, waypoint, detail, time), `pose`(x, y, yaw), `battery`(percentage, voltage), `last_seen`, `online`. yaw는 쿼터니언에서 변환한다. |
| FR1-4 | WebSocket `/ws`는 접속 즉시 전체 스냅샷을 보내고, 이후 변경분만 이벤트로 보낸다. pose 이벤트는 로봇당 최대 10Hz로 제한한다. |
| FR1-5 | `online`은 해당 로봇의 어떤 토픽이든 마지막 수신 후 5초 이내인지로 판정한다. `patrol_status`는 상태가 바뀔 때만 발행될 수 있으므로 이 토픽만으로 판정하지 않는다. |
| FR1-6 | `patrol_status`가 JSON이 아니면 서버가 죽지 않고 경고 로그를 남기며, 원문을 `detail`에 보관한다. |
| FR1-7 | `--mock` 옵션으로 ROS 없이 실행할 수 있다. 두 로봇이 사각형 경로를 도는 가짜 데이터를 같은 형식으로 내보낸다. |
| FR1-8 | 기본 바인드는 `127.0.0.1`이고, `--host 0.0.0.0`으로 다른 PC에서 접속할 수 있다. |

### WebSocket 메시지 형식

```json
{"type": "snapshot", "robots": {"pinky1": {"online": true,
  "patrol": {"state": "MOVING", "waypoint": 2, "detail": "", "time": 1759560000.0},
  "pose": {"x": 0.65, "y": 0.15, "yaw": 0.0},
  "battery": {"percentage": 0.82, "voltage": 7.9}}},
 "zone": "free"}

{"type": "robot_update", "robot": "pinky1", "field": "pose", "data": {"x": 0.7, "y": 0.15, "yaw": 0.0}}
{"type": "zone", "status": "occupied_by:pinky2"}
```

### 완료 기준

- [x] `--mock` 모드에서 `/api/state`가 두 로봇의 데이터를 반환한다. (`tests/test_state.py`, `test_mock_flow.py`)
- [ ] 브라우저 콘솔에서 WebSocket에 접속하면 스냅샷과 pose 업데이트가 계속 찍힌다.
- [ ] 실제 로봇 연결 시 `ros2 topic echo /pinky1/amcl_pose`의 값과 `/api/state`의 pose가 일치한다.
- [ ] 로봇의 전원을 끄면 5초 후 `online: false`가 된다.
- [x] 잘못된 JSON을 `patrol_status`로 보내도 서버가 유지된다. (`test_invalid_json_keeps_server_and_raw_in_detail`)
- [ ] 기존 소스 파일은 수정되지 않았다. **→ 어긋남**: `/battery_state` 를 받으려고 `domain_bridge/config/pinky_bridge_pinky{1,2}.yaml` 에 배터리 토픽을 추가했다 (커밋 `d923481`, 이후 위험구역 대기열 Step Q3 에서 v3 토픽 5개도 추가). 새 코드는 `pinky_web_gui/` 에만 둔다는 원칙의 의도된 예외이고 `CLAUDE.md` 6절의 "기존 파일 수정 금지" 와 충돌하니 다음 변경 때 정리가 필요하다.

### Claude Code 프롬프트

```text
CLAUDE.md와 pinky_web_gui/docs/interfaces.md를 먼저 읽어줘.
pinky_web_gui/backend/ 에 FastAPI + rclpy 백엔드를 만들어줘. 요구사항은 PRD의 Step 1 FR1-1 ~ FR1-8과 같아.
- 로봇 ID는 config/robots.yaml에서 읽을 것 (하드코딩 금지)
- rclpy는 별도 스레드에서 spin, WebSocket 브로드캐스트와는 스레드 안전하게 연결
- --mock 옵션으로 ROS 없이도 실행되게 할 것
- 기존 소스 파일은 수정하지 말 것
만든 뒤에는 mock 모드로 실제 실행해서 /api/state 결과를 보여주고, 실행 방법을 README에 적어줘.
```

## Step 2. 지도 화면: 맵과 로봇 2대 위치 표시

브라우저에서 `my_pinky_map10` 지도를 보여주고, 그 위에 pinky1·pinky2의 실시간 위치와 방향을 표시한다. 기존 `pinky_map_viewer.html`의 좌표 확인 기능은 그대로 가져간다. 이 단계가 끝나면 "로봇이 지금 어디 있는지"를 한눈에 볼 수 있다.

### 범위

- **포함**: 맵 제공 API, Canvas 지도 렌더링, 확대·이동, 로봇 마커, 로봇 상태 카드, WebSocket 재연결, 정적 파일 서빙.
- **제외**: 로봇으로 나가는 명령 (Step 3), 경로·LiDAR 오버레이 (Step 4).

### 기능 요구사항

| ID | 요구사항 |
| --- | --- |
| FR2-1 | 백엔드가 `GET /api/map`으로 지도 메타데이터(해상도, origin, 가로·세로 픽셀)와 PNG 이미지를 제공한다. 값은 `my_pinky_map10.yaml`에서 읽고 코드에 박지 않는다. |
| FR2-2 | 프론트엔드에 월드↔픽셀 변환 함수를 한 곳에만 둔다. 변환식: `px = (x - origin_x) / res`, `py = H - (y - origin_y) / res` (H는 지도 높이 픽셀). 이후 모든 오버레이가 이 함수를 쓴다. |
| FR2-3 | Canvas에 지도를 그린다. 마우스 휠로 확대·축소, 드래그로 이동한다. |
| FR2-4 | 로봇 마커는 로봇별 고정 색, 방향 화살표(yaw), 이름 라벨을 가진다. `online: false`이면 회색으로 표시한다. |
| FR2-5 | 마우스를 올리면 map 좌표(m)와 occupancy(free / occupied / unknown)를 표시한다. 판정은 지도 YAML의 `negate: 0`, `occupied_thresh: 0.65`, `free_thresh: 0.196`을 따른다. |
| FR2-6 | 지도를 클릭하면 좌표를 목록에 기록하고 복사할 수 있다 (기존 뷰어 기능 유지, waypoint 좌표를 정할 때 사용). |
| FR2-7 | 사이드바에 로봇 카드 2개를 둔다: online 배지, 순찰 state, 배터리 %, 현재 좌표. |
| FR2-8 | WebSocket이 끊기면 상단에 "연결 끊김"을 표시하고, 1초에서 5초까지 늘려가며 자동 재연결한다. 끊긴 동안 마커는 흐리게 표시한다. |
| FR2-9 | FastAPI가 `frontend/`를 정적으로 서빙해서 `http://localhost:8000`으로 바로 접속된다. npm 같은 빌드 단계는 없다. |

### 완료 기준

- [ ] mock 모드에서 지도 위 두 마커가 부드럽게 움직이고 방향 화살표가 진행 방향과 일치한다.
- [ ] 실제 로봇에서 마커 위치와 로봇의 실제 위치가 맞는다 (원점 P1, 우측 끝 P3에서 직접 확인).
- [x] 지도 위 클릭 좌표가 기존 `pinky_map_viewer.html`의 같은 지점 좌표와 일치한다 (변환식 검증, `tests/verify_transform.py`).
- [ ] 백엔드를 껐다 켜면 새로고침 없이 자동 재연결된다.
- [ ] 모바일 브라우저 폭에서도 사이드바가 지도 아래로 내려가며 깨지지 않는다.

### Claude Code 프롬프트

```text
CLAUDE.md와 pinky_map_viewer.html을 먼저 읽어줘.
pinky_web_gui/frontend/ 에 단일 HTML + JS 지도 화면을 만들어줘. 요구사항은 PRD의 Step 2 FR2-1 ~ FR2-9와 같아.
- 월드↔픽셀 변환은 한 함수로 만들고, 변환식과 기존 pinky_map_viewer.html의 결과가 같은지 코드로 검증해줘
- 지도 정보(해상도, origin)는 map yaml에서 읽어 /api/map으로 내려줄 것
- 프레임워크와 npm 빌드는 쓰지 말 것
- --mock 모드로 실행해서 마커가 움직이는 것을 확인하고 스크린샷 또는 확인 방법을 알려줘
```

## Step 3. 명령 패널: start / stop / goto

로봇별로 순찰 시작, 정지, 지점 이동 명령을 웹에서 내리고, 명령이 어떻게 처리됐는지 화면에서 확인한다. 이 단계부터 실제 로봇이 움직이므로, 잘못된 명령을 막는 장치가 핵심이다.

### 범위

- **포함**: 명령 REST API, 명령 허용 목록 검증, 로봇 카드의 명령 버튼, 지도 위 포인트 표시, 명령 이력 패널.
- **제외**: 비상정지와 수동 조작(`cmd_vel`), 구역 상태 표시 (Step 4).

### 설계 전제 (기존 코드 동작)

- 순찰 노드는 이미 작업 중이면 `start`와 `goto`를 **조용히 무시**하고 로그만 남긴다. 무시했다는 응답 메시지는 없다.
- `goto`는 여러 지점을 받는다: `goto:P2,P3,P6` (쉼표나 공백으로 구분, 대소문자 무시). 노드는 지점을 순서대로 방문하고, **지점마다 10초 대기**(`GOTO_WAIT_SEC`, `WAIT_EVERY_POINT = True`)한 뒤 홈으로 복귀한다. 홈은 pinky1이 P1, pinky2가 P7이다.
- 빈 목록, 알 수 없는 포인트, 허용되지 않은 포인트가 하나라도 있으면 경로 전체가 거절되고 `FAILED` 상태와 `detail`(`no point given`, `unknown point`, `not allowed`)이 온다.
- `RED1IN`, `RED1OUT`, `RED2IN`, `RED2OUT`은 직접 `goto`가 금지된다. 구역 안(P3\~P6)과 밖 지점 사이를 오갈 때 RED 문 경유, 진입 허가 대기, 락 반납은 노드가 **자동으로** 처리하므로 GUI는 신경 쓰지 않는다.
- 노드를 켠 뒤 첫 작업(`start` 또는 `goto`)에서는 저장된 마지막 위치로 AMCL 초기 위치를 설정하고 약 4초 제자리 회전을 한 다음 Nav2 활성화를 기다린다. 이 동안 상태는 `STARTING`이다.
- `goto` 경로 중 상태의 `waypoint`는 경로 안 순번(0부터), `detail`은 지점 이름이다.
- 따라서 GUI는 "명령 전송 성공"과 "로봇이 받아들임"을 구분해서 보여줘야 한다.

### 기능 요구사항

| ID | 요구사항 |
| --- | --- |
| FR3-1 | `POST /api/robots/{id}/command`에 `{"cmd": "start" \| "stop" \| "goto", "points": ["P2", "P3", "P6"]}`를 받아 `/{id}/patrol_cmd`로 발행한다. `goto`는 `goto:P2,P3,P6` 형식(쉼표 구분, 대문자)의 문자열로 만든다. 퍼블리셔는 서버 시작 시 한 번만 만들어 재사용한다 (기존 클라이언트의 1초 대기가 필요 없다). |
| FR3-2 | 백엔드는 허용 목록 밖의 명령을 400으로 거절한다. `points`는 1개 이상 최대 10개(설정값)이고, 각 지점은 로봇별 허용 목록(`config/robots.yaml`)에 있어야 한다. RED\* 포인트는 항상 거절한다. 로봇 노드도 같은 검사를 하지만, 노드 쪽 거절은 `FAILED` 상태로만 알려지므로 먼저 막는다. |
| FR3-3 | 응답의 `accepted: true`는 "토픽에 발행했다"는 뜻일 뿐이다. 노드는 `start`와 `goto`를 받으면 곧바로 `STARTING`을 발행하므로, 화면은 명령 후 5초 안에 `patrol_status`가 바뀌지 않으면 "로봇이 응답하지 않았거나 무시했을 수 있음"을 표시한다. |
| FR3-4 | 로봇 카드에 버튼을 둔다: 순찰 시작, 정지, 경로 이동(지점을 순서대로 골라 목록을 만들고 이동 버튼). 카드에 "완료 후 복귀: P1"(pinky2는 P7)과 "지점마다 10초 대기"를 표시한다. |
| FR3-5 | 버튼 활성 조건은 화면 힌트다. `start`와 `goto`는 IDLE, DONE, STOPPED, FAILED에서만, `stop`은 작업 중 상태(STARTING, MOVING, WAITING\_ZONE, ARRIVED, LEAVING\_ZONE, RETURNING, RETRY)에서만 활성화한다. `online: false`이면 전부 비활성화한다. 최종 판단은 항상 로봇 노드가 한다. |
| FR3-6 | `start`와 `goto`는 확인 팝업을 거친다. `goto` 팝업에는 경로 요약을 보여준다 (예: "pinky1: P2 → P3 → P6 → 홈(P1), 지점마다 10초 대기"). `stop`은 확인 없이 즉시 보낸다. |
| FR3-7 | 같은 버튼의 연타를 1초간 막는다. |
| FR3-8 | 지도 위에 `goto` 가능한 포인트(P1\~P7)를 표시한다. 포인트를 클릭하면 경로 목록 끝에 추가되고, 목록에서 순서 변경과 삭제를 할 수 있다. 구역 안 포인트(P3\~P6)는 "구역 안"으로 표시한다. 좌표는 `config/points.yaml`(Step 0의 포인트 표)에서 읽고 화면 표시용으로만 쓴다. |
| FR3-9 | state 배지 색은 로봇 LED 규칙을 따른다: 주행 중 초록, WAITING\_ZONE 빨강, ARRIVED(도착 후 대기) 노랑, FAILED 진한 빨강, 그 외 회색. |
| FR3-10 | 화면 하단에 명령 이력을 표시한다: 시각, 로봇, 명령(`goto`는 지점 목록 포함), 결과(전송됨 / 거절됨 / 응답 없음). 최근 50건. |
| FR3-11 | `STARTING` 상태가 5초 넘게 이어지면 "시작 준비 중 (첫 작업은 초기 위치 보정으로 로봇이 제자리에서 회전합니다)"을 표시한다. |
| FR3-12 | `goto` 진행 중에는 `waypoint`(경로 안 순번)와 `detail`(지점 이름)로 카드에 진행 상황을 표시한다 (예: "지점 2/3 · P3 이동 중"). `detail`이 RED 문 이름이면 "구역 문 이동 중"으로 표시한다. |
| FR3-13 | 노드가 보낸 `FAILED`의 `detail`이 `no point given`, `unknown point`, `not allowed`이면 거절 이유를 한글로 표시한다. |

### 완료 기준

- [x] mock 모드에서 start → MOVING → DONE 흐름과 stop → STOPPED 흐름이 화면에 반영된다. (`tests/test_mock_flow.py`, `verify_step3_mock.py`)
- [ ] 실제 pinky1에 `start`를 보내면 순찰이 시작되고, 기존 `pinky_control_client_v2.py`로 보낸 것과 같은 동작을 한다.
- [ ] 지점 3개(P2, P3, P6)를 골라 보내면 로봇에 `goto:P2,P3,P6`가 전달되고, 카드에 지점별 진행이 표시되며, 마지막에 홈으로 복귀해 DONE이 된다.
- [ ] (mock 통과: `test_two_robots_contend_for_zone_one_waits`, 실로봇은 대기열 v3 기준으로 다시 확인 필요) 구역 안 지점(P3)이 포함된 경로를 두 로봇이 동시에 요청하면 한 대는 WAITING\_ZONE으로 표시되고, 구역이 비면 이어서 진행한다.
- [x] 순찰 중에 `start`를 다시 보내면 "응답 없음/무시됨" 표시가 5초 후에 나타난다. (`test_no_response_after_timeout`)
- [x] `goto` 지점에 `RED1IN`을 넣어 API를 직접 호출하면 400이 반환된다. (`test_goto_rejects`; v3 에서 문이 없어져도 허용 목록 방식이라 계속 거절된다)
- [ ] 노드를 막 켠 뒤 첫 작업에서 `STARTING`이 길어지면 "시작 준비 중" 안내가 표시된다.
- [ ] 로봇 한 대를 끄면 그 로봇의 버튼만 비활성화되고 다른 로봇은 영향이 없다.
- [ ] 두 로봇에 동시에 `start`해도 각각 올바른 토픽(`/pinky1/...`, `/pinky2/...`)으로 나간다.

### Claude Code 프롬프트

```text
CLAUDE.md와 docs/interfaces.md, 그리고 my_pinky_package/my_pinky_package/pinky_patrol_node_pinky1_v2.py의 _cmd_cb와 _run_route 함수, my_pinky_package/PinkyPatrolNode.html을 먼저 읽어줘.
PRD의 Step 3 FR3-1 ~ FR3-13을 구현해줘.
- goto는 여러 지점(goto:P2,P3,P6)을 지원할 것. 지점 목록은 로봇별 허용 목록으로 백엔드에서 반드시 검증할 것 (프론트 검증만으로 끝내지 말 것)
- 순찰 노드가 start/goto를 조용히 무시하는 동작과, 첫 작업의 초기 위치 보정(STARTING이 길어짐)을 감안할 것
- 구역 문(RED*) 경유와 락 처리는 노드가 하므로 GUI는 RED 지점을 보내지 말 것
- 로봇별 허용 포인트와 홈 복귀 지점은 config에서 읽을 것
- 먼저 mock 모드에서 전체 흐름을 테스트하고, 실제 로봇 테스트용 체크리스트를 따로 작성해줘
실제 로봇에는 내가 직접 테스트할 테니 로봇 연결은 시도하지 마.
```

## Step 4. 구역 상태, 비상정지, 경로와 LiDAR

이 프로젝트의 핵심 규칙인 "위험 구역에는 한 대만 들어간다"를 화면에서 볼 수 있게 하고, 문제가 생겼을 때 바로 멈출 수 있는 비상정지와 주행 상황을 보조하는 오버레이를 추가한다.

### 범위

- **포함**: 구역 상태 표시, 순찰 경로 오버레이, LiDAR 오버레이(요청 시에만 구독), 비상정지, 수동 조작(선택).
- **제외**: 카메라 영상, 로그 저장, 인증 (Step 5).

### 설계 전제

- **zone 상태 형식**: 현재 구성은 v1이다. `/zone_manager/status`는 `free` 또는 `occupied_by:<id>`다. v2(`/zone_manager_v2/status`, `occupied_by:<id>:<token>`)는 순찰 노드가 쓰지 않아 이번 범위에서 제외하되, v2 전환에 대비해 두 형식을 모두 파싱한다.
- **구역 좌표**: v1 설정(`zone_params.yaml`)에는 구역의 중심·반경이 없고 `robot_ids`와 `max_hold_sec`만 있다. 구역은 코드의 포인트로 정의된다: 구역 안 지점 P3\~P6, 진입·이탈 문 RED1IN/OUT과 RED2IN/OUT. v2 파일의 중심 (1.0, -0.6)·반경 0.5는 "TODO" 값이고, P4 외의 지점은 이 반경 밖이라 사용하지 않는다. 화면의 구역 영역은 `config/zone.yaml`을 새로 만들어 정의한다.
- **LiDAR 대역폭**: 레포에 Wi-Fi 끊김 진단 스크립트(`ros2_network_test`)가 있을 만큼 무선 품질이 변수다. `/scan`은 브라우저가 켰을 때만 구독한다.
- **경로 오버레이**: Nav2의 `/plan` 토픽은 브리지 설정에 없다. 대신 `goto`는 백엔드가 기억하는 마지막 지점 목록과 `patrol_status`의 `waypoint`, `detail`로, `start` 순찰은 waypoint 번호로 경로와 현재 목표를 그린다.
- **비상정지의 한계**: 이 기능은 소프트웨어 정지이며 하드웨어 비상정지를 대체하지 않는다. 관제 PC의 `lane_follower_node`나 Nav2도 `cmd_vel`을 낼 수 있어서 마지막 발행이 이긴다. 또한 첫 작업의 초기 위치 보정 회전(`spin_in_place`, 약 4초)은 코드상 `stop`을 확인하지 않고 끝까지 돈다. 이 구간에서는 `patrol_cmd`의 `stop`이 즉시 먹지 않을 수 있어서 0 속도 발행이 중요하다. 다만 이때 노드도 `cmd_vel`을 계속 발행하므로 실제 효과는 로봇에서 확인해야 한다.

### 기능 요구사항

| ID | 요구사항 |
| --- | --- |
| FR4-1 | 지도에 위험 구역을 영역으로 그린다. 구역 안 지점(P3\~P6)을 감싸는 사각형과 진입·이탈 문(RED1IN/OUT, RED2IN/OUT)의 위치를 `config/zone.yaml`에서 읽어 표시한다. 이 좌표는 코드에 따로 없으므로 Step 0의 포인트 표를 바탕으로 만들고 사용자가 확인한다. |
| FR4-2 | 상단에 구역 상태 배지를 둔다: "구역: 비어 있음" 또는 "구역: pinky1 점유 중 (42초)". 점유 시간은 백엔드가 점유 상태를 처음 본 시각부터 센다. 점유 중이면 구역 영역을 빨갛게 칠한다. |
| FR4-3 | `WAITING_ZONE` 상태의 로봇은 카드와 마커에 "구역 진입 대기"를 표시하고, `detail`의 지점 이름(가려는 지점)을 함께 보여준다. |
| FR4-4 | 로봇별 경로를 지도에 선으로 그리고 현재 지점을 강조한다. `goto`는 마지막으로 보낸 지점 목록과 `patrol_status`의 `waypoint`, `detail`을 쓰고, 구역 문 경유(`detail`이 RED\*)는 문 위치에 표시한다. `start` 순찰은 `config/routes.yaml`의 경로와 `waypoint` 번호를 쓰며, 번호 의미는 Step 0에서 코드와 대조해 확정한다. `waypoint`가 -1이면 강조하지 않는다. |
| FR4-5 | LiDAR 오버레이는 로봇별 토글이다. 켤 때만 백엔드가 `/{id}/scan`을 구독하고, 5Hz로 제한하며 점은 3개당 1개로 줄여 보낸다. 모든 브라우저가 끄면 구독을 해제한다. |
| FR4-6 | LiDAR 점은 `amcl_pose`를 기준으로 지도 좌표로 변환한다. 센서와 로봇 중심의 오프셋은 무시하고, 이 사실을 화면 도움말에 적는다. |
| FR4-7 | 로봇별 "비상정지" 버튼과 전체 "모두 정지" 버튼을 항상 화면 상단에 보이게 둔다. 확인 팝업 없이 한 번 눌러 동작한다. |
| FR4-8 | 비상정지는 두 가지를 순서대로 한다: ① `patrol_cmd`로 `stop` 발행 (Nav2 작업 취소), ② `/{id}/cmd_vel`에 0 속도를 10Hz로 2초간 발행. 첫 작업의 초기 위치 보정 회전 중에는 ①이 즉시 먹지 않을 수 있으므로 ②를 반드시 함께 보낸다. 결과는 명령 이력에 기록한다. |
| FR4-9 | (선택) 수동 조작: 버튼을 누르고 있는 동안만 `cmd_vel`을 10Hz로 발행하고, 떼거나 WebSocket이 끊기거나 0.5초 동안 입력이 없으면 즉시 0 속도를 보낸다. 최대 속도는 설정값(기본 선속도 0.1 m/s, 각속도 0.5 rad/s)으로 제한하고, 순찰 중에는 비활성화한다. |

### 완료 기준

- [x] mock 모드에서 한 로봇이 구역에 들어가면 배지와 구역 영역 색이 바뀌고, 나가면 원래대로 돌아온다. (`tests/test_zone.py`, `verify_step4_mock.py`, `verify_step4_ui.py`)
- [ ] 실제 로봇에서 한 대가 구역에 있을 때 다른 로봇이 `WAITING_ZONE`으로 표시된다.
- [ ] 지도의 구역 영역이 실제 맵의 P3\~P6 위치와 맞고, 사용자가 경계를 확인했다.
- [ ] LiDAR 토글을 켜면 점이 벽 윤곽과 대체로 겹치고, 모두 끄면 `ros2 topic hz /pinky1/scan`의 구독자가 사라진다.
- [ ] 비상정지를 누르면 순찰 중인 로봇이 2초 안에 멈춘다 (낮은 속도로 직접 확인). 노드를 막 켠 뒤 첫 작업의 초기 위치 보정 회전 중에도 확인한다.
- [ ] (mock 통과: `tests/test_motion.py` 입력 끊김·해제 시 즉시 0 속도, 실로봇 확인 대기) 수동 조작 중 브라우저 탭을 닫으면 로봇이 0.5초 안에 멈춘다.
- [ ] 순찰 경로 선과 강조 지점이 `start`와 `goto` 모두에서 로봇의 실제 진행에 맞게 갱신된다.

### Claude Code 프롬프트

```text
CLAUDE.md와 docs/interfaces.md, zone_traffic_control/zone_traffic_control/zone_manager_node.py(v1)와 zone_gate_client.py, my_pinky_package/PinkyPatrolNode.html을 먼저 읽어줘.
PRD의 Step 4 FR4-1 ~ FR4-9를 구현해줘. FR4-9(수동 조작)는 마지막에, 나머지가 끝난 뒤에 해줘.
- zone status는 v1(free / occupied_by:id)을 기준으로 하되 v2 형식(occupied_by:id:token)도 파싱할 것
- 구역 좌표는 v1 설정에 없으므로 config/zone.yaml을 새로 만들고, P3~P6와 RED 문 좌표를 바탕으로 한 초안을 만든 뒤 내가 확인할 수 있게 해줘
- /scan은 브라우저가 켰을 때만 구독하고, 끄면 구독 해제할 것
- 비상정지는 stop 명령과 0 속도 burst를 둘 다 보낼 것
- 수동 조작은 데드맨 방식(누르는 동안만)으로 하고, 연결이 끊기면 반드시 0 속도를 보낼 것
실제 로봇에는 연결하지 말고 mock 모드에서 먼저 검증해줘. 실제 로봇 테스트 절차는 별도 체크리스트로 줘.
```

## Step 5. 카메라, 로그와 이력, 인증 (선택)

> 상태 (2026-10-14): **일부만 구현**. 로그 화면(`backend/logstore.py`, `frontend/logview.js`)과 인증(Step 6-1 로 대체)은 구현됐다. **카메라(FR5-1~2, 전용 브리지 YAML)는 구현하지 않았다** (`backend/`, `frontend/`, `domain_bridge/config/` 어디에도 없음). 로봇 추가는 `robots.yaml` 에 직접 쓰는 대신 왼쪽 메뉴 `Add Robot` 안내 팝업(`frontend/addrobot.js`)이 붙여 넣을 내용을 알려 주는 방식이다. 이력 파일 저장·재시작 후 유지는 로그 화면 기준으로 **확인 필요**, 알람 이력은 알람바 PRD 의 `data/alarms/` 파일이 맡는다. 아래 완료 기준은 모두 미확인이다.

운영에 필요한 부가 기능을 추가한다: 로봇 카메라 영상, 이벤트 이력 저장, 접속 제한. 모두 선택 사항이므로 필요한 것만 골라서 진행해도 된다.

### 범위

- **포함**: 카메라 영상 보기, 이벤트 이력 저장과 조회, 간단한 인증, 실행 스크립트.
- **제외**: 다수 사용자 계정 관리, 외부 인터넷 공개(HTTPS, 리버스 프록시).

### 설계 전제

- 카메라 노드는 `<namespace>/camera/image_raw/compressed`(JPEG)를 발행하고, 차선 검출 노드는 `<namespace>/lane/debug/compressed`를 구독자가 있을 때만 발행한다.
- 확인한 pinky1 브리지 설정에는 카메라 토픽이 없다. 기존 YAML을 고치지 않고, 카메라 전용 브리지 YAML(`pinky_bridge_camera_pinky1.yaml` 등)을 새로 만들어 따로 실행한다.
- 영상은 Wi-Fi를 많이 쓰므로 브라우저가 볼 때만 구독하고, 프레임 수를 제한한다.
- 인증 없이 `--host 0.0.0.0`으로 열면 같은 네트워크의 누구나 로봇에 명령을 보낼 수 있다.

### 기능 요구사항

| ID | 요구사항 |
| --- | --- |
| FR5-1 | 카메라 전용 브리지 YAML을 로봇별로 추가한다. 기존 `pinky_bridge_*.yaml`은 수정하지 않는다. |
| FR5-2 | 백엔드가 `GET /api/robots/{id}/camera?src=raw\|lane`을 MJPEG 스트림(`multipart/x-mixed-replace`)으로 제공해서 `<img>` 태그로 바로 볼 수 있게 한다. `raw`는 카메라 원본, `lane`은 차선 검출 디버그 영상이다. |
| FR5-3 | 카메라 토픽은 해당 스트림을 보는 브라우저가 있을 때만 구독하고, 모두 닫히면 해제한다. 프레임은 최대 5fps로 제한한다. |
| FR5-4 | 로봇 카드를 누르면 카메라 패널이 열린다. `raw`와 `lane` 전환 버튼을 둔다. 영상이 5초 이상 들어오지 않으면 "영상 없음"을 표시한다. |
| FR5-5 | 이벤트 이력을 SQLite(표준 라이브러리 `sqlite3`)에 저장한다: 시각, 로봇, 종류(상태 변경, 명령, 구역 점유·해제, 비상정지, 온라인·오프라인), 상세(JSON). 30일이 지난 기록은 자동 삭제한다. |
| FR5-6 | 이력 화면에서 로봇·종류·기간으로 거르고, CSV로 내려받을 수 있다. |
| FR5-7 | 로봇이 오프라인이 되거나 `FAILED`가 되면 화면 상단에 알림을 띄운다. 확인 버튼을 누르기 전까지 유지한다. |
| FR5-8 | (**Step 6-1에서 대체됨**: 고정 계정 `mngr`/`oper`) 인증: 환경 변수로 지정한 비밀번호 두 개를 둔다. 보기 전용(viewer)과 명령 가능(operator). viewer는 모든 명령 API와 비상정지 외의 제어 버튼이 비활성화된다. 로그인은 서버 세션 쿠키로 유지한다. |
| FR5-9 | `run.sh` 하나로 ROS 환경 `source`, 도메인 ID 설정, 서버 실행까지 한다. (선택) systemd 유닛 파일 예시를 제공한다. |
| FR5-10 | `robots.yaml`에 로봇을 추가하면 코드 수정 없이 카드, 마커, 이력에 나타난다. |

### 완료 기준

- [ ] 로봇 카드를 눌러 카메라 영상이 보이고, 패널을 닫으면 `ros2 topic info`의 구독자가 사라진다.
- [ ] 순찰 한 번을 돌린 뒤 이력에 상태 변경과 구역 점유·해제가 순서대로 남는다.
- [ ] 서버를 재시작해도 이력이 유지된다.
- [ ] 비밀번호 없이 접속하거나 viewer로 접속하면 `start` API 호출이 401 또는 403으로 거절된다.
- [ ] `robots.yaml`에 가짜 로봇 `pinky3`를 추가하면(mock 모드) 화면에 세 번째 카드와 마커가 나타난다.
- [ ] 새 PC(Ubuntu)에서 `README`대로 `run.sh`만 실행해서 서버가 뜬다.

### 보안 메모

비밀번호는 HTTP로 전달되므로 같은 내부망(관제 PC가 있는 로컬 네트워크)에서만 쓰는 것을 전제로 한다. 인터넷에서 접속해야 한다면 HTTPS가 필요하며, 이는 이 PRD의 범위를 벗어난다.

### Claude Code 프롬프트

```text
CLAUDE.md와 pinky_camera/pinky_camera/camera_node.py, lane_detector_node.py, domain_bridge_pc/config/pinky_bridge_pinky1.yaml을 먼저 읽어줘.
PRD의 Step 5 FR5-1 ~ FR5-10 중에서 내가 지금 요청하는 것만 구현해줘: [여기에 번호 적기. 예: FR5-5, FR5-6, FR5-8]
- 기존 브리지 YAML과 기존 노드 코드는 수정하지 말 것
- 카메라는 보는 사람이 있을 때만 구독, 5fps 제한
- 비밀번호는 코드에 쓰지 말고 환경 변수에서 읽을 것
- 새 의존성이 필요하면 requirements.txt에 적고 이유를 알려줘
```

## Step 6. 인증과 프로세스 제어 (시스템 패널)

> 상태: 6-1 ~ 6-5 구현됨(mock 검증), 6-6 ~ 6-8 **실물 확인 대기**. 이 Step은 처음 계획(Step 0~5)에는 없었고 개발 중에 추가됐다. Step 5의 인증(FR5-8)은 여기서 **대체**됐다: 환경 변수 비밀번호 2개(viewer/operator) 대신 프로그램에 고정된 ID/비밀번호 계정(`mngr`=manager, `oper`=operator)을 쓴다. 역할 이름은 `operator`→`manager`, `viewer`→`operator`로 바뀌었다.
> 실물 확인 절차: `../checklists/step6_process_control_real_checklist.md`. 사용법: `../usage.md`의 "인증과 프로세스 제어" 절.

관제 PC의 터미널과 로봇의 SSH 터미널을 오가며 켜던 것을 시스템 패널의 버튼으로 옮긴다. 켜고 끄는 대상: 도메인 브릿지(`ros2 launch launch/bridges.launch.xml`), `zone_manager`, 로봇별 bringup, map(Nav2), 순찰 노드.

### 범위

- **포함**: 로그인과 역할, 허용 목록 기반 프로세스 시작·정지, 전체 시작·정지, 시스템 패널과 로그 화면, 로봇 추가 안내 팝업, 실물 확인.
- **제외**: 임의 명령 실행, 비밀번호 저장, 다수 계정 관리, 로봇의 ROS 패키지 설치와 빌드, 외부 인터넷 공개(HTTPS).

### 설계 전제

- 실행할 수 있는 것은 `config/processes.yaml`에 적힌 프로세스뿐이다. 브라우저가 보낸 값은 id로만 쓰고 명령으로 쓰지 않는다.
- 관제 PC 쪽(`kind: local`)은 `bash -c 'source ... && export ROS_DOMAIN_ID=.. && exec <command>'`로, 로봇 쪽(`kind: ssh`)은 SSH 키 로그인(`BatchMode=yes`, `StrictHostKeyChecking=yes`)으로 같은 스크립트를 원격 실행한다. SSH 비대화형 셸은 `~/.bashrc`를 읽지 않으므로 DDS 환경(`RMW_IMPLEMENTATION`, `CYCLONEDDS_URI`)과 ROS 배포판은 `env`/`source`에 명시한다.
- 브릿지를 로봇 토픽보다 먼저 켜면 센서 QoS(best_effort)가 안 맞아 데이터가 안 넘어온다 (격리 도메인에서 재현). 그래서 시작 순서는 브릿지 → bringup → map → zone_manager → 순찰 노드이고 정지는 반대다.
- 인증이 꺼져 있으면(`--no-auth`) 프로세스 제어와 로그 화면의 원격 실행은 강제로 꺼진다.
- 로봇 IP와 명령은 `processes.yaml`의 값이 **로봇에서 직접 확인한 값이 아니다**. 기존 파일(`launch/`, 순찰 노드 등)은 수정하지 않는다.

### Step 6-1. 인증과 계정 (구현됨)

| ID | 요구사항 |
| --- | --- |
| FR6-1 | 계정은 `backend/auth.py`에 고정한다: `mngr`(manager: 명령, 수동 조작, 프로세스 제어, 로그 화면), `oper`(operator: 보기 전용, 비상정지는 가능). 환경 변수나 설정 파일로 바꾸지 않는다. |
| FR6-2 | `POST /api/login {id, password}`, 서버 세션 쿠키(HttpOnly, SameSite=Strict, 12시간, 재시작 시 만료). 같은 주소에서 1분 안에 5번 틀리면 잠시 막는다(429). 비교는 상수 시간. |
| FR6-3 | `계정 전환` 버튼: 로그아웃 없이 다른 계정으로 바꾼다. 틀리면 지금 계정을 유지하고, 성공하면 이전 세션을 끝낸다. |
| FR6-4 | `--no-auth`로 인증을 끈다 (개발용). 정적 화면, `/api/health`, `/api/me`, `/api/login` 외의 `/api/*`와 `/ws`는 로그인이 필요하다. 다른 사이트의 요청을 막기 위해 POST는 JSON + 같은 Origin만, WebSocket은 같은 Origin만 받는다. |

완료 기준: 비밀번호 없이 또는 operator로 `start` API를 부르면 401/403 · 계정 전환 성공/실패 동작 · 5회 실패 후 429 (`tests/test_auth.py`, `verify_proc_mock.py`).

### Step 6-2. 프로세스 관리 코어 (구현됨)

| ID | 요구사항 |
| --- | --- |
| FR6-5 | `config/processes.yaml`에 프로세스 id, 이름, 묶음(`group`), 종류(`local`/`ssh`), 도메인, `cwd`, `source`, `env`, `command`, `detect`, `health`, `stop_on_exit`을 둔다. 명령이 비어 있으면 "명령 미설정"으로 비활성이다. |
| FR6-6 | 시작 전에 `detect`(pgrep 패턴)로 같은 프로세스가 이미 도는지 확인해서, 터미널에서 직접 켠 것과 겹치면 시작하지 않는다 (409). 그런 프로세스는 GUI로 끌 수 없다. |
| FR6-7 | 정지: 프로세스 그룹에 SIGINT → 5초 후 SIGTERM → SIGKILL. SSH는 pty에 Ctrl-C를 보내고, 안 되면 로봇에서 `pkill -INT`, 그래도 안 되면 SSH를 끊는다. 마지막에 로봇에서 아직 도는지 다시 확인해서 남아 있으면 "실패"로 표시한다. |
| FR6-8 | 로봇 프로세스를 정지하거나 전체 정지에 포함하면 확인 팝업과 서버의 `confirm: true`가 필요하다 (없으면 409 `needs_confirm`). |
| FR6-9 | 서버를 종료하면(Ctrl-C, SIGHUP, SIGTERM) GUI가 켠 프로세스를 모두 정지한다. 비상정지의 마지막 0 속도를 먼저 보낸 뒤 정지하고, `ros2 launch` 리더가 끝난 뒤 남은 자식(`domain_bridge`)은 그룹에 SIGTERM/SIGKILL로 정리한다. `stop_on_exit: false`인 것은 남긴다. |
| FR6-10 | 상태 `stopped\|starting\|running\|stopping\|failed\|exited`. 배지는 두 겹이다: 초록 "실행 중"(프로세스가 떠 있고 `health: true`면 그 로봇의 토픽도 옴), 노랑 "실행 중 · 토픽 없음". |
| FR6-11 | 로봇이 bringup만 켜져 있어도 online이 되도록 `/odom`을 생존 확인용으로 받는다 (초당 2번 반영). |

완료 기준: 중복 실행 거절 · 정지 후 프로세스가 남지 않음 · 서버 종료 시 정리 (`tests/test_procs.py`, mock 검증).

### Step 6-3. 전체 시작·정지 시퀀스 (구현됨)

| ID | 요구사항 |
| --- | --- |
| FR6-12 | `POST /api/procs/start_all`, `/stop_all`은 백그라운드 시퀀스로 돌고 진행은 `sequence`(`{kind, state: running\|done\|failed, step, message, results}`)로 알린다. 순서는 `processes.yaml` 목록 순서다. |
| FR6-13 | 각 단계가 **켜진 것을 확인한 뒤** 다음으로 간다: 프로세스가 settle 시간 동안 살아 있어야 하고, `health`가 있는 bringup은 로봇 토픽이 올 때까지(`ready_sec`, 기본 25초) 기다린다. 진행 문구에 대기 사유를 보인다. |
| FR6-14 | 토픽이 안 오면 `restart_if_stalled`인 브릿지를 한 번 재시작하고, 그래도 안 오면 거기서 중단하고 사유를 보인다. 터미널에서 이미 켠 프로세스는 "건너뜀"으로 처리한다. |

완료 기준: 켜짐 대기, 브릿지 재시작, 실패 시 중단, `health` 없는 프로세스 흐름 (`tests/test_procs.py`).

### Step 6-4. 시스템 패널과 로그 화면 (구현됨)

| ID | 요구사항 |
| --- | --- |
| FR6-15 | 사이드바 `시스템` 탭: `group`별 카드(브릿지 / bringup / map / zone_manager / 순찰 노드), 카드 머리글에 실행 중 개수, 프로세스별 시작·정지·로그, 실행 시간 실시간 표시, 전체 시작·정지 버튼, 진행 문구. operator에게는 버튼이 꺼진다. |
| FR6-16 | 로그 화면(왼쪽 메뉴 `로그`): GUI가 켠 프로세스의 출력과 GUI 서버 로그를 `전체 | GUI 서버 | 프로세스별` 탭으로 본다. 레벨 필터, 검색, 일시정지, 따라가기, 복사, 지우기. 소스마다 최근 1000줄을 메모리에만 두고 1초마다 새 줄만 받는다 (`GET /api/logs?since=`). manager만 볼 수 있다. |
| FR6-17 | 로봇 online/offline이 바뀌면 시스템 패널을 다시 보내 낡은 "토픽 없음" 배지를 없앤다. offline 배지에 마지막 수신 토픽/시각을 툴팁으로 보인다. 서버 시작 때 `ROS_DOMAIN_ID`/`ROS_LOCALHOST_ONLY`를 로그로 남긴다. |

### Step 6-5. 로봇 추가 안내 팝업 (구현됨)

| ID | 요구사항 |
| --- | --- |
| FR6-18 | 왼쪽 메뉴 `로봇 추가`: 이름, 도메인 ID, IP(SSH 사용자)를 입력하면 `robots.yaml`, `zone.yaml`, `processes.yaml`에 붙여 넣을 조각과 GUI 밖에서 할 일(SSH 키 등록, DDS 설정, 브릿지 YAML·순찰 노드 만들기, 문 지점 좌표, 서버 재시작)을 단계 카드로 보여 준다. 입력값이 들어간 자리는 입력칸 색으로 표시하고, 복사할 때는 색 표시를 뺀다. 서버 설정을 자동으로 바꾸지 않는다. |

### Step 6-6. 실물 확인 A: 로봇이 움직이지 않는 확인 (대기)

체크리스트 A1~A7. 로그인 역할별 버튼, 터미널에서 미리 켠 브릿지와의 중복 거절, GUI로 브릿지 시작·로그·정지(자식 프로세스 잔존 없음), zone_manager 시작·정지와 구역 배지, 잘못된 SSH host 실패 표시. 준비: SSH 키 등록(`ssh-copy-id`)과 `processes.yaml` 값 확인(`ls ~/pinky_slam_nav /opt/ros/*/setup.bash`, `map:=my_pinky_map10.yaml`을 `cwd`에서 찾는지).

### Step 6-7. 실물 확인 B: 로봇 프로세스 (대기, **로봇이 움직일 수 있음**)

체크리스트 B1~B7 + B6a~c. pinky1 bringup 시작과 online·health, 이미 켠 프로세스 거절, map(Nav2) 시작과 `/amcl_pose`, 확인 팝업 후 정지(Nav2 노드 소멸), 정지 후 로봇에 남은 경우의 "실패" 표시, 전체 시작 진행 문구(브릿지 자동 재시작 1회), SSH로 띄운 프로세스의 DDS `env` 적용, pinky2 순찰 노드(`ssh-copy-id pinky@192.168.45.22` 선행), 전체 정지(반대 순서, 로봇 경고 팝업). 주변을 비우고 한다.

### Step 6-8. 실물 확인 C: 서버 재시작과 한계 (대기)

체크리스트 C1~C4. 서버를 Ctrl-C로 끄면 관제 PC 프로세스와 로봇의 bringup/map이 같이 정지하는지, 다시 켜면 "이미 실행 중"이 나오지 않는지, `kill -9`/전원 차단 뒤에는 잔존 프로세스 때문에 거절되는지(로봇에서 직접 `pkill -INT -f "[r]os2 launch pinky_bringup"`).

### 완료 기준 (Step 6 전체)

- [x] 6-1 ~ 6-5: 단위 테스트와 mock 검증 통과 (`tests/test_auth.py`, `test_procs.py`, `test_logstore.py`, `verify_proc_mock.py`, `verify_proc_ui.py`) — 구현 시 통과 확인됨
- [ ] 6-6: A1~A7 모두 통과 (사용자 확인)
- [ ] 6-7: B1~B7 모두 통과 (사용자 확인)
- [ ] 6-8: C1~C4 모두 통과 (사용자 확인)
- [ ] 위가 끝나면 `../reference/known-pitfalls.md`의 "프로세스 제어는 실물에서 실행하지 않았다" 항목과 `CLAUDE.md` 7절의 같은 줄을 지운다.

### 알려진 한계

- GUI는 자기가 띄운 프로세스만 정지할 수 있다. 터미널에서 켠 것은 감지만 한다.
- SSH 원격 정지는 네트워크가 끊기면 보장되지 않는다. 로봇에서 `ros2 node list`로 확인한다.
- 소프트웨어 버튼이다. 주행 중 비상 상황은 기존 비상정지(하드웨어 정지 포함)로 대응한다.
- 비밀번호는 소스에 고정돼 있고 평문 HTTP로 오간다. 신뢰하는 내부망에서만 쓴다. 저장소를 GitHub에 올리면 비밀번호도 공개된다.
- `kill -9`로 서버를 죽이면 정리하지 못해 관제 PC 쪽 자식이 남는다 (`pgrep -fa domain_bridge`).

### Claude Code 프롬프트 (실물 확인 결과 반영용)

```text
CLAUDE.md와 docs/checklists/step6_process_control_real_checklist.md를 먼저 읽어줘.
내가 아래에 적는 실물 확인 결과(통과/실패, 화면 캡처, 백엔드 로그)를 Step 6-6 ~ 6-8의 완료 기준에 반영해줘.
- 통과한 항목은 체크리스트와 PRD의 체크박스를 채우고, 실패한 항목은 원인을 코드로 확인한 뒤 수정안을 먼저 보여줘
- 로봇에서 직접 실행하지는 말고, 확인이 필요한 명령은 내가 실행할 수 있게 적어줘
- processes.yaml의 값이 실제와 달랐다면 어떤 값인지 알려줘
[여기에 결과 붙여넣기]
```

## 실물 로봇 테스트 가이드

각 Step은 mock 검증을 통과한 뒤 아래 순서로 직접 확인한다. 순서는 '읽기만 하는 기능 → 명령 기능 → 위험한 동작'이고, 한 번에 한 가지만 바꿔서 문제 원인을 GUI, 로봇, 네트워크로 나눠 찾을 수 있게 한다.

### 공통 원칙

1. 첫 시험은 pinky1 한 대만, 주변 1 m 이상을 비운 바닥에서, 로봇 전원에 손이 닿는 거리에서 한다. 소프트웨어 비상정지에 의존하지 않는다.
2. GUI를 의심하기 전에 기존 CLI(`pinky_control_client_v2.py`)로 같은 동작이 되는지 먼저 확인한다. CLI가 되면 로봇과 네트워크는 정상이고 문제는 GUI다.
3. 화면에 보이는 값은 항상 `ros2 topic echo`의 원본 값과 나란히 비교한다.
4. 속도와 거리는 가장 낮은 값으로 시작한다.
5. 통신이 끊기는 것 같으면 `ros2_net_diag_v2.sh`로 Wi-Fi 상태부터 본다.

### 시작 순서와 사전 점검 (매번)

순서는 README의 실행 순서에 GUI 백엔드를 마지막에 더한다: ① 로봇 Nav2 bringup → ② 관제 PC `domain_bridge` → ③ `zone_manager_node` → ④ 로봇 순찰 노드 → ⑤ GUI 백엔드. 순찰 노드는 시작할 때 `IDLE`을 한 번만 발행하므로, Step 1의 초기 상태 확인 때만 백엔드를 먼저 켜고 순찰 노드를 나중에 켠다.

```bash
# 관제 PC (배포판에 따라 --once가 없으면 Ctrl+C로 종료)
export ROS_DOMAIN_ID=50
ros2 node list                                  # zone_manager_node가 있는지
ros2 topic echo /zone_manager/status --once     # free
ros2 topic echo /pinky1/amcl_pose --once        # 위치가 들어오는지
ros2 topic echo /pinky1/battery_state --once
python3 pinky_control_client_v2.py pinky1 monitor   # 기준선: patrol_status 수신
```

### Step 0. 로봇 없이 확인

`ros2 node list`로 `zone_manager_node`(v1)가 실제로 떠 있는지 확인해서 v1/v2 결정을 확정한다.

### Step 1. 읽기 전용 (로봇이 움직이지 않음)

1. 사전 점검 명령으로 `amcl_pose`, `battery_state`, `patrol_status`가 관제 PC에 들어오는지 확인한다.
2. 백엔드를 실행하고 `/api/state`의 pose와 배터리 값을 `ros2 topic echo` 값과 비교한다.
3. 백엔드를 먼저 켠 상태에서 순찰 노드를 재시작해 `IDLE`이 표시되는지 본다. 반대로 백엔드를 나중에 켰을 때 상태가 비어 있어도 서버가 죽지 않고 '알 수 없음'으로 보이는지 본다.
4. 로봇 전원을 끄고 5초 안에 `online: false`가 되는지, 다시 켜면 `true`로 돌아오는지 본다.
5. 한 로봇만 꺼도 다른 로봇의 값이 정상인지 본다.

**합격**: 값이 원본과 일치하고, 오프라인 판정이 5초 안에 이뤄진다.

### Step 2. 지도 위치 정합

1. 로봇을 지도의 알려진 지점(P1 원점, P3 등)에 놓고 AMCL 초기 위치를 맞춘다 (RViz 또는 순찰 노드의 초기 위치 보정).
2. 마커 위치를 `amcl_pose` 값과 비교한다. 마커가 `amcl_pose`와 같은데 실제 위치와 다르면 GUI가 아니라 AMCL 추정의 문제다.
3. 기존 teleop으로 로봇을 천천히 움직이며 마커와 방향 화살표가 따라가는지 본다. 방향이 90도 또는 180도 틀리면 yaw 변환 오류다.
4. 브라우저를 새로고침하고 백엔드를 껐다 켜서 자동 재연결되는지 본다.

**합격**: 마커가 `amcl_pose`와 일치하고, 이동과 방향을 정확히 따라간다.

### Step 3. 명령 (처음으로 로봇이 움직임)

1. **기준선**: CLI로 `goto P2`를 보내 동작과 홈 복귀를 확인한다. 노드를 켠 뒤 첫 작업이면 초기 위치 보정 회전이 있으니 공간을 비워 둔다.
2. GUI로 같은 `goto P2`를 보내 CLI와 같은 동작인지 확인한다.
3. 구역 밖 두 지점(`P2`, `P7`)을 보내 지점별 10초 대기, 홈 복귀, 화면의 진행 표시를 확인한다.
4. 구역 안 지점(`P3`)을 한 대만 보내 RED 문 경유와 진입 허가 후 진행되는지 확인한다.
5. 이동 중에 정지를 눌러 `STOPPED`가 되고 로봇이 멈추는지 확인한다.
6. 작업 중에 `start`를 다시 보내 5초 후 '응답 없음/무시됨'이 뜨는지 확인한다.
7. 순찰 노드를 재시작한 직후 첫 명령에서 '시작 준비 중' 안내가 뜨는지 확인한다.
8. **두 로봇**: 각각 `goto P3`를 거의 동시에 보내 한 대가 `WAITING_ZONE`(빨강)으로 대기하고, 구역이 비면 이어서 진행하는지 확인한다. 로봇마다 사람이 곁에 있어야 한다.

**합격**: GUI와 CLI의 동작이 같고, 구역에는 한 번에 한 대만 들어간다. **중단**: 로봇이 예상과 다른 방향으로 가면 즉시 전원을 끄고 CLI로 같은 명령을 재현해 본다.

### Step 4. 비상정지와 오버레이 (가장 위험한 단계)

1. **구역 배지**: CLI로 한 대를 구역에 보내고 `ros2 topic echo /zone_manager/status` 값과 화면 배지를 비교한다.
2. **LiDAR**: 토글을 켜고 끌 때 관제 PC(도메인 50)에서 `ros2 topic info /pinky1/scan`의 구독자 수가 바뀌는지, 점이 벽 윤곽과 겹치는지 본다.
3. **바퀴를 바닥에서 띄운 상태**(받침 위)로 비상정지와 수동 조작을 먼저 시험한다. 수동 조작은 버튼을 떼거나 탭을 닫으면 0.5초 안에 바퀴가 멈춰야 하고, 비상정지는 바퀴가 도는 중에 눌러 멈춰야 한다.
4. 바닥에서 낮은 속도로 순찰 중에 비상정지를 눌러 2초 안에 서는지 본다.
5. 노드를 켠 뒤 **첫 작업의 초기 위치 보정 회전 중**에 비상정지를 눌러 본다.
6. `lane_follower_node`가 실행 중일 때도 비상정지가 먹히는지 본다.

**합격**: 3\~6이 모두 통과한다. 하나라도 실패하면 GUI 비상정지를 안전장치로 쓰지 않고, 원인(`cmd_vel` 경합 등)을 해결한 뒤 다시 시험한다.

### Step 6. 인증과 프로세스 제어

`../checklists/step6_process_control_real_checklist.md`를 따른다 (6-6 로봇이 움직이지 않는 확인 → 6-7 로봇 프로세스 → 6-8 서버 재시작). 이 Step의 실물 확인은 아직 한 번도 하지 않았다.

### Step 5. 부가 기능

1. **카메라**: 카메라 브리지를 띄운 뒤 `ros2 topic hz /pinky1/camera/image_raw/compressed`로 수신 주기를 보고, 영상을 보는 동안 `ros2_net_diag_v2.sh`로 끊김이 늘지 않는지, 패널을 닫으면 구독이 해제되는지 확인한다.
2. **이력**: 순찰을 한 번 돌린 뒤 이력에 상태 변경과 구역 점유·해제가 순서대로 남는지 본다.
3. **인증**: 다른 기기(스마트폰 등)로 접속해 보기 전용 계정에서 명령이 거절되는지 본다.

## 부록. Claude Code 사용법과 결정이 필요한 사항

### Claude Code로 진행하는 방법

1. **한 번에 한 Step만** 요청한다. 해당 Step의 "기능 요구사항" 표와 "완료 기준"을 프롬프트에 그대로 붙여 넣는다.
2. 새 세션을 열 때마다 `CLAUDE.md`를 먼저 읽게 한다 (Step 0에서 만든 파일이 기억 역할을 한다).
3. 구현 전에 **계획을 먼저 보여 달라고** 요청하고, 계획이 "기존 파일 수정 금지" 원칙을 지키는지 확인한 뒤 진행시킨다.
4. Step이 끝나면 Claude Code에게 "완료 기준 항목을 하나씩 실제로 실행해서 확인하고 결과를 표로 보여줘"라고 요청한다.
5. Step마다 git 브랜치를 만들고 커밋한다. 문제가 생기면 이전 Step으로 되돌릴 수 있다.
6. 실제 로봇 테스트는 사람이 직접 한다. Claude Code에게는 mock 모드 검증과 로봇 테스트 체크리스트 작성까지만 시킨다.

### 결정 또는 확인이 필요한 사항

| 항목 | 왜 중요한가 | 필요한 시점 |
| --- | --- | --- |
| 구역 영역 좌표 (`config/zone.yaml`) | v1에는 구역 좌표가 없어서 P3\~P6와 RED 문 좌표로 새로 정의해야 한다. 실제 구역 경계와 맞는지 확인이 필요하다. | Step 4 |
| v1에서 v2 zone manager로 전환할 계획 | 현재 순찰 노드는 v1 클라이언트를 쓰고, v2 파일은 구역 좌표가 TODO 상태이며 순찰 노드에 연결되어 있지 않다. 전환 계획이 있으면 Step 4의 구역 표시 방식이 달라진다. | Step 0 |
| 포인트 좌표의 중복 관리 | 같은 `POINTS`가 pinky1·pinky2 파일에 각각 복사되어 있고, GUI의 `points.yaml`까지 합치면 세 곳이다. 값이 어긋나면 화면과 실제 주행이 달라진다. 장기적으로 순찰 노드가 공통 YAML을 읽도록 바꾸는 것이 좋다 (별도 작업). | Step 3 |
| RED 문 좌표 | RED1(0.60, -0.50)과 RED2(0.55, -0.60)의 문 위치가 서로 가깝다. 실제 맵에서 두 문이 의도한 위치인지 확인한다. | Step 0 |
| 첫 작업의 초기 위치 보정 | 노드 실행 후 첫 작업에서 저장된 마지막 위치(`~/.pinky_last_pose.json`)로 AMCL을 초기화하고 회전한다. 로봇을 손으로 옮긴 뒤 노드를 재시작하면 위치 추정이 틀어질 수 있다. GUI에서 초기 위치(`initialpose`)를 지정하는 기능이 필요한지 정한다. | Step 3 |
| 지점별 대기 시간 | 지점마다 10초 대기는 노드 안의 상수(`GOTO_WAIT_SEC`)다. GUI에서 바꾸려면 노드가 파라미터로 받도록 고쳐야 한다 (별도 작업). 지금은 표시만 한다. | Step 3 |
| `cmd_vel` 발행 주체 | 관제 PC의 `lane_follower_node`, Nav2, 순찰 노드의 초기 위치 보정 회전이 모두 `cmd_vel`을 낼 수 있다. 비상정지가 실제로 먹히는지 로봇에서 확인해야 한다. | Step 4 |
| 명령 무시에 대한 응답 | 순찰 노드가 작업 중 `start`나 `goto`를 받으면 응답 없이 무시한다. 현재는 GUI에서 "응답 없음"으로 추정한다. 노드가 `IGNORED` 같은 상태를 발행하게 바꾸면 더 정확해진다 (별도 작업). | Step 3 |
| ROS 2 배포판, Python 버전 | 아직 확인하지 못했다. `rclpy`와 FastAPI의 설치 방식이 달라질 수 있다. | Step 0 |
| 카메라 영상 대역폭 | Wi-Fi 품질에 따라 5fps도 부담이 될 수 있다. `ros2_net_diag_v2.sh`로 먼저 측정해 보는 것이 좋다. | Step 5 |

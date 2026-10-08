# pinky_web_gui (Step 1~4: 백엔드, 지도, 명령 패널, 구역/비상정지/오버레이)

관제 PC(`ROS_DOMAIN_ID=50`)에서 두 로봇의 상태 토픽을 구독해 하나의 상태 모델로 합치고 WebSocket으로 중계하는 FastAPI 백엔드다. 로봇으로는 허용 목록 검증을 거친 `start`/`stop`/`goto`(Step 3)와, 비상정지·수동 조작의 `cmd_vel`(Step 4)만 나간다.
브라우저에서 `http://localhost:8000/` 로 지도 화면(`frontend/`)이 열린다 (빌드 단계 없음).
토픽 규약은 `docs/interfaces.md`, 작업 규칙은 `CLAUDE.md`를 본다.

## 설치 (한 번만)

`pinky_web_gui/` 에서 실행한다. rclpy 는 ROS 2 가 제공하므로 venv 를 `--system-site-packages` 로 만든다. 시스템 Python 에는 설치하지 않는다.

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

## 실행

### mock 모드 (ROS 불필요)

```bash
.venv/bin/python -m backend.main --mock
```

가짜 순찰 노드가 두 로봇을 시뮬레이션한다 (Step 4: 받은 `cmd_vel` 로 pose 를 움직이고, LiDAR 는 지도 점유 격자에 레이캐스트해서 만든다. `GET /api/mock/cmd_vel` 로 받은 `cmd_vel`/`patrol_cmd` 기록을 본다) (pose 20Hz, battery, zone). 로봇은 홈 지점에서 IDLE 로 서 있다가 화면(또는 `/api/robots/{id}/command`)에서 받은 `start`/`stop`/`goto` 대로 움직인다. 실제 노드처럼 작업 중 `start`/`goto` 는 조용히 무시하고, 첫 작업은 초기 위치 보정으로 STARTING 이 6초 이어지며, 구역(P3~P6)은 두 로봇이 하나만 쓴다 (`backend/mock.py` 상단 설명).

### 실제 로봇 연결

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=50
.venv/bin/python -m backend.main
```

먼저 브리지(`launch/bridges.launch.xml`)가 떠 있어야 `/{id}/...` 토픽이 보인다 (`CLAUDE.md` 3절).

### 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--mock` | 꺼짐 | ROS 없이 가짜 데이터로 실행 (rclpy 를 import 하지 않음) |
| `--host` | `127.0.0.1` | 다른 PC 에서 접속하려면 `0.0.0.0` |
| `--port` | `8000` | |
| `--mock-wait-scale` | `1.0` | `--mock` 에서 goto 지점 대기 시간 배율 (0.2 면 10초 대신 2초) |
| `--config` | `config/robots.yaml` | 설정 파일 경로 |

`--host 0.0.0.0` 은 인증이 없으므로 신뢰할 수 있는 네트워크에서만 쓴다 (Step 4 부터는 같은 네트워크의 누구나 비상정지와 수동 조작도 보낼 수 있다. 시작할 때 경고를 로그로 낸다). 다른 사이트의 페이지가 localhost 의 제어 API 를 부르지 못하도록 POST 는 `Content-Type: application/json` + 같은 Origin 만, WebSocket 은 같은 Origin 만 받는다 (Origin 헤더가 없는 curl 등은 통과).

## 설정 (`config/robots.yaml`)

로봇 ID 목록, zone status 토픽, `online_timeout_sec`(5), `pose_max_hz`(10)를 둔다. 코드에는 로봇 이름이 없다. 로봇을 늘리거나 이름을 바꾸려면 이 파일만 고친다.

명령 패널(Step 3) 설정도 여기에 있다.
- `robot_settings.<id>`: `goto_allowed`(백엔드가 허용하는 goto 지점), `home`(goto 완료 후 복귀 지점), `goto_wait_sec`/`wait_every_point`(노드의 `GOTO_WAIT_SEC`/`WAIT_EVERY_POINT` 와 맞춘다), `start_route`(노드의 `WAYPOINTS` 이름). `goto_allowed` 에 `RED*` 를 넣으면 서버가 시작되지 않는다.
- 전역: `max_goto_points`(10), `no_response_sec`(5), `starting_notice_sec`(5), `command_history_size`(50).
- `config/points.yaml`: 지도에 그리는 포인트 좌표. 화면과 mock 용이며 검증에는 쓰지 않는다.
- 이 값들은 로봇의 노드 상수와 **수동으로 맞춰야** 한다. 노드의 `GOTO_ALLOWED` 나 `HOME_NAME` 을 바꾸면 여기도 고친다.

Step 4 설정도 `robots.yaml` 에 있다.
- `zone_yaml: zone.yaml` → `config/zone.yaml`: 위험 구역 사각형(구역 지점 `points` 의 경계 + `margin`)과 로봇별 진입·이탈 문(`doors`). 좌표는 `points.yaml` 에서 계산한다. **`confirmed: false` 는 초안이다**: 사용자가 지도와 대조해 경계를 확인하고 `margin` 을 고친 뒤 `true` 로 바꾼다. 그 전에는 화면이 구역을 점선으로 그리고 모서리 좌표와 "초안(미확인)" 을 표시한다.
- `planner_inflation_m`(0.08): 경로 추정이 벽에서 떨어지는 거리. 지점의 벽 여유 최소값(0.10 m)보다 작아야 모든 지점이 닿는다. `motion`: `estop_burst_hz`(10), `estop_burst_sec`(2.0). `manual`: `enabled`, `max_linear`(0.1 m/s), `max_angular`(0.5 rad/s), `input_timeout_sec`(0.5), `rate_hz`(10). 코드에 더 낮은 하드 상한(0.2 m/s, 1.0 rad/s)이 있어서 설정이 이를 넘으면 서버가 시작되지 않는다. `scan`: `max_hz`(5), `decimate`(3).

## 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /api/health` | `{"ok", "mock", "robots", "ws_clients", "scan_clients", "scan_subscribed", "motion"}` (`scan_subscribed` 는 실제로 `/scan` 을 구독 중인 로봇) |
| `GET /api/state` | 전체 상태 (WebSocket `snapshot` 과 같은 구조) |
| `WS /ws` | 접속 즉시 `snapshot`, 이후 변경분 `robot_update` / `zone` / `command` / `scan` / `drive_denied`. 브라우저가 보내는 제어 메시지는 아래 "Step 4" 절 |
| `POST /api/robots/{id}/command` | 명령 전송 (아래 "명령 API") |
| `POST /api/robots/{id}/estop`, `POST /api/estop` | 비상정지 (한 대 / 모든 로봇). 아래 "Step 4" 절 |
| `GET /api/plans` | 지점 쌍마다 벽을 피하는 경로 추정 (`"P3>P6": {"path": [[x, y], ...], "length"}`) |
| `GET /api/zone/config`, `GET /api/motion/config` | 구역 사각형·문·`confirmed`, 비상정지/수동 조작/LiDAR 설정 |
| `GET /api/commands/config` | 로봇별 허용 지점·홈·대기·`start_route`, 지점 좌표, 표시 임계 시간 |
| `GET /api/map` | 지도 메타데이터 (width, height, resolution, origin, negate, occupied_thresh, free_thresh, image_url). 값은 `config/robots.yaml` 의 `map_yaml` 이 가리키는 yaml/pgm 에서 읽는다 |
| `GET /api/map/image` | 지도 PNG (pgm 픽셀 값 그대로, 8비트 그레이) |
| `GET /` | 지도 화면 (`frontend/index.html`) |
| `GET /console` | 콘솔 확인용 최소 HTML (메시지를 `console.log` 로 출력) |

### 명령 API (Step 3)

```bash
curl -s -X POST localhost:8000/api/robots/pinky1/command -H 'Content-Type: application/json' \
  -d '{"cmd": "goto", "points": ["P2", "P3", "P6"]}'      # -> {"accepted": true, "sent": "goto:P2,P3,P6", "id": 7}
```

| 본문 | 발행 문자열 |
|---|---|
| `{"cmd": "start"}` | `start` |
| `{"cmd": "stop"}` | `stop` |
| `{"cmd": "goto", "points": ["P2", ...]}` | `goto:P2,...` (대문자, 쉼표) |

- 검증은 **백엔드가 한다** (`backend/commands.py`). 지점은 strip + 대문자로 바꾼 뒤 그 로봇의 `goto_allowed` 와 정확히 일치해야 한다. 1~`max_goto_points`개. `RED*` 는 설정과 무관하게 항상 거절한다. `start`/`stop` 에 `points` 를 주면 거절한다. 실패는 **400** (`{"accepted": false, "error": 사유}`), 알 수 없는 로봇은 404.
- 구독자가 없으면(브리지가 꺼져 있으면) **503** 이다. 발행해도 아무도 받지 않기 때문이다.
- `accepted: true` 는 "토픽에 발행했다" 는 뜻일 뿐이다. 응답 여부는 이후 `patrol_status` 로 판단해 이력의 결과로 나온다: `start`/`goto` 는 `STARTING`, `stop` 은 `STOPPED` 가 오면 `acknowledged`, `no_response_sec` 안에 안 오면 `no_response`. 작업 중인 로봇은 무시한 명령과 상관없이 상태가 계속 바뀌므로 아무 상태 변화나 응답으로 보지 않는다.
- `stop` 은 노드가 작업 중이 아닐 때와 초기 위치 보정 회전 중에는 반영하지 않으므로 `no_response` 로 보일 수 있다.
- `online: false` 로봇에도 명령은 막지 않는다 (버튼 비활성은 화면의 힌트). 거절과 전송 실패도 이력에 남는다.
- WS `command` 이벤트(`{"type": "command", "entry": {id, time, robot, cmd, points, sent, result, detail}}`)는 명령 기록과 결과 갱신을 `id` 로 구분해 보낸다. `snapshot` 에는 `commands`(최근 N건)와 로봇별 `last_command`(마지막 명령), `last_task`(마지막 start/goto, 진행 문구 해석 기준)가 들어 있다.

### 구역, 비상정지, LiDAR, 수동 조작 (Step 4)

**구역 상태**: `/zone_manager/status` 의 `free`, `occupied_by:<id>`(현재 구성, v1)와 `occupied_by:<id>:<token>`(v2)을 모두 해석한다 (`backend/zone.py`). 알 수 없는 값은 비어 있다고 추측하지 않고 `unknown` 이다. WS `zone` 메시지는 `status`(원문)에 `state`(`free`|`occupied`|`unknown`), `holder`, `token`, `held_sec`(백엔드가 그 점유를 처음 본 뒤 지난 시간)를 더해 보내고, `snapshot` 에는 `zone_info` 가 들어 있다. 매니저는 점유가 **바뀔 때만** 발행하므로, 백엔드를 구역 점유 중에 켜면 다음 변화 전까지 `null`("상태 수신 전")이다.

**비상정지** (`POST /api/robots/{id}/estop`, `POST /api/estop`; 본문 `{}`): ① `patrol_cmd` 로 `stop` ② `/{id}/cmd_vel` 에 0 속도를 즉시 1회, 이어서 10Hz 로 2초. 확인이 없고 오프라인이거나 구독자가 없어도 시도한다. 응답: `{"accepted", "patrol_stop", "cmd_vel", "errors"}` (둘 중 하나라도 나가면 200, 둘 다 못 나가면 503). 이력(`cmd: "estop"`)은 `stop` 에 `STOPPED` 가 오면 `acknowledged` 이고, 오지 않아도 `no_response` 가 아니라 "이미 멈춰 있었거나 stop 이 반영되지 않았을 수 있음 (cmd_vel 0 속도는 전송됨)" 설명이 붙는다. 이 기능은 **소프트웨어 정지**이며 하드웨어 비상정지를 대체하지 않는다.

**LiDAR**: 브라우저가 `{"type":"scan","robot":id,"on":true|false}` 를 보내면, 한 화면이라도 켠 로봇만 서버가 `/{id}/scan` 을 구독한다 (`qos_profile_sensor_data`, 5Hz 제한, 3개당 1개). 마지막 화면이 끄거나 끊기면 구독을 해제한다. 서버가 지도 좌표로 바꿔 켠 화면에만 `{"type":"scan","robot","source","frame","offset_deg","pose","points":[[x,y],...]}` 를 보낸다 (`backend/scan.py`). 센서의 위치와 방향은 로봇의 `/{id}/tf`(켠 동안만 구독), `/{id}/tf_static`(시작할 때부터, TRANSIENT_LOCAL)에서 map → odom → base → 센서(scan 헤더의 `frame_id`) 변환을 합성해 구한다 (`source: "tf"`, 3D 회전이라 돌려 달았거나 뒤집힌 센서도 맞는다). 변환을 못 구하면 `amcl_pose` + `config/robots.yaml` 의 `scan.yaw_offset_deg`(로봇별 보정 각도)로 대신 그린다 (`source: "amcl"`). 화면의 LiDAR 줄에 어느 쪽인지 표시된다. 점이 로봇 정면과 다른 방향으로 어긋나 보이는데 `amcl` 로 표시된다면 `yaw_offset_deg` 에 그 각도(예: `pinky1: 180`)를 적는다.

**수동 조작** (데드맨): 브라우저는 누르는 동안 `{"type":"drive","robot":id,"linear":v,"angular":w}` 를 10Hz 로 보내고, 떼면 `{"type":"drive_stop"}` 을 보낸다. 서버(`backend/motion.py`)는 속도를 설정 상한으로 자르고 10Hz 로 `cmd_vel` 을 발행하며, **입력이 0.5초 없거나, WebSocket 이 끊기거나, 순찰이 시작되거나, 오프라인이 되거나, 비상정지가 오면 즉시 0 속도**를 보낸다. 순찰 중(작업 중 상태)·오프라인·비상정지 burst 중에는 입력을 거절하고(`drive_denied`), 한 로봇은 한 화면만 조작한다. 서버를 종료할 때도 마지막 0 속도를 보낸다.

- `cmd_vel` 은 관제 PC 의 `lane_follower_node`, 순찰 노드의 보정 회전(`spin_in_place`)도 낼 수 있어서 **마지막 발행이 이긴다**. 비상정지가 실제로 먹히는지는 로봇에서 확인해야 한다 (`docs/step4_real_robot_checklist.md`).
- 서버가 `kill -9` 등으로 갑자기 죽으면 0 속도를 보낼 수 없다. 로봇 쪽에 `cmd_vel` 타임아웃이 있는지는 코드로 확인되지 않았다.

### 상태 모델

```json
{"type": "snapshot", "zone": "free",
 "robots": {"pinky1": {"online": true, "last_seen": 1759560000.1,
   "patrol":  {"state": "MOVING", "waypoint": 2, "detail": "", "time": 1759560000.0},
   "pose":    {"x": 0.65, "y": 0.15, "yaw": 0.0},
   "battery": {"percentage": 82.0, "voltage": 7.9}}}}
```

- 아직 수신하지 못한 값은 `null`이다 (`patrol`/`pose`/`battery`/`zone`). `last_seen` 은 서버 시계(Unix 초)다.
- `patrol.time` 은 **로봇 시계** 기준이다 (`docs/interfaces.md` 3절).
- `robot_update` 의 `field` 는 `patrol` | `pose` | `battery` | `online` | `last_command` | `last_task`. pose 는 로봇당 최대 10Hz 로 보내며 `/api/state` 는 항상 최신값이다.
- `online`: 해당 로봇의 **어떤 토픽이든** 마지막 수신 후 5초 이내. `patrol_status` 는 변할 때만 발행되므로 이 토픽만으로 판정하지 않는다.
- `patrol_status` 가 JSON 객체가 아니면 서버는 경고 로그만 남기고, 원문을 `detail` 에 넣고 `waypoint=-1`, `state` 는 직전 값을 유지한다(없으면 `null`).
- 같은 내용의 `patrol_status` 가 반복되면 이벤트를 내지 않는다.
- 배터리는 `/{id}/battery/percent`, `/{id}/battery/voltage`(Float32) 두 토픽을 합친 값이다. 로봇이 `/battery_state` 를 발행하지 않아서 브리지 YAML 두 개에 이 토픽을 추가했다 (브리지 재시작 필요). 한쪽만 오면 다른 쪽은 `null`이다. 값은 로봇이 준 그대로이며 **percent 는 0~100 단위**다 (pinky1 실측 94.65).

## 테스트

```bash
.venv/bin/python -m pytest tests -q        # 단위 테스트 (상태, 명령 검증, mock 흐름, 구역 파싱, 스캔 변환, 비상정지/데드맨)
.venv/bin/python tests/verify_step3_mock.py   # Step 3 API (mock 서버를 띄워 확인)
.venv/bin/python tests/verify_step4_mock.py   # Step 4 API/WS: LiDAR 구독 수, 비상정지, 데드맨, 출처 검사
.venv/bin/python tests/verify_step3_ui.py     # headless Chrome (Chrome 필요)
.venv/bin/python tests/verify_step4_ui.py --shot docs/step4_screenshot.png --zone-shot docs/step4_zone_draft.png --scan-shot docs/step4_lidar.png
```

`RosBridge` 의 ROS 경로(스캔 구독/해제, tf 로 센서 방향 구하기, `cmd_vel`, 종료 시 0 속도)는 **실제 로봇 도메인과 분리된** 격리 도메인에서만 확인한다. 이 스크립트는 `ROS_DOMAIN_ID=77`, `ROS_LOCALHOST_ONLY=1` 이 아니면 실행을 거부한다 (이 PC 의 기본 `ROS_DOMAIN_ID` 는 관제 도메인 50 이다).

```bash
bash -c 'source /opt/ros/jazzy/setup.bash && ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1 .venv/bin/python tests/verify_step4_ros.py'
```

mock 서버를 띄운 상태에서:

```bash
curl -s localhost:8000/api/health
curl -s localhost:8000/api/state | python3 -m json.tool
```

브라우저에서 `http://localhost:8000/console` 을 열고 F12 콘솔에서 `snapshot`/`robot_update` 를 확인한다.

### 지도 화면 확인 (Step 2)

```bash
.venv/bin/python -m backend.main --mock      # 그리고 브라우저에서 http://localhost:8000/
.venv/bin/python tests/verify_transform.py   # 변환식이 기존 pinky_map_viewer.html 과 같은지 (Chrome 필요, PASS 가 나와야 함)
```

mock 모드에서 두 마커가 지도 오른쪽 방 안의 사각형을 돌고, 마커 방향 화살표가 진행 방향과 같아야 한다. 스크린샷: `docs/step2_screenshot.png`.

| 확인 | 방법 |
|---|---|
| 호버 좌표/occupancy | 지도 위에서 마우스를 움직이면 아래 줄에 pixel, map(m), occupancy 가 나온다 |
| 클릭 좌표 기록 | 지도를 클릭하면 오른쪽 "클릭 좌표"에 쌓이고 복사/삭제할 수 있다. 드래그(4px 초과 이동)는 클릭으로 치지 않는다 |
| 확대/이동 | 휠로 커서 위치 기준 확대, 드래그로 이동, "맞춤" 버튼으로 복귀 |
| 재연결 | 백엔드를 껐다 켜면 "연결 끊김"이 뜨고, 1초에서 5초까지 늘려가며 자동 재연결한다. 끊긴 동안 마커는 흐리게 보인다 |
| 모바일 폭 | 창 너비를 860px 아래로 줄이면 메뉴가 위 가로 줄이 되고 사이드바가 지도 아래로 내려간다 |

## 실제 로봇 확인 절차 (사용자 수행)

1. `ros2 topic echo /pinky1/amcl_pose` 의 값과 `/api/state` 의 `pose` 가 일치하는지 본다 (yaw 는 쿼터니언 → rad).
2. 로봇 전원을 끄고 5초쯤 뒤 `/api/state` 의 `online` 이 `false` 가 되는지 본다.
3. `ros2 topic pub /pinky1/patrol_status std_msgs/msg/String "{data: 'not json'}"` 후에도 서버가 살아 있고 `detail` 에 원문이 담기는지 본다. 이 발행은 실제 로봇 상태 표시를 덮어쓰므로 로봇이 꺼져 있거나 테스트 중일 때만 한다.

## 구조

```
config/robots.yaml     설정
backend/main.py        FastAPI 앱, 옵션, lifespan
backend/state.py       StateStore (상태 모델, online 판정, pose 제한)
backend/ros_bridge.py  rclpy 노드, 별도 스레드 spin
backend/mock.py        --mock 가짜 데이터
backend/hub.py         WebSocket 클라이언트별 큐와 브로드캐스트
backend/map_loader.py  지도 yaml/pgm -> 메타데이터 + PNG (표준 라이브러리만 사용)
backend/static/        콘솔 확인용 HTML
backend/commands.py    명령 검증(validate), 이력과 응답 추적(CommandTracker, 비상정지 기록 포함)
backend/zone.py        구역 상태 해석(v1/v2)과 점유 시간 (rclpy 무관)
backend/scan.py        LiDAR 점 감소, 빈도 제한, 극좌표 -> 지도 좌표 (rclpy 무관)
backend/planner.py     지도 점유 격자 위의 최단 경로 추정 (A*), 경로 선과 mock 이동에 사용
backend/motion.py      cmd_vel 발행 관리: 비상정지 burst, 수동 조작 데드맨 (rclpy 무관, 시계/발행 주입)
config/zone.yaml       위험 구역 사각형과 문 (confirmed: false 는 초안)
frontend/              지도 화면 (index.html, style.css, app.js, mapmath.js, patrolview.js, zoneview.js)
frontend/zoneview.js   구역 배지 문구, 구역 진입 대기 문구, 경로 오버레이 계산 (순수 함수)
frontend/patrolview.js 배지 색, 버튼 활성 조건, 진행 문구 등 명령 패널 화면 로직 (순수 함수)
frontend/mapmath.js    월드<->픽셀 변환과 occupancy 판정 (변환식이 있는 유일한 곳)
tests/                 단위 테스트, verify_transform.py (변환식 대조), verify_step{3,4}_mock.py (API/WS), verify_step{3,4}_ui.py (headless Chrome 화면), verify_step4_ros.py (격리 도메인)
```

스레드 모델: rclpy 콜백(별도 스레드)은 값을 뽑아 `loop.call_soon_threadsafe` 로 `asyncio.Queue` 에 넣는다. 큐를 소비하는 단일 태스크만 `StateStore` 를 바꾸고 브로드캐스트하므로 락이 없다. mock 도 같은 큐로 같은 이벤트를 넣는다.

## 화면 구성 (Step 2)

```
┌────────┬──────────────────────────────┬────────────┐
│ 메뉴바  │ 시스템 알람 (연결, 구역, 이벤트)  │            │
│ 메인    ├──────────────────────┬───────┤            │
│ 로그*   │                      │ 로봇   │            │
│ 포인트* │     지도 (Canvas)     │ 카드   │            │
│        │                      │ 경로이동│            │
│        │  pixel / map / occ.  │ 클릭좌표│            │
└────────┴──────────────────────┴───────┴────────────┘
* 구조만 있고 비활성 (로그, 포인트 화면은 이후 Step)
```

Step 3 에서 카드에 `순찰 시작`/`정지`/`경로 이동` 버튼과 상태 배지·진행 문구가 생겼고, 지도에 goto 포인트(P1~P7, 구역 안은 사각형)가, 아래에 명령 이력(최근 50건)이 생겼다. 스크린샷: `docs/step3_screenshot.png`.

## 명령 패널 사용 (Step 3)

- 포인트를 클릭하면 "경로 이동" 패널에서 고른 대상 로봇의 경로 끝에 추가된다 (카드를 클릭해도 대상이 바뀐다). 포인트가 아닌 곳을 클릭하면 기존처럼 좌표가 기록된다. 구역 문(RED*)은 그리지 않는다. 문 경유와 락은 노드가 처리한다.
- `순찰 시작`, `경로 이동` 은 확인 팝업(경로 요약)을 거친다. `정지` 는 즉시 보낸다. 같은 버튼은 1초간 잠긴다.
- 버튼 활성은 힌트일 뿐이다: `start`/`goto` 는 IDLE/DONE/STOPPED/FAILED(또는 아직 상태를 못 받은 online 로봇), `stop` 은 작업 중 상태에서만 켠다. offline 로봇은 전부 끈다. 최종 판단은 노드가 한다.
- STARTING 이 5초를 넘으면 "시작 준비 중" 안내가 나온다. 노드를 켠 뒤 첫 작업은 초기 위치 보정(제자리 회전 약 4초)이 있다.
- 명령 뒤 `no_response_sec` 안에 기대한 상태가 오지 않으면 카드와 이력에 "응답 없음" 이 나온다. 노드가 이미 작업 중이라 무시했거나 `stop` 이 반영 전일 수 있다.
- FAILED 의 `detail`(`no point given`/`unknown point`/`not allowed`)은 한글로 풀어서 보여 준다. 백엔드가 먼저 막으므로 정상 경로에서는 나오지 않는다.
- `last_task` 는 서버 메모리라 서버를 껐다 켜면 사라진다. 그 뒤 CLI 로 시작한 작업은 진행 문구가 원문 상태로 나온다.

- 지도 판정: `negate: 0` 이면 `p = (255 - v) / 255`, `p > occupied_thresh` 는 occupied, `p < free_thresh` 는 free, 그 외 unknown 이다 (map_server trinary 규칙).
- 지도의 회색(205)은 `free_thresh: 0.196` 때문에 unknown 으로 나온다 (205 -> p = 0.19608).
- 마커 색은 `/api/state` 의 로봇 순서대로 고정 팔레트에서 받는다 (로봇 이름은 코드에 없다).

## Step 4 화면 (구역, 비상정지, 오버레이, 수동 조작)

스크린샷: `docs/step4_screenshot.png`(구역 점유와 진입 대기), `docs/step4_lidar.png`(LiDAR 점이 벽 위에 겹침), 구역 영역 초안 확인용 `docs/step4_zone_draft.png`.

- **상단**: 구역 배지("구역: 비어 있음" / "구역: pinky1 점유 중 (42초)" / "구역: 상태 수신 전")와 **항상 보이는 비상정지 줄**(로봇별 `pinky1 정지` 와 `모두 정지`). 확인 팝업도 연타 잠금도 없다. 좁은 화면에서는 상단이 고정된다.
- **지도**: 구역 사각형(점유 중이면 빨갛게, 초안이면 점선과 모서리 좌표), 진입·이탈 문(로봇 색 마름모), 작업 중인 로봇의 경로 선(**기본 꺼짐**, 지도 오른쪽 위의 `경로` 버튼으로 켠다. 지난 구간은 연하게, 현재 목표로 가는 구간은 굵게, 남은 구간은 점선, 현재 목표는 고리). `goto` 는 `lastTask.points` + `patrol_status` 의 `waypoint`/`detail` 로, `start` 는 `start_route` + `waypoint` 번호로 그린다. 지점 사이의 선은 직선이 아니라 **지도의 벽을 피하는 최단 경로 추정**이다 (`backend/planner.py`, `GET /api/plans`, 벽에서 `planner_inflation_m` 0.08 m 이상 떨어진 칸만 지난다). 이것은 지도 점유 격자 위의 A* 계산이며 **Nav2 의 실제 경로가 아니다** (`/plan` 토픽은 브리지에 없다). 실제 경로는 코스트맵 팽창과 평활화 때문에 조금 다를 수 있다. mock 로봇도 같은 경로를 따라 움직이고 수동 조작으로 벽 안에 들어가지 않는다. `waypoint` 가 -1 이면 강조하지 않는다 (예외: `RETURNING` 은 `detail` 이 홈 이름이라 홈을 강조). 경로 선은 이 서버가 기억하는 명령(`last_task`)만 그린다 (서버를 껐다 켠 뒤 CLI 로 시작한 작업은 그리지 않는다).
- **패널 크기 조절**: 지도와 사이드바 사이의 틈, 지도와 명령 이력 사이의 틈을 끌면 사이드바 너비(220~720px, 창의 55% 이내)와 이력 높이(80px~창의 60%)가 바뀌고 지도가 따라간다. 더블클릭(또는 손잡이에서 `Home`)은 기본값(300px, 170px)으로 되돌리고, 손잡이에 포커스를 둔 채 방향키를 누르면 20px(Shift 는 60px)씩 조절된다. 사이드바의 섹션 제목(▾)을 누르면 그 섹션이 접힌다. 크기와 접힘 상태는 브라우저(`localStorage`)에 저장된다. 860px 이하 좁은 화면에서는 손잡이가 없다.
- **구역 진입 대기**: `WAITING_ZONE` 이면 카드와 마커에 "구역 진입 대기 (P3)" 와 점유 중인 로봇, 마커에 빨간 점선 고리.
- **오버레이 (LiDAR)**: 로봇별 체크박스. 켠 로봇만 서버가 `/scan` 을 구독한다. 로봇 이름 옆에 그리는 기준(`tf: 센서 프레임 …` 또는 `amcl 위치 + 보정 N°, tf 없음`)이 표시된다.
- **수동 조작**: "수동 조작 켜기"를 체크하면 방향 버튼(또는 방향키/WASD)이 활성화되고 누르는 동안만 움직인다. 버튼을 뗌, 창이 포커스를 잃음, 탭이 숨겨짐, 연결 끊김, 체크 해제, 로봇 변경은 즉시 정지한다. 순찰 중·오프라인이면 체크박스가 비활성이다.

# pinky_web_gui 백엔드 (Step 1)

관제 PC(`ROS_DOMAIN_ID=50`)에서 두 로봇의 상태 토픽을 구독해 하나의 상태 모델로 합치고 WebSocket으로 중계하는 FastAPI 백엔드다. **구독 전용**이며 로봇으로 나가는 명령은 없다 (Step 3).
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

두 로봇이 사각형 경로를 서로 다른 위상으로 도는 가짜 데이터(pose 20Hz, patrol, battery, zone)가 나온다.

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
| `--config` | `config/robots.yaml` | 설정 파일 경로 |

`--host 0.0.0.0` 은 인증이 없으므로 신뢰할 수 있는 네트워크에서만 쓴다.

## 설정 (`config/robots.yaml`)

로봇 ID 목록, zone status 토픽, `online_timeout_sec`(5), `pose_max_hz`(10)를 둔다. 코드에는 로봇 이름이 없다. 로봇을 늘리거나 이름을 바꾸려면 이 파일만 고친다.

## 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /api/health` | `{"ok", "mock", "robots", "ws_clients"}` |
| `GET /api/state` | 전체 상태 (WebSocket `snapshot` 과 같은 구조) |
| `WS /ws` | 접속 즉시 `snapshot`, 이후 변경분 `robot_update` / `zone` |
| `GET /` | 콘솔 확인용 최소 HTML (메시지를 `console.log` 로 출력) |

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
- `robot_update` 의 `field` 는 `patrol` | `pose` | `battery` | `online`. pose 는 로봇당 최대 10Hz 로 보내며 `/api/state` 는 항상 최신값이다.
- `online`: 해당 로봇의 **어떤 토픽이든** 마지막 수신 후 5초 이내. `patrol_status` 는 변할 때만 발행되므로 이 토픽만으로 판정하지 않는다.
- `patrol_status` 가 JSON 객체가 아니면 서버는 경고 로그만 남기고, 원문을 `detail` 에 넣고 `waypoint=-1`, `state` 는 직전 값을 유지한다(없으면 `null`).
- 같은 내용의 `patrol_status` 가 반복되면 이벤트를 내지 않는다.
- 배터리는 `/{id}/battery/percent`, `/{id}/battery/voltage`(Float32) 두 토픽을 합친 값이다. 로봇이 `/battery_state` 를 발행하지 않아서 브리지 YAML 두 개에 이 토픽을 추가했다 (브리지 재시작 필요). 한쪽만 오면 다른 쪽은 `null`이다. 값은 로봇이 준 그대로이며 **percent 는 0~100 단위**다 (pinky1 실측 94.65).

## 테스트

```bash
.venv/bin/python -m pytest tests -q        # StateStore 단위 테스트 (online 타임아웃, 잘못된 JSON, pose 제한, NaN)
```

mock 서버를 띄운 상태에서:

```bash
curl -s localhost:8000/api/health
curl -s localhost:8000/api/state | python3 -m json.tool
```

브라우저에서 `http://localhost:8000/` 를 열고 F12 콘솔에서 `snapshot`/`robot_update` 를 확인한다.

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
backend/static/        콘솔 확인용 HTML
tests/                 단위 테스트
```

스레드 모델: rclpy 콜백(별도 스레드)은 값을 뽑아 `loop.call_soon_threadsafe` 로 `asyncio.Queue` 에 넣는다. 큐를 소비하는 단일 태스크만 `StateStore` 를 바꾸고 브로드캐스트하므로 락이 없다. mock 도 같은 큐로 같은 이벤트를 넣는다.

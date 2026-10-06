# pinky_web_gui

관제 PC 웹 브라우저에서 pinky1·pinky2의 상태를 보는 GUI. 현재는 **Step 1: 백엔드 뼈대**(상태 구독 → WebSocket 중계)까지 있고, 구독 전용이라 로봇으로 나가는 명령은 없다.

- 규약과 토픽: `docs/interfaces.md`, 프로젝트 규칙: `CLAUDE.md`, 단계 계획: 레포 루트의 PRD.
- 로봇 ID, zone 토픽 이름, online 타임아웃, pose 빈도는 `config/robots.yaml`에서 읽는다 (코드에 로봇 이름을 쓰지 않는다).

## 1회 준비

`pinky_web_gui/`에서 실행한다. venv는 `--system-site-packages`로 만든다 (`rclpy`가 pip가 아니라 apt 설치본이라서).

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 실행

### mock 모드 (ROS 불필요)

```bash
cd pinky_web_gui
source .venv/bin/activate
python -m backend.main --mock
```

두 로봇이 사각형 경로를 도는 가짜 데이터를 같은 형식으로 내보낸다. 브라우저에서 <http://127.0.0.1:8000/> 를 열면 WebSocket 메시지가 화면과 개발자 도구 콘솔에 찍힌다.

### 실제 로봇 연결

ROS를 먼저 source하고 venv를 나중에 활성화한다 (순서가 바뀌면 `PYTHONPATH` 때문에 venv 패키지가 가려질 수 있다).

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=50          # 관제 PC 도메인
source pinky_web_gui/.venv/bin/activate
cd pinky_web_gui
python -m backend.main
```

먼저 브리지(`ros2 launch launch/bridges.launch.xml`)와 zone manager가 떠 있어야 `/pinky1/...` 토픽과 `/zone_manager/status`가 보인다 (`CLAUDE.md` 3절).

### 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--mock` | 끔 | ROS 없이 가짜 데이터로 실행 |
| `--host` | `127.0.0.1` | 다른 PC에서 접속하려면 `0.0.0.0` (인증이 없으니 신뢰하는 네트워크에서만) |
| `--port` | `8000` | |
| `--mock-silence ROBOT:SEC` | 없음 | (mock 전용) 그 로봇이 SEC초 뒤 데이터를 멈춘다. online 만료 확인용. 예: `--mock-silence pinky2:3` |

## 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /api/health` | `{"status","mode":"mock"\|"ros","clients"}` |
| `GET /api/state` | 전체 상태 스냅샷 (WebSocket의 `snapshot`과 같은 형식) |
| `WS /ws` | 접속 즉시 `snapshot`, 이후 변경분만 전송 |
| `GET /` | 확인용 최소 HTML |

### WebSocket 메시지

```json
{"type": "snapshot", "robots": {"pinky1": {"online": true,
  "patrol": {"state": "MOVING", "waypoint": 2, "detail": "P3", "time": 1759560000.0},
  "pose": {"x": 0.65, "y": 0.15, "yaw": 0.0},
  "battery": {"percentage": 0.82, "voltage": 7.9},
  "last_seen": 1759560001.2}}, "zone": "free"}

{"type": "robot_update", "robot": "pinky1", "field": "pose", "data": {"x": 0.7, "y": 0.15, "yaw": 0.0}}
{"type": "robot_update", "robot": "pinky1", "field": "online", "data": false}
{"type": "zone", "status": "occupied_by:pinky2"}
```

- `field`는 `patrol` | `pose` | `battery` | `online`. 아직 한 번도 받지 못한 값은 snapshot에서 `null`, zone은 `"unknown"`이다.
- `pose` 이벤트는 로봇당 최대 `pose_max_hz`(10Hz). `/api/state`의 pose는 항상 최신 값이다.
- `online`: 해당 로봇의 어떤 토픽이든 마지막 수신 후 `online_timeout_sec`(5초) 이내인지로 판정한다. `patrol_status`는 상태가 바뀔 때만 발행되므로 그 토픽만으로 판정하지 않는다.
- `patrol_status`가 JSON이 아니면 서버는 유지되고 경고 로그를 남기며, `state: "UNKNOWN"`에 원문이 `detail`로 들어간다.
- `patrol.time`은 **로봇 시계 기준**이다. online 판정은 PC 수신 시각(`last_seen`)을 쓴다.

## 구조

```
config/robots.yaml     로봇 ID 등 설정
backend/config.py      설정 로더
backend/state.py       상태 모델 (순수 파이썬: 파싱, pose 제한, online 판정)
backend/hub.py         WebSocket 클라이언트별 큐와 브로드캐스트
backend/ros_node.py    rclpy 구독 (별도 스레드 spin, call_soon_threadsafe로 이벤트 루프에 전달)
backend/mock.py        --mock 가짜 데이터
backend/main.py        FastAPI 앱, 실행 진입점
backend/static/        확인용 최소 HTML
```

## 알려진 한계 (Step 1)

- **늦게 접속한 구독자**: `patrol_status`의 `IDLE`과 `/zone_manager/status`는 값이 바뀔 때만 발행된다. 백엔드를 나중에 켜면 다음 변화가 오기 전까지 `patrol`이 `null`, zone이 `unknown`일 수 있다. 실제 로봇에서 확인하고, 필요하면 QoS(TRANSIENT_LOCAL)를 검토한다.
- **QoS**: `patrol_status`/zone은 RELIABLE로, `amcl_pose`/`battery_state`는 발행 쪽 QoS를 확인하지 못해 BEST_EFFORT로 구독한다 (어느 쪽 발행자와도 연결된다).
- `--workers`로 여러 프로세스를 띄우면 안 된다. 상태가 프로세스 메모리에 있다.
- 실제 로봇 연결 검증(`ros2 topic echo`와 pose 일치, 전원 차단 후 `online: false`)은 아직 하지 않았다.

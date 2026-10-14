# pinky_web_gui

관제 PC 에서 Pinky 로봇 2대(`pinky1`, `pinky2`)의 위치·상태를 지도로 보고, 순찰 명령과 비상정지를 보내는 웹 GUI (FastAPI + rclpy, 빌드 단계 없는 정적 프론트엔드).

> 상세 설명(옵션, 설정, API, 화면별 사용법, 인증·프로세스 제어, 테스트)은 [`docs/usage.md`](docs/usage.md), 토픽·명령·상태 JSON 규약은 [`docs/interfaces.md`](docs/interfaces.md), 작업 규칙은 [`CLAUDE.md`](CLAUDE.md)에 있다.

## 주요 기능

- **지도와 로봇 위치**: `amcl_pose`, 순찰 상태, 배터리를 두 로봇 카드와 지도 마커로 표시, WebSocket 으로 실시간 갱신 (끊기면 자동 재연결)
- **명령 패널**: `start` / `stop` / `goto`. 허용 목록(`config/robots.yaml`)에 있는 지점만 백엔드가 통과시키고, 응답 여부는 `patrol_status` 로 판단해 "응답 없음"을 표시
- **구역·비상정지·오버레이**: 위험 구역 점유 표시, 비상정지(`stop` + `cmd_vel` 0 속도), LiDAR 오버레이(켠 로봇만 구독), 누르는 동안만 움직이는 수동 조작
- **알람바와 Alarm 화면**: 서버가 만든 알람의 ACK/SIL, 레벨별 소리, 이력 파일(`data/alarms/`) 조회·CSV, 알람 카탈로그 편집(manager 계정)
- **시스템 패널**: 도메인 브릿지, zone_manager, 로봇 bringup/map/순찰 노드를 버튼으로 시작·정지 (`config/processes.yaml` 에 적힌 것만 실행, SSH 키 로그인)
- **로그 화면, 라이트/다크 모드, 고정 계정 인증** (`mngr` manager, `oper` operator)
- **`--mock` 모드**: ROS 없이 가짜 로봇 2대로 화면 전체를 시험

> 실제 로봇에서의 확인은 아직 하지 않았다. 프로세스 제어는 mock 으로만 검증했다 ([`docs/checklists/`](docs/checklists/README.md) 의 절차는 사용자가 수행).

## 설치 방법

사전 요구 (이 PC 에서 확인한 값, [`docs/reference/environment.md`](docs/reference/environment.md)): Ubuntu 24.04, ROS 2 `jazzy`, Python 3.12. 다른 버전은 **[TODO] 확인 안 됨**. `rclpy` 는 pip 가 아니라 ROS 2 가 제공하므로 venv 를 `--system-site-packages` 로 만든다.

`pinky_web_gui/` 폴더에서 한 번만 실행한다.

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

## 실행 방법

### mock 모드 (ROS 불필요)

```bash
.venv/bin/python -m backend.main --mock
```

브라우저에서 <http://localhost:8000/> 을 열고 `mngr` / `mngr` 로 로그인한다.

### 실제 로봇 연결

먼저 로봇 Nav2 와 도메인 브릿지(`ros2 launch domain_bridge bridges.launch.xml`)가 떠 있어야 `/pinky1/...`, `/pinky2/...` 토픽이 보인다 (순서는 [`CLAUDE.md`](CLAUDE.md) 3절).

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=50
.venv/bin/python -m backend.main
```

### 옵션

`.venv/bin/python -m backend.main --help` 와 같다.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--mock` | 꺼짐 | ROS 없이 가짜 데이터로 실행 |
| `--host` | `127.0.0.1` | 다른 PC 에서 접속하려면 `0.0.0.0` (신뢰하는 내부망에서만) |
| `--port` | `8000` | |
| `--config` | `config/robots.yaml` | 설정 파일 경로 |
| `--no-auth` | 꺼짐 | 로그인을 끈다 (개발·시험용, 프로세스 제어도 꺼진다) |
| `--mock-wait-scale` | `1.0` | `--mock` 에서 goto 지점 대기 시간 배율 (0.2 면 10초 대신 2초) |
| `--mock-scan-mount-deg` | `0.0` | `--mock` 에서 가짜 LiDAR 가 돌아 달린 각도 (센서 방향 보정 시험용) |
| `--mock-scan-no-tf` | 꺼짐 | `--mock-scan-mount-deg` 와 함께: tf 를 주지 않는다 |
| `--mock-proc-fail` | 없음 | `--mock` 에서 시작 직후 실패하는 프로세스 id (쉼표 구분) |
| `--mock-proc-external` | 없음 | `--mock` 에서 이미 실행 중인 것으로 보이는 프로세스 id (쉼표 구분) |
| `--alarm-history-dir` | 설정값 `alarm_history_dir` | 알람 이력 폴더 (mock 이면 뒤에 `-mock`) |
| `--alarm-history-chunk` | 설정값 `alarm_history_chunk` | 이력 파일 하나에 담는 건수 (시험용) |
| `--alarms-local` | 설정값 `alarms_local_yaml` | GUI 가 저장하는 알람 카탈로그 변경 파일 (시험용) |
| `--alarm-sound-dir` | 설정값 `alarm_sound_dir` | 알람 사운드 폴더 (`config/sounds`) |

## 사용 예시

mock 서버를 띄운 상태(`--mock`)에서, 다른 터미널에서 실행한다.

```bash
# 상태 확인 (로그인 없이 가능)
curl -s localhost:8000/api/health

# 로그인 후 pinky1 에 goto 명령 (쿠키 파일에 세션을 저장)
curl -s -c /tmp/pinky.cookie -X POST localhost:8000/api/login \
  -H 'Content-Type: application/json' -d '{"id": "mngr", "password": "mngr"}'
curl -s -b /tmp/pinky.cookie -X POST localhost:8000/api/robots/pinky1/command \
  -H 'Content-Type: application/json' -d '{"cmd": "goto", "points": ["P2", "P3", "P6"]}'
```

화면에서는 지도의 포인트를 눌러 경로를 만들고 `경로 이동` 을 누른다. 명령 규칙과 응답 형식은 [`docs/usage.md`](docs/usage.md) 의 "명령 API" 절을 본다.

테스트 (ROS 불필요):

```bash
.venv/bin/python -m pytest tests -q
```

## 환경변수

서버 자체가 읽는 환경변수는 적다. 아래 외의 값은 `config/*.yaml` 에 있다.

| 변수 | 값 | 설명 |
|---|---|---|
| `ROS_DOMAIN_ID` | `50` | 실제 로봇 연결 시 관제 도메인. 다르면 서버가 시작 때 경고한다 (`backend/main.py`) |
| `ROS_LOCALHOST_ONLY` | 설정하지 않음 | `1` 이면 브리지 토픽을 못 받는다는 경고가 나온다 |
| `ROS_DOMAIN_ID=77` + `ROS_LOCALHOST_ONLY=1` | 시험용 | ROS 경로 검증(`tests/verify_step4_ros.py`)에서만 쓰는 격리 값. 실제 도메인과 섞이지 않게 이 값으로만 검증한다 |
| `ssh.password_env` 가 가리키는 변수 | 선택 | `processes.yaml` 의 항목마다 변수 **이름**을 적으면 그 환경변수를 SSH 비밀번호로 쓴다 (`sshpass` 필요). 기본은 키 로그인이고 비밀번호를 파일에 두지 않는다 |
| `RMW_IMPLEMENTATION`, `CYCLONEDDS_URI`, `ROS_AUTOMATIC_DISCOVERY_RANGE` | `processes.yaml` 의 `env` | 서버 환경변수가 아니라, SSH 로 로봇 프로세스를 켤 때 서버가 넣어 주는 DDS 설정 |

이전의 `PINKY_*_PASSWORD` 변수는 더 이상 읽지 않는다. 로그인 계정은 `backend/auth.py` 에 고정돼 있다.

## 프로젝트 구조

```
pinky_web_gui/
├── backend/        FastAPI 앱(main.py), rclpy 노드(ros_bridge.py), 상태·명령·알람·인증·프로세스 제어, mock
├── frontend/       정적 화면 (index.html, app.js, style.css 등, 빌드 없음)
├── config/         robots.yaml, processes.yaml, zone.yaml, points.yaml, alarms.yaml, sounds/
├── docs/           usage.md, interfaces.md, prd/(계획서), checklists/(실물 확인), reference/(결정·함정·환경)
├── tests/          단위 테스트(pytest)와 verify_*.py (mock 서버, headless Chrome)
├── data/           실행 중 생기는 알람 이력 (git 무시)
├── requirements.txt
└── CLAUDE.md       작업 규칙
```

상위 저장소(`pinky_slam_nav/`)의 다른 폴더(`my_pinky_package/` 순찰 노드, `zone_traffic_control/`, `domain_bridge/`, `map_view_pc/` …)와 역할은 [`CLAUDE.md`](CLAUDE.md) 2절에 있다. 파일 단위 설명은 [`docs/usage.md`](docs/usage.md) 의 "구조" 절.

## 기여 방법

별도 기여 가이드는 없어서 [`CLAUDE.md`](CLAUDE.md) 의 규칙을 요약한다.

- 새 코드는 `pinky_web_gui/` 안에만 만든다. 기존 ROS 패키지(`my_pinky_package/`, `zone_traffic_control/`, `domain_bridge/` 등)는 수정하지 않는다.
- 주석과 docstring 은 한국어로, "왜 이렇게 했는지"를 적는다.
- 로봇 이름을 코드에 하드코딩하지 않는다 (`config/robots.yaml`).
- 로봇으로 나가는 명령은 허용 목록(`start`, `stop`, `goto:<허용 포인트>`)만 통과시킨다.
- mock 모드로 먼저 확인하고, 실제 로봇 테스트는 사용자가 직접 한다. ROS 검증은 `ROS_DOMAIN_ID=77`, `ROS_LOCALHOST_ONLY=1` 로만 한다.
- 변경 후 `.venv/bin/python -m pytest tests -q` 를 통과시킨다.
- 이슈·PR 절차, 브랜치 규칙: **[TODO]** 정해진 것이 없다.


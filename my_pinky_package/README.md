# my_pinky_package

Pinky 로봇에서 실행하는 Nav2 순찰 노드 패키지. 개발 중인 **v3**(`pinky_patrol_node_pinky1_v3`, `pinky_patrol_node_pinky2_v3`)는 위험 구역에 **한 대만** 들어가도록 `zone_traffic_control`의 대기열 슬롯 방식과 연동합니다.

## 주요 기능

- **명령 수신**: `patrol_cmd`(String)로 `start` / `stop` / `goto:<P>[,<P>,...]`
- **상태 발행**: `patrol_status`에 JSON 발행 — `{"state": "MOVING", "waypoint": 2, "detail": "P3", "time": ...}`
  `IDLE` `STARTING` `MOVING` `QUEUEING` `WAITING_ZONE` `ARRIVED` `LEAVING_ZONE` `RETURNING` `DONE` `STOPPED` `FAILED` `RETRY`
- **대기열 진입(v3)**: 구역 진입 전에 먼저 요청을 보냅니다.
  - 구역이 비어 있으면 바로 허가를 받아 목적지로 직행
  - 점유 중이면 매니저가 준 슬롯으로 이동해 `QUEUEING` 대기, 슬롯이 바뀌면 새 슬롯으로 재이동
  - 대기열이 가득 차면 `FAILED`(`queue_full`), `stop`/이동 실패 시 대기열에서 빠지고 허가를 받았다면 락 반납
- **위치 복원**: 마지막 위치를 `~/.pinky_last_pose.json`에 저장하고, 노드 실행 후 첫 작업에서만 AMCL 초기 위치로 보낸 뒤 제자리 회전으로 방향 보정
- **LED**: 대기 초록 깜박임 / 주행 초록 / 구역 대기 빨강 깜박임 / 도착 후 대기 노랑 깜박임

## 연관 파일

```
관제 PC(100)  pinky_control_client_v2.py ──/<robot>/patrol_cmd──▶ domain_bridge ──▶ patrol_node_v3 (로봇 10/20/30)
             zone_manager_node_v3 ◀── request_entry / cancel_entry / notify_exit ── ZoneGateClient
                                  ──▶ grant_entry / queue_slot ──────────────────▶ (zone_gate_client_v3)
```
![시스템 구성도](docs/pinky_patrol_v3_flow.svg)

| 파일 | 실행 위치 | 역할 |
|---|---|---|
| `my_pinky_package/pinky_patrol_node_pinky{1,2}_v3.py` | 각 로봇 | 순찰·goto, 구역 진입 절차 |
| `zone_traffic_control/.../zone_gate_client_v3.py` | 각 로봇 (import) | 진입 요청·취소·이탈 통보, 허가·슬롯 수신 |
| `zone_traffic_control/.../zone_manager_node_v3.py` | 관제 PC (1대) | 점유자 1명 + FIFO 대기열, 허가·슬롯 좌표 발행 |
| `pinky_patrol_cmd/pinky_control_client_v2.py` | 관제 PC | 터미널에서 명령 전송 |

- 로봇 코드에는 슬롯 좌표가 없습니다. 앵커 `ZONE_ENTRY (1.0, -0.7, 0.0)`와 출구 `ZONE_EXIT (0.7, -0.5, 3.14)`만 있고, 슬롯은 매니저가 `queue_slot`으로 줍니다.
- **같은 구역의 로봇은 모두 v3(또는 모두 v2)로 맞추세요.** 섞으면 두 대가 동시에 구역에 들어갈 수 있습니다.
- `robot_id`는 `zone_params_v3.yaml`의 `robot_ids`와 같아야 합니다.

### 경로와 규칙

| 로봇 | `start` 경로 | goto 홈 |
|---|---|---|
| pinky1 | `P2` → `ZONE_ENTRY` → `P3` → `P6` → `ZONE_EXIT` → `P1` | `P1` |
| pinky2 | `ZONE_ENTRY` → `P3` → `P6` → `ZONE_EXIT` → `P7` | `P7` |

- 위험 구역 안 지점: `P3` `P4` `P5` `P6`. goto 허용 지점: `P1` `P2` `P7` + 구역 안 지점 (`ZONE_*`는 직접 지정 불가 → `FAILED`).
- goto는 지점마다 10초 대기 후 홈으로 복귀합니다. 구역 밖→안은 진입 허가 후 이동, 안→밖은 `ZONE_EXIT`에서 락 반납입니다.
- 작업 중 온 `start`/`goto`는 무시됩니다. **구역 안에서 `stop`하면 락은 `max_hold_sec`(기본 120초) 후 매니저가 강제 해제**합니다.

## 설치 방법

로봇에는 Nav2, `pinky_interfaces`(`SetLed`), `pinky_led`가 이미 있어야 합니다 (이 저장소에 없음). 환경: Ubuntu 24.04, ROS 2 `jazzy`.

```bash
# 로봇 (저장소 루트에서)
git clone https://github.com/cdwlsgh1-source/pinky_slam_nav.git
cd pinky_slam_nav
source /opt/ros/jazzy/setup.bash
colcon build --packages-select zone_traffic_control my_pinky_package
source install/setup.bash
```

관제 PC는 `colcon build --packages-select zone_traffic_control domain_bridge` 후 `source install/setup.bash` 합니다.

## 실행 방법

각 터미널에서 `source /opt/ros/jazzy/setup.bash && source install/setup.bash`를 먼저 합니다.

1. **로봇**: Nav2 bringup 실행
2. **관제 PC**: 도메인 브릿지
   ```bash
   export ROS_DOMAIN_ID=100
   ros2 launch domain_bridge bridges.launch.xml
   ```
3. **관제 PC**: zone_manager v3 (새 터미널)
   ```bash
   export ROS_DOMAIN_ID=100
   ros2 launch zone_traffic_control zone_manager_v3.launch.xml
   ```
4. **각 로봇**: 순찰 노드
   ```bash
   # pinky1
   export ROS_DOMAIN_ID=10
   ros2 run my_pinky_package pinky_patrol_node_pinky1_v3 --ros-args -p robot_id:=pinky1

   # pinky2
   export ROS_DOMAIN_ID=20
   ros2 run my_pinky_package pinky_patrol_node_pinky2_v3 --ros-args -p robot_id:=pinky2

   # pinky3 (홈 지점은 임시로 pinky2 와 같은 P7 - 실측 후 수정)
   export ROS_DOMAIN_ID=30
   ros2 run my_pinky_package pinky_patrol_node_pinky3_v3 --ros-args -p robot_id:=pinky3
   ```

## 사용 예시

관제 PC(저장소 루트)에서 `ROS_DOMAIN_ID=100`으로 실행합니다.

```bash
python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky1 monitor          # 상태 출력 (별도 터미널, Ctrl+C 종료)
python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky1 start            # 전체 순찰
python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky1 goto P2 P3 P6    # 지점 이동 후 홈 복귀
python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky1 stop             # 중지

# 대기열 확인: 두 로봇에 구역 지점 goto를 거의 동시에 보내면 늦은 쪽이 QUEUEING으로 대기
python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky1 goto P3
python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky2 goto P3
ros2 topic echo /zone_manager_v3/status      # free | occupied_by:<robot_id>
```

테스트(가짜 navigator + 실제 매니저, 격리 도메인에서만 통합 테스트 실행). 이 문서 작성 환경에는 ROS가 없어 실행해 보지 못했습니다.

```bash
ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1 python3 -m pytest my_pinky_package/test/test_patrol_queue_v3.py -q
```

## 환경변수

| 변수 | 값 | 설명 |
|---|---|---|
| `ROS_DOMAIN_ID` | 관제 PC `100`, pinky1 `10`, pinky2 `20`, pinky3 `30` | 장비별 도메인 (도메인 간 중계는 `domain_bridge`) |
| `ROS_DOMAIN_ID=77` + `ROS_LOCALHOST_ONLY=1` | 시험용 | 통합 테스트 전용 격리 값 (아니면 통합 테스트 skip) |
| `RMW_IMPLEMENTATION`, `CYCLONEDDS_URI`, `ROS_AUTOMATIC_DISCOVERY_RANGE` | `rmw_cyclonedds_cpp`, `file:///home/pinky/.ros/cyclonedds_robot.xml`, `SUBNET` | 로봇 DDS 설정. 웹 GUI가 SSH로 켤 때 주입 (직접 실행 시 로봇 터미널 값 사용) |

노드 파라미터 `robot_id`(기본: pinky1 노드 `pinky1`, pinky2 노드 `pinky2`)는 환경변수가 아닙니다. 대기 시간·재시도·지점 좌표는 노드 파일의 클래스 상수(`GOTO_WAIT_SEC`, `NAV_RETRIES`, `POINTS`)라 코드를 수정해야 바뀝니다.

## 프로젝트 구조

```
my_pinky_package/
├── README.md, package.xml, setup.py, setup.cfg
├── PinkyPatrolNode.html                    # 순찰 노드 흐름도
├── docs/prd/                               # 위험구역 대기열 단계별 PRD
├── test/test_patrol_queue_v3.py            # v3 대기열 진입 테스트
└── my_pinky_package/
    ├── pinky_patrol_node_pinky{1,2}_v3.py  # 대기열 방식 (개발 중)
    ├── pinky_patrol_node_pinky{1,2}_v2.py  # 이전 버전, 웹 GUI가 이 버전 기준
    ├── pinky_patrol_node_pinky{1,2}.py     # 이전 버전
    └── pinky_patrol.py, my_pinky_patrol.py, pinky_multi_patrol.py, my_pinky_package.py   # 초기 시험 코드
```

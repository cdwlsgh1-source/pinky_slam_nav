# zone_traffic_control (v2 — self-report 방식)
<img width="1480" height="1030" alt="architecture_diagram_light" src="https://github.com/user-attachments/assets/e214a12d-6e1e-42eb-a520-4c288d4e6de2" />

여러 대의 로봇(예: Pinky Pro pinky1, pinky2)이 지정된 공유 구역(zone)에
**동시에 한 대만** 들어갈 수 있도록 상호 배제(mutual exclusion)를 제공하는
ROS2 패키지입니다.

- 한 로봇이 구역 안에 있으면 → 다른 로봇은 구역 진입 전 waypoint에서 대기
- 들어간 로봇이 구역을 빠져나오면 → 대기 중이던 로봇에게 자동으로 진입 허가

## v1에서 v2로 바뀐 점

v1은 zone_manager가 각 로봇의 `amcl_pose`를 직접 구독해서 자동으로 이탈을
감지하는 방식이었습니다. 하지만 실제 사용 중인 `PinkyPatrolNode`처럼
**로봇마다 도메인이 분리되어 있고, 관제 PC와는 `patrol_cmd`/`patrol_status`
같은 가벼운 String 토픽만 도메인 브릿지로 주고받는 구조**에서는
`amcl_pose`까지 브릿지해야 해서 기존 설계 철학과 맞지 않습니다.

v2는 로봇이 **이미 Nav2 feedback으로 알고 있는 자기 위치**를 이용해서
"나 구역 들어간다"(`request_entry`) / "나 구역 나왔다"(`notify_exit`)를
직접 보고하는 방식으로 바뀌었습니다. 필요한 토픽은 `patrol_cmd`/`patrol_status`와
동일한 성격의 가벼운 String 토픽 3개뿐입니다:

| 토픽 | 방향 | 내용 |
|---|---|---|
| `/zone_manager/request_entry` | 로봇 → zone_manager | `"<robot_id>:<token>"` |
| `/zone_manager/grant_entry` | zone_manager → 로봇 | `"<robot_id>:<token>"` |
| `/zone_manager/notify_exit` | 로봇 → zone_manager | `"<robot_id>"` |
| `/zone_manager/status` (선택) | zone_manager → 모니터링 | `"free"` 또는 `"occupied_by:pinky1"` |

기존에 `patrol_cmd`/`patrol_status`를 도메인 브릿지에 등록해둔 것과 **동일한 방식으로
이 4개 토픽만 추가**하면 됩니다.

## 구성

```
zone_traffic_control/
├── zone_traffic_control/
│   ├── zone_manager_node.py   # 중앙 관리 노드 (락 / 대기열 / 타임아웃 안전장치)
│   └── zone_gate_client.py    # 로봇 쪽 헬퍼 (request_entry 대기 + notify_exit)
├── launch/zone_manager.launch.xml
├── config/zone_params.yaml
└── package.xml / setup.py / setup.cfg
```

여기에 더해, **여러분의 기존 `PinkyPatrolNode`에 최소한의 코드만 추가한 버전**을
`pinky_patrol_node_with_zone.py`로 별도 전달드렸습니다. `# === ZONE-MUTEX ADDED`로
표시된 부분만 원래 코드에 추가된 내용입니다.

## 배치 구조 (중요)

`zone_traffic_control` 패키지 안에는 두 가지 역할이 같이 들어있습니다.

| 파일 | 어디서 실행? | 역할 |
|---|---|---|
| `zone_manager_node.py` | **관제(중앙) PC — 딱 1대에서만** | 누가 구역을 점유중인지 판정하는 심판 |
| `zone_gate_client.py` | **각 로봇(pinky1, pinky2)** | patrol 노드가 import해서 허가 요청/이탈 통보에 사용하는 헬퍼 |

즉 이 패키지 자체는 **로봇 2대 + 관제 PC, 총 3곳 모두에 똑같이 설치**하되, 실제로 실행하는 노드는 곳마다 다릅니다.
- 로봇: `zone_manager_node`는 실행하지 않음. patrol 노드가 `zone_gate_client`만 import해서 씀.
- 관제 PC: `zone_manager_node`만 `ros2 launch`로 띄움. patrol 노드는 없음.

로봇 안에 `zone_manager_node`를 띄우면 안 되는 이유: 그 로봇이 재부팅/크래시되면 심판이 같이 죽어서 다른 로봇이 영원히 허가를 못 받고 멈춥니다. 또한 로봇끼리 서로 직접 브릿지가 안 되어 있는 구조라면(로봇↔관제 PC 브릿지만 있는 경우) 다른 로봇이 그 노드에 아예 도달하지 못합니다.

## 관제 PC에 zone_manager_node 설치하기

1. 관제 PC에도 ROS2(로봇과 같은 버전, 예: Jazzy)와 colcon 워크스페이스가 있어야 합니다. 없으면 새로 하나 만드세요.
   ```bash
   mkdir -p ~/zone_ws/src
   ```
2. `zone_traffic_control.zip`을 관제 PC의 `~/zone_ws/src/`에 풀어줍니다 (로봇에 설치할 때와 동일한 방식).
   ```bash
   cd ~/zone_ws/src
   unzip zone_traffic_control.zip
   ```
3. 빌드 (관제 PC에는 Nav2가 없어도 됩니다 — 이 패키지는 `rclpy`, `std_msgs`, `launch`, `launch_ros`만 필요합니다).
   ```bash
   cd ~/zone_ws
   rosdep install --from-paths src --ignore-src -r -y
   colcon build --packages-select zone_traffic_control
   source install/setup.bash
   ```
4. `config/zone_params.yaml`의 `robot_ids`가 로봇에서 쓰는 `robot_id` 파라미터 값(`pinky1`, `pinky2`)과 정확히 같은지 확인합니다.
5. 실행:
   ```bash
   ros2 launch zone_traffic_control zone_manager.launch.xml
   ```
6. 로봇 쪽 도메인 브릿지 설정(기존에 `patrol_cmd`/`patrol_status`를 등록해둔 그 설정 파일)에, 아래 4개 String 토픽도 똑같은 방식으로 추가합니다. 이제 이 토픽들이 로봇 ↔ 관제 PC 사이를 오갑니다.
   - `/zone_manager/request_entry`
   - `/zone_manager/grant_entry`
   - `/zone_manager/notify_exit`
   - `/zone_manager/status` (모니터링용, 선택)



```bash
cd ~/pinky_pro/src
# 이 폴더(zone_traffic_control)를 여기에 복사/압축 해제
cd ~/pinky_pro
rosdep install --from-paths src --ignore-src -r -y
colcon build --packages-select zone_traffic_control
source install/setup.bash
```

기존 patrol 패키지가 이 패키지를 import할 수 있어야 하므로, 같은 워크스페이스에서
같이 build 되어야 합니다 (`colcon build`).

## 2. zone_manager_node 실행 (관제 PC 또는 두 로봇이 모두 볼 수 있는 곳)

```bash
ros2 launch zone_traffic_control zone_manager.launch.xml
```

`config/zone_params.yaml`에서 `robot_ids`를 실제 사용하는 robot_id
(`pinky1`, `pinky2`)와 동일하게 맞춰주세요.

## 3. 도메인 브릿지 설정 추가

기존에 `patrol_cmd` / `patrol_status`를 브릿지하던 설정 파일에, 아래 4개 토픽도
같은 방식(String 타입)으로 추가하세요:

- `/zone_manager/request_entry`
- `/zone_manager/grant_entry`
- `/zone_manager/notify_exit`
- `/zone_manager/status` (모니터링용, 선택)

## 4. PinkyPatrolNode 쪽 변경 요약 (`pinky_patrol_node_with_zone.py` 참고)

1. `from zone_traffic_control.zone_gate_client import ZoneGateClient` 추가
2. `robot_id` 파라미터 추가 (`--ros-args -p robot_id:=pinky1`)
3. `self.navigator = BasicNavigator()` 다음 줄에
   `self.gate = ZoneGateClient(self.navigator, robot_id=self.robot_id)` 추가
   - `navigator`를 그대로 넘기는 이유: 기존 코드가 `isTaskComplete()` 폴링 등으로
     이미 `navigator`를 spin하는 패턴과 똑같이 동작해서, 별도 스레드/executor
     충돌이 생기지 않습니다.
4. `WAYPOINTS` 리스트에서 **구역으로 들어가는 지점의 인덱스**(`ZONE_ENTRY_INDEX`)와
   **구역을 완전히 벗어나는 지점의 인덱스**(`ZONE_EXIT_INDEX`)를 지정
5. 순찰 루프에서:
   - `i == ZONE_ENTRY_INDEX`인 waypoint로 출발하기 **직전**에
     `self.gate.wait_for_entry(stop_check=lambda: self._stop_requested)` 호출
     → 다른 로봇이 구역 안에 있으면 여기서 대기 (stop 명령이 오면 즉시 중단)
   - `i == ZONE_EXIT_INDEX`인 waypoint에 **도착 성공한 직후**
     `self.gate.notify_exit()` 호출

## 5. 실행

로봇 1:
```bash
ros2 run <your_pkg> pinky_patrol_node --ros-args -p robot_id:=pinky1
```
로봇 2:
```bash
ros2 run <your_pkg> pinky_patrol_node --ros-args -p robot_id:=pinky2
```
(pinky2는 `WAYPOINTS` / `ZONE_ENTRY_INDEX` / `ZONE_EXIT_INDEX`를 코드 내 주석에
있는 P7 경로 기준 값으로 바꿔서 별도 빌드/실행하거나, 파라미터화해서 분리하세요.)

## 6. 동작 확인

```bash
ros2 topic echo /zone_manager/status
```
- `free` : 구역이 비어 있음
- `occupied_by:pinky1` : pinky1이 구역을 점유중 (pinky2는 진입 전 waypoint에서 대기)

`patrol_status`에도 `WAITING_ZONE` 상태가 새로 추가되어, 로봇이 구역 진입 허가를
기다리는 중인지 관제 PC에서 바로 확인할 수 있습니다.

## 안전장치 / 한계

- 로봇이 crash 등으로 `notify_exit`을 못 보내는 경우를 대비해,
  `max_hold_sec`(기본 120초) 이상 점유가 지속되면 zone_manager가 강제로 락을 풉니다.
  순찰 경로가 길어서 구역 통과에 120초 이상 걸린다면 `config/zone_params.yaml`의
  `max_hold_sec`을 늘려주세요.
- `stop` 명령으로 로봇이 **구역 안에서** 멈춘 경우, 로봇이 물리적으로 아직 구역
  안에 있으므로 `notify_exit`을 보내지 않습니다. 이 경우 다음 `start`에서 이어서
  구역을 빠져나가거나, `max_hold_sec` 타임아웃으로 최종 해제됩니다.
- 현재는 구역 1개 기준입니다. 여러 구역이 필요하면 `zone_manager_node`를
  구역별로 복수 실행(다른 노드 이름/토픽 네임스페이스)하면 됩니다.

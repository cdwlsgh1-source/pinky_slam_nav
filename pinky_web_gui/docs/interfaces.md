# 인터페이스 문서 (관제 PC 기준)

관제 PC(`ROS_DOMAIN_ID=50`)에서 본 토픽 규약, 명령 문법, 상태 스키마, 포인트 좌표, 순찰 노드 동작을 정리한다.

- 근거 표기: `파일:줄`. 약칭 — **P1v2** = `my_pinky_package/my_pinky_package/pinky_patrol_node_pinky1_v2.py`, **P2v2** = 같은 폴더의 `pinky_patrol_node_pinky2_v2.py`, **Y1** = `domain_bridge/config/pinky_bridge_pinky1.yaml`, **Y2** = `pinky_bridge_pinky2.yaml`.
- 확인하지 못한 것은 **확인 필요**로 표시했다. 정적 분석(파일 읽기)만 했고 로봇이나 ROS 그래프에서 실행해 확인한 것은 없다.
- 두 로봇의 순찰 노드(`_v2`)는 `WAYPOINTS`(구성과 개수), `ZONE_ENTRY/EXIT_INDEX`, `ZONE_ENTRY/EXIT_NAME`, `HOME_NAME`, `robot_id` 기본값만 다르고 나머지(`POINTS`, `ZONE_POINTS`, `GOTO_ALLOWED` 등)는 같다. `pinky_patrol_node_pinky2_v2.py`는 현재 작업 트리(미커밋) 기준이며, 84줄 이후 줄 번호가 pinky1과 1씩 다르다. **pinky2는 인덱스 상수가 `WAYPOINTS`와 맞지 않는다** (아래 5절, 7절).

## 1. 토픽 표 (관제 PC, `{id}` = `pinky1` | `pinky2`)

| 관제 PC 토픽 | 타입 | 방향 | Y1 | Y2 |
|---|---|---|---|---|
| `/{id}/patrol_cmd` | std_msgs/String | PC → 로봇 | :9-13 | :5-9 |
| `/{id}/patrol_status` | std_msgs/String (JSON) | 로봇 → PC | :15-19 | :11-15 |
| `/{id}/cmd_vel` | geometry_msgs/Twist | PC → 로봇 | :21-25 | :17-21 |
| `/{id}/initialpose` | PoseWithCovarianceStamped | PC → 로봇 | :27-31 | :23-27 |
| `/{id}/goal_pose` | PoseStamped | PC → 로봇 | :33-37 | :29-33 |
| `/{id}/scan` | sensor_msgs/LaserScan | 로봇 → PC | :40-44 | :36-40 |
| `/{id}/odom` | nav_msgs/Odometry | 로봇 → PC | :46-50 | :42-46 |
| `/{id}/tf`, `/{id}/tf_static` | tf2_msgs/TFMessage | 로봇 → PC | :52-62 | :48-58 |
| `/{id}/amcl_pose` | PoseWithCovarianceStamped | 로봇 → PC | :65-69 | :60-64 |
| `/{id}/battery_state` | sensor_msgs/BatteryState | 로봇 → PC | :71-75 | :66-70 |
| `/{id}/battery/percent`, `/{id}/battery/voltage` | std_msgs/Float32 | 로봇 → PC | 추가됨 (2026-10-06) | 추가됨 (2026-10-06) |
| `/pinky1/lane/center_offset` | Float32 | 로봇 → PC | :81-84 | 없음 |
| `/pinky1/lane/detected`, `/pinky1/crossline/detected` | Bool | 로봇 → PC | :86-94 | 없음 |
| `/zone_manager/request_entry` | String (`<id>:<token>`) | 로봇 → PC | :104-108 | :80-84 |
| `/zone_manager/notify_exit` | String (`<id>`) | 로봇 → PC | :110-114 | :86-90 |
| `/zone_manager/grant_entry` | String (`<id>:<token>`) | PC → 로봇 | :116-119 | :92-95 |
| `/zone_manager/status` | String | 관제 PC 내부 (브리지 없음) | - | - |
| `/{id}/camera/image_raw/compressed` | sensor_msgs/CompressedImage | **브리지에 없음** | - | - |

설명:
- **배터리**: 로봇(도메인 20, 22 실측)은 `/battery_state`를 발행하지 않고 `/battery/percent`, `/battery/voltage`(Float32, RELIABLE/VOLATILE)만 발행한다. `/battery_state` 항목은 브리지 YAML에 있지만 원본이 없어 비어 있다. 두 YAML에 Float32 두 토픽을 추가했고(`/{id}/battery/percent|voltage`), 백엔드는 이 둘을 구독한다. percent 는 **0~100 단위**다 (pinky1 실측 94.65, 2026-10-06). PRD 예시의 0.82(0~1)와 다르다.
- 로봇 쪽 원래 이름은 remap 전 이름이다 (`/patrol_cmd`, `/patrol_status`, `/cmd_vel`, `/amcl_pose` …). 관제 PC에서만 `/{id}/` 접두가 붙는다.
- `/zone_manager/request_entry|notify_exit|grant_entry`는 remap이 없고 두 로봇이 한 토픽을 공유한다 (메시지 안의 `robot_id`로 구분, Y1:96-102). QoS는 RELIABLE, depth 10 (`zone_gate_client.py:43-52`, `zone_manager_node.py:55-63`).
- `/zone_manager/status`는 `zone_manager_node`가 발행하고(`zone_manager_node.py:64`) 값은 `free` | `occupied_by:<robot_id>` (`:156`). 점유가 바뀔 때만 발행한다 (`:134`, `:140`).
- 카메라 토픽은 `pinky_camera/pinky_camera/camera_node.py:32,57`에 이름이 있으나 두 브리지 YAML에는 없다. 쓰려면 브리지에 추가해야 한다 (기존 파일 수정이 필요하므로 별도 결정).
- `patrol_cmd`/`patrol_status`의 QoS는 기본값(depth 10)이다 (P1v2:139-140). 브리지 쪽 QoS 설정은 YAML에 없다. 구독자가 늦게 붙었을 때 마지막 값을 받는지는 **확인 필요**.
- zone manager는 접미사 없는 `zone_manager_node`로 **확정**됐다 (v2는 시도 후 폐기, 사용자 결정). `_v2` 파일은 삭제하지 않고 레포에 남아 있으나 사용하지 않는다. `occupied_by:<id>:<token>` 형식은 `_v2`의 것이므로 현재 구성과 무관하다.

## 2. patrol_cmd 명령 문법

`std_msgs/String`의 `data`에 아래 문자열을 넣는다. 처리는 `_cmd_cb` (P1v2:242-298).

| 명령 | 동작 | 근거 |
|---|---|---|
| `start` | 로봇별 `WAYPOINTS` 전체 순회 (`_run_patrol`). 비교는 소문자 변환 후 | :251-258 |
| `stop` | 중단 플래그 + Nav2 `cancelTask()` | :290-295 |
| `goto:<P>[,<P>,...]` | 지정한 지점을 **순서대로** 방문 후 홈 복귀 (`_run_route`) | :261-287 |

`goto` 규칙:
- 예: `goto:P2`, `goto:P2,P3,P6`. 포인트 이름은 대소문자를 가리지 않고(`.upper()`), **쉼표와 공백 모두 구분자**다 (`goto:p2, p3 ,P6` → `P2,P3,P6`). 근거 :263
- 허용 목록은 **P1, P2, P3, P4, P5, P6, P7** (`GOTO_ALLOWED = {'P1','P2','P7'} | ZONE_POINTS`, :113). `RED1IN/OUT`, `RED2IN/OUT`은 직접 goto 금지.
- 같은 지점을 중복해서 적는 것이 허용되는지 막히는지는 코드에 별도 검사가 없다 (:269-278). 동작은 **확인 필요**.
- 검증 순서와 실패 시 발행되는 상태 (모두 `waypoint=-1`):
  1. 포인트가 하나도 없음 → `FAILED`, detail `no point given` (:265-268)
  2. `POINTS`에 없는 이름 → `FAILED`, detail `unknown point: ['X']` (:269-273)
  3. 허용 목록 밖 → `FAILED`, detail `not allowed: ['RED1IN']` (:274-278)
  4. 이미 작업 중 → 경고 로그만, 상태 발행 없음 (:279-281)
- **주의**: 1~3의 검증이 "작업 중" 확인(4)보다 먼저다. 작업 중에 잘못된 goto를 보내면 진행 중인 작업과 무관한 `FAILED`가 발행된다. 백엔드가 사전 검증해서 잘못된 명령은 아예 발행하지 않는다.
- `start`가 작업 중이면 경고만 남고 상태 발행 없음 (:252-254). `stop`이 작업 중이 아니면 무시, 상태 발행 없음 (:291-293). 알 수 없는 명령은 경고만 (:297-298).
- `stop` 이후의 상태: 이동 중에는 `STOPPED`가 발행된다 (아래 4절). 첫 작업의 초기 위치 보정 회전(4초) 중에는 `stop`을 확인하지 않아 즉시 먹지 않는다 (:376-378, :398).

## 3. patrol_status JSON 스키마

```json
{"state": "MOVING", "waypoint": 2, "detail": "P3", "time": 1759560000.0}
```

| 필드 | 타입 | 의미 | 근거 |
|---|---|---|---|
| `state` | string | 아래 11종 중 하나 | P1v2:13-14, :238 |
| `waypoint` | int | 진행 위치. `-1`은 "해당 없음". **`start`와 `goto`에서 의미가 다르다 (4절)** | :236-239 |
| `detail` | string | 보조 정보. `goto`에서는 지점 이름 | :236-239 |
| `time` | float | 로봇이 발행한 Unix 시간 (`time.time()`, **로봇 시계 기준**) | :239 |

state 11종: `IDLE`, `STARTING`, `MOVING`, `WAITING_ZONE`, `ARRIVED`, `LEAVING_ZONE`, `RETURNING`, `DONE`, `STOPPED`, `FAILED`, `RETRY`.

`IDLE`은 노드 시작 시 `waypoint=-1`로 한 번만 발행한다 (:155). 작업이 끝나면(`DONE`/`STOPPED`/`FAILED`) 이후 별도의 `IDLE`은 코드에서 확인되지 않는다 — 마지막 state가 그대로 남는다. (`_run_patrol`/`_run_route`의 `finally`는 LED와 `_running`만 바꾼다. :578-580, :669-671)

## 4. `start` 순찰과 `goto` 경로의 차이

GUI는 **마지막으로 보낸 명령이 `start`인지 `goto`인지** 기억하고 그에 맞게 해석한다. 상태 메시지만으로는 구분되지 않는다 (예외: `goto`의 `STARTING`은 detail에 경로가 있다).

### 4-1. `start` (`_run_patrol`, P1v2:585-671)

`waypoint`는 `WAYPOINTS`의 인덱스이고 `detail`은 거의 비어 있다. **경로는 로봇마다 다르다** (아래 표). 아래 줄 번호는 pinky1 기준이며 pinky2는 1씩 앞선다 (같은 구조).

| 로봇 | `WAYPOINTS` (인덱스: 지점) | 개수 | 진입 인덱스 | 이탈 인덱스 | 근거 |
|---|---|---|---|---|---|
| pinky1 | 0 P2, 1 RED1IN, 2 P3, 3 P6, 4 RED1OUT, 5 P1 | 6 | 1 | 4 | P1v2:84-91, :99-100 |
| pinky2 | 0 RED2IN, 1 P3, 2 P6, 3 RED2OUT, 4 P7 | 5 | **코드 값 1** (의도 0) | **코드 값 4** (의도 3) | P2v2:84-90, :98-99 |

GUI는 로봇별 `WAYPOINTS`(지점 이름과 개수)를 설정으로 가지고 있어야 `waypoint` 번호를 지점으로 바꿀 수 있다. 로봇 이름이나 개수를 코드에 박지 않는다.

| state | waypoint | detail | 근거 |
|---|---|---|---|
| `IDLE` | -1 | `""` | :155 |
| `STARTING` | -1 | `""` | :588 |
| `MOVING` | **`i+1`** — 방금 도착한 웨이포인트 *다음* 번호. 이동 시작 시가 아니라 **도착 후** 발행 | `""` | :628 |
| `WAITING_ZONE` | `i` (진입 지점 인덱스: pinky1 = 1, pinky2 = 0) | `""` | :637 |
| `RETRY` | `i+1` | `str(result)` (Nav2 결과) | :649 |
| `FAILED` | `i+1` | `""` | :653 |
| `STOPPED` | `i` | `""` | :599, :664 |
| `DONE` | `len(WAYPOINTS)` (pinky1 = 6, pinky2 = 5) | `""` | :668 |

- `ARRIVED`, `LEAVING_ZONE`, `RETURNING`은 `start` 순찰에서 **발행되지 않는다.**
- `i`와 `i+1`이 섞여 있다. 같은 웨이포인트를 가리켜도 state에 따라 값이 1 차이난다.
- `WAITING_ZONE`의 `waypoint`는 진입 문 인덱스다 (pinky1 = 1 RED1IN, pinky2 = 0 RED2IN). pinky2의 첫 웨이포인트가 문이라 RED2IN 도착 전에는 `MOVING`이 없고, 도착 후 `MOVING(waypoint=1)` → 곧바로 `WAITING_ZONE(waypoint=0)` 순으로 나온다 (:628, :637).

### 4-2. `goto` (`_run_route`, P1v2:500-580)

`waypoint`는 명령으로 보낸 **지점 리스트 안의 0-기반 순번** `idx`, `detail`은 **지점 이름**이다. 홈 복귀 단계와 완료는 `-1`.

| state | waypoint | detail | 근거 |
|---|---|---|---|
| `STARTING` | -1 | 전체 경로 `"P2,P3,P6"` (쉼표로 이은 이름) | :517 |
| `MOVING` | `idx` | 이동 **시작** 시 목표 지점 이름. 구역 문으로 먼저 갈 때는 `RED1IN`(이동 시작, :461) | :544, :461 |
| `WAITING_ZONE` | `idx` | 들어가려는 **구역 안 지점 이름** (문 이름이 아님) | :468 |
| `LEAVING_ZONE` | `idx` (또는 마지막 지점 `last`) | `RED1OUT` | :536, :565 |
| `ARRIVED` | `idx` | 도착한 지점 이름 (이후 10초 대기) | :554, :111 |
| `RETURNING` | -1 | 홈 이름 (`P1`, pinky2는 `P7`) | :573 |
| `DONE` | -1 | `returned home` | :575 |
| `STOPPED` | `idx` (또는 홈 복귀 중 -1) | 지점 이름 (복귀 중 `return`) | :523, :549, :560, :577 |
| `FAILED` | `idx` (또는 복귀 중 -1) | 지점 이름 (복귀 중 `return`), 검증 실패 시 위 2절의 메시지 | :464, :549, :577 |

- `RETRY`는 `goto`에서 **발행되지 않는다.** `_navigate`가 재시도(최대 3회)하며 로그만 남긴다 (:411-433).
- 구역 문을 거치는 단계의 `MOVING`/`LEAVING_ZONE`에서 `detail`이 `RED*`일 수 있다. 이 경우 `waypoint=idx`는 *문을 거쳐 가려는 목표 지점*의 순번이다.
- 구역 안에서 마지막 지점에 도착하면 `LEAVING_ZONE`에는 `waypoint=last`(리스트 마지막 순번)가 실린다 (:565).
- 위 표의 `STOPPED`/`FAILED`가 정확히 어떤 `idx/detail` 조합으로 나오는지는 분기가 많다 (:464, :474, :538, :549, :567, :577). 표는 대표 경로이며, 모든 분기의 전수 확인은 **확인 필요**.

### 4-3. 로봇별 값

두 로봇은 같은 스키마를 쓴다. 다른 점은 구역 문 이름(`RED1*` vs `RED2*`)과 홈(P1 vs P7)뿐이다 (5절).

## 5. 포인트 좌표

두 로봇의 `POINTS`는 동일하다 (P1v2:67-81 = P2v2:67-81, `diff`에서 차이 없음). 좌표는 map 프레임(m), yaw는 rad.

| 포인트 | x | y | yaw | 구역 | goto |
|---|---|---|---|---|---|
| P1 | 0.00 | 0.00 | - | 밖 (시작/홈) | 허용 |
| P2 | 0.65 | 0.15 | - | 밖 | 허용 |
| P3 | 1.55 | -0.40 | - | **안** | 허용 |
| P4 | 1.00 | -0.40 | - | **안** | 허용 |
| P5 | 1.00 | 0.10 | - | **안** | 허용 |
| P6 | 1.55 | 0.06 | - | **안** | 허용 |
| P7 | 0.00 | -0.60 | - | 밖 | 허용 |
| RED1IN | 0.60 | -0.50 | 0 | 문(진입) | **금지** |
| RED1OUT | 0.60 | -0.50 | 3.14 | 문(이탈) | **금지** |
| RED2IN | 0.55 | -0.60 | 0 | 문(진입) | **금지** |
| RED2OUT | 0.55 | -0.60 | 3.14 | 문(이탈) | **금지** |

(근거: P1v2:67-81, `ZONE_POINTS` :105, `GOTO_ALLOWED` :113)

| 항목 | pinky1 | pinky2 | 근거 |
|---|---|---|---|
| 홈 복귀 지점 `HOME_NAME` (`goto` 완료 후) | **P1** | **P7** | P1v2:110, P2v2:109 |
| 구역 문 | RED1IN / RED1OUT | RED2IN / RED2OUT | P1v2:103-104, P2v2:102-103 |
| `start` 경로 | P2 → RED1IN → P3 → P6 → RED1OUT → P1 (6개) | RED2IN → P3 → P6 → RED2OUT → P7 (5개, P2 경유 없음) | P1v2:84-91, P2v2:84-90 |
| 진입/이탈 인덱스 (코드 값) | 1 / 4 | **1 / 4 (불일치, 의도는 0 / 3)** | P1v2:99-100, P2v2:98-99 |
| goto 허용 목록 | P1, P2, P3, P4, P5, P6, P7 | 동일 | :113 |
| 구역 안 포인트 | P3, P4, P5, P6 | 동일 | :105 |

- pinky2의 `start` 경로는 `start`/`goto` 모두 P7에서 끝난다 (`pinky_patrol_node_pinky2_v2.py` 현재 작업 트리 기준, 미커밋). 단, 진입/이탈 인덱스 상수는 `1`/`4`로 남아 있어 5개 경로(RED2IN=0, RED2OUT=3)와 맞지 않는다. 사용자가 이전에 0/3으로 고쳤으나 GitHub 동기화로 되돌아갔다. 이 값이면 P3에서 진입을 요청하고 P7에서 이탈을 알리므로, 수정 전에는 `WAITING_ZONE.waypoint`도 0이 아니라 1로 나온다. 코드는 수정하지 않았다.
- pinky2 코드 주석은 사용자 확인에 따라 코드의 `POINTS`가 맞고 주석이 틀렸다. 현재 파일은 `pinky_patrol_node_pinky2_v2.py:83`이 옛 경로(`P2 -> RED2IN -> ... -> P1`)이고 `WAYPOINTS` 줄 주석(85-89)이 옛 좌표(`0.600,-0.600`, `1.502,-0.400`, `1.498,0.100`, `0.002,-0.597`)다. 실제 `POINTS`는 RED2 `(0.55,-0.60)`, P3 `(1.55,-0.40)`, P6 `(1.55,0.06)`, P7 `(0.00,-0.60)`이다. 주석 수정도 동기화로 되돌아갔다.
- 지도: `map_view_pc/my_pinky_map10.yaml:3-4` — 해상도 0.010 m/px, origin `[-0.167, -0.907, 0]`.
### 5-1. 위험 구역과 지도 대조

- **위험 구역은 P3, P4, P5, P6** (사용자 확인, `ZONE_POINTS`, P1v2:105). 이 네 점의 경계 사각형은 x 1.00~1.55, y -0.40~0.10 m (포인트 외곽 기준)이다.
- 코드와 설정에는 구역 영역 좌표가 없다. 접미사 없는 zone manager는 구역 좌표를 쓰지 않고(`zone_params.yaml`은 `robot_ids`, `max_hold_sec`만 있음), 구역은 `ZONE_POINTS`와 RED 문 포인트로만 정의된다. 화면에 그릴 사각형의 여유 폭은 Step 4에서 정한다 (`zone.yaml`, 값은 **확인 필요**).
- 지도 `map_view_pc/my_pinky_map10.pgm`(P5 바이너리, 189×120 px, maxval 255)과 `my_pinky_map10.yaml`(해상도 0.010, origin `[-0.167, -0.907, 0]`)로 대조했다. 지도 범위는 x -0.167~1.723, y -0.907~0.293 m. 픽셀 변환은 `col = (x - origin_x) / res`, `row = (H - 1) - (y - origin_y) / res`.
- 표의 포인트 11개(P1~P7, RED1IN/OUT, RED2IN/OUT)와 pinky2 주석 좌표 4개는 모두 지도 범위 안이고 **free 픽셀(값 254)** 위에 있다. 점유(0)나 미지(205) 픽셀 위의 점은 없다. 이 대조는 한 점의 픽셀만 본 것이며, 로봇 반경이나 점 사이 경로의 통과 가능 여부는 확인하지 않았다 (**확인 필요**).

## 6. 순찰 노드 동작 요약 (`PinkyPatrolNode.html`, P1v2)

흐름도 `my_pinky_package/PinkyPatrolNode.html`의 goto 탭(:145, :187-202)과 `_init_pose_once` 노드(:286-295)를 코드와 대조했고 내용이 일치한다. HTML의 나머지 노드(순찰 탭, LED, 종료 등)를 모두 대조한 것은 아니다.

### 6-1. goto 경로 순회 (`_run_route`, :500-580)

1. `STARTING`(detail=전체 경로) → `_init_pose_once()` → `navigator.waitUntilNav2Active()` (:517-519)
2. 지점마다 (:521-561):
   - `stop`이 요청됐으면 `STOPPED` 발행 후 종료 (:522-524)
   - 구역 분기 (6-2)
   - `MOVING` 발행 → `_navigate(POINTS[name])` (:544-545). 실패하면 `FAILED`/`STOPPED` 후 종료 (:549-551)
   - `ARRIVED` 발행, 노랑 깜박임으로 `GOTO_WAIT_SEC = 10`초 대기 (`WAIT_EVERY_POINT=True`면 모든 지점, 아니면 마지막 지점만) (:554-561, :111-112)
3. 마지막 지점이 구역 안이면 `LEAVING_ZONE` → RED*OUT에서 락 반납 (:564-570)
4. `RETURNING`(홈) → `_navigate(홈)` → 성공 시 `DONE`, 아니면 `FAILED`/`STOPPED` (:573-577)
5. `finally`: 대기 LED 복귀, `_running = False` (:578-580)

`_navigate`(:404-434)는 한 지점으로 이동하며 실패 시 최대 3회 재시도하고, 이동할 때마다 마지막 위치를 `~/.pinky_last_pose.json`에 저장한다.

### 6-2. 구역 처리 (인접한 두 지점의 구역 여부, :504-513, :526-541)

| 이전 → 다음 | 동작 |
|---|---|
| 구역 밖 → 안 | RED*IN으로 이동 → `WAITING_ZONE`(빨강 깜박임) → `gate.wait_for_entry()`로 허가 대기 → 락 보유 (`_enter_zone` :452-476) |
| 안 → 안 | 락 유지, 문 경유 없이 바로 이동 |
| 안 → 밖 | `LEAVING_ZONE` → RED*OUT으로 이동 → `gate.notify_exit()`로 락 반납 (`_leave_zone` :479-495) |
| 밖 → 밖 | 바로 이동 |

- 구역 안에서 목표 이동이 실패(stop 아님)하면 RED*OUT 후퇴를 시도하고 성공 시 락을 반납한다 (:546-548).
- 구역 안에서 `stop`되거나 RED*OUT 도달에 실패하면 락을 반납하지 않는다. `zone_manager_node`의 `max_hold_sec`(기본 120초, `zone_params.yaml:6`) 타임아웃에 의존한다 (:489-491, :560).
- 허가 대기 중에 `stop`이 오면 `STOPPED`로 종료한다 (:472-474).

### 6-3. 초기 위치 보정 (`_init_pose_once`, :382-399)

- **노드를 실행한 뒤 첫 작업(`start` 또는 `goto`)에서 1회만** 수행한다. `_pose_initialized`를 먼저 `True`로 세팅하므로 실패해도 다시 시도하지 않는다 (:388-390).
- `~/.pinky_last_pose.json`이 없으면 보정을 건너뛴다 (:392-395).
- 있으면: `publish_initial_pose` — `initialpose`를 발행한다. 구독자가 붙을 때까지 최대 5초 대기, 공분산 x/y 0.25, yaw 0.3 (:342-364). 이어서 `spin_in_place(duration=4.0, angular_speed=0.5)`로 제자리 회전 (:398). 기본값(12.6초)은 이 호출에서 쓰이지 않는다 (:366, 흐름도 :292-295도 같은 내용).
- 회전 루프는 `_stop_requested`를 보지 않는다 (:376-378). 보정 중의 `stop`은 회전이 끝난 뒤에야 반영된다 (**이후 동작은 확인 필요**).
- 이 단계는 `STARTING` 발행 직후이며 `waitUntilNav2Active()`보다 먼저 실행된다 (:517-519).

## 6-4. 웹 GUI 명령 API (Step 3)

관제 PC 의 백엔드가 `/{id}/patrol_cmd` 로 보내는 명령은 `POST /api/robots/{id}/command` 로만 나가고, 허용 목록 검증을 통과한 `start`, `stop`, `goto:<허용 지점>[,...]` 만 발행한다. 자세한 규칙과 응답 코드는 `README.md` 의 "명령 API" 절, 허용 지점과 홈은 `config/robots.yaml` 의 `robot_settings` 를 본다.

## 6-5. 웹 GUI 구역·비상정지·LiDAR·수동 조작 (Step 4)

관제 PC 백엔드가 `ROS_DOMAIN_ID=50` 에서 쓰는 토픽과 브라우저 메시지다. 자세한 동작은 `README.md` 의 "Step 4" 절을 본다.

| 방향 | 토픽 | 언제 | 비고 |
|---|---|---|---|
| 구독 | `/zone_manager/status` (String) | 항상 | `free` \| `occupied_by:<id>` (v1). `occupied_by:<id>:<token>` (v2)도 해석. 점유가 바뀔 때만 발행 |
| 구독 | `/{id}/tf` (TFMessage) | **LiDAR 를 켠 동안만** | map → odom(AMCL) → base(오도메트리) 변환. 점을 지도 좌표로 옮기는 데 쓴다 |
| 구독 | `/{id}/tf_static` (TFMessage) | 시작할 때부터 | base → 센서(scan `frame_id`) 같은 고정 변환. 한 번만 발행되므로 TRANSIENT_LOCAL 로 받는다 (브리지가 이 QoS 를 넘겨 주는지는 **확인 필요**) |
| 구독 | `/{id}/scan` (LaserScan) | **브라우저가 켰을 때만** | `qos_profile_sensor_data`(BEST_EFFORT)로 구독하므로 발행자가 RELIABLE 이든 BEST_EFFORT 든 연결된다. 5Hz 제한, 3개당 1개. 모두 끄면 구독 해제 |
| 발행 | `/{id}/patrol_cmd` (String) | 비상정지 | `stop` (기존 명령) |
| 발행 | `/{id}/cmd_vel` (Twist) | 비상정지, 수동 조작 | 비상정지: 0 속도 즉시 1회 + 10Hz 로 2초. 수동 조작: 설정 상한(기본 0.1 m/s, 0.5 rad/s, 하드 상한 0.2, 1.0)으로 자른 속도를 10Hz, 입력이 0.5초 없거나 연결이 끊기면 0 속도 |

브라우저 → 서버 (WebSocket 텍스트, JSON):

| 메시지 | 의미 |
|---|---|
| `{"type":"scan","robot":id,"on":bool}` | 이 화면의 LiDAR 구독 요청/해제 |
| `{"type":"drive","robot":id,"linear":m/s,"angular":rad/s}` | 수동 조작 입력 (누르는 동안 10Hz) |
| `{"type":"drive_stop"}` | 수동 조작 끝 |

서버 → 브라우저 추가 메시지: `scan`(켠 화면에만, 지도 좌표 점), `drive_denied`(조작 거절 사유, 같은 사유는 한 번만), `zone`/`snapshot` 의 해석된 구역 상태(`state`, `holder`, `token`, `held_sec`).

설정: 구역 사각형과 문은 `config/zone.yaml`(초안, `confirmed: false`), 나머지는 `config/robots.yaml` 의 `motion`/`manual`/`scan`.

## 6-6. 웹 GUI 인증과 프로세스 제어

프로그램에 고정된 계정: ID `mngr`(manager 역할, 명령 가능)와 ID `oper`(operator 역할, 보기 전용). 로그인은 `POST /api/login` 에 `{id, password}`. 서버를 `--no-auth` 로 실행하면 인증이 꺼지고 프로세스 제어도 꺼진다.

| 메서드 경로 | 권한 | 설명 |
|---|---|---|
| `GET /api/me` | 공개 | `{auth, role, user, procs_allowed, procs_configured}` |
| `POST /api/login` `{id, password}` | 공개 | 성공 시 `pinky_session` 쿠키와 `{ok, role, user}`. 이미 로그인한 상태에서 부르면 이전 세션을 끝낸다(계정 전환). 틀리면 401, 5회 연속(1분) 실패 시 429. 인증이 꺼져 있으면 400 |
| `POST /api/logout` | 공개 | 세션 삭제 |
| 그 밖의 `/api/*`, `/ws` | operator 이상 | 인증이 켜져 있으면 로그인 필요 (401). `/api/health` 는 공개 |
| `POST /api/robots/{id}/command`, WS `drive` | manager | operator 는 403 / `drive_denied` |
| `POST /api/robots/{id}/estop`, `POST /api/estop` | operator 이상 | 정지는 누구나 누를 수 있다 |
| `GET /api/procs` | operator 이상 | `{enabled, procs:[{id,label,kind,robot,host,configured,state,message,pid,uptime_sec,exit_code,confirm_stop,health,log_n}], sequence}` |
| `POST /api/procs/{id}/start`, `/stop` | manager | stop 은 `confirm_stop` 인 프로세스(로봇)에 `{"confirm": true}` 필요 (없으면 409 + `needs_confirm`) |
| `POST /api/procs/start_all`, `/stop_all` | manager | 백그라운드 시퀀스. 진행은 `sequence` (`{kind, state: running\|done\|failed, step, message, results}`). `start_all` 순서는 `config/processes.yaml` 의 목록 순서(도메인 브릿지 → bringup → map → zone_manager → 순찰 노드)이고, 각 단계가 켜진 것을 확인한 뒤 다음으로 간다: `message` 에 대기 사유가 나오고, 로봇 토픽이 `ready_sec`(기본 25초) 안에 안 오면 `restart_if_stalled` 인 브릿지를 한 번 재시작하며, 그래도 안 오면 `failed` 와 사유로 멈춘다. stop_all 도 로봇 프로세스가 돌면 `confirm` 필요 |
| `GET /api/procs/{id}/log` | manager | 최근 200줄 |
| `GET /api/logs?since=<번호>&limit=<개수>` | manager | 로그 화면용. `{epoch, last, entries, sources}`: 프로세스 출력과 GUI 서버 로그를 한 목록으로, `since` 이후 새 줄만 (소스마다 최근 1000줄, 메모리에만 둠). `sources` 는 `[{id, label, configured}]` |

로봇의 생존 확인: `online` 은 `patrol_status`, `amcl_pose`, `battery/*` 외에 `/{id}/odom`(`qos_profile_sensor_data`, 초당 2번만 반영, 값은 쓰지 않음)으로도 유지된다. bringup 만 켠 로봇도 online 으로 보이게 하려는 것이다 (`backend/ros_bridge.py`). `odom` 은 두 브리지 YAML 에 이미 있다.

프로세스 상태 `state`: `stopped | starting | running | stopping | failed | exited`. WS 에는 `{"type":"procs", ...}` (snapshot 의 `procs` 와 같은 모양)가 변경될 때 0.1초 단위로 모아서 온다. 프로세스 제어 API 는 `procs_allowed`(인증이 켜져 있을 때만 true, `--no-auth` 면 false)가 아니면 403. 허용 코드: 알 수 없는 id 404, 명령 미설정·이미 실행 중·외부에서 실행 중·확인 필요 409, 시작/정지 실패 502.

## 6-7. 웹 GUI 알람 (알람바 Step A1, A2, A3, A4, A5, A7)

알람바(화면 위)에 보이는 사건은 **서버가 만든다** (`backend/alarms.py` 의 `AlarmManager`). 접속한 모든 화면이 같은 알람을 보고, 하단 명령 이력 패널은 알람바에 통합돼 없어졌다. 서버가 알 수 없는 사건(WS 끊김, 지도·설정 불러오기 실패, 화면 안내, 요청이 서버에 닿지 못함)은 브라우저가 같은 목록에 직접 추가한다 (id 가 `local-N`, 시각은 브라우저 시계).

**반복의 정의** (PRD 결정 19): 같은 `code`+`robot` 알람이 **active 이고 아직 ACK 안 됐으면** 새 행 없이 `count` 만 올린다(화면 `×N`, `t_last`·`text` 갱신). ACK 했거나 해소된 뒤에 또 생기면 새 행이다. **이력 파일에는 화면과 달리 시도 하나하나를 시간 순서로 모두 남긴다** (조작이든 로봇에서 받은 이벤트든 같다. 언제 어떤 조작을 했는지로 문제를 추적하기 위해서다).

**알람 객체** (WS `alarm` 의 `alarm`, `snapshot.alarms` 의 원소, `GET /api/alarms`):

| 필드 | 뜻 |
|---|---|
| `id` | 서버가 매기는 정수 (서버를 다시 켜면 1 부터 다시) |
| `code` | 알람 종류 (아래 표) |
| `level` | `HH` > `H` > `L` > `LL` > `info` (높을수록 심각) |
| `robot` | 로봇 id 또는 `null` |
| `text` | 알람바에 보이는 문구 (코드별 템플릿, 한영 혼용 가능) |
| `detail` | `{reason}` 원문 (한국어 오류 원인 그대로) |
| `state` | `active`(조건 지속) \| `cleared`(조건 사라짐). `info` 는 해소 개념이 없어 만들 때부터 `cleared`. ACK 는 상태를 바꾸지 않는다 (아래 `acked_by`) |
| `t_raised`, `t_last`, `t_cleared` | 처음 발생, 마지막 발생, 해소 시각 (서버 epoch 초, 해소 전엔 `t_cleared` 가 `null`) |
| `count` | 같은 알람이 ACK 전에 반복된 횟수 (새 행은 1) |
| `acked_by`, `acked_at` | ACK 한 계정 ID(`mngr` \| `oper`, `--no-auth` 서버는 `no-auth`)와 시각(epoch 초). ACK 전에는 `null`. `info` 는 ACK 대상이 아니라 항상 `null`. **해소된 알람이 ACK 되어 있으면 알람바에서 숨긴다** (활성 알람은 `확인됨` 로 계속 보이다가 해소되면 숨는다). 숨긴 알람은 서버 메모리와 이력 파일에는 남고 `snapshot.alarms`/`GET /api/alarms` 에서는 빠진다 |

**메시지와 REST**

| 방향 | 내용 |
|---|---|
| 서버 → 브라우저 WS | `{"type":"alarm","alarm":{…}}` 생성과 변경(반복, 해소, ACK)마다 하나. 같은 `id` 는 교체하고, 해소+ACK 로 숨겨진 알람이면 목록에서 지운다 |
| 서버 → 브라우저 WS | `snapshot` 의 `alarms`: 현재 보관 중인 알람 전체(최신이 앞). 접속(재접속)할 때마다 받으며, 브라우저는 서버 알람을 이것으로 통째로 바꾼다 |
| `GET /api/alarms` | operator 이상. `{alarms:[…]}` (snapshot 과 같은 값, 숨긴 알람 제외 최대 100건) |
| `POST /api/alarms/ack` | **operator 이상** (로그인 안 하면 401, 출처·Content-Type 검사는 다른 POST 와 같음). 본문 `{"id": 정수 \| [정수…] \| null}` (`id` 없음/`null` 이면 전체). 대상은 `info` 가 아니고 아직 ACK 안 된 알람(활성·해소). 응답 `{ok:true, acked:[id…]}` (이미 ACK 했거나 없는 id 는 빠진다). 형식이 틀리면 400 `{ok:false, error}`. 바뀐 알람마다 WS `alarm` 을 모든 화면에 보내고 이력 파일에 `acked` 줄을 남긴다 |
| `GET /api/alarms/history?date=YYYY-MM-DD&limit=N` | operator 이상. 저장된 이력 레코드(아래 "알람 이력 파일"), 최신이 앞. `date` 를 주면 그날 것만, `limit` 기본 100 최대 1000. 날짜 형식이 틀리면 400, 저장이 꺼져 있으면 404 |
| `GET /api/alarms/history/files` | operator 이상. `{dir, chunk, files:[{name, date, started, seq, records, size}]}` (최신이 앞). `started` 는 시각 이름 파일의 `HH:MM:SS`, `seq` 는 예전 번호 이름 파일의 번호이고 해당 없으면 `null` |
| `GET /api/alarms/history?view=alarms&from=&to=&robot=&level=&state=&code=&q=&limit=&before=` | **Alarm 화면 History 탭 (Step A7)**, operator 이상. 이벤트 줄을 `(id, t_raised)` 로 묶어 **알람 한 건당 한 행**으로, 발생이 최신인 것부터 `{alarms:[…], next}`. `from`/`to`: `YYYY-MM-DD`(서버 PC 현지, 발생 날짜, 둘 다 포함, 최대 366일), `robot`, `level`(쉼표로 여러 개: `HH,H,L,LL,info`), `state`(쉼표로 여러 개: `active`·`acknowledged`·`cleared`·`uncleared`), `code`, `q`(`text`·`detail` 부분 일치, 대소문자 무시, 100글자), `limit` 1~200(기본 200), `before`(이전 응답의 `next` = `발생시각:id`). 값이 틀리면 400 `{error}`, 저장이 꺼져 있으면 404. `view` 가 없거나 `events` 면 위의 이벤트 단위 응답 그대로다. 행: `{id, t_raised, code, level, robot, text, detail, state, count, acked_by, acked_at, t_last, t_cleared, duration(해소된 비-info 만 초, 아니면 null), events(줄 수), file(발생 줄이 있는 파일)}` |
| `GET /api/alarms/history/{id}/events?t_raised=…` | operator 이상. 한 알람의 모든 이벤트 줄(일어난 순서, 줄마다 `file`)과 묶은 행 `{alarm, events}`. `id` 는 서버를 다시 켜면 겹치므로 행의 `t_raised` 를 함께 보낸다. 없으면 404 |
| `GET /api/alarms/history/export?from=&to=&robot=&level=&state=&code=&q=` | operator 이상. 같은 조건의 CSV(UTF-8 **BOM**, `alarms_<from>_<to>.csv`). 열: 발생, 로봇, 레벨, 코드, 메시지, 상태, 횟수, 확인 계정, 확인 시각, 해소, 지속 시간(초). 최대 10000행이고 넘으면 잘라서 주며 응답 헤더 `X-Export-Truncated: 1`(`X-Export-Rows` 는 행 수) |
| `GET /api/alarms/history/summary` | operator 이상. `{enabled, dir, chunk, files:[{name, date, started, seq, records(이벤트 줄), alarms(raised 줄), size}], total_files, total_events, total_alarms}`. 저장이 꺼져 있으면 404 가 아니라 `{enabled:false}` |
| (mock 전용) `POST /api/mock/robot/{id}/silent` `{on}` | 그 로봇이 신호를 끊어 online 타임아웃 뒤 오프라인이 된다 |
| (mock 전용) `POST /api/mock/no-subscriber` `{on}` | patrol_cmd/cmd_vel 구독자가 없는 것처럼 명령 전송이 실패한다 |
| (mock 전용) `POST /api/mock/robot/{id}/patrol` `{state, detail}` | 임의 patrol 상태를 주입한다 (GUI 로는 못 만드는 FAILED/RETRY 시험용) |

**알람 코드** (기본값은 `backend/alarms.py` 의 `CATALOG`. 레벨 순서는 PRD 결정 9 로 확정: `HH` > `H` > `L` > `LL` > `info`, 색은 HH·H 빨강, L 주황, LL 노랑, info 기본):

| code | 레벨 | 언제 만들고 언제 해소하나 |
|---|---|---|
| `ESTOP_FAILED` | HH | 비상정지의 patrol stop 과 cmd_vel 0 이 **둘 다** 못 나감. 이후 비상정지가 성공하면 해소 |
| `ESTOP_PARTIAL` | HH | 둘 중 하나만 나감. 이후 비상정지가 성공하면 해소 |
| `CMD_SEND_FAILED` | H | 명령 발행 실패 (구독자 없음 등, HTTP 503). 같은 로봇에 새 명령이 나가면 해소 |
| `ROBOT_OFFLINE` | H | 로봇 online 이 꺼짐 (`StateStore` 타임아웃). 신호가 돌아오면 해소. 서버 시작 뒤 한 번도 신호가 없던 로봇은 만들지 않는다 |
| `PROC_FAILED` | H | 시스템 패널 프로세스가 `failed` 가 되는 순간(시작 실패, SSH 연결 실패, 시작 직후 종료, 예기치 않은 종료, 정지 실패). 알람 키(`robot` 칸)는 프로세스 id, 문구 `{resource} 실패: {reason}`. 다시 시작하거나(`starting`) 정지·종료되면 해소. 이 실패 응답에는 `alarmed: true` 가 실려 화면이 `PROC_REQUEST_FAILED` 를 또 만들지 않는다(알람을 끄면 `false`) |
| `PATROL_FAILED` / `PATROL_RETRY` | H | `patrol_status` 의 `FAILED` / `RETRY`. 다른 상태로 바뀌면 해소 (한 로봇에 순찰 알람은 하나만 active) |
| `CMD_REJECTED` | L | 백엔드 검증이 명령을 거절 (HTTP 400). 로봇 응답이 오거나 새 명령이 나가면 해소 |
| `CMD_NO_RESPONSE` | L | 명령 뒤 `no_response_sec` 안에 `patrol_status` 가 안 바뀜. 응답이 오거나 새 명령이 나가면 해소. 비상정지는 무응답이어도 만들지 않는다 |
| `PATROL_STOPPED` | LL | `patrol_status` 가 `STOPPED`. 다른 상태로 바뀌면 해소 |
| `CMD_SENT`, `ROBOT_ONLINE` | info | 기록용 행 (명령 전송 성공 · 비상정지 성공은 `cmd` 가 `estop` · 로봇이 온라인이 됨). 소리·확인 대상이 아니고 알람바 `Hide Info` 로 숨길 수 있다 |
| `SERVER_DISCONNECTED`, `LOAD_FAILED` | H, LL | **브라우저 전용** (서버는 만들지 않는다). 연결 끊김은 재접속하면 해소하고 `서버에 다시 연결됨`(info) 를 남긴다 |

**Alarm 화면 History 의 상태** (Step A7): 이력 파일에는 해소되지 못하고 서버가 꺼진 알람의 종료 줄이 없다(형식 불변). 그래서 행의 `state` 는 서버 메모리와 맞춰 정한다 — `active`/`acknowledged`: 지금 서버 메모리의 active 알람과 `(id, t_raised)` 가 같다(ACK 여부로 갈린다). `cleared`: 마지막 줄의 `state` 가 `cleared`(해소된 알람을 나중에 ACK 해도 마찬가지). `uncleared`: 그 밖의 모두 — 해소 기록이 없고 지금은 메모리에도 없는 알람(서버가 꺼졌다). 추정하지 않는다. 화면(`frontend/alarmview.js`, `app.js` 의 History)은 서버 메모리의 active 알람을 WS 로 받아 조회 결과 맨 위에 고정하고(기간 조건은 걸지 않는다) 같은 알람은 `(id, t_raised)` 로 한 번만 보인다. 읽기 비용: 파일을 최신부터 필요한 만큼만 읽고, 기간 시작일보다 이른 파일은 열지 않는다.

**알람 이력 파일** (`backend/alarmlog.py`): 알람바(모니터링)에는 최근 **100건**만 있지만(서버도 100건으로 자른다), 일어난 알람 이벤트는 모두 파일로 남는다.

| 항목 | 규칙 |
|---|---|
| 위치 | `config/robots.yaml` 의 `alarm_history_dir` (기본 `data/alarms/`, `.gitignore` 대상). `--alarm-history-dir` 로 바꿀 수 있고, `--mock` 은 기본적으로 뒤에 `-mock` 을 붙인 폴더(`data/alarms-mock/`)에 써서 실제 이력과 섞이지 않는다. 설정 줄을 지우면 저장하지 않는다 |
| 파일 이름 | `alarms_YYYY-MM-DD_HHMMSS.jsonl`. 날짜와 시각은 **그 파일을 처음 만든 시각**(서버 PC 현지 시각, 첫 줄의 `saved_at` 과 같음). 같은 초에 파일이 또 필요하면 `-2` 가 붙는다. 예전 버전의 번호 이름(`…_001.jsonl`)도 읽고 이어서 쓴다 |
| 분할 | 한 파일에 `alarm_history_chunk`(기본 **1000**)건. 차면 **다음 이벤트가 오는 시각**의 이름으로 새 파일을 만든다. 날짜가 바뀌면 안 찼어도 새 파일. 서버를 다시 켜면 오늘 가장 최근 파일이 안 찼을 때 이어서 쓴다. 알람바(화면)의 100건 한도와는 별개다 |
| 한 줄(JSON) | 저장 일시 `saved_at`·`t`, 이벤트 `event`(`raised` \| `repeated` \| `acked` \| `cleared`)에 알람 객체의 모든 필드를 더한 16개 키. **일어난 순서대로 줄을 추가만 하고 기존 줄은 고치지 않는다** (묶인 알람도 반복마다 `repeated` 줄이 한 줄씩 쌓여, 시도마다 시각·`text`·`count` 가 남는다). 필드별 타입, 예시, 읽는 법은 `reference/alarm-history-format.md` |
| 저장 대상 | 서버가 만든 알람 전부(info 행 포함). 브라우저 전용 알람(서버 끊김 등)은 서버가 모르므로 저장하지 않는다 |
| 쓰기 시점 | 이벤트가 올 때마다 즉시 한 줄 추가 (갑자기 꺼져도 직전까지 남는다). 쓰기에 실패해도 알람은 계속 동작하고 경고 로그만 남기며, 복구되면 다시 쓴다 |
| 보관 | 파일은 자동으로 지우지 않는다 (오래된 것은 직접 지운다) |

**알람 소리** (Step A3, 소리는 브라우저에서 나므로 서버는 파일만 준다. 판단 로직은 `frontend/alarmsound.js`):

| 항목 | 내용 |
|---|---|
| `GET /api/alarms/sounds` | 사운드 파일 목록 `{files: [{name, size, type}], max_bytes, extensions}`. `config/sounds/`(설정 `alarm_sound_dir`, `--alarm-sound-dir`)의 `.mp3 .wav .ogg` 중 1MB 이하만 이름 순으로. 로그인 필요(인증을 켠 경우). 폴더가 없으면 빈 목록 |
| `GET /sounds/<파일>` | 그 파일. 폴더 밖 경로, 하위 폴더, 숨김 파일, 허용 안 된 확장자, 크기 초과, 폴더 밖을 가리키는 심볼릭 링크는 모두 404 (존재 여부를 구분해 주지 않는다). 화면 파일처럼 로그인 없이 받을 수 있다 (`<audio>` 가 쓰는 주소) |
| 업로드 | 없다 (PRD 결정 10 의 1차안). 파일을 폴더에 직접 두면 소리 설정 화면을 열 때(또는 화면에 다시 접속할 때) 목록에 나타난다 |
| 소리를 내는 알람 | ACK 안 된 active 알람 (`info` 제외, SIL 대상과 같다). ACK, 해소, SIL, 전체 끄기, 레벨 끄기 중 하나면 멈춘다 |
| 소리의 종류 | 레벨마다 파일 하나를 고른다. 파일이 없거나 목록에 없거나 재생이 실패하면 레벨별 합성 비프음(HH 1400Hz 4연타, H 1000Hz 2연타, L 700Hz, LL 480Hz). 자동재생이 막힌 것은 실패가 아니다 (클릭 뒤 다시 시도) |
| 반복 | 설정한 주기(1~60초, 기본 5)마다. 더 심각한 알람이 생기거나 남은 알람의 레벨이 바뀌면 주기를 기다리지 않고 바로 |
| 설정 저장 | 이 브라우저의 localStorage `pinky.alarmsound.v1` `{enabled, volume, interval, levels: {HH: {on, file}, H, L, LL}}`. 깨진 값은 기본값으로 고치고, 저장소를 못 써도 이번 화면에서는 동작한다 |
| 시각 경고 | 소리 설정과 상관없이 알람 행은 그대로 보인다 (HH 도 전체 음소거에서 행이 유지된다) |

**알람 카탈로그** (Step A4 표시, Step A5 수정. 정의와 검증은 `backend/alarmcatalog.py`):

| 항목 | 내용 |
|---|---|
| 정의 | 시스템이 낼 수 있는 모든 알람 22개: 코드(ID), 이름, 발생 조건, 메시지 템플릿, 레벨, `origin`, 근거 코드(`source`), 허용 자리표시자(`placeholders`), `soundable`. `origin` 은 `server`(서버가 만들어 모든 화면에 보냄)와 `browser`(브라우저가 이 화면에만 만듦: 서버 끊김, 요청 실패, 로드 실패, 화면 안내 등. `addAlarm(..., {code})` 의 `code` 로 구분). `soundable` 은 소리를 낼 수 있는 종류인지: 서버 알람과 `SERVER_DISCONNECTED`(active 상태가 있음)만 true, 나머지 브라우저 알람은 "한 번 알리고 끝나는 기록"(ACK 로 끌 수 없음)이라 소리가 없다 |
| 세 겹 | 코드 기본값 ← `config/alarms.yaml`(사람이 편집기로 고침, GUI 는 쓰지 않음) ← `config/alarms.local.yaml`(manager 가 GUI 에서 저장한 변경분). local 파일에는 **기본과 다른 항목만** 들어가고, 기본과 같아지면 그 항목이 빠진다. 임시 파일에 쓴 뒤 `os.replace` 로 교체(쓰다 끊겨도 옛 파일이 남음), git 에는 올리지 않는다(`.gitignore`). `--mock` 은 `alarms.local-mock.yaml`(인자 `--alarms-local` 로 지정 가능). local 파일이 깨졌으면 서버는 멈추지 않고 기본값으로 시작하며 `local_error` 로 알린다(파일은 건드리지 않음). `alarms.yaml` 이 틀렸으면 서버 시작을 거절한다 |
| `GET /api/alarms/catalog` | operator 이상(로그인 없으면 401). `{alarms: [{code, name, condition, message, level, sound, sound_file, repeat, enabled, protected, origin, soundable, source, placeholders, defaults, modified, last_raised, count}], levels, counted_since, can_edit, local_error}`. 심각한 레벨 순. `sound` 는 해소된 값(기본: 소리를 낼 수 있고 `info` 가 아니면 true). `defaults`(기본값)와 `modified`(GUI 로 바꾼 적 있음)로 변경 여부를 안다. `last_raised`(epoch)·`count` 는 **이번 서버 실행 중** 센 값이고 반복(`repeated`)도 한 번으로 센다(끈 알람은 세지 않음). 브라우저 알람은 서버가 모르므로 `null` |
| `PUT /api/alarms/catalog/{code}` | **manager 만** (로그인 없음 401, operator 403 `알람 카탈로그는 manager 만 바꿀 수 있습니다`, 응답 문구는 한국어). Origin·Content-Type(JSON) 검사. 본문은 바꿀 항목만: `message`, `level`, `enabled`, `sound`, `sound_file`, `repeat`. 하나라도 검증에 실패하면 400 과 한국어 `error` 이고 **아무것도 바뀌지 않는다**. 성공하면 `{ok, changes, alarm}` 이고 모든 화면에 WS `alarm_policy` 를 방송한다. 모르는 코드 404, 저장 실패 500(메모리 상태는 되돌림) |
| `POST /api/alarms/catalog/{code}/reset` | manager 만. 그 알람의 GUI 변경분을 지워 기본값(코드 + `alarms.yaml`)으로 되돌린다. 이미 기본이면 변경 없음으로 200 |
| 검증 (FRA5-6, 오류 문구는 한국어, 화면의 검사와 같은 문구) | `message`: 1~120 **글자**(바이트 아님, 한글·이모지도 한 글자), 줄바꿈·제어문자 거부, 자리표시자는 `{name}` 형태만(`{a.b}` `{a[0]}` `{a:>5}` `{}` 와 짝 없는 중괄호 거부)이고 이름은 그 알람의 **기본 문구가 쓰는 것과 `{robot}`** 만. 브라우저 알람의 `message` 는 바꿀 수 없음(호출부의 동적 문장). `level` ∈ HH,H,L,LL,info. `enabled`·`sound` 는 bool. `sound_file` 은 `config/sounds/` 에 지금 있는 파일(또는 빈 값=레벨 파일). `repeat` 는 1~60 정수 또는 null(=브라우저 소리 설정의 주기). 이름·조건·모르는 항목은 거절 |
| 보호 (FRA5-4) | `ESTOP_FAILED`, `ESTOP_PARTIAL`, `ESTOP_REQUEST_FAILED`(비상정지 요청 실패, 브라우저)는 **끄기·레벨 변경·소리 끄기를 서버가 거부**한다(레벨은 HH 고정). 문구·사운드 파일·반복 주기는 바꿀 수 있다. `info` 알람에 `sound: true`, 소리 없는 알람(`soundable: false`)에 소리·파일·주기를 주는 것도 거부 |
| 끄기의 의미 (FRA5-1) | `enabled: false` 인 서버 알람은 **만들어지지 않는다**(알람바·이력 파일·발생 횟수 모두 없음). 끄는 순간 이미 active 인 같은 코드 알람은 해소(cleared)되어 소리가 멈춘다. 브라우저 알람은 서버 정책(`alarm_policy`)을 받은 화면이 만들지 않는다. **레벨·문구 변경은 이후에 생기는 알람부터** 적용되고 이미 뜬 행은 그대로다 |
| 정책 방송 | `snapshot.alarm_policy` 와 WS `{type: 'alarm_policy', policy}`: `{code: {enabled, level, sound, sound_file, repeat}}`. 접속한 모든 화면이 브라우저 알람의 Enable·레벨과 알람별 소리를 같게 적용한다 |
| 알람별 소리 (결정 11 확장) | 소리 우선순위: **알람의 `sound_file` > 소리 설정 화면에서 레벨마다 고른 파일 > 합성 비프**. 알람 파일이 없거나 재생에 실패하면 레벨 파일로, 그것도 안 되면 비프. 반복 주기는 이기는 알람(가장 높은 레벨, 같으면 가장 최근)의 `repeat`, 없으면 소리 설정의 주기. 음량, 전체 켜기/끄기, 레벨별 켜기/끄기는 계속 브라우저(localStorage)가 정한다. `sound: false` 인 알람은 소리 대상도 SIL 대상도 아니다(알람바에는 보인다) |
| 감사 기록 (FRA5-5) | 저장·되돌리기·거절은 GUI 서버 로그로 남아 **로그 화면(`GUI 서버`)**에서 보인다: `알람 카탈로그 변경 (mngr): CMD_REJECTED level 'L'→'H'`, `알람 카탈로그 되돌리기 (mngr): ...`, `알람 카탈로그 변경 거절 (mngr, CMD_REJECTED): <사유>`. 끄기는 WARN. 계정은 `mngr`(인증이 꺼져 있으면 `no-auth`). 별도 이력 파일은 없다 |
| 화면 | 왼쪽 메뉴 `Alarm` → `Catalog` 탭 (`History` 탭은 전체 알람 이력, Step A7). 열 `ID / 이름 / 발생 조건 / 메시지 / 레벨 / 소리 / 사용 / 최근 발생 / 횟수`, manager 에게만 `편집` 열(버튼 → 대화상자: 사용, 레벨, 메시지, 소리, 사운드 파일, 반복 주기(초), 저장 / 취소 / 기본값으로 되돌리기). 바꾼 알람은 ID 옆에 `•`. 열 머리글 경계를 끌어 열 너비 조절(더블클릭: 자동, 브라우저 localStorage `pinky.avColWidths`), 탭 줄의 `알람소리` 버튼은 소리 설정 창을 연다. 필터 레벨·출처·검색, 열려 있는 동안 5초마다와 정책 방송 때 갱신. 고정 문구는 한국어 |
| 누락 검사 | `tests/test_alarmcatalog.py`: `raise_(...)`·`PATROL_CODES`·`addAlarm(.., {code})` 가 내는 모든 코드가 카탈로그에 있고, `code` 없는 `addAlarm` 호출이 없고, 어디에서도 내지 않는 항목이 없고, `origin` 이 실제로 내는 쪽과 맞는지 코드를 읽어 확인한다. 새 알람을 추가할 때 카탈로그를 빠뜨리면 이 테스트가 실패한다 |

**`command` 이벤트** (기존, 이력 패널이 없어진 뒤에도 서버는 계속 보낸다): WS `{"type":"command","entry":{id, time, robot, cmd, points, sent, result, detail}}`, `snapshot.commands`(오래된 것이 앞). `result` 는 `sent` \| `acknowledged` \| `no_response` \| `rejected` \| `failed`. 화면은 그리지 않고 브라우저의 `app.history` 와 카드의 진행 문구(`last_command`/`last_task`)가 쓴다.

## 7. 확인 필요 목록

- (프로세스 제어) pinky1/pinky2 의 bringup/map/순찰 노드 명령, source 경로, SSH 주소(`192.168.45.20`/`.22`)는 `config/processes.yaml` 에 채워져 있지만 **로봇에서 실제로 실행해 확인하지 않았다** (`docs/step6_process_control_real_checklist.md`)
- (프로세스 제어) SSH pty 에 Ctrl-C 를 보내 `ros2 launch` 가 로봇에서 깨끗이 종료되는지, 자식 프로세스가 남는지 (`detect` 확인 단계가 남은 것을 잡는다)
- (프로세스 제어) 서버 종료 때 로봇의 bringup/map 이 실제로 정지하는지 (SSH Ctrl-C → 원격 pkill → SSH 끊기 순서, 남아 있으면 종료 로그에 남는다)
- (프로세스 제어) 브릿지는 `ros2 launch domain_bridge bridges.launch.xml` 하나로 띄운다 (이 PC 에서 `install/setup.bash` 로 확인됨). 실제 로봇 토픽이 넘어오는지, 센서 QoS 때문에 bringup 뒤에 켜야 하는지는 실물 확인 필요

- `goto`에 같은 지점을 중복해서 보냈을 때의 동작
- `goto`의 `STOPPED`/`FAILED` 분기별 `waypoint`/`detail` 전수 확인
- 늦게 접속한 구독자가 `patrol_status`, `/zone_manager/status`의 마지막 값을 받는지 (QoS 실측)
- 보정 회전 중 `stop`을 보낸 뒤 실제 동작
- 화면에 그릴 구역 사각형의 여유 폭 (구역 = P3~P6은 확정, 5-1절)
- 로봇 반경과 점 사이 경로의 통과 가능 여부 (지도 대조는 점 픽셀만 확인)
- 카메라 토픽 브리지 추가 방법 (기존 파일 수정이 필요)
- `PinkyPatrolNode.html`의 goto/초기 보정 외 나머지 노드와 코드의 일치 여부
- (Step 4) 비상정지의 `cmd_vel` 0 속도가 로봇에서 실제로 먹히는지: 관제 PC 의 `lane_follower_node`, Nav2, 순찰 노드의 보정 회전(`spin_in_place`, 4초간 `cmd_vel` 발행)이 같은 토픽을 쓰므로 마지막 발행이 이긴다
- (Step 4) 로봇 쪽 모터 제어가 `cmd_vel` 타임아웃을 가지는지: 백엔드나 브리지가 갑자기 죽었을 때 로봇이 마지막 속도를 유지하는지 코드로 확인되지 않았다
- (Step 4) `/{id}/scan` 의 실제 QoS, 주기, 프레임(센서와 로봇 중심의 오프셋), Wi-Fi 에서의 대역폭
- (Step 4) 화면에 그리는 구역 사각형의 `margin`(0.15 m 는 근거 없는 초안)과 문 위치
- (Step 4) `/zone_manager/status` 의 늦은 접속: 매니저가 변화 때만 발행(VOLATILE)하므로 점유 중에 백엔드를 켜면 다음 변화 전까지 "상태 수신 전"

해소된 항목: zone manager는 접미사 없는 매니저로 확정(`_v2` 파일은 남기되 미사용), `_v2` 순찰 노드가 현재 버전, 코드의 `POINTS`가 정답, `my_pinky_package/setup.py:30`의 존재하지 않는 모듈 entry point는 빌드에 영향 없음(사용자 확인).

미해결(사용자 조치 필요): `pinky_patrol_node_pinky2_v2.py:98-99`의 `ZONE_ENTRY_INDEX`/`ZONE_EXIT_INDEX`를 0/3으로 수정, 83-89행 주석 정정 (동기화로 되돌아감, 2026-10-06 확인).

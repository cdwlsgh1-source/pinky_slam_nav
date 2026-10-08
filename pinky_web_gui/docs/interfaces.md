# 인터페이스 문서 (관제 PC 기준)

관제 PC(`ROS_DOMAIN_ID=50`)에서 본 토픽 규약, 명령 문법, 상태 스키마, 포인트 좌표, 순찰 노드 동작을 정리한다.

- 근거 표기: `파일:줄`. 약칭 — **P1v2** = `my_pinky_package/my_pinky_package/pinky_patrol_node_pinky1_v2.py`, **P2v2** = 같은 폴더의 `pinky_patrol_node_pinky2_v2.py`, **Y1** = `domain_bridge_pc/config/pinky_bridge_pinky1.yaml`, **Y2** = `pinky_bridge_pinky2.yaml`.
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

## 7. 확인 필요 목록

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

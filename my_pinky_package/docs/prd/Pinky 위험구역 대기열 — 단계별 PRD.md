# Pinky 위험구역 대기열 — 단계별 PRD

Oct 12, 2026 · 초안 v3 (**기존 파일은 수정하지 않고 전부 `_v3` 신규 파일로 만든다** — 이상 시 복구 목적. **Step Q1 완료**, 문 좌표는 A안 확정, 대기 앵커는 두 로봇이 공유. 복구 절차는 6절, 실측이 필요한 값은 7절)

| Step | 상태 |
| --- | --- |
| Q1 매니저 v3 | **완료** (단위 테스트 15개, 시뮬 9개 PASS) |
| Q2 게이트 클라이언트 v3 | 대기 |
| Q3 순찰 노드 v3 | 대기 |
| Q4 브릿지·GUI | 대기 |
| Q5 실로봇 | 대기 |

## 0. 공통 개요

### 0-1. 배경과 문제

`pinky_patrol_node_pinky1_v2.py`는 자기 진입문(`RED1IN`)에 도착한 뒤 `ZoneGateClient.wait_for_entry()`로 허가를 기다린다 (`_enter_zone`). pinky2는 `RED2IN`에서 기다린다. 그래서 여러 대가 몰리면:

- 대기 위치가 로봇마다 달라 흩어진다 (`RED1IN`/`RED2IN`은 서로 가깝다: (0.60,-0.50) / (0.55,-0.60)).
- 같은 문 앞에 두 대가 서면 서로 막거나 부딪힐 수 있다.
- `zone_manager_node.py`는 FIFO 큐로 "순서"는 알지만 "어디에 서는지"는 모른다.

### 0-2. 목표와 비목표

- **목표**: (1) 대기 지점을 **한 곳(앵커)** 으로 통일한다. (2) 앵커에 이미 로봇이 있으면 뒤에 오는 로봇은 **일정 간격 d 뒤**에 줄줄이 선다. (3) 앞 로봇이 진입하면 뒤 로봇이 **한 칸씩 전진**한다. (4) **문제가 생기면 v2 구성으로 즉시 되돌릴 수 있다.**
- **비목표**: 구역 안 주행 변경 없음. **기존 파일(`zone_manager_node.py`, `zone_gate_client.py`, `pinky*_v2.py`, `zone_params.yaml`, 기존 브릿지 설정)은 한 줄도 바꾸지 않는다.** 로봇끼리 직접 통신하지 않는다 (도메인 분리).

### 0-3. 방향 결정

**(a) 매니저가 슬롯을 지정한다**

| | 로봇이 서로 위치를 보고 추종 | **매니저가 슬롯 지정 (채택)** |
| --- | --- | --- |
| 필요 토픽 | 상대 `amcl_pose` 브릿지 | 가벼운 String 토픽 |
| 도메인 분리(50/20/22) | 부담 | 영향 없음 |
| 순서 일관성 | 로봇마다 판단 | FIFO 큐가 단일 진실 |
| 로봇 쪽 구현 | 추종 제어 필요 | 좌표로 이동만 하면 됨 |

**(b) 전부 `_v3` 신규 파일 + 토픽 이름공간 분리 (채택)**

| | 기존 파일 제자리 수정 | **v3 신규 파일 (채택)** |
| --- | --- | --- |
| 복구 | git으로 되돌림 (미커밋 변경이 많아 위험) | v3 프로세스를 끄고 v2를 켜면 끝 |
| v2 로봇 영향 | 같은 매니저를 공유해 영향 가능 | 없음 |
| 비용 | 파일 수 적음 | 매니저·클라이언트 코드 중복, 브릿지 항목 추가 |

비용은 감수한다. 매니저 로직(FIFO 큐, 락, 타임아웃)은 v1 매니저를 복사해서 시작한다.

**(c) 로봇이 구역 앞에서 보고하는 방식 (채택). 위치 기반(`zone_manager_node_v2`)이 아니다**

| | **보고 방식 (v3)** | 위치 기반 (`zone_manager_node_v2`) |
| --- | --- | --- |
| 판단 기준 | 로봇이 `request_entry`로 자기 보고 | 매니저가 `amcl_pose`로 위치 판정 |
| 대기 위치 | 줄 서는 슬롯 (앵커 + k·d) | hold 좌표 1개 |
| 위치 정보 | 필요 없음 (도메인 분리에 유리) | `amcl_pose` 브릿지 필요 |
| 한계 | 구역 앞 요청 지점에 도달하기 전 구간은 Nav2 회피에만 의존 | 브릿지 부담, hold 좌표가 겹침 |

허가를 못 받은 로봇은 제자리에 멈추지 않고 **매니저가 지정한 슬롯으로 이동해서 대기**한다. 요청 전 구간(예: `P2`에 도달하기 전)은 로봇끼리 서로 모른다.

**(d) 문 좌표는 로봇 코드에 유지 (A안, 확정)**

| 좌표 | 역할 | v3 |
| --- | --- | --- |
| 대기 위치 (v2에서는 `RED*IN`에 서서 기다림) | 허가 전 대기 자리 | **제거**. 매니저가 `queue_slot`으로 지정 |
| `RED*IN` | 허가 후 구역으로 들어가는 문 | **유지** (`POINTS`) |
| `RED*OUT` | 도착하면 `notify_exit`로 락 반납 | **유지** (`POINTS`) |

매니저가 계산하는 값은 다른 로봇의 상태에 따라 바뀌는 **대기 슬롯뿐**이다. 문 좌표는 고정값이고 `RED1`/`RED2`는 yaw와 경로가 달라 로봇별 값이므로 로봇 코드에 둔다 (B안 "문 좌표도 yaml로 이동", C안 "`RED*IN` 제거"는 채택하지 않음).

### 0-4. 용어

- **앵커(anchor)**: 대기 줄의 맨 앞 지점 (slot 0). 구역 진입문(`RED1IN`) 바로 앞이 아니라, 점유자의 이탈 경로와 겹치지 않는 한 곳.
- **슬롯 k**: `anchor + k · d · back_dir`. k=0이 앵커, k=1은 그 뒤 d만큼, …
- **후방 벡터(back_dir)**: 줄이 늘어나는 방향의 단위 벡터.
- **대기자 순번**: 현재 점유자를 제외한 큐에서의 순서 (0부터).

### 0-5. 동작 시나리오

기존에는 `RED1IN` **도착 후** 허가를 요청했지만, v3는 **앵커로 가기 전에** 요청한다 (순번을 먼저 받아야 서 있을 슬롯을 알 수 있다).

1. 로봇이 구역 밖 마지막 지점에서 `request_entry`(v3 토픽)를 보낸다.
2. 매니저(v3):
   - **구역이 비어 있고 대기자가 없음** → 즉시 `grant` (슬롯 없이 곧바로 `RED1IN`으로 진행, 기다림 없음)
   - **점유 중** → 큐에 등록하고 대기자 순번 k를 `queue_slot`으로 발행
3. 로봇은 `queue_slot`의 좌표로 이동해 정지한다 (상태 `QUEUEING`, 빨강 깜박임).
4. 점유자가 `notify_exit` → 매니저가 큐 선두에 `grant`, 나머지 대기자에게 k-1로 `queue_slot` 재발행.
5. `grant`를 받은 로봇은 `RED1IN`으로 이동해 구역에 진입한다. 나머지는 한 칸씩 전진한다.

```
        앵커(slot0)   slot1    slot2
  구역 ←  [pinky1] ←d→ [pinky2] ←d→ [ ... ]   → back_dir
```

**대기 앵커는 pinky1과 pinky2가 공유한다.** `zone_params_v3.yaml`의 `queue_anchor` 한 값이 기준이고 로봇 코드에는 대기 좌표가 없다. 앵커에 서는 로봇은 로봇 종류가 아니라 **먼저 요청한 순서**로 정해진다. 두 대가 같은 점에 서는 것이 아니라, 줄의 기준점이 같다는 뜻이다. 로봇이 늘어도 로봇 코드는 바꾸지 않고 `robot_ids`와 `queue_max`만 조정한다.

**로봇별 흐름 (pinky1 기준, pinky2는 `RED2IN`/`RED2OUT`)**

```
P2 도착 → request_entry
  ├ 빈 구역 → grant 즉시 → RED1IN → 구역 안 순회 → RED1OUT → notify_exit → 홈
  └ 점유 중 → queue_slot 수신 → 슬롯 이동·정지(QUEUEING), 1초마다 request_entry 재전송
              → (앞이 빠지면 슬롯 갱신, 1.5초 지연 후 전진)
              → grant → 슬롯 goal 취소 → RED1IN → 구역 진입
```

| 단계 | 로봇 | 매니저 |
| --- | --- | --- |
| 구역 밖 마지막 지점 도착 | `request_entry` | 큐 등록 또는 즉시 grant |
| 허가 전 | 슬롯 좌표로 이동·정지, 1초마다 재요청 | 순번 k마다 슬롯 좌표 계산·발행 |
| 허가 후 | 슬롯 goal 취소 → `RED*IN` → 진입 | 점유자 등록 |
| 구역 이탈 | `RED*OUT` 도착 → `notify_exit` | 락 해제 → 선두 grant → 뒤쪽은 지연 후 한 칸 전진 |

### 0-6. 파일 구성 (전부 신규, 기존은 그대로)

| 구분 | 신규 파일 (v3) | 복사 원본 (수정 안 함) |
| --- | --- | --- |
| 매니저 | `zone_traffic_control/zone_traffic_control/zone_manager_node_v3.py` | `zone_manager_node.py` |
| 클라이언트 | `zone_traffic_control/zone_traffic_control/zone_gate_client_v3.py` (독립 파일, v1 클래스를 import하지 않고 복사해 토픽만 v3로) | `zone_gate_client.py` |
| 파라미터 | `zone_traffic_control/config/zone_params_v3.yaml` | `zone_params.yaml` |
| 실행 | `zone_traffic_control/launch/zone_manager_v3.launch.xml` | `zone_manager.launch.xml` |
| 순찰 노드 | `pinky_patrol_node_pinky1_v3.py`, `pinky_patrol_node_pinky2_v3.py` | `pinky_patrol_node_pinky1_v2.py`, `pinky_patrol_node_pinky2_v2.py` |
| 브릿지 | `domain_bridge/launch/bridges_v3.launch.xml` (기존 `bridges.launch.xml`은 미수정) | `bridges.launch.xml` |

- 신규 파일을 실행 가능하게 하는 **`setup.py`의 `entry_points` 등록은 불가피하게 기존 파일에 줄을 추가**한다. 이것만 예외이며 기존 항목은 건드리지 않는다.
- `pinky_patrol_node_pinky2_v3.py`는 `pinky2_v2`의 **현재 작업 트리 내용**(미커밋 변경 포함)을 복사해 시작한다.

### 0-7. 파라미터 (`zone_params_v3.yaml`)

| 이름 | 예시 | 설명 |
| --- | --- | --- |
| `robot_ids` | `["pinky1","pinky2"]` | 기존과 동일 |
| `max_hold_sec` | `120.0` | 기존과 동일 |
| `queue_anchor` | `[x, y, yaw]` | slot 0 위치와 방향. **실측 필요** |
| `queue_back_dir` | `[dx, dy]` | 단위 벡터. 줄이 늘어나는 방향. **실측 필요** |
| `queue_spacing` | `0.35` | 슬롯 간격 d(m). 로봇 폭 약 0.13 m + 정지 오차·Nav2 tolerance 여유 |
| `queue_max` | `3` | 최대 대기자 수 |
| `queue_stale_sec` | `5.0` | 이 시간 동안 `request_entry` 재전송이 없으면 큐에서 제거 |
| `queue_advance_delay_sec` | `1.5` | **(Q1에서 추가)** 앞 슬롯이 비워질 때 뒤 로봇의 전진(k 감소)을 이 시간만큼 늦춘다. 허가받은 선두가 앵커를 떠나기 전에 뒤 로봇이 앵커로 들어와 겹치는 것을 막는다. 0이면 즉시 전진 |

> 현재 `zone_params_v3.yaml`의 `queue_anchor`(`[0.30, -0.45, 0.0]`)와 `queue_back_dir`(`[-1, 0]`)는 **임시값**이다 (7절 실측 후 교체).

### 0-8. 인터페이스 (v3 전용 이름공간 `/zone_manager_v3/*`)

v2 매니저가 `/zone_manager_v2/*`를 쓰는 것과 같은 방식으로, v3는 **모든 토픽 이름을 분리**한다. 그래서 v3 매니저와 기존 매니저를 동시에 켜도 메시지가 섞이지 않는다.

| 토픽 | 방향 | 형식 | 비고 |
| --- | --- | --- | --- |
| `/zone_manager_v3/request_entry` | 로봇 → 매니저 | `robot_id:token` | 기존 형식과 같음 |
| `/zone_manager_v3/grant_entry` | 매니저 → 로봇 | `robot_id:token` | 기존 형식과 같음 |
| `/zone_manager_v3/notify_exit` | 로봇 → 매니저 | `robot_id` | 기존 형식과 같음 |
| `/zone_manager_v3/status` | 매니저 → 모두 | `free` / `occupied_by:robot_id` | 기존 형식과 같음 |
| `/zone_manager_v3/queue_slot` | 매니저 → 로봇 | `robot_id:k:x,y,yaw` | **신규**. 변경 시 즉시 + 1초 주기 재발행 (유실 대비) |
| `/zone_manager_v3/cancel_entry` | 로봇 → 매니저 | `robot_id` | **신규**. 대기 포기 (stop 등). 큐 제거 후 뒤 로봇 전진 |

- 모두 `std_msgs/String`, QoS는 기존과 같은 RELIABLE. 노드 이름은 `zone_manager_node_v3`.
- 브릿지에는 위 6개 토픽을 **`bridges_v3.launch.xml`에 모두** 추가한다 (Step Q4).

### 0-9. 예외와 안전

| 상황 | 처리 |
| --- | --- |
| 대기 중 `stop` | `cancel_entry` 발행 → 큐 제거 → STOPPED |
| 로봇 크래시·통신 끊김 | `request_entry` 재전송이 `queue_stale_sec` 동안 없으면 매니저가 큐에서 제거, 뒤 로봇 전진 |
| 슬롯 이동 Nav2 실패 | 기존 3회 재시도 → 최종 실패 시 `cancel_entry` + FAILED |
| `grant` 후 `RED1IN` 도달 실패 | 락은 기존 `max_hold_sec`로 해제, 큐는 계속 진행 |
| 대기자가 `queue_max` 초과 | 매니저가 `queue_slot`에 `k=-1` (거부)을 보내고 로봇은 FAILED(`queue_full`). 슬롯 공유(겹침) 금지 |
| 슬롯 재배정 중 이동 | 새 `queue_slot`을 받으면 현재 goal을 취소하고 새 좌표로 재이동 |
| 앞 슬롯이 아직 비워지지 않은 채 전진 | 뒤 로봇은 "새 슬롯 좌표 도달"까지만 이동 (Nav2 장애물 회피에 의존). 간격 d가 충분히 커야 함 |
| 대기자 취소·stale 제거 | 뒤 로봇의 전진에도 `queue_advance_delay_sec`를 적용 (취소된 로봇이 아직 그 자리에 있을 수 있음) |
| 허가 도중 슬롯으로 이동 중 | `grant`를 받으면 슬롯 goal을 취소하고 `RED*IN`으로 이동 |
| v3 매니저 장애 | 6절 복구 절차대로 v2 구성으로 전환 |

## 1. 단계별 계획

각 단계는 "내용 → 요청 커맨드" 순서다. 커맨드는 Claude에 그대로 붙여 넣고, 한 단계씩 진행한 뒤 `git diff`로 확인하고 커밋한다. **모든 단계에서 기존 파일 수정이 없는지 `git diff`로 확인한다** (`setup.py` entry point 줄 추가는 예외).

#### Step Q1. 매니저 v3 — **완료**

- **내용**: `zone_manager_node.py`를 복사해 `zone_manager_node_v3.py` 작성. 토픽 이름을 `/zone_manager_v3/*`로 바꾸고, 슬롯 계산 순수 함수 `slot_pose(k)`, `queue_slot` 발행, `cancel_entry` 구독, stale 제거, `queue_max` 거부 추가
- **변경 파일**: `zone_manager_node_v3.py`, `zone_params_v3.yaml`, `zone_manager_v3.launch.xml`, `test/test_zone_manager_v3.py` (모두 신규), `setup.py` entry point
- **구현 구조**: ROS에 의존하지 않는 순수 로직(`slot_pose`, `normalize_dir`, `ZoneQueue` 클래스: 요청·해제·취소·stale·점유 시간 초과)을 분리하고, 노드(`ZoneManagerNodeV3`)는 락과 발행만 담당한다. 시간은 인자로 받아 가짜 시계로 테스트한다.
- **검증 결과**: pytest 15개 통과. 시뮬(`ROS_DOMAIN_ID=77` + localhost 전용, mock 로봇 5대) 9개 PASS — 빈 구역 즉시 grant, 슬롯 k=0/1/2 좌표, `queue_max` 초과 `k=-1`, 취소 후 약 1.7초 뒤 전진, 이탈 시 선두 grant + 지연 후 전진, stale 제거, `free` 복귀. `colcon build`는 하지 않았다 (소스 트리에서 직접 실행).
- **기존 파일 변경**: `setup.py`에 entry point 1줄만 추가

**요청 커맨드**

```text
PRD "my_pinky_package/docs/prd/Pinky 위험구역 대기열 — 단계별 PRD.md" 의 Step Q1을 구현해줘.
- 기존 파일은 절대 수정하지 말고 전부 신규 파일로 만들 것 (setup.py 의 entry_points 줄 추가만 예외)
- zone_traffic_control/zone_traffic_control/zone_manager_node.py 를 복사해 zone_manager_node_v3.py 를 만들고, 노드 이름은 zone_manager_node_v3, 토픽은 전부 /zone_manager_v3/* (request_entry, grant_entry, notify_exit, status) 로 변경
- 추가: slot_pose(k) 순수 함수(앵커 + k·d·후방벡터), /zone_manager_v3/queue_slot 발행(변경 시 즉시 + 1초 재발행), /zone_manager_v3/cancel_entry 구독, queue_stale_sec 초과 시 큐 제거, queue_max 초과 시 k=-1 거부
- zone_params_v3.yaml(신규): robot_ids, max_hold_sec, queue_anchor, queue_back_dir, queue_spacing(0.35), queue_max(3), queue_stale_sec(5.0) (앵커/방향은 임시값 + TODO 주석)
- zone_manager_v3.launch.xml(신규): 기존 zone_manager.launch.xml 형식을 따라 작성
- slot_pose 단위 테스트(pytest)를 작성하고 실행해서 결과 보여줘
- ROS 시뮬 테스트는 ROS_DOMAIN_ID=77 + localhost 전용으로만 해줘
- 끝나면 git status 로 기존 파일이 수정되지 않았는지 확인해서 보여줘
```

#### Step Q2. 게이트 클라이언트 v3

- **내용**: `zone_gate_client.py`를 복사해 `zone_gate_client_v3.py` 작성 (v1 클래스를 import하지 않는 독립 파일). 토픽을 v3로 바꾸고, **순찰 노드가 이동 폴링 루프 안에서 쓸 수 있는 비블로킹 API**를 제공한다.
  - `reset()`: 이전 grant·슬롯 상태 초기화
  - `send_request()`: `request_entry` 발송, 토큰 반환
  - `resend_if_due()`: 마지막 발송 후 `retry_period`(1초)가 지났으면 재전송 (매니저의 `queue_stale_sec` 제거 방지)
  - `granted(token)`: 해당 토큰으로 허가를 받았는가
  - `current_slot`: 최신 `queue_slot` (k, x, y, yaw), `k=-1`은 `queue_full`
  - `cancel_entry()`: 대기 포기 통보
  - 기존 `wait_for_entry()`(블로킹)는 호환을 위해 유지
- **변경 파일**: `zone_gate_client_v3.py` (신규)
- **검증**: mock 매니저로 슬롯 변경·거부·허가 수신 확인
- **설계 이유**: 슬롯 이동 중에도 재전송과 슬롯 수신이 계속되어야 한다. 게이트 클라이언트는 `BasicNavigator` 노드에 붙어 있으므로 콜백은 `isTaskComplete()` 폴링이 spin할 때 처리된다.

**요청 커맨드**

```text
PRD의 Step Q2를 구현해줘.
- zone_traffic_control/zone_traffic_control/zone_gate_client.py 는 수정하지 말고, 복사해서 zone_gate_client_v3.py 를 신규 작성 (v1 클래스를 import하지 말고 독립 파일로)
- 토픽은 전부 /zone_manager_v3/* 로 변경, wait_for_entry / notify_exit 로직은 그대로 유지
- /zone_manager_v3/queue_slot 수신 후 current_slot (k, x, y, yaw) 로 노출하고 k=-1 은 queue_full 로 구분
- 비블로킹 API 추가: reset(), send_request() (토큰 반환), resend_if_due() (retry_period 1초 경과 시 재전송), granted(token), cancel_entry(). 기존 wait_for_entry() 는 유지
- 순수 로직(재전송 타이밍, 슬롯 파싱)은 ROS 없이 테스트할 수 있게 분리
- mock 매니저(zone_manager_node_v3 를 실제로 띄워도 됨)로 슬롯 변경·거부·허가 수신을 확인하는 테스트 작성·실행 (도메인 77 + localhost 전용)
- 끝나면 git status 로 기존 파일이 수정되지 않았는지 확인해서 보여줘
```

#### Step Q3. 순찰 노드 v3 (pinky1, pinky2)

- **내용**: `pinky1_v2`, `pinky2_v2`를 각각 복사해 v3 작성.
  - `_enter_zone`(goto 경로)을 `_queue_and_enter`로 교체한다. 흐름: 요청(슬롯 이동 **전**) → (대기 시) 슬롯 이동 → 슬롯 변경 시 goal 취소 후 재이동 → grant → `RED*IN` → 진입. 상태 `QUEUEING` 추가
  - `_run_patrol`의 `ZONE_ENTRY_INDEX`(start 경로) 허가 대기도 **같은 `_queue_and_enter`를 쓰도록 통합**한다 (v2는 허가 대기 코드가 두 벌).
  - 슬롯 이동 중 폴링 루프 안에서 `resend_if_due()`를 호출한다 (재전송 끊기면 5초 뒤 큐에서 제거됨).
  - `stop`, 이동 최종 실패, `queue_full`일 때 반드시 `cancel_entry()`를 보낸다.
  - 로봇 코드에는 대기 좌표를 두지 않는다. `RED*IN`/`RED*OUT`은 문 좌표로 유지(A안).
- **변경 파일**: `pinky_patrol_node_pinky1_v3.py`, `pinky_patrol_node_pinky2_v3.py` (신규), `setup.py` entry point
- **검증**: 단일 로봇(대기 없이 통과), 대기 중 stop, 슬롯 재배정, 슬롯 이동 중 grant 수신
- **확인 필요**: `pinky2_v2`는 `WAYPOINTS`에서 `RED2IN`이 0번인데 `ZONE_ENTRY_INDEX = 1`이다. `_run_route`(goto)는 이름 기준이라 영향이 없지만 `_run_patrol`(start)은 순번을 쓴다. 4절 6번 결정에 따라 처리한다.

**요청 커맨드**

```text
PRD의 Step Q3를 구현해줘.
- 기존 v2 파일은 수정하지 말고, my_pinky_package/my_pinky_package/pinky_patrol_node_pinky1_v2.py 와 pinky_patrol_node_pinky2_v2.py(작업 트리의 현재 내용 그대로)를 각각 복사해 pinky_patrol_node_pinky1_v3.py, pinky_patrol_node_pinky2_v3.py 생성, setup.py 에 entry point 추가
- ZoneGateClient 대신 zone_gate_client_v3 사용
- _enter_zone 을 _queue_and_enter 로 교체: send_request()(슬롯 이동 전에 먼저) → (대기 시) queue_slot 좌표로 이동 → 슬롯이 바뀌면 현재 goal 취소 후 재이동 → grant → 슬롯 goal 취소 → RED*IN 이동 → 진입
- _run_patrol 의 ZONE_ENTRY_INDEX 허가 대기(start 경로)도 같은 _queue_and_enter 를 쓰도록 통합 (허가 대기 코드를 한 곳으로)
- 슬롯으로 이동하는 폴링 루프 안에서 resend_if_due() 를 1초마다 호출 (재전송이 끊기면 매니저가 5초 뒤 큐에서 제거)
- 로봇 코드에는 대기 좌표를 두지 않는다. 대기 좌표는 queue_slot 으로만 받고, RED*IN / RED*OUT 은 문 좌표로 POINTS 에 유지
- 대기자가 없으면 v2와 똑같이 곧바로 진행 (슬롯 이동·지연 추가 금지)
- 상태 QUEUEING 발행, LED는 빨강 깜박임 유지
- stop / 이동 최종 실패 / queue_full 시 cancel_entry 발행
- pinky2_v2 의 WAYPOINTS 순번과 ZONE_ENTRY_INDEX(RED2IN 이 0번인데 인덱스 1) 불일치를 점검해서 보고하고, 임의로 고치지 말고 내 결정을 기다릴 것
- 기존 주석 스타일과 파일 구성(섹션 번호)을 맞출 것
- 실제 로봇은 움직이지 말고, mock 매니저 + ROS_DOMAIN_ID=77 로 문법·흐름만 확인
- 끝나면 git status 로 기존 파일이 수정되지 않았는지 확인해서 보여줘
```

#### Step Q4. 브릿지·GUI

- **내용**: v3 토픽 6개를 도메인 브릿지에 추가, 관제 GUI에 `QUEUEING` 상태 표시
- **변경 파일**: `bridges_v3.launch.xml` (신규), `pinky_web_gui` (`QUEUEING`은 기존 상태 목록에 항목만 추가하는 최소 수정)
- **검증**: 도메인 간 중계 확인

**요청 커맨드**

```text
PRD의 Step Q4를 구현해줘.
- domain_bridge/launch/bridges.launch.xml 은 수정하지 말고, 같은 방식으로 domain_bridge/launch/bridges_v3.launch.xml 을 신규 작성해 /zone_manager_v3/ 의 6개 토픽(request_entry, grant_entry, notify_exit, status, queue_slot, cancel_entry)을 기존 zone_manager 토픽과 같은 방향·QoS로 중계. CMakeLists.txt 에 설치 항목이 필요하면 줄 추가만 할 것
- pinky_web_gui 에서 QUEUEING 상태를 인식해 표시 (기존 WAITING_ZONE 처리 코드를 먼저 찾아서 같은 방식으로, 최소 수정)
- README 에 v3 구성과 복구 절차(PRD 6절) 요약 추가
- 기존 테스트가 깨지지 않는지 실행해서 결과 보여줘
```

#### Step Q5. 실로봇

- **내용**: 앵커·간격·방향 실측과 튜닝. 앞 로봇이 구역에 있을 때 뒤 로봇이 슬롯에 정지하는지 확인. 복구 절차 리허설
- **변경 파일**: `zone_params_v3.yaml`
- **검증**: 체크리스트 (`docs/checklists/`)

**요청 커맨드**

```text
PRD의 Step Q5를 준비해줘.
- pinky_web_gui/docs/checklists/ 의 기존 체크리스트 형식에 맞춰 대기열 실로봇 체크리스트 작성
- 포함: 앵커/후방 벡터 실측 방법, 간격 d 결정(Nav2 xy_goal_tolerance 확인), 시나리오(1대만 통과, 2대 대기, stop 중 취소, 크래시 시 stale 제거), v2로 복구하는 리허설(PRD 6절)
- 실측값을 zone_params_v3.yaml 에 넣는 절차까지 적어줘
- 로봇을 직접 구동하지는 말고 문서만 작성
```

> 앵커 좌표를 이미 알고 있으면 Q1 요청 끝에 `앵커는 (x, y, yaw), 후방 벡터는 (dx, dy)로 넣어줘`를 덧붙인다. 그러면 TODO 없이 바로 들어간다.

### 검증 규칙 (모든 단계 공통)

- ROS 검증은 `ROS_DOMAIN_ID=77` + localhost 전용으로만 한다. 셸 기본 도메인 50은 실제 관제 도메인이므로 사용하지 않는다.
- 실로봇 시험은 Step Q5에서만, 구역 안 로봇 1대 + 대기 1대로 시작한다.
- 단계마다 `git status`로 기존 파일이 수정되지 않았는지 확인한다.

## 2. 성공 기준

- 2대 동시 진입 시 충돌·교착 0회.
- 뒤 로봇이 앵커 뒤 `d ± 0.1 m` 안에 정지한다.
- 앞 로봇이 `notify_exit`한 뒤 3초 안에 뒤 로봇이 한 칸 전진한다.
- stop·크래시 상황에서 큐가 정상 복구된다 (남은 로봇이 순서대로 진행).
- 대기자가 없을 때는 v2와 같은 시간에 진입한다 (지연 추가 없음).
- v2 구성으로의 복구가 5분 안에 가능하다 (6절).

## 3. 호환성과 혼용 금지

- 기존 v2 파일과 기존 매니저는 변경이 없으므로 **그대로 동작한다**.
- 토픽 이름공간이 다르기 때문에 v3 매니저와 기존 매니저를 **동시에 켜도 메시지는 섞이지 않는다**.
- 하지만 **같은 구역을 쓰는 로봇은 모두 같은 계열(전부 v2 또는 전부 v3)로 맞춘다.** v2 로봇과 v3 로봇이 섞이면 두 매니저가 서로의 점유를 모르므로 상호 배제가 깨져 **두 대가 동시에 구역에 들어갈 수 있다.**

## 4. 열린 질문 (TBD)

1. 앵커·후방 벡터의 실측 좌표 (맵에서 `RED1OUT` 이탈 경로와 겹치지 않는 곳).
2. 간격 d 최종값 (0.35 m는 초기 추정).
3. ~~pinky2 v3 시점~~ → **결정: pinky1_v3와 pinky2_v3를 함께 만든다** (혼용 금지 규칙 때문에 한 구역의 로봇은 같은 계열이어야 함).
4. 관제 GUI에서 큐 상태(누가 몇 번째인지)를 보일지 (현재는 `QUEUEING` 상태만).
5. 기존 `zone_gate_client.py`와 v3의 공통 코드가 중복되는데, 안정화 후 공통 모듈로 합칠지 (이번 범위 아님).
6. `pinky2_v2`의 `WAYPOINTS`(`RED2IN`이 0번)와 `ZONE_ENTRY_INDEX = 1`의 순번 불일치. `pinky2_v3`의 `start` 경로를 순번에 맞게 고칠지, `start`는 건드리지 않고 `goto`만 v3 대기열을 쓰게 할지 (Q3 시작 전 결정).
7. ~~문 좌표(`RED*IN/OUT`) 위치~~ → **결정: A안, 로봇 코드에 유지** (0-3절 (d)).
8. 요청 전 구간(예: `P2` 도달 전)의 로봇 간 충돌은 Nav2 회피에만 의존한다. 이 구간까지 제어하려면 위치 기반 방식이 필요한데 이번 범위 밖이다 (0-3절 (c)).

## 5. 참조

- `my_pinky_package/my_pinky_package/pinky_patrol_node_pinky1_v2.py` — `_enter_zone`, `_leave_zone`, `_run_route`, ZONE 상수
- `my_pinky_package/my_pinky_package/pinky_patrol_node_pinky2_v2.py` — pinky2 복사 원본 (미커밋 변경 포함)
- `zone_traffic_control/zone_traffic_control/zone_manager_node.py` — `_on_request`, `_release_and_advance`, `_check_timeout`
- `zone_traffic_control/zone_traffic_control/zone_gate_client.py` — `wait_for_entry`, `notify_exit`
- `zone_traffic_control/zone_traffic_control/zone_manager_node_v2.py` — 토픽 이름공간 분리(`/zone_manager_v2/*`)의 선례
- `zone_traffic_control/config/zone_params.yaml`, `domain_bridge/launch/bridges.launch.xml`

## 6. 이상 시 복구 절차 (v3 → v2)

기존 파일을 건드리지 않으므로 **되돌릴 코드가 없다.** 실행 중인 프로세스만 바꾼다.

1. 로봇에서 `pinky_patrol_node_pinky*_v3` 프로세스에 `stop` 명령을 보내고 종료한다.
2. 관제 PC에서 `zone_manager_node_v3`(`zone_manager_v3.launch.xml`)를 종료한다.
3. 브릿지를 `bridges_v3.launch.xml` 대신 기존 `bridges.launch.xml` 구성으로 되돌린다 (v3 토픽은 쓰이지 않으니 남겨 두어도 무방하다).
4. 기존 `zone_manager_node`(`zone_manager.launch.xml`)를 켠다.
5. 로봇에서 `pinky_patrol_node_pinky*_v2`를 실행한다.
6. 같은 구역의 로봇이 **전부 v2**인지 확인한다 (혼용 금지).

## 7. 구현 전에 필요한 실측값

- 앵커 좌표 `(x, y, yaw)`, 후방 벡터 `(dx, dy)`
- 앵커에서 `RED1IN`까지 Nav2 경로가 막히지 않는지
- 실제 로봇 정지 오차 (Nav2 `xy_goal_tolerance`) → d 결정에 사용

# Pinky 위험구역 대기열 — 단계별 PRD

Oct 12, 2026 · 초안 v4 (**기존 코드는 수정하지 않고 전부 `_v3` 신규 파일로 만든다** — 이상 시 복구 목적. 예외는 `setup.py` entry point와 브릿지 yaml의 v3 토픽 블록 추가뿐. **Step Q1~Q3 완료**, 문 좌표는 A안 확정, 대기 앵커는 두 로봇이 공유. **GUI 연동은 실로봇 코드 검증(Q4~Q5) 뒤로 미룬다(Q6).** 복구 절차는 6절, 실측이 필요한 값은 7절)

| Step | 상태 |
| --- | --- |
| Q1 매니저 v3 | **완료** (단위 테스트 15개, 시뮬 9개 PASS) |
| Q2 게이트 클라이언트 v3 | **완료** (단위 4+, 통합 2 PASS) |
| Q3 순찰 노드 v3 | **완료** (A안 적용, 모의 통합 테스트 11개 PASS, 실로봇 미검증) |
| Q4 브릿지 + 실로봇 코드 테스트 (GUI 없이) | 브릿지 yaml 추가 **완료**, 앵커 실측·실기 테스트 대기 |
| Q5 튜닝·복구 리허설·체크리스트 | 대기 |
| Q6 GUI 연동 (`QUEUEING` 표시) | 대기 (Q4~Q5 통과 후) |

## 0. 공통 개요

### 0-1. 배경과 문제

`pinky_patrol_node_pinky1_v2.py`는 자기 진입문(`RED1IN`)에 도착한 뒤 `ZoneGateClient.wait_for_entry()`로 허가를 기다린다 (`_enter_zone`). pinky2는 `RED2IN`에서 기다린다. 그래서 여러 대가 몰리면:

- 대기 위치가 로봇마다 달라 흩어진다 (`RED1IN`/`RED2IN`은 서로 가깝다: (0.60,-0.50) / (0.55,-0.60)).
- 같은 문 앞에 두 대가 서면 서로 막거나 부딪힐 수 있다.
- `zone_manager_node.py`는 FIFO 큐로 "순서"는 알지만 "어디에 서는지"는 모른다.

### 0-2. 목표와 비목표

- **목표**: (1) 대기 지점을 **한 곳(앵커)** 으로 통일한다. (2) 앵커에 이미 로봇이 있으면 뒤에 오는 로봇은 **일정 간격 d 뒤**에 줄줄이 선다. (3) 앞 로봇이 진입하면 뒤 로봇이 **한 칸씩 전진**한다. (4) **문제가 생기면 v2 구성으로 즉시 되돌릴 수 있다.**
- **비목표**: 구역 안 주행 변경 없음. **기존 코드 파일(`zone_manager_node.py`, `zone_gate_client.py`, `pinky*_v2.py`, `zone_params.yaml`)은 한 줄도 바꾸지 않는다.** 기존 브릿지 yaml에는 **v3 토픽 블록만 덧붙인다**(0-3절 (e)). 로봇끼리 직접 통신하지 않는다 (도메인 분리).

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
| `RED*IN` → **`ZONE_ENTRY`** | 허가 후 진입 지점 | **변경 (2026-10-13)**: RED*IN 은 주석 처리. 대기열 앵커 `(0.8, -0.7, 0)`(= yaml `queue_anchor`)를 `POINTS['ZONE_ENTRY']` 로 두고, 허가 후 이 앵커를 경유해 구역으로 진행 |
| `RED*OUT` → **`ZONE_EXIT`** | 도착하면 `notify_exit`로 락 반납 | **변경 (2026-10-13)**: RED*OUT 은 주석 처리. 두 로봇 공통 출구 `(0.8, -0.5, 3.14)`(yaw 3.14 = 뒤 방향 180°)에 도착하면 락 반납 |

매니저가 계산하는 값은 다른 로봇의 상태에 따라 바뀌는 **대기 슬롯뿐**이다. 문 좌표는 고정값이고 `RED1`/`RED2`는 yaw와 경로가 달라 로봇별 값이므로 로봇 코드에 둔다 (B안 "문 좌표도 yaml로 이동", C안 "`RED*IN` 제거"는 채택하지 않음).

**(e) 브릿지는 새 launch를 만들지 않고 기존 yaml에 추가 (확정)**

판단 기준은 **새로 중계해야 할 토픽 수**다.

| 항목 | 개수 | 비고 |
| --- | --- | --- |
| 매니저의 v3 토픽 | 6 | `request_entry`, `notify_exit`, `cancel_entry`, `grant_entry`, `queue_slot`, `status` |
| 도메인을 건너야 하는 토픽 | **5** | `status`는 매니저와 GUI가 모두 관제 PC(50)에 있어 중계 불필요 (v1도 `status`는 중계하지 않음) |
| 로봇별 yaml에 추가되는 분량 | 항목 5개 (약 35줄) | v1 블록(3개)과 같은 형식 |

5개는 기존 yaml에 한 블록으로 덧붙여도 읽기 쉬운 분량이고, 새 launch를 만들면 로봇당 브릿지 프로세스가 하나씩 더 필요하다(yaml도 새로 있어야 함). 그래서 **`pinky_bridge_pinky1.yaml`, `pinky_bridge_pinky2.yaml`에 `ZONE-QUEUE-V3 ADDED ~ END` 블록을 추가**했다. 토픽 이름이 `/zone_manager_v3/*`라 기존 v1 토픽과 섞이지 않고, **복구는 이 블록을 지우고 브릿지를 재시작하면 끝**이다. (토픽이 수십 개로 늘거나 v3 브릿지만 따로 켜고 끄고 싶어지면 그때 별도 파일로 분리한다.)

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
5. `grant`를 받은 로봇은 **(2026-10-13 변경)** 슬롯·앵커를 거치지 않고 진행 중인 goal 만 취소한 뒤 곧바로 목적지(P3 등)로 직행한다. 나머지는 한 칸씩 전진한다.

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
| 허가 후 | 슬롯 goal 취소 → 목적지로 직행 (앵커 미경유, 2026-10-13 변경) | 점유자 등록 |
| 구역 이탈 | `ZONE_EXIT` 도착 → `notify_exit` | 락 해제 → 선두 grant → 뒤쪽은 지연 후 한 칸 전진 |

### 0-6. 파일 구성 (코드는 전부 신규, 기존은 그대로)

| 구분 | 신규 파일 (v3) | 복사 원본 (수정 안 함) |
| --- | --- | --- |
| 매니저 | `zone_traffic_control/zone_traffic_control/zone_manager_node_v3.py` | `zone_manager_node.py` |
| 클라이언트 | `zone_traffic_control/zone_traffic_control/zone_gate_client_v3.py` (독립 파일, v1 클래스를 import하지 않고 복사해 토픽만 v3로) | `zone_gate_client.py` |
| 파라미터 | `zone_traffic_control/config/zone_params_v3.yaml` | `zone_params.yaml` |
| 실행 | `zone_traffic_control/launch/zone_manager_v3.launch.xml` | `zone_manager.launch.xml` |
| 순찰 노드 | `pinky_patrol_node_pinky1_v3.py`, `pinky_patrol_node_pinky2_v3.py` | `pinky_patrol_node_pinky1_v2.py`, `pinky_patrol_node_pinky2_v2.py` |
| 브릿지 | 기존 `domain_bridge/config/pinky_bridge_pinky1.yaml`, `pinky_bridge_pinky2.yaml`에 `ZONE-QUEUE-V3` 블록(토픽 5개) **추가** (launch는 변경 없음, 0-3절 (e)) | — |

- 기존 파일에 **덧붙이는 예외는 두 가지**다: (1) 신규 파일을 실행 가능하게 하는 `setup.py`의 `entry_points` 줄, (2) 브릿지 yaml의 `ZONE-QUEUE-V3` 블록. 둘 다 추가만 하고 기존 항목은 건드리지 않는다.
- `pinky_patrol_node_pinky2_v3.py`는 `pinky2_v2`의 **현재 작업 트리 내용**(미커밋 변경 포함)을 복사해 시작한다.

### 0-7. 파라미터 (`zone_params_v3.yaml`)

| 이름 | 예시 | 설명 |
| --- | --- | --- |
| `robot_ids` | `["pinky1","pinky2"]` | 기존과 동일 |
| `max_hold_sec` | `120.0` | 기존과 동일 |
| `queue_anchor` | `[x, y, yaw]` | slot 0 위치와 방향. **실측 필요** |
| `queue_back_dir` | `[dx, dy]` | 단위 벡터. 줄이 늘어나는 방향. **실측 필요** |
| `queue_spacing` | `0.50` | 슬롯 간격 d(m). 0.20 m 에서는 로봇이 슬롯으로 가며 뒤 로봇과 부딪혀 0.50 m 로 증가 (사용자 지정, 2026-10-13). Nav2 `xy_goal_tolerance`(0.25 m)의 2배 |
| `queue_max` | `3` | 최대 대기자 수 |
| `queue_stale_sec` | `5.0` | 이 시간 동안 `request_entry` 재전송이 없으면 큐에서 제거 |
| `queue_advance_delay_sec` | `1.5` | **(Q1에서 추가)** 앞 슬롯이 비워질 때 뒤 로봇의 전진(k 감소)을 이 시간만큼 늦춘다. 허가받은 선두가 앵커를 떠나기 전에 뒤 로봇이 앵커로 들어와 겹치는 것을 막는다. 0이면 즉시 전진 |

> 현재 `zone_params_v3.yaml`: `queue_anchor`는 **`[1.0, -0.7, 0.0]`**(2026-10-13 현장 조정), `queue_spacing`은 **`0.50` m**(0.20 m 에서 충돌해 증가), `queue_back_dir`은 `[-1, 0]`(−x, 홈 쪽). 슬롯은 0 = `(1.00, -0.70)`, 1 = `(0.50, -0.70)`, 2 = `(0.00, -0.70)`이고 슬롯 2는 `pinky2` 홈 `P7`(0.0, -0.6)과 약 0.10 m 로 거의 겹친다(2대 테스트에서는 슬롯 0·1만 사용). 순찰 노드의 `ZONE_ENTRY` 는 이 앵커와 같아야 한다(테스트가 검사).

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
- 브릿지에는 `status`를 뺀 **5개**를 기존 로봇별 yaml에 추가했다 (0-3절 (e)). 방향: `request_entry`·`notify_exit`·`cancel_entry`는 로봇 → PC, `grant_entry`·`queue_slot`은 PC → 로봇. 토픽 이름은 양쪽 도메인에서 같다(remap 없음).

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
- zone_params_v3.yaml(신규): robot_ids, max_hold_sec, queue_anchor, queue_back_dir, queue_spacing(현재 0.50), queue_max(3), queue_stale_sec(5.0) (앵커/방향은 임시값 + TODO 주석)
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

#### Step Q4. 브릿지 + 실로봇 코드 테스트 (GUI 없이)

GUI 연동 전에 **코드만으로** 실물 로봇에서 동작을 확인한다. 상태는 관제 GUI 대신 터미널(`ros2 topic echo`, 매니저·순찰 노드 로그)로 본다.

- **내용**: (1) 브릿지 yaml에 v3 토픽 추가 — **완료**. (2) 앵커·후방 벡터 실측 후 `zone_params_v3.yaml` 반영. (3) 아래 T0~T7 순서로 실기 테스트.
- **변경 파일**: `domain_bridge/config/pinky_bridge_pinky{1,2}.yaml`(완료), `zone_traffic_control/config/zone_params_v3.yaml`(실측값). 코드는 수정하지 않는다. 문제가 나오면 결과를 기록하고 별도 수정 요청으로 처리한다.
- **하지 않는 것**: GUI 수정, `QUEUEING` 표시. GUI는 Q6.

**공통 전제**

- 도메인: 관제 PC 50, pinky1 20, pinky2 22. 같은 구역의 로봇은 **전부 v3**여야 한다 (v2 순찰 노드·v2/v1 매니저는 모두 종료, 3절).
- 기존 yaml에 블록을 추가했으므로 **브릿지를 재시작**해야 새 토픽이 중계된다 (`bridges.launch.xml`을 다시 실행).
- `zone_manager_v3.launch.xml`은 관제 PC에서 실행하고, 로봇에서는 `pinky_patrol_node_pinky{1,2}_v3`를 실행한다 (`colcon build` 후 `source`).
- **앵커 `(0.8, -0.7, 0)`, 간격 0.50 m 로 변경**. 후방 벡터·간격은 임시값이므로 T3(슬롯 0만 사용)부터 시작하고, 슬롯 1 이상(T5)은 후방 벡터를 확인한 뒤 진행한다.
- 로봇 옆에서 비상정지(전원/`cmd_vel` 정지)를 바로 할 수 있어야 한다. 처음에는 **구역 안 로봇 1대 + 대기 1대**로 시작한다.

**테스트 순서**

| 단계 | 이름 | 로봇 | 하는 일 | 합격 기준 |
| --- | --- | --- | --- | --- |
| T0 | 실측·설정 | 정지 | 맵에서 앵커 `(x, y, yaw)`, 후방 벡터 `(dx, dy)` 측정 (앵커가 `RED*OUT` 이탈 경로와 겹치지 않는 곳, 앵커→`RED*IN` 경로가 막히지 않는지). `xy_goal_tolerance` 확인 후 간격 d 결정. `zone_params_v3.yaml`에 반영하고 매니저 재시작 | 매니저 시작 로그에 실측 앵커·`back_dir`·`spacing`이 찍힘. **앵커 `(1.0, -0.7, 0)`(0.6 → 0.8 → 1.0 로 현장 조정), 간격 0.50 m, 출구 `(0.7, -0.5, 3.14)` 반영**. 실기 결과는 "실기 테스트 결과" 참고 |
| T1 | 통신 점검 | **정지** (v3 노드는 `IDLE`) | 가짜 로봇 id로 토픽만 발행해 브릿지 5개 확인. 아래 "T1 명령" 참고 | 로봇 도메인에서 `grant_entry`, `queue_slot` 수신. 매니저 로그에 요청·취소가 보임. 끝나면 `status`가 `free` |
| T2 | 단독 통과 | 1대 | `pinky1`에 `goto:P3` (대기자 없음) | `QUEUEING` 없음, 슬롯 이동 없음. v2와 같은 시간에 진입. 복귀 후 `status = free` |
| T3 | 2대 대기 | 2대 | `pinky1` 구역 안(`goto:P3,P6`, 도착마다 10초 대기)일 때 `pinky2`에 `goto:P3` | `pinky2`가 슬롯 0(앵커)에 정지(`QUEUEING`), 앵커 오차 ≤ 0.1 m. `pinky1` 이탈 후 3초 안에 `pinky2`가 허가받고 `RED*IN`으로 이동·진입 |
| T4 | stop 중 취소 | 2대 | T3 상태에서 대기 중인 `pinky2`에 `stop` | `pinky2` 정지·`STOPPED`, 매니저 로그에 `대기 취소`. `pinky1`이 나온 뒤 `status`가 곧바로 `free` (아무에게도 허가 안 감) |
| T5 | 3번째 슬롯 | 실로봇 2대 + 가짜 대기자 | 가짜 id로 슬롯 0을 먼저 점유(`ros2 topic pub -r 1`)시켜 놓고 실로봇이 슬롯 1에 서는지 확인 | 슬롯 1 = 앵커 − d·후방 벡터, 오차 ≤ 0.1 m. 가짜 대기자를 끊으면 5초 뒤 stale 제거, 실로봇이 한 칸 전진 |
| T6 | 크래시 | 2대 | 대기 중인 `pinky2` 노드를 Ctrl+C로 종료 | 약 5초 뒤 매니저 로그에 `request_entry 재전송 끊김 -> 대기열에서 제거` |
| T7 | `start` 경로 | 1대씩 | `pinky1`, `pinky2`에 `start`. `pinky2`는 인덱스를 정정한 경로(`RED2IN`→`P3`→`P6`→`RED2OUT`→`P7`) 확인 | `RED2IN`에서 허가 절차, `RED2OUT` 도착 때 락 반납, `P7` 복귀 후 `DONE` |

**실기 테스트 결과 (2026-10-13, 실로봇 pinky1·pinky2, GUI 없음)**

사용 설정: 앵커 `(1.0, -0.7, 0)`, `queue_back_dir (-1, 0)`, 간격 0.50 m, 출구(`ZONE_EXIT`) `(0.7, -0.5, 3.14)`. 순찰 노드는 v3(RED*IN/OUT 주석 처리, 허가 후 목적지 직행), 코드는 `ed77e0c` 기준.

| 시험 | 방법 | 결과 |
| --- | --- | --- |
| 가짜 점유자 + 2대 정렬 (T3·T5 해당) | 가짜 id 로 구역을 점유시킨 뒤 실로봇을 `goto` 로 보냄 | **성공**. 1번 로봇이 허가를 받아 진입, 2번 로봇은 1번이 있던 대기 위치(앞 슬롯)로 이동 |
| 동시 진입 | 두 로봇이 거의 동시에 진입 요청 | **성공**. 토큰을 먼저 받은 로봇은 대기열을 생략하고 목적지로 이동(단, 구역을 나올 때 토큰 반납 위치 `ZONE_EXIT` 는 경유). 토큰을 받지 못한 로봇은 바로 대기열로 이동해 대기 |

해석:
- 허가 후 앵커를 거치지 않고 직행하는 동작(0-5절 변경)과 `ZONE_EXIT` 도착 시 `notify_exit` 반납 동작이 실기에서 확인됨.
- 0.20 m 간격에서 로봇이 부딪히던 문제는 0.50 m 간격으로 바꾼 뒤 재발하지 않음 (확인은 위 두 시험 범위).

아직 측정/시험하지 않은 항목:
- T3 슬롯 정지 오차 (`/pinky*/amcl_pose` 대 `queue_slot` 좌표). Nav2 `xy_goal_tolerance` 0.25 m 판단(4절 11번)에 필요
- T2 단독 통과, T4 stop 중 취소, T6 크래시(stale 제거), T7 `start` 경로
- 슬롯 2 (`(0.0, -0.7)`)와 `P7`(0.0, -0.6) 근접, `back_dir (-1, 0)` 확정

**T1 명령 (로봇은 움직이지 않는다 — 실제 로봇 id를 쓰지 않는다)**

```bash
# 로봇 도메인(예: pinky1 = 20)에서
export ROS_DOMAIN_ID=20
ros2 topic echo /zone_manager_v3/grant_entry &
ros2 topic echo /zone_manager_v3/queue_slot &
# 1) 빈 구역 요청 -> grant 수신
ros2 topic pub --once /zone_manager_v3/request_entry std_msgs/msg/String "{data: 'tester1:t1'}"
# 2) 점유 중 요청 -> queue_slot "tester2:0:<앵커 x>,<앵커 y>,<yaw>" 수신
ros2 topic pub -r 1 /zone_manager_v3/request_entry std_msgs/msg/String "{data: 'tester2:t2'}"   # Ctrl+C 로 중단
# 3) 정리
ros2 topic pub --once /zone_manager_v3/cancel_entry std_msgs/msg/String "{data: 'tester2'}"
ros2 topic pub --once /zone_manager_v3/notify_exit std_msgs/msg/String "{data: 'tester1'}"
# 관제 PC(도메인 50)에서 점유 상태 확인
ros2 topic echo /zone_manager_v3/status
```

**관찰 방법**

- 관제 PC(도메인 50): `ros2 topic echo /zone_manager_v3/status`, `ros2 topic echo /pinky1/patrol_status`, `ros2 topic echo /pinky2/patrol_status` (`QUEUEING`·`WAITING_ZONE` 등 상태 확인), 매니저 터미널 로그.
- 슬롯 정지 오차: 정지한 로봇의 `/pinky*/amcl_pose`와 `queue_slot` 좌표를 비교한다.
- 명령은 기존과 같이 `patrol_cmd`로 보낸다 (예: `ros2 topic pub --once /pinky2/patrol_cmd std_msgs/msg/String "{data: 'goto:P3'}"`).

**중단 조건**: 로봇이 슬롯이 아닌 곳으로 가거나 다른 로봇과 충돌 위험이 있으면 즉시 `stop` 후 비상정지하고, 매니저·순찰 노드 로그를 보존한 뒤 6절 복구 절차로 v2로 되돌린다.

**요청 커맨드**

```text
PRD의 Step Q4 중 코드 작업은 이미 끝났어. 실로봇 테스트 준비를 도와줘.
- 브릿지는 기존 domain_bridge/config/pinky_bridge_pinky{1,2}.yaml 의 ZONE-QUEUE-V3 블록으로 이미 추가됨. 코드는 수정하지 말 것
- 내가 입력한 앵커 (x, y, yaw), 후방 벡터 (dx, dy), 간격 d 를 zone_traffic_control/config/zone_params_v3.yaml 에 반영하고 기존 TODO/임시값 주석을 갱신. 값을 모르면 임시값을 그대로 두고 T0 이후 단계는 진행하지 말 것
- 테스트 중 나온 매니저/순찰 노드 로그를 붙여 줄 테니 T1~T7 합격 기준과 비교해서 판정하고, 불합격 항목은 원인 후보만 정리 (코드 수정은 별도 요청이 있을 때만)
- 로봇을 직접 구동하는 명령은 실행하지 말고, 내가 칠 명령만 알려줘 (ROS 검증은 ROS_DOMAIN_ID=77 로컬 전용)
```

#### Step Q5. 튜닝·복구 리허설·체크리스트

- **내용**: Q4 결과를 바탕으로 간격 d, 앵커 위치 보정. v2로의 복구 절차 리허설(6절). Q4의 T0~T7을 체크리스트 문서로 정리.
- **변경 파일**: `zone_params_v3.yaml`(튜닝), 체크리스트 문서(`pinky_web_gui/docs/checklists/`의 기존 형식을 따르되 GUI 항목은 제외).
- **검증**: 성공 기준(2절)을 실기로 충족. 복구 리허설을 5분 안에 완료.

**요청 커맨드**

```text
PRD의 Step Q5를 준비해줘.
- pinky_web_gui/docs/checklists/ 의 기존 체크리스트 형식에 맞춰 대기열 실로봇 체크리스트 작성 (PRD Q4 의 T0~T7 을 항목으로, GUI 항목은 제외)
- 포함: 앵커/후방 벡터 실측 방법, 간격 d 결정(Nav2 xy_goal_tolerance 확인), v2로 복구하는 리허설(PRD 6절)
- 실측값을 zone_params_v3.yaml 에 넣는 절차까지 적어줘
- 로봇을 직접 구동하지는 말고 문서만 작성
```

#### Step Q6. GUI 연동 (Q4~Q5 통과 후)

- **내용**: 관제 GUI에 `QUEUEING` 상태 표시 (기존 `WAITING_ZONE` 처리 방식을 따라 **항목만 추가하는 최소 수정**). README에 v3 구성과 복구 절차 요약.
- **변경 파일**: `pinky_web_gui`(상태 목록), README.
- **검증**: 기존 GUI 테스트가 깨지지 않을 것. `status` 토픽은 관제 PC 안에서만 쓰이므로 브릿지 변경은 없다.

**요청 커맨드**

```text
PRD의 Step Q6을 구현해줘.
- pinky_web_gui 에서 QUEUEING 상태를 인식해 표시 (기존 WAITING_ZONE 처리 코드를 먼저 찾아서 같은 방식으로, 최소 수정)
- README 에 v3 구성과 복구 절차(PRD 6절) 요약 추가
- 기존 테스트가 깨지지 않는지 실행해서 결과 보여줘
```

> 앵커 좌표를 이미 알고 있으면 Q4 요청 커맨드에 `앵커는 (x, y, yaw), 후방 벡터는 (dx, dy), 간격은 d`를 덧붙인다. 그러면 TODO 없이 바로 들어간다.

### 검증 규칙 (모든 단계 공통)

- ROS 검증은 `ROS_DOMAIN_ID=77` + localhost 전용으로만 한다. 셸 기본 도메인 50은 실제 관제 도메인이므로 사용하지 않는다.
- 실로봇 시험은 Step Q4부터, **앵커를 실측한 뒤에만**, 구역 안 로봇 1대 + 대기 1대로 시작한다. GUI 없이 터미널·로그로 먼저 확인한다.
- 단계마다 `git status`로 기존 파일이 수정되지 않았는지 확인한다.

## 2. 성공 기준

- 2대 동시 진입 시 충돌·교착 0회.
- 뒤 로봇이 앵커 뒤 `d ± 0.1 m` 안에 정지한다.
- 앞 로봇이 `notify_exit`한 뒤 3초 안에 뒤 로봇이 한 칸 전진한다.
- stop·크래시 상황에서 큐가 정상 복구된다 (남은 로봇이 순서대로 진행).
- 대기자가 없을 때는 v2와 같은 시간에 진입한다 (지연 추가 없음).
- v2 구성으로의 복구가 5분 안에 가능하다 (6절).

## 3. 호환성과 혼용 금지

- 기존 v2 코드와 기존 매니저는 변경이 없으므로 **그대로 동작한다**. 브릿지 yaml에는 v3 토픽 블록만 추가했고 기존 토픽 항목은 그대로다.
- 토픽 이름공간이 다르기 때문에 v3 매니저와 기존 매니저를 **동시에 켜도 메시지는 섞이지 않는다**.
- 하지만 **같은 구역을 쓰는 로봇은 모두 같은 계열(전부 v2 또는 전부 v3)로 맞춘다.** v2 로봇과 v3 로봇이 섞이면 두 매니저가 서로의 점유를 모르므로 상호 배제가 깨져 **두 대가 동시에 구역에 들어갈 수 있다.**

## 4. 열린 질문 (TBD)

1. ~~앵커 실측 좌표~~ → **`(0.8, -0.7, 0)`로 변경** (2026-10-13). 후방 벡터는 미확정 (맵에서 `RED1OUT` 이탈 경로와 겹치지 않는 방향).
2. ~~간격 d~~ → **0.50 m로 변경** (0.20 m 에서 로봇이 부딪힘, 2026-10-13). 실기(T3·T5)에서 정지 오차를 보고 조정한다. 11번 참고.
3. ~~pinky2 v3 시점~~ → **결정: pinky1_v3와 pinky2_v3를 함께 만든다** (혼용 금지 규칙 때문에 한 구역의 로봇은 같은 계열이어야 함).
4. 관제 GUI에서 큐 상태(누가 몇 번째인지)를 보일지 (현재는 `QUEUEING` 상태만, Q6에서 다룸).
5. 기존 `zone_gate_client.py`와 v3의 공통 코드가 중복되는데, 안정화 후 공통 모듈로 합칠지 (이번 범위 아님).
6. (결정 완료: A안) `pinky2_v2`의 `WAYPOINTS`(`RED2IN`이 0번)와 `ZONE_ENTRY_INDEX = 1`(`ZONE_EXIT_INDEX = 4`도 `RED2OUT`이 3번이라 어긋남)의 순번 불일치를 `pinky2_v3`에서 `0`/`3`으로 정정했고, `start`와 `goto`가 같은 `_queue_and_enter`를 쓴다. v2 `start`와 순서가 달라지므로 Q5에서 `start` 경로를 실기로 한 번 더 확인한다.
7. ~~문 좌표(`RED*IN/OUT`) 위치~~ → **결정(2026-10-13 갱신)**: 실기에서 RED*IN/OUT 문 이동이 대기열과 섞여 혼란을 주어 **주석 처리**하고, 진입은 앵커 `ZONE_ENTRY (0.8,-0.7,0)`, 락 반납은 출구 `ZONE_EXIT (0.8,-0.5,3.14)` 로 교체. 이 문서의 이전 절에 남은 `RED*IN/OUT` 서술은 변경 전 설계 이력이다. 앵커를 yaml 에서 바꾸면 순찰 노드 `ZONE_ENTRY` 도 같이 바꿔야 하며 `test_zone_entry_matches_yaml_anchor` 가 검사한다.
8. 요청 전 구간(예: `P2` 도달 전)의 로봇 간 충돌은 Nav2 회피에만 의존한다. 이 구간까지 제어하려면 위치 기반 방식이 필요한데 이번 범위 밖이다 (0-3절 (c)).
9. (결정 완료) 브릿지는 새 launch 대신 기존 yaml에 추가 (판단 기준: 새로 중계할 토픽 5개, 0-3절 (e)).
10. (이력) 간격을 0.20 m로 바꾸면서 슬롯 2가 `(0.20, -0.70)`이 되어 `P7`(0.0, -0.6)과 약 0.22 m로 멀어졌다. 슬롯 1 `(0.40, -0.70)`은 `RED2IN`(0.55, -0.6)에서 약 0.18 m다.
11. **Nav2 `xy_goal_tolerance`가 0.25 m**(`pinky_pro/pinky_navigation/params/nav2_params.yaml`)라 "목표 반경 25 cm 안이면 도착"으로 처리된다. 간격 0.20 m보다 크므로 로봇이 슬롯에서 최대 25 cm 벗어나 멈출 수 있고, 인접 슬롯의 로봇과 가까워지거나 겹칠 수 있다. 성공 기준(`d ± 0.1 m`)도 이 설정으로는 보장되지 않는다. T3에서 실제 정지 오차를 측정한 뒤, (a) tolerance를 줄이거나 (b) 간격을 키우거나 (c) 슬롯 도착 판정을 순찰 노드에서 따로 엄격히 할지 정한다. 이번에는 Nav2 설정과 코드를 바꾸지 않았다.

## 5. 참조

- `my_pinky_package/my_pinky_package/pinky_patrol_node_pinky1_v2.py` — `_enter_zone`, `_leave_zone`, `_run_route`, ZONE 상수
- `my_pinky_package/my_pinky_package/pinky_patrol_node_pinky2_v2.py` — pinky2 복사 원본 (미커밋 변경 포함)
- `zone_traffic_control/zone_traffic_control/zone_manager_node.py` — `_on_request`, `_release_and_advance`, `_check_timeout`
- `zone_traffic_control/zone_traffic_control/zone_gate_client.py` — `wait_for_entry`, `notify_exit`
- `zone_traffic_control/zone_traffic_control/zone_manager_node_v2.py` — 토픽 이름공간 분리(`/zone_manager_v2/*`)의 선례
- `zone_traffic_control/config/zone_params.yaml`, `domain_bridge/config/pinky_bridge_pinky{1,2}.yaml`(v1 `ZONE-MUTEX ADDED` 블록, v3 `ZONE-QUEUE-V3 ADDED` 블록), `domain_bridge/launch/bridges.launch.xml`

## 6. 이상 시 복구 절차 (v3 → v2)

기존 파일을 건드리지 않으므로 **되돌릴 코드가 없다.** 실행 중인 프로세스만 바꾼다.

1. 로봇에서 `pinky_patrol_node_pinky*_v3` 프로세스에 `stop` 명령을 보내고 종료한다.
2. 관제 PC에서 `zone_manager_node_v3`(`zone_manager_v3.launch.xml`)를 종료한다.
3. 브릿지 yaml의 `ZONE-QUEUE-V3 ADDED ~ END` 블록을 지우고 브릿지를 재시작한다 (v3 토픽은 쓰이지 않으니 블록을 남겨 두어도 무방하다. v1 `ZONE-MUTEX` 블록은 그대로 둔다).
4. 기존 `zone_manager_node`(`zone_manager.launch.xml`)를 켠다.
5. 로봇에서 `pinky_patrol_node_pinky*_v2`를 실행한다.
6. 같은 구역의 로봇이 **전부 v2**인지 확인한다 (혼용 금지).

## 7. 구현 전에 필요한 실측값

- 앵커 좌표 `(x, y, yaw)`, 후방 벡터 `(dx, dy)`
- 앵커에서 `RED1IN`까지 Nav2 경로가 막히지 않는지 (**Q4 T0에서 실측. 이 값 없이는 실로봇을 움직이지 않는다**)
- 실제 로봇 정지 오차 (Nav2 `xy_goal_tolerance`) → d 결정에 사용

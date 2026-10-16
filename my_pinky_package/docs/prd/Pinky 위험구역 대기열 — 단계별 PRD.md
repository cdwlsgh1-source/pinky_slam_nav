# Pinky 위험구역 대기열 — 단계별 PRD

Oct 12, 2026 · 초안 v4 (**기존 코드는 수정하지 않고 전부 `_v3` 신규 파일로 만든다** — 이상 시 복구 목적. 예외는 `setup.py` entry point와 브릿지 yaml의 v3 토픽 블록 추가뿐. **Step Q1~Q3 완료**, 문 좌표는 A안 확정, 대기 앵커는 두 로봇이 공유. **GUI 연동은 실로봇 코드 검증(Q4~Q5) 뒤로 미룬다(Q6).** 복구 절차는 6절, 실측이 필요한 값은 7절)

| Step | 상태 |
| --- | --- |
| Q1 매니저 v3 | **완료** (단위 테스트 15개, 시뮬 9개 PASS) |
| Q2 게이트 클라이언트 v3 | **완료** (단위 4+, 통합 2 PASS) |
| Q3 순찰 노드 v3 | **완료** (A안 적용, 모의 통합 테스트 11개 PASS, 실로봇 미검증) |
| Q4 브릿지 + 실로봇 코드 테스트 (GUI 없이) | 브릿지 yaml 추가 **완료**, 앵커 실측·실기 테스트 대기 |
| Q5 튜닝·복구 리허설·체크리스트 | **완료 (2026-10-16, 사용자 판단)** — 미시험 5개 실기 확인, 튜닝 값 확정. 복구 리허설 시간·체크리스트 문서는 미수행(아래 "Q5에서 하지 않은 것") |
| Q6 GUI 연동 (4개 세부 단계) | 대기 (Q4~Q5 통과 후) |
| ├ Q6-1 상태 인식 (`QUEUEING`·`RETREATING`) | 대기 |
| ├ Q6-2 문 제거 반영 (앵커·슬롯·출구 표시) | 대기 |
| ├ Q6-3 명령·프로세스 전환 (v3 토픽, `emergency_stop`) | 대기 |
| └ Q6-4 실기 확인·README | 대기 |

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
| `RED*OUT` → **`ZONE_EXIT`** | 도착하면 `notify_exit`로 락 반납 | **변경 (2026-10-13)**: RED*OUT 은 주석 처리. 두 로봇 공통 출구 `(0.8, -0.5, 3.14)`(yaw 3.14 = 뒤 방향 180°)에 도착하면 락 반납. **2026-10-14**: 출구를 `(0.5, -0.5, 3.14)` → 같은 날 `(0.5, -0.4, 3.14)` 로 변경 (정상 이탈·stop 공통, 입구 줄(y=-0.7)과 거리를 벌려 충돌을 줄임) |

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
| `queue_spacing` | `0.30` | 슬롯 간격 d(m). 0.20 m 에서는 로봇이 부딪혀 0.50 m 로 시험한 뒤 0.30 m 로 확정 (사용자 의도값, 2026-10-14). Nav2 `xy_goal_tolerance`(0.25 m)보다 약간 큼. T3 실측 정지 오차 ≈0.10 m, 로봇 간 거리 0.285 m 로 확인 (2026-10-14) |
| `queue_max` | `3` | 최대 대기자 수 |
| `queue_stale_sec` | `5.0` | 이 시간 동안 `request_entry` 재전송이 없으면 큐에서 제거 |
| `queue_advance_delay_sec` | `3.0` | **(Q1에서 추가, 기본 1.5 → 2026-10-14 사용자 변경 3.0)** 앞 슬롯이 비워질 때 뒤 로봇의 전진(k 감소)을 이 시간만큼 늦춘다. 허가받은 선두가 앵커를 떠나기 전에 뒤 로봇이 앵커로 들어와 겹치는 것을 막는다. 0이면 즉시 전진 |

> 현재 `zone_params_v3.yaml`: `queue_anchor`는 **`[1.0, -0.7, 0.0]`**(2026-10-13 현장 조정), `queue_spacing`은 **`0.30` m**(0.20 m 충돌 → 0.50 m 시험 → 0.30 m 확정), `queue_back_dir`은 `[-1, 0]`(−x, 홈 쪽). 슬롯은 0 = `(1.00, -0.70)`, 1 = `(0.70, -0.70)`, 2 = `(0.40, -0.70)`이고 슬롯 2는 `pinky2` 홈 `P7`(0.0, -0.6)과 약 0.41 m 로 겹치지 않는다. 순찰 노드의 `ZONE_ENTRY` 는 이 앵커와 같아야 한다(테스트가 검사).

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
| 구역 안(토큰 보유) `stop` (**2026-10-14 변경**) | 멈춘 뒤 `ZONE_STOP_EXIT` 로 후퇴(`RETREATING`) → 도착하면 `notify_exit` 로 토큰 반납 → STOPPED. 퇴장 위치(`ZONE_STOP_EXIT`)는 두 로봇 모두 `ZONE_EXIT` 와 같은 `(0.5,-0.4,3.14)`(처음에는 로봇별 +x 0.30 m 오프셋으로 줄을 세우려 했으나 2026-10-14 사용자 지정으로 같은 점으로 통일. 동시 stop 시 겹칠 수 있음, 미시험). 후퇴 실패 시 락 유지 → `max_hold_sec` 뒤 해제 |
| `emergency_stop` (**신규, 2026-10-14**) | 이전 `stop` 동작. 그 자리 정지 + 락 유지, 후퇴 중이어도 즉시 멈춤. 락은 매니저 `max_hold_sec`(yaml, 기본 120초, 사용자 조정) 뒤 자동 해제 |
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
- zone_params_v3.yaml(신규): robot_ids, max_hold_sec, queue_anchor, queue_back_dir, queue_spacing(현재 0.30), queue_max(3), queue_stale_sec(5.0) (앵커/방향은 임시값 + TODO 주석)
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
| T0 | 실측·설정 | 정지 | 맵에서 앵커 `(x, y, yaw)`, 후방 벡터 `(dx, dy)` 측정 (앵커가 `RED*OUT` 이탈 경로와 겹치지 않는 곳, 앵커→`RED*IN` 경로가 막히지 않는지). `xy_goal_tolerance` 확인 후 간격 d 결정. `zone_params_v3.yaml`에 반영하고 매니저 재시작 | 매니저 시작 로그에 실측 앵커·`back_dir`·`spacing`이 찍힘. **앵커 `(1.0, -0.7, 0)`(0.6 → 0.8 → 1.0 로 현장 조정), 간격 0.30 m(현재, 실기 시험은 0.50 m), 출구 `(0.7, -0.5, 3.14)` 반영**. 실기 결과는 "실기 테스트 결과" 참고 |
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

**T3 슬롯 정지 오차 실측 (2026-10-14, 간격 0.30 m, 앵커 `(1.0, -0.7, 0)`, fake 점유 + pinky1·pinky2)**

`/pinky*/amcl_pose` 를 구독만 해서 `queue_slot` 목표 좌표와 비교 (스크립트는 발행 없음).

| 로봇 | 슬롯 | 목표 (x, y) | 실제 (x, y) | 오차 |
| --- | --- | --- | --- | --- |
| pinky1 | 0 | (1.00, -0.70) | (0.92, -0.65) | 0.096 m |
| pinky2 | 1 | (0.70, -0.70) | (0.63, -0.63) | 0.097 m |

- 두 로봇 사이 실제 거리 **0.285 m** (목표 0.30 m, 차이 0.015 m).
- **합격**: 오차 ≈0.10 m ≤ Nav2 `xy_goal_tolerance` 0.25 m, 간격도 유지됨. 열린 질문 11번 종결.
- 두 로봇 모두 목표보다 x 는 약 −0.07~−0.08, y 는 약 +0.05~+0.07 쪽으로 같은 방향으로 어긋남 → 개별 정지 오차보다 맵/amcl 위치 또는 좌표 실측의 편향일 가능성. 현재 수준에서는 앵커 보정 불필요.
- 슬롯 1(x=0.63)이 슬롯 0(x=0.92)보다 x 가 작아 `back_dir (-1, 0)` 과 데이터상 일치.
- 한계: 로봇이 실제로 붙거나 부딪히는지는 숫자만으로 판단 못 함 (육안 확인 별도).

**T4 stop 중 취소 실기 결과 (2026-10-14)**

| 시험 | 결과 |
| --- | --- |
| fake 점유 중 대기하는 pinky2 에 `stop` | **성공**. 매니저 로그 `[pinky2] 대기 취소 -> 대기열: []` (대기열에서 빠짐) |
| 대기 중 `stop` 에 순찰 노드가 죽음 | **버그 발견 → 수정**. `Executor is already spinning` (`_cmd_cb` 에서 `navigator.cancelTask()` 를 호출하면 작업 스레드와 executor 충돌). 콜백은 플래그만 세우고 취소는 작업 스레드가 하도록 수정, LED 가 초록으로 남은 것도 노드 비정상 종료 때문으로 추정(미확인) |
| 토큰 보유 중 `stop` | **문제 확인**. 구역 안이면 락을 일부러 유지하던 설계라 토큰이 반납되지 않음(120초 뒤 해제) → 아래 변경으로 처리 |

변경 (2026-10-14, 사용자 결정): 토큰 보유 중 `stop` → `ZONE_STOP_EXIT` 로 나와 락 반납(대기열 밖 줄은 순찰 노드 고정 오프셋, 매니저 변경 없음). 이전 `stop` 동작은 새 명령 `emergency_stop` 으로 유지. 클라이언트 `pinky_patrol_cmd/pinky_control_client_v3.py` 에 `emergency_stop` 추가. 코드·가짜 navigator 테스트 57개 통과.

**T4 재시험 실기 결과 (2026-10-14, 위 변경 반영 후)**

| 시험 | 결과 | 판정 |
| --- | --- | --- |
| `emergency_stop` (토큰 보유) | 현재 위치에서 120초 대기 후 토큰 반납 (`max_hold_sec` 기본값) | **성공** |
| `stop` (토큰 보유) | `ZONE_STOP_EXIT` 로 이동, 도착 직후 토큰 반납 | **성공** |

- T4(대기 중 stop / 토큰 보유 중 stop / emergency_stop) 세 경우 모두 실기 확인 완료.
- 확인하지 않은 것: yaml `max_hold_sec` 를 바꿨을 때 `emergency_stop` 해제 시간이 따라가는지. (pinky2 의 `ZONE_STOP_EXIT (0.8,-0.5)` 오프셋은 이후 `(0.5,-0.4)` 로 통일되어 해당 없음.)

**T6 크래시(stale 제거) 실기 결과 (2026-10-14)**

| 시험 | 결과 | 판정 |
| --- | --- | --- |
| 슬롯 0 대기 중인 pinky1 노드를 Ctrl+C 로 종료 (뒤에 pinky2 대기) | 종료 약 5초 뒤 매니저 로그 `['pinky1'] request_entry 재전송 끊김 (> 5.0s) -> 대기열에서 제거`, pinky2 는 큐에 남아 슬롯 0 으로 전진 | **성공** |

**추후 과제 (미해결, 2026-10-14 사용자 결정으로 보류)**: 통신이 죽은 로봇이 슬롯 위에 그대로 서 있으면 매니저가 5초 뒤 큐에서 지우므로, 뒤 로봇이 그 슬롯으로 계속 들어가려 한다(v3 는 신청 메시지만 보고 실제 위치는 보지 않음, 열린 질문 8번과 같은 한계). 검토한 방향: (a) 슬롯 이동이 반복 실패하면 FAILED 로 끝내지 않고 큐에 남아 일정 시간 뒤 재시도(순찰 노드만 수정, 구현·가짜 navigator 테스트까지 해 봤으나 롤백), (b) 매니저가 `amcl_pose` 를 구독해 제거된 로봇의 마지막 위치가 슬롯 근처면 그 슬롯을 예약 유지(정확하지만 매니저·브릿지 변경이 큼, 통신 두절 시 포즈도 끊김). 현재 운영 규칙: 통신이 죽은 로봇은 사람이 치운다. Q5 에서 다시 다룬다.

**T2 단독 통과 실기 결과 (2026-10-14)**

pinky1, pinky2 를 각각 대기자 없이 단독으로 `goto:P3` (매니저 로그 기준)

| 확인 | 근거 | 판정 |
| --- | --- | --- |
| 대기 없이 즉시 허가 | `대기열 등록` 로그 없이 `진입 허가` 만 찍힘 (두 로봇 모두) | **성공** |
| 슬롯 경유 없음 | 대기열에 등록되지 않음 → 슬롯 미배정 | **성공** |
| `ZONE_EXIT` 도착 시 반납 | 두 로봇 모두 `zone 이탈 통보 -> 락 해제` | **성공** |
| 구역 통과 시간 | pinky1 약 71초, pinky2 약 71초 (P3 10초 대기 포함) | 두 로봇 동일 |

- 위 판정은 매니저 로그 기준이다. 허가 후 앵커에서 멈추지 않고 P3 로 직행했는지, `patrol_status` 의 `QUEUEING` 없음, 마지막 `DONE` 은 로그에 없어 **육안/상태 확인 필요**(대기열에 등록되지 않았으므로 `QUEUEING` 은 없었을 것으로 추정).
- "v2와 같은 시간에 진입" 은 v2 시간을 재지 않아 비교하지 못함.

**T7 `start` 경로 실기 결과 (2026-10-14)**

pinky2 가 `start` 로 구역을 도는 중에 pinky1 도 `start` 를 보내 대기열 시험을 겸함 (출구는 변경 전 `(0.5,-0.5)` 기준 로그). 매니저·순찰 노드 로그 기준.

| 확인 | pinky1 | pinky2 | 판정 |
| --- | --- | --- | --- |
| 경로 순서 | P2 → 앵커 `(1.0,-0.7)` → P3 → P6 → 출구 → P1 | P3 → P6 → 출구 → P7 (`ZONE_ENTRY → P3 → P6 → ZONE_EXIT → P7`) | **성공** (pinky2 인덱스 정정 경로 그대로) |
| 락 반납 | 출구 도착 직후 `zone 이탈 통보 전송` | 출구 도착 직후 `zone 이탈 통보 전송` | **성공** |
| 완주 | 마지막 6번째 목표(P1) 도착 | 마지막 5번째 목표(P7) 도착 | **성공** (`DONE` 상태 토픽은 로그에 없음, 도착 로그로 판단) |
| 대기열 | pinky2 점유 중 앵커에서 약 77초 대기 → pinky2 반납 직후(1.6 ms) 허가 → 앵커를 거치지 않고 P3 직행 | - | **성공** |

- pinky1 이 앵커로 가는 도중 한 번 취소 후 같은 좌표로 재출발함(슬롯 0 = 앵커 좌표라 생기는 정상 동작).
- pinky2 로그의 `이미 순찰 중입니다. 명령 무시` 는 복귀 중 `start` 를 한 번 더 보내서 생긴 정상 동작.

**Q4 실기 항목 T1~T7 완료.**

**Q5 미시험 5개 항목 실기 확인 (2026-10-16, 사용자 보고: 5개 모두 문제 없음, 로그는 첨부되지 않음)**

| 항목 | 결과 |
| --- | --- |
| 변경한 출구 `(0.5,-0.4,3.14)` 실기 확인 (T7 은 `(0.5,-0.5)` 기준이었음) | 문제 없음 |
| 두 로봇 동시 `stop` 시 `ZONE_STOP_EXIT` 겹침 | 문제 없음 |
| `max_hold_sec` 변경 시 `emergency_stop` 해제 시간 | 문제 없음 |
| `back_dir (-1, 0)` 육안 확정 | 문제 없음 |
| T2 단독 통과의 앵커 직행·`QUEUEING` 없음·`DONE` 육안 확인 | 문제 없음 |

참고: 위 5개는 사용자가 구두로 보고한 결과이며 상세 수치·로그는 이 문서에 없다.

**Q5에서 하지 않은 것 (남음)**
- v3→v2 복구 리허설(6절, 5분 이내 기준)의 시간 측정 결과는 보고되지 않았다.
- T0~T7 체크리스트 문서(`pinky_web_gui/docs/checklists/` 형식)는 작성하지 않았다.
- 죽은 로봇이 슬롯을 막는 문제는 계속 보류(추후 과제, 운영 규칙: 사람이 치운다).

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

- **배경(v3 이력)**: GUI(`pinky_web_gui`)는 v2 기준으로 만들어졌고, 그 뒤 v3 가 파일명·컨셉을 바꿨다. 한 덩어리로 하면 범위가 안 보여서 4개 세부 단계로 나눈다. 각 단계는 커밋 1개 단위이며 순서(Q6-1 → 2 → 3 → 4)대로 한다.
- **변경 사항 (GUI 가 맞춰야 할 것)**
  - 파일: `zone_manager_node_v3`, `zone_gate_client_v3`, `pinky_patrol_node_pinky{1,2}_v3`, `zone_params_v3.yaml`, `zone_manager_v3.launch.xml`, `pinky_control_client_v3.py`. v1/v2 는 복구용으로 그대로 둔다.
  - 토픽: `/zone_manager_v3/*`. GUI 는 아직 `robots.yaml` 의 `zone_status_topic: /zone_manager/status` 를 본다(값 형식 `free` | `occupied_by:<id>` 는 같다).
  - 문 폐지: `RED*IN/OUT` 대신 `ZONE_ENTRY`(앵커 (1.0,-0.7)) / `ZONE_EXIT`((0.5,-0.4)). `ZONE_*` 는 직접 `goto` 금지. 허가를 받으면 슬롯·앵커를 거치지 않고 목적지로 직행한다.
  - 상태 이름: 새로 `QUEUEING`(대기 슬롯에서 정지), `RETREATING`(`stop` 후 `ZONE_STOP_EXIT` 로 후퇴). `STOPPED` 의 `detail='retreated'`. v3 에는 문 앞 대기가 없어 `WAITING_ZONE` 은 사실상 쓰이지 않는다.
  - 명령: `stop` = 구역 밖으로 후퇴 후 락 반납, `emergency_stop`(신설) = 제자리 정지·락 유지·`max_hold_sec` 뒤 자동 해제.
  - GUI 쪽 현황: 상태 목록(`backend/motion.py:19`, `frontend/patrolview.js:5`, `frontend/zoneview.js:5`, `docs/interfaces.md:77`)에 `QUEUEING`·`RETREATING` 이 없고, `patrolview.js`·`zoneview.js`·`interfaces.md` 가 문(`RED*`) 기준이며, `emergency_stop` 은 없다. `processes.yaml` 은 v1 매니저와 v2 순찰 노드를 띄운다. `pinky_web_gui/CLAUDE.md`·`docs/reference/decisions.md` 에 "연동 전에는 processes.yaml 을 v3 로 바꾸지 않는다"는 규칙이 있다.
- **공통 제약**: GUI 쪽 파일만 수정(비GUI `_v2`·`_v3` 파일은 건드리지 않음). `status` 토픽은 관제 PC 안에서만 쓰이므로 브릿지 변경은 없다. ROS 검증은 `ROS_DOMAIN_ID=77` + localhost 전용. 로봇을 움직이는 명령은 내가 실행하지 않음. 커밋은 요청할 때만, `git add` 는 경로를 명시(세션 export `.txt` 제외).

| 세부 단계 | 내용 | 변경 파일 | 검증 |
| --- | --- | --- | --- |
| **Q6-1 상태 인식** | `QUEUEING`·`RETREATING` 을 상태 목록·LED·문구·작업중 판정에 추가(기존 `WAITING_ZONE` 방식 그대로, 최소 수정). mock 에 QUEUEING 흐름 추가 | `backend/motion.py`, `backend/mock.py`, `frontend/patrolview.js`·`zoneview.js`·`app.js`, `docs/interfaces.md` | 기존 pytest + mock 흐름 통과, 신규 상태 단위 테스트 |
| **Q6-2 문 제거 반영** | `RED*`·`doors` 기준 표시를 앵커/출구 기준으로 정리. 구역 도면에 대기 슬롯(앵커+후방 벡터+간격)·`ZONE_ENTRY`/`ZONE_EXIT` 표시, 대기 표시를 `QUEUEING` 슬롯 번호로. 값은 `zone_params_v3.yaml` 에서 읽어 중복 하드코딩 금지 | `config/zone.yaml`, `frontend/zoneview.js`, `backend/`(설정 로더), `docs/interfaces.md` | 도면 표시 단위 테스트, `tests/verify_*_ui.py` |
| **Q6-3 명령·프로세스 전환** | `zone_status_topic` 을 `/zone_manager_v3/status` 로, `processes.yaml` 의 zone_manager·순찰 노드를 v3 로 교체(v2 항목은 주석으로 보존). 명령 패널에 `emergency_stop` 추가, `stop` 의미 변경(후퇴) 안내 | `config/robots.yaml`, `config/processes.yaml`, `backend/`(명령 검증), `frontend/app.js` | pytest, 런치 확인은 도메인 77 에서만. 실로봇은 사용자가 시험 |
| **Q6-4 실기 확인·README** | GUI 로 `start`·대기열·`stop`·`emergency_stop` 한 번씩(사용자 시험, 로그·스크린샷으로 판정). README 에 v3 구성과 복구 절차(6절) 요약, `CLAUDE.md`·`decisions.md` 의 "v3 로 바꾸지 말 것" 문구 해제 | README, `pinky_web_gui/CLAUDE.md`, `docs/reference/decisions.md`, 이 PRD | 사용자 실기 로그, 문서 점검 |

**요청 커맨드 (공통 머리말 + 단계별 본문)**

공통 머리말(매 단계 맨 앞에 붙인다):

```text
[v3 이력] 대기열 v3 는 기존 v2 를 건드리지 않고 _v3 신규 파일로 만들었다(복구용 보존).
- 문(RED*IN/OUT) 폐지 → ZONE_ENTRY(앵커 1.0,-0.7) / ZONE_EXIT(0.5,-0.4), ZONE_* 는 직접 goto 금지
- 토픽 /zone_manager_v3/*, 설정 zone_params_v3.yaml, 순찰 노드 pinky_patrol_node_pinky{1,2}_v3
- 새 상태 QUEUEING, RETREATING / stop = 후퇴 후 락 반납, emergency_stop = 제자리 정지(락 유지)
- 상세는 my_pinky_package/docs/prd/Pinky 위험구역 대기열 — 단계별 PRD.md 의 Step Q6 절과 6절(복구)
- pinky_web_gui 안의 파일만 수정하고, 비GUI _v2/_v3 파일은 수정하지 않는다. ROS 검증은 ROS_DOMAIN_ID=77 + localhost 전용
- 로봇을 움직이는 명령은 실행하지 말고, 내가 칠 명령만 알려줘. 커밋은 내가 요청할 때만
```

```text
[Q6-1] PRD 의 Step Q6-1 을 구현해줘.
- pinky_web_gui 에서 WAITING_ZONE 을 처리하는 코드를 먼저 전부 찾아서(backend/motion.py, frontend/patrolview.js, zoneview.js, app.js, docs/interfaces.md) 같은 방식으로 QUEUEING·RETREATING 을 추가 (최소 수정)
- LED 색, 상태 문구, 작업중(WORKING) 판정, "응답 없음" 판정에 빠지는 곳이 없는지 확인
- backend/mock.py 에 QUEUEING 흐름(점유 중이면 슬롯에서 대기 → 허가 → 진행)을 추가
- 기존 테스트가 깨지지 않는지 실행하고, 신규 상태 테스트를 추가해서 결과 보여줘
```

```text
[Q6-2] PRD 의 Step Q6-2 를 구현해줘. (Q6-1 완료 후)
- RED*·doors 기준으로 그리는 표시(zoneview.js, config/zone.yaml, interfaces.md)를 찾아서 앵커/출구 기준으로 정리
- 구역 도면에 대기 슬롯(앵커 + 후방 벡터 + 간격)과 ZONE_ENTRY / ZONE_EXIT 를 표시하고, QUEUEING 로봇은 슬롯 번호를 보여줘
- 앵커·후방 벡터·간격은 zone_params_v3.yaml 에서 읽고 GUI 에 값을 복사해 하드코딩하지 말 것 (바꾸면 한 곳만 고치면 되게)
- 도면 표시 단위 테스트와 tests/verify_*_ui.py 결과를 보여줘
```

```text
[Q6-3] PRD 의 Step Q6-3 을 구현해줘. (Q6-2 완료 후)
- robots.yaml 의 zone_status_topic 을 /zone_manager_v3/status 로, processes.yaml 의 zone_manager 와 순찰 노드를 v3(zone_manager_v3.launch.xml, pinky_patrol_node_pinky*_v3)로 교체
- v2 항목은 지우지 말고 주석으로 남기고, 되돌리는 방법(PRD 6절)을 파일 상단 주석에 적어줘
- 명령 패널에 emergency_stop 을 추가하고 stop 은 "구역 밖으로 후퇴 후 락 반납"임을 안내 문구로 표시. 백엔드 명령 검증 목록도 맞춰줘
- pytest 결과와, 도메인 77 에서 런치가 뜨는지만 확인한 결과를 보여줘 (실로봇 연동은 내가 시험)
```

```text
[Q6-4] PRD 의 Step Q6-4 를 준비해줘. (Q6-3 완료 후, 내가 GUI 로 시험한 뒤 로그를 붙이면 판정)
- GUI 실기 시험 순서 정리: start(대기열), 대기 중 stop, 토큰 보유 중 stop, emergency_stop, 두 로봇 동시 stop
- README 에 v3 구성과 복구 절차(PRD 6절) 요약 추가
- 시험이 통과하면 pinky_web_gui/CLAUDE.md 와 docs/reference/decisions.md 의 "processes.yaml 을 v3 로 바꾸지 말 것" 문구를 현재 상태로 고치고, 이 PRD 표를 갱신
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
2. ~~간격 d~~ → **0.30 m로 확정** (0.20 m 충돌 → 0.50 m 시험 → 0.30 m, 2026-10-14). 실기(T3·T5)에서 정지 오차를 보고 조정한다. 11번 참고.
3. ~~pinky2 v3 시점~~ → **결정: pinky1_v3와 pinky2_v3를 함께 만든다** (혼용 금지 규칙 때문에 한 구역의 로봇은 같은 계열이어야 함).
4. 관제 GUI에서 큐 상태(누가 몇 번째인지)를 보일지 (현재는 `QUEUEING` 상태만, Q6에서 다룸).
5. 기존 `zone_gate_client.py`와 v3의 공통 코드가 중복되는데, 안정화 후 공통 모듈로 합칠지 (이번 범위 아님).
6. (결정 완료: A안) `pinky2_v2`의 `WAYPOINTS`(`RED2IN`이 0번)와 `ZONE_ENTRY_INDEX = 1`(`ZONE_EXIT_INDEX = 4`도 `RED2OUT`이 3번이라 어긋남)의 순번 불일치를 `pinky2_v3`에서 `0`/`3`으로 정정했고, `start`와 `goto`가 같은 `_queue_and_enter`를 쓴다. v2 `start`와 순서가 달라지므로 Q5에서 `start` 경로를 실기로 한 번 더 확인한다.
7. ~~문 좌표(`RED*IN/OUT`) 위치~~ → **결정(2026-10-13 갱신)**: 실기에서 RED*IN/OUT 문 이동이 대기열과 섞여 혼란을 주어 **주석 처리**하고, 진입은 앵커 `ZONE_ENTRY (1.0,-0.7,0)`(0.8 → 1.0 현장 조정), 락 반납은 출구 `ZONE_EXIT (0.5,-0.4,3.14)`(0.8 → 0.7 → 0.5 → y -0.4 조정, 2026-10-14) 로 교체. 이 문서의 이전 절에 남은 `RED*IN/OUT` 서술은 변경 전 설계 이력이다. 앵커를 yaml 에서 바꾸면 순찰 노드 `ZONE_ENTRY` 도 같이 바꿔야 하며 `test_zone_entry_matches_yaml_anchor` 가 검사한다.
8. 요청 전 구간(예: `P2` 도달 전)의 로봇 간 충돌은 Nav2 회피에만 의존한다. 이 구간까지 제어하려면 위치 기반 방식이 필요한데 이번 범위 밖이다 (0-3절 (c)).
9. (결정 완료) 브릿지는 새 launch 대신 기존 yaml에 추가 (판단 기준: 새로 중계할 토픽 5개, 0-3절 (e)).
10. (이력) 간격을 0.20 m로 바꾸면서 슬롯 2가 `(0.20, -0.70)`이 되어 `P7`(0.0, -0.6)과 약 0.22 m로 멀어졌다. 슬롯 1 `(0.40, -0.70)`은 `RED2IN`(0.55, -0.6)에서 약 0.18 m다.
11. (종결: T3 실측 정지 오차 ≈0.10 m, 간격 0.30 m 에서 로봇 간 거리 0.285 m, 2026-10-14. 아래는 측정 전 우려 기록) **Nav2 `xy_goal_tolerance`가 0.25 m**(`pinky_pro/pinky_navigation/params/nav2_params.yaml`)라 "목표 반경 25 cm 안이면 도착"으로 처리된다. 간격 0.20 m보다 크므로 로봇이 슬롯에서 최대 25 cm 벗어나 멈출 수 있고, 인접 슬롯의 로봇과 가까워지거나 겹칠 수 있다. 성공 기준(`d ± 0.1 m`)도 이 설정으로는 보장되지 않는다. T3에서 실제 정지 오차를 측정한 뒤, (a) tolerance를 줄이거나 (b) 간격을 키우거나 (c) 슬롯 도착 판정을 순찰 노드에서 따로 엄격히 할지 정한다. 이번에는 Nav2 설정과 코드를 바꾸지 않았다.

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

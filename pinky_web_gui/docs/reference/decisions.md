# 결정 기록

> 역할: CLAUDE.md 에서 옮겨 온 참고 문서. 작업에 필요할 때만 읽는다. 내용은 옮기기 전과 같고, 규칙은 `CLAUDE.md` 에 남아 있다.
> 옮긴 날: 2026-10-11

## 4. zone manager 버전 (결정 기록)

현재 구성은 **접미사 없는 쪽(PRD의 "v1")** 이다.
- 순찰 노드 4개(`pinky_patrol_node_pinky1.py:12`, `pinky2.py:12`, `pinky1_v2.py:47`, `pinky2_v2.py:47`)가 모두 `zone_traffic_control.zone_gate_client`를 import한다. 생성자 시그니처도 `ZoneGateClient(node, robot_id)`로 순찰 노드(`pinky1_v2.py:136`)와 맞다.
- 이 클라이언트가 쓰는 토픽 `/zone_manager/request_entry`, `notify_exit`, `grant_entry` (`zone_gate_client.py:47-52`)는 두 브리지 YAML에 모두 있다 (pinky1 `:104-119`, pinky2 `:80-95`).
- 매니저는 `/zone_manager/status`를 발행한다 (`zone_manager_node.py:64`). 값은 `free` 또는 `occupied_by:<id>` (`:156`). 이 토픽은 관제 PC 안에서만 쓰이므로 브리지에 없다.
- `_v2` zone 파일(`zone_manager_node_v2.py`, `zone_gate_client_v2.py`, `zone_manager_v2.launch.xml`, `zone_params_v2.yaml`)은 **레포에 남아 있지만 사용하지 않는다** (사용자 결정: v2는 시도 후 폐기, 삭제는 하지 않음). `zone_traffic_control/setup.py:28`에 `zone_manager_node_v2` entry point도 남아 있다. 토픽은 `/zone_manager_v2/*`이며 브리지 YAML에 없다.
- 이름 혼동 주의: `zone_manager_node.py:3`의 docstring은 `(v2 - self-report 방식)`, `zone_gate_client.py:3`은 `(v2)`라고 적혀 있다. 이 문서에서 "접미사 없음 = self-report", "`_v2` 파일 = token/hold 방식"으로 구분한다. `pinky_patrol_node_*_v2.py`의 `_v2`는 goto 지원 버전이라는 뜻이며 zone v2와 무관하다.
- 접미사 없는 매니저를 쓰기로 **확정**됐다 (사용자 결정). **확인 필요**: 실제로 실행 중인 매니저 (`ros2 node list`).

## 전체 시작 순서 (웹 GUI 시스템 패널)

웹 GUI 시스템 패널의 **전체 시작** 순서는 위와 다르다: 도메인 브릿지 → bringup → map → zone_manager → 순찰 노드(정지는 반대). 브릿지를 로봇 토픽보다 먼저 켜면 센서 QoS(best_effort)가 안 맞아 데이터가 안 넘어오는 것을 격리 도메인에서 재현해서 정한 순서이고, 각 단계가 켜진 것을 확인한 뒤 다음으로 간다 (`pinky_web_gui/config/processes.yaml` 의 목록 순서).

## 위험구역 대기열 v3 와 GUI (2026-10-14)

- 대기열 v3 는 `zone_manager_node_v3`, `zone_gate_client_v3`, 순찰 노드 `pinky_patrol_node_pinky{1,2}_v3` 로, 기존 v2 를 건드리지 않고 `_v3` 신규 파일로만 만들었다 (이상 시 v2 로 복구하려는 목적). 토픽은 `/zone_manager_v3/*`, 설정은 `zone_params_v3.yaml`, 실행은 `zone_manager_v3.launch.xml`.
- v3 는 구역 문(RED*IN/OUT)을 없애고 앵커 진입(`ZONE_ENTRY`)·출구 반납(`ZONE_EXIT`)으로 바꿨다. 허가를 받은 로봇은 슬롯·앵커를 거치지 않고 목적지로 직행한다.
- 웹 GUI(관제주행 PRD Step 3·4)는 v2 기준이다. `detail=RED*` 표시, 구역 영역의 문 표시, `interfaces.md` 의 허용 목록 설명이 v3 와 다르다. 연동은 대기열 PRD 의 Step Q6 에서 하며, 그 전에는 GUI 의 `processes.yaml` 을 v3 로 바꾸지 않는다.
- 브리지 yaml 은 예외적으로 두 번 수정됐다: Step 1 의 배터리 토픽(`d923481`), 대기열 Q3 의 v3 토픽 5개(`899da88`).

# Step 3 실물 로봇 체크리스트 (사용자 수행)

mock 으로는 `tests/verify_step3_mock.py`, `tests/verify_step3_ui.py`, `pytest` 가 통과했다. 아래는 **실제 로봇에서만** 확인되는 항목이다. 이 문서의 어떤 항목도 아직 실행되지 않았다.
통과/실패와 이상한 화면 캡처, 백엔드 로그(`logging` 의 `backend`, `backend.ros`)를 알려 주면 된다.

## 사전 조건
- [ ] 로봇: Nav2 bringup, `pinky_patrol_node_pinky1_v2` (pinky2 도 쓰면 `..._pinky2_v2`) 실행. 주변을 비운다.
- [ ] 관제 PC: 브리지(`launch/bridges.launch.xml`)를 **재시작**한다 (배터리 토픽이 추가된 YAML). `zone_manager_node` 실행.
- [ ] 관제 PC: `export ROS_DOMAIN_ID=50` 후 `.venv/bin/python -m backend.main` (**`--mock` 없이**). 브라우저 `http://localhost:8000/`.
- [ ] `config/robots.yaml` 의 `robot_settings` 가 노드 상수와 같다 (`goto_allowed` = 노드 `GOTO_ALLOWED`, `home` = `HOME_NAME`, `goto_wait_sec` = `GOTO_WAIT_SEC`, `start_route` = `WAYPOINTS`).
- [ ] **pinky2 주의**: `pinky_patrol_node_pinky2_v2.py` 의 `ZONE_ENTRY_INDEX/ZONE_EXIT_INDEX` 가 1/4 로 남아 있으면 start 의 구역 진입·이탈 시점이 틀린다 (`CLAUDE.md` 7절). 구역 항목은 pinky1 단독으로 먼저 확인하고, pinky2 는 0/3 으로 고친 뒤 확인한다.

## A. 안전한 항목부터 (로봇이 움직이지 않음)

| # | 확인 | 방법 | 기대 결과 | 결과 |
|---|---|---|---|---|
| 1 | 구독자 연결 | `ros2 topic info /pinky1/patrol_cmd` (도메인 50) | Subscription count ≥ 1 (domain_bridge), Publisher 에 `pinky_web_gui_backend` | |
| 2 | RED 거절 | `curl -s -X POST localhost:8000/api/robots/pinky1/command -H 'Content-Type: application/json' -d '{"cmd":"goto","points":["RED1IN"]}'` | HTTP 400, 로봇에는 아무 토픽도 가지 않음 (`ros2 topic echo /pinky1/patrol_cmd` 로 확인). 이력에 "거절됨" | |
| 3 | 알 수 없는 지점 / 개수 초과 | `points:["P99"]`, 11개 | 400 | |
| 4 | 브리지 꺼짐 | 브리지를 끄고 화면에서 `정지` | 503 (구독자 없음), 이력 "전송 실패", 알람 바에 사유. 브리지를 다시 켠다 | |
| 5 | offline | 로봇 한 대의 전원을 끈다 | 그 카드의 버튼 3개만 비활성, 다른 로봇은 그대로 | |

## B. pinky1 단독 (로봇이 움직임)

| # | 확인 | 방법 | 기대 결과 | 결과 |
|---|---|---|---|---|
| 6 | 전달된 문자열 | 터미널 A: `ros2 topic echo /pinky1/patrol_cmd` (도메인 50), 터미널 B: 도메인 20 에서 `ros2 topic echo /patrol_cmd`. 화면에서 P2, P3, P6 를 클릭하고 `경로 이동` → 팝업 확인 | 양쪽 모두 `goto:P2,P3,P6`. 팝업 요약이 "pinky1: P2 → P3 → P6 → 홈(P1), 지점마다 10초 대기" | |
| 7 | goto 진행 | 위 이어서 지켜본다 | 카드 진행 문구 "지점 1/3 · P2 이동 중" → 도착 대기 → 구역 진입 시 "구역 문 이동 중" → "지점 2/3 · P3" … → "홈(P1) 복귀 중" → "완료 (홈 복귀)". 배지 색: 이동 초록, 도착 대기 노랑 | |
| 8 | 첫 작업 STARTING | 노드를 막 켠 직후 첫 작업 (`~/.pinky_last_pose.json` 이 있을 때) | STARTING 이 5초를 넘으면 "시작 준비 중 (첫 작업은 초기 위치 보정으로 …)" 안내. 로봇이 제자리에서 회전 | |
| 9 | start 동등성 | 화면 `순찰 시작` (팝업 확인) 과, 같은 상태에서 `python3 pinky_patrol_cmd/pinky_control_client_v2.py pinky1 start` | 두 방식이 같은 동작 (경로, 상태 순서) | |
| 10 | 작업 중 start | 순찰 중에 `curl ... -d '{"cmd":"start"}'` (화면 버튼은 비활성이라 API 로) | 200 이지만 5초 뒤 카드와 이력에 "응답 없음". 순찰은 영향 없음 (노드가 무시) | |
| 11 | stop | 이동 중에 `정지` | 상태 STOPPED (알람 바에 표시). 구역 안에서 멈췄다면 구역 칩이 `max_hold_sec`(기본 120초) 동안 점유로 남을 수 있음 | |
| 12 | 보정 중 stop | 노드 재시작 직후 첫 작업의 회전 중에 `정지` | 회전이 끝날 때까지 반영 안 됨 → 이력에 "응답 없음" 이 먼저 나올 수 있음 (노드 동작, 알려진 함정) | |
| 13 | 중복 지점 | `P2, P2` 경로 | 노드 동작은 코드로 확정되지 않았다 (`docs/interfaces.md` 7절). 어떻게 움직이는지 알려 주세요. GUI 는 막지 않는다 | |
| 14 | 이력/새로고침 | 명령 몇 개 뒤 브라우저 새로고침 | 이력과 카드 진행 문구가 유지 (서버가 기억). 백엔드를 껐다 켜면 사라짐 | |

## C. 두 로봇 (구역 경합, 주변 확인 필수)

| # | 확인 | 방법 | 기대 결과 | 결과 |
|---|---|---|---|---|
| 15 | 동시 start 의 토픽 분리 | 두 카드에서 거의 동시에 `순찰 시작` | `/pinky1/patrol_cmd`, `/pinky2/patrol_cmd` 에 각각 하나씩. 서로의 상태를 바꾸지 않음 | |
| 16 | 구역 경합 | 두 로봇에 P3 가 든 경로를 동시에 | 한 대는 "진입 허가 대기 중" (WAITING_ZONE, 빨강 배지), 구역 칩 `pinky? 점유`. 앞선 로봇이 나가면 이어서 진행 | |
| 17 | pinky2 홈 | pinky2 `goto` | 완료 후 P7 로 복귀 (카드 "완료 후 복귀: P7") | |

끝나면 모든 로봇에 `정지` 를 보내고 홈 위치를 확인한다.

## 알려진 한계 (기대 결과가 아님)
- `stop` 은 노드가 작업 중이 아닐 때나 보정 회전 중에는 반영하지 않아 "응답 없음" 으로 보일 수 있다.
- `last_task`(진행 문구 해석 기준)는 서버 메모리다. 서버를 재시작한 뒤 CLI 로 시작한 작업은 원문 상태 (`MOVING #2 P3` 같은) 로 보인다.
- 로봇 쪽 `GOTO_ALLOWED` 등을 바꾸면 `config/robots.yaml` 도 수동으로 맞춰야 한다.

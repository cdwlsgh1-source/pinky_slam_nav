# 알람바 Step A1 실물 로봇 체크리스트 (사용자 수행)

> 역할: 알람바 Step A1(서버 알람, 이력 통합, 알람 이력 파일)을 **실제 로봇에서** 확인하는 순서. mock 으로는 `tests/test_alarms.py`, `tests/test_alarmlog.py`, `tests/verify_alarm_ui.py` 가 통과했다. 이 문서의 어떤 항목도 아직 실제 로봇에서 실행되지 않았다.
> 규칙 전체는 `../interfaces.md` 6-7절, 계획은 `../prd/Pinky 알람바 개선 — 단계별 PRD.md` Step A1.

> 로봇이 움직일 수 있는 항목은 주변을 비우고 전원 스위치에 손이 닿는 곳에서 한다. GUI 비상정지는 소프트웨어 정지이며 하드웨어 비상정지를 대체하지 않는다.

통과/실패와 이상한 화면 캡처, 백엔드 로그(`backend`, `backend.alarmlog`)를 알려 주면 된다.

## 사전 조건
- [ ] Step 3, 4 체크리스트의 명령 경로가 통과했다.
- [ ] 관제 PC: `export ROS_DOMAIN_ID=50` 후 `.venv/bin/python -m backend.main` (**`--mock` 없이**). 시작 로그에 `알람 이력 저장: .../data/alarms` 가 나온다.
- [ ] 브라우저를 **두 개**(또는 탭 두 개) 열어 `http://localhost:8000/` 를 본다.

## A. 알람 발생과 해소 (두 화면에서 같은 알람이 보이는지)

| # | 항목 | 방법 | 기대 | 결과 |
|---|---|---|---|---|
| 1 | 로봇 오프라인 | 로봇 한 대의 전원을 끈다 (또는 브리지를 끈다) | 약 5초 뒤 `... is offline` (H, 빨강, Active). **두 화면 모두** | |
| 2 | 오프라인 해소 | 로봇을 다시 켠다 | 같은 행이 `Cleared HH:MM:SS` 로 바뀌고 `... is online`(info) 행이 생긴다 | |
| 3 | 브리지 꺼짐 + 명령 | 브리지를 끄고 `순찰 정지` | 503, `Command ... could not be sent` (H). 다시 누르면 새 행이 아니라 `×2` (ACK 전까지만 묶는다, Step A2) | |
| 4 | 비상정지 실패 | 브리지를 끈 채 `비상 정지` | `E-STOP failed on ...` (HH, 굵은 빨강). 사유 두 가지 | |
| 5 | 비상정지 성공과 해소 | 브리지를 켜고 `비상 정지` | 4번 행이 Cleared, `Command estop sent` (info) 행 | |
| 6 | 무응답 | 순찰 중인 로봇에 `순찰 시작` 한 번 더 | 5초 뒤 `No response from ...` (L). 로봇이 응답하거나 새 명령이 나가면 Cleared | |
| 7 | 거절 | 허용 안 된 지점으로 goto (API 로 직접) | `Command ... rejected` (L). 이유가 한국어 원문 그대로 붙는다 | |
| 8 | 순찰 FAILED/STOPPED | 실제로 순찰 FAILED 가 나는 상황, 또는 `순찰 정지` | `Patrol failed ...`(H) / `Patrol stopped ...`(LL). 다른 상태로 바뀌면 Cleared | |
| 9 | 시작 직후 미수신 | 꺼 둔 로봇이 있는 채 서버 시작 | **오프라인 알람이 생기지 않는다** (한 번도 신호가 없던 로봇은 알람 없음, 의도된 동작) | |

## B. 화면 동작
- [ ] 알람이 많으면 목록 안에서 스크롤되고, 아래 손잡이를 끌면 높이가 바뀐다. 새로고침해도 높이가 유지된다. 더블클릭하면 기본 높이.
- [ ] `Hide info` 로 info 행(전송 기록, online)을 숨길 수 있고 새로고침 뒤에도 유지된다.
- [ ] 새로고침(F5)해도 서버 알람이 그대로 보인다. 서버를 껐다 켜면 화면 알람은 비지만(메모리), 이력 파일은 남는다.
- [ ] 서버를 잠깐 껐다 켜면 `Server disconnected` → `Server reconnected` 가 남고, **새로고침해도 사라지지 않는다**.
- [ ] 하단 명령 이력 패널이 없다. 지도와 사이드바가 전체 높이를 쓴다.

## C. 알람 이력 파일 (`data/alarms/`)
한 줄의 필드와 타입은 `../reference/alarm-history-format.md`.

- [ ] A 의 사건들이 `data/alarms/alarms_<오늘 날짜>_<HHMMSS>.jsonl` 에 한 줄씩 쌓인다 (`tail -f` 로 확인). 줄마다 `saved_at`, `event`(raised/acked/cleared, 로봇에서 받은 알람은 repeated 도), `code`, `robot`, `text` 가 있다.
- [ ] 1000줄이 차면 그 시각의 이름(`_<HHMMSS>`)으로 새 파일이 생긴다 (파일당 1000줄, 실물에서 채우기 어려우면 `--alarm-history-chunk 20` 으로 서버를 띄워 확인). 서버를 다시 켜도 안 찬 파일에 이어서 쓴다.
- [ ] 자정을 넘기면 새 날짜의 파일이 생긴다 (자정을 기다리기 어려우면 생략하고 알려 준다).
- [ ] `curl localhost:8000/api/alarms/history/files` 로 파일 목록이 나온다.
- [ ] 폴더 권한을 막아 쓰기를 실패시켜도 알람바는 계속 동작하고 로그에 경고가 한 번 나온다 (선택).

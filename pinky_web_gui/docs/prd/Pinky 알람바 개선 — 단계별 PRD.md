# Pinky 알람바 개선 — 단계별 PRD
2026-10-12 · 초안 v6 (Step A7: 왼쪽 메뉴 Alarm 화면 추가, 6절 확정 결정 반영) · **Step A1~A5, A7 구현 완료(사용자 확인 대기)** (실물 확인: `../checklists/alarm_a1_real_robot_checklist.md`, `alarm_a2_...`, `alarm_a3_...`, `alarm_a4_...`, `alarm_a5_...`, `alarm_a7_real_robot_checklist.md`) (알람 레벨 5단계, 사운드 파일 업로드·시험 재생, GUI 전체 영문(EN) 표시, 알람 문구는 한영 혼용 허용. 결정이 필요한 항목은 6절)

## 0. 공통 개요
### 0-1 배경과 문제
- 상단 알람과 하단 명령 이력이 같은 사건(예: 비상정지 실패)을 따로 보여 준다. 화면 공간만 쓰고 정보가 겹친다.
- 알람은 브라우저 메모리에만 있어 접속한 화면마다 다르고, 해소 여부·확인 여부를 알 수 없고, 쌓이기만 한다.
- 소리가 없어 관제자가 화면을 보고 있지 않으면 놓친다.
- 어떤 알람이 존재하는지 목록이 없고 끌 수도 없다.

### 0-2 목표
| # | 목표 |
|---|---|
| G1 | 명령 이력 패널(2번)을 알람바(1번)에 통합하고 2번을 삭제 |
| G2 | 알람바에 ACK, SIL 버튼 추가 |
| G3 | 알람 소리와 소리 설정 |
| G4 | 알람 전체 목록(카탈로그) 화면. 이후 mngr 가 내용 수정·저장·Enable/Disable |
| G5 | 알람을 서버가 만들어 GUI 와 연동 (모든 접속 화면이 같은 알람을 봄) |
| G7 | 왼쪽 메뉴에 `Alarm` 버튼을 두고, 알람바의 100건 제한과 무관하게 저장된 **전체 알람 목록**(이력 파일)을 조회·검색·내려받는 화면을 만든다 (Step A7). 카탈로그(A4)는 같은 화면의 탭으로 둔다 |
| G6 | (철회) GUI 전체 영문화는 하지 않는다. 화면 문구는 기존 화면과 같은 한국어를 기본으로 하고, 새로 만드는 알람바·소리·카탈로그·Alarm 화면도 한국어로 쓴다 (사용자 결정 2026-10-12, 문구 목록은 8절) |

### 0-3 비목표
- 이메일·푸시 등 외부 알림, 로봇 쪽 코드 변경(기존 ROS 코드 수정 금지).
- 비상정지 버튼·연결/구역 칩의 동작 변경(안전 요소 유지).
- 계정 체계 변경(`mngr`/`oper` 고정).

### 0-4 용어
- **알람바**: 화면 상단 영역(옛 1번)이며 통합 후 이벤트 목록의 유일한 자리.
- **알람 상태**: `활성`(조건 지속) → `해소`(조건 사라짐) → `ACK`(확인되어 알람바에서 숨김).
- **ACK**: 해소된 알람을 알람바에 더 이상 표시하지 않는 기능. 활성 알람은 숨기지 않고 "확인됨" 표시만 한다.
- **SIL**(Silence): 지금 울리는 알람 소리만 끄는 기능. 알람 상태는 바꾸지 않는다. 새 알람이 발생하면 다시 울린다.
- **알람 레벨**: `HH`(High High) / `H`(High) / `L`(Low) / `LL`(Low Low) / `info` 5단계. 심각도 순서와 기존 `bad/warn/ok` 와의 대응은 결정 9.
- **알람 카탈로그**: 시스템에서 발생할 수 있는 모든 알람의 정의 목록(ID, 조건, 메시지, 레벨, 소리, 사용 여부).

### 0-5 현재 코드에서 재사용할 것
- `backend/commands.py:CommandTracker` (이력 데이터·결과 상수·`tick()` 무응답 판정), `main.py:do_estop`, `hub.py:Hub.broadcast`, `main.py:full_snapshot`
- `frontend/app.js`: `addAlarm`, `renderHistory`, `upsertHistory`, `PatrolView.resultLabel/failReason`
- `backend/config.py` 의 `xxx_yaml` + `_load_xxx` 패턴, `backend/auth.py` 의 `require(request, MANAGER)`
- 브라우저 설정: localStorage 키 `pinky.xxx.vN` + try/catch (`theme.js`, `app.js`)

### 0-6 아키텍처 결정
```
 이벤트 원천(서버)                   AlarmManager (신규 backend/alarms.py)           브라우저
 CommandTracker(명령 결과) ─┐        카탈로그 조회 → enabled? → 알람 생성/해소      WS: snapshot.alarms / alarm
 StateStore(online, patrol) ─┤  ───▶  상태(활성/해소/ACK) 보관(메모리+선택 파일)  ───▶ 알람바 렌더, 소리 재생(Web Audio)
 zone/procs/브리지 상태     ─┘        ACK/카탈로그 REST                              SIL·소리 설정은 브라우저 로컬
```
- 알람 생성을 브라우저(`addAlarm`)에서 서버로 옮긴다. 단 "서버 연결 끊김"은 서버가 알 수 없으므로 브라우저 전용 알람으로 남긴다.
- 정상 명령(start/stop/goto 전송 성공)은 `info` 레벨 행으로 알람바에 남기되 소리·ACK 대상이 아니다(결정 1).

### 0-7 공통 원칙
1. `pinky_web_gui/` 안에서만 새 코드를 만든다. 기존 ROS 패키지는 수정하지 않는다.
2. 로봇 이름은 설정에서 읽는다(하드코딩 금지).
3. 비상정지 버튼과 비상정지 결과 알람은 어떤 설정으로도 끌 수 없다(최고 레벨 `HH` 고정).
4. 권한: 보기·ACK·SIL·소리 설정은 operator 이상, 카탈로그 수정·Enable/Disable 은 manager(`mngr`)만. 서버에서 검증.
5. 각 Step 은 `--mock` 으로 확인한다. 실제 로봇 확인은 사용자가 한다.
6. 편집 툴 PRD 의 "알람 바는 편집 대상 제외" 원칙을 유지한다.
7. **표시 언어 규칙**: GUI 의 문구(메뉴, 버튼, 열 제목, 상태, 안내, 팝업, 오류 응답)는 기존 화면과 같은 **한국어**로 쓴다 (영문화는 철회, 결정 12~14). ACK, SIL, HH/H/L/LL/info, ID, LiDAR 같은 약어·식별자는 그대로 쓴다. 알람 메시지는 카탈로그 템플릿으로 만든 한국어 문장이고 `{reason}` 에는 기존 한국어 원인 문구가 그대로 들어간다. 이 PRD 문서와 코드 주석도 한국어다. 문구 목록은 8절.

### 0-8 로드맵
- Step A1. 서버 알람 모델과 통합(이력 패널 삭제)
- Step A2. ACK / SIL
- Step A3. 알람 소리와 소리 설정
- Step A4. 알람 카탈로그 목록(읽기 전용)
- Step A5. 카탈로그 수정·Enable/Disable (mngr)
- Step A6. (취소) GUI 전체 영문화: 사용자 결정으로 하지 않는다 (아래 Step A6 참고)
- Step A7. Alarm 화면: 왼쪽 메뉴 `Alarm` 버튼과 전체 알람 목록 (A1 의 이력 파일을 읽으므로 A1 직후 가능, A4 의 카탈로그는 이 화면의 탭)

## Step A1. 서버 알람 모델과 통합
### 범위
**포함**: `AlarmManager`, 알람 데이터 모델, WS `alarm`/`snapshot.alarms`, 알람바가 옛 이력 내용을 흡수, 하단 `panelHist` 삭제와 레이아웃 키 v3 이전, 알람바 높이·스크롤.
**제외**: ACK/SIL, 소리, 카탈로그 편집.

### 알람 데이터 구조(안)
`id, code(카탈로그 ID), level(HH|H|L|LL|info), robot, text, detail, state(active|cleared), t_raised, t_last, t_cleared, acked_by, acked_at` (A2 에서 `acked_by/acked_at` 을 추가했다. `count` 는 유지)

### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA1-1 | 서버가 명령 결과(rejected/failed/no_response/비상정지 실패)·로봇 online 변화·patrol FAILED/RETRY/STOPPED 를 알람으로 만든다 |
| FRA1-2 | 같은 `code`+`robot` 의 반복 알람은 **ACK 전까지만** `count` 증가로 묶는다. ACK 했거나 해소된 뒤에 또 생기면 새 행 (결정 19) |
| FRA1-3 | 조건이 사라지면(온라인 복귀, 명령 성공 등) 알람이 `해소` 로 바뀌고 해소 시각을 표시한다 |
| FRA1-4 | 새로 접속한 화면도 `snapshot.alarms` 로 현재 알람을 똑같이 본다 |
| FRA1-5 | 알람바에 시각·로봇·메시지·상태 열을 두고 목록 영역은 스크롤된다(기존 4건 제한 폐지) |
| FRA1-6 | 하단 명령 이력 패널과 `renderHistory`/`upsertHistory`/`historyBody` 를 제거하고 레이아웃 저장 키를 `pinky.layout.v3` 로 올린다(구버전 값은 무시) |
| FRA1-7 | 서버 시각(epoch)만 쓴다(브라우저 시각 혼용 제거) |
| FRA1-8 | `docs/interfaces.md` 에 알람 WS/REST 와 `command` 이벤트 구조를 기록한다 |
| FRA1-9 | 알람바의 고정 문구(열 제목 시각 / 로봇 / 메시지 / 상태, 상태 활성 / 해소 / 확인됨, 버튼, 안내)는 한국어다 (8-1절). 시각은 24시간제 `HH:MM:SS` 로 쓴다 |
| FRA1-11 | (추가, 구현됨) 알람바(모니터링)는 최근 100건만 보이고, 일어난 모든 알람 이벤트는 `alarm_history_dir`(기본 `data/alarms/`)에 1000건씩 파일로 나눠 저장한다(처음 100건에서 A2 때 1000건으로 변경). 파일 이름(`alarms_YYYY-MM-DD_HHMMSS.jsonl`)에는 파일을 만든 날짜와 시각이, 각 레코드(`saved_at`)에는 저장 시각이 들어가고, 가득 차면 그 시각의 이름으로 새 파일을 만든다. 조회는 `GET /api/alarms/history[/files]`. 보관 기간 정리와 재시작 때 이력에서 복원은 미정 (`docs/interfaces.md` 6-7절) |
| FRA1-12 | (추가, 구현됨) 알람 목록은 안에서 스크롤되고, 아래 손잡이로 높이를 늘리고 줄인다(56px ~ 화면의 60%, 브라우저에 저장). 서버가 모르는 브라우저 전용 알람(서버 끊김, 로드 실패 등)은 `localStorage` 에 저장해 새로고침해도 남긴다 |
| FRA1-10 | 알람 메시지(`메시지` 열)는 카탈로그 템플릿으로 만든 한국어 문장이다 (8-2절). 기존 한국어 오류 원인 문구(`commands.py`, `main.py:do_estop`, `PatrolView.failReason`)는 번역 없이 `{reason}` 에 그대로 싣는다 |

### 완료 기준
- [x] `AlarmManager` 단위 테스트(생성, 묶기(ACK 전까지, A2 에서 정의 확정), 해소, 비상정지 비활성 불가)
- [x] mock 으로 구독자 없음·오프라인·FAILED 를 일으켜 알람바에 표시되고 새 탭에서도 동일
- [x] 하단 이력 패널이 없고 지도·사이드 패널 레이아웃이 깨지지 않음(headless Chrome 스크린샷)
- [x] 기존 `test_commands.py` 통과, `verify_step4_ui.py` 의 `#historyBody` 검사를 알람바 기준으로 수정
- [x] 알람바의 고정 문구(열 제목·상태·버튼)에 한글이 없음(알람 메시지 문구는 제외, mock 시나리오로 확인)
- [x] 알람 메시지 문구를 검사하는 기존 테스트(`test_commands.py` 등)가 새 알람 구조에서도 통과
- [x] 알람 이력 파일 분할(1000건)·생성 시각 파일명(`tests/test_alarmlog.py`, `verify_alarm_ui.py`)
- [ ] 실제 로봇 확인은 사용자가 수행 (`docs/checklists/alarm_a1_real_robot_checklist.md`)

### Claude Code 프롬프트

```text
CLAUDE.md, docs/interfaces.md, docs/reference/known-pitfalls.md 를 먼저 읽고, backend/commands.py(CommandTracker), main.py(do_estop, full_snapshot), hub.py, frontend/app.js(addAlarm, renderHistory, upsertHistory)를 읽어줘. 기존 ROS 패키지는 수정하지 마.
이 PRD 의 "Step A1" 기능 요구사항 표(FRA1-1~10)와 완료 기준, 0-6 아키텍처, 0-7 공통 원칙, 8절 문구를 그대로 따라 구현해줘. ACK/SIL, 소리, 카탈로그 편집은 만들지 마.
1. backend/alarms.py(AlarmManager): 데이터 구조는 PRD 의 안을 따르고 시계는 주입 가능하게. 8-2절 코드/레벨/메시지 템플릿을 코드 기본값으로. 같은 code+robot 은 count 증가, 조건 해소 시 cleared, 비상정지 계열은 비활성 불가.
2. 이벤트 원천 연결: CommandTracker 결과, do_estop 실패/부분 실패, 로봇 online 변화, patrol FAILED/RETRY/STOPPED. 서버 시각(epoch)만 사용.
3. WS alarm 이벤트와 snapshot.alarms. 새 탭도 같은 알람을 본다.
4. 프론트엔드: 알람바(Time/Robot/Message/State, 스크롤, info 행 필터), 하단 panelHist·renderHistory·upsertHistory·historyBody 제거, 레이아웃 키를 pinky.layout.v3 로 이전(구버전 무시). "Server disconnected" 는 브라우저 전용 알람으로 유지.
5. docs/interfaces.md 에 알람 WS/REST 와 command 이벤트 구조 기록.
6. 테스트: AlarmManager 단위 테스트, 기존 test_commands.py 통과, verify_step4_ui.py 의 #historyBody 검사를 알람바 기준으로 수정. ROS 검증은 ROS_DOMAIN_ID=77, ROS_LOCALHOST_ONLY=1 로만. mock 으로 구독자 없음·오프라인·FAILED 를 일으켜 새 탭 동일 표시 확인, headless Chrome 스크린샷. 완료 기준을 하나씩 실행한 결과 표를 보여줘. 실제 로봇 확인은 내가 한다.
```

## Step A2. ACK / SIL
### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA2-1 | 알람바에 `ACK`, `SIL` 버튼. 비활성 조건: 대상 없음 |
| FRA2-2 | ACK: 해소된 알람을 알람바에서 숨긴다. 활성 알람은 숨기지 않고 "Acknowledged (by whom, when)" 표시 |
| FRA2-3 | ACK 는 서버에 저장되어 모든 화면에 반영된다 (`POST /api/alarms/ack`, 전체 또는 id 지정) |
| FRA2-4 | SIL: 현재 울리는 소리만 끈다. 알람 상태는 그대로이며 이후 새 알람은 다시 울린다 |
| FRA2-5 | 숨긴 알람은 사라지지 않고 Alarm 화면(Step A7)의 History 탭에서 조회 가능 |
| FRA2-6 | operator 도 ACK/SIL 가능, ACK 한 계정이 기록된다 |
| FRA2-7 | (추가, 구현됨) 이력 파일에는 발생·반복·ACK·해소를 **시도마다 시간 순서로 한 줄씩** 남긴다(화면은 ACK 전까지 `×N` 으로 묶어도 이력은 묶지 않는다). ACK 줄에 `acked_by`/`acked_at` 이 들어간다 (결정 19) |

### 완료 기준
- [x] 해소 알람 ACK → 두 브라우저 모두에서 사라짐, 활성 알람은 유지 (`verify_alarm_ui.py`, mock)
- [x] SIL 후 새 알람이 오면 SIL 이 풀림 (소리 재생은 A3 이므로 상태만 `verify_alarm_ui.py` 로 확인, 실제 소리는 A3 에서 수동 확인)
- [x] 권한 테스트(로그인 안 함 401) (`tests/test_alarm_api.py`)
- [ ] 실제 로봇 확인 (사용자): `docs/checklists/alarm_a2_real_robot_checklist.md`

### Claude Code 프롬프트

```text
CLAUDE.md, docs/interfaces.md, 그리고 이 PRD 의 Step A1 구현물(backend/alarms.py, frontend/app.js 의 알람바)을 먼저 읽어줘. 기존 ROS 패키지는 수정하지 마.
이 PRD 의 "Step A2" 기능 요구사항 표(FRA2-1~6)와 완료 기준, 0-7 공통 원칙 4번(권한), 7번(표시 언어)을 그대로 따라 구현해줘.
1. backend/alarms.py: ack(id|전체, 계정, 시각) 추가. 해소된 알람만 숨김 처리하고 활성 알람은 acked_by/acked_at 만 기록. ACK 이력은 메모리 보관.
2. POST /api/alarms/ack (operator 이상, 로그인 안 하면 401). 결과를 WS alarm 이벤트로 모든 화면에 브로드캐스트.
3. 프론트엔드: 알람바에 ACK / SIL 버튼(툴팁은 8-1절 문구, 대상이 없으면 비활성). SIL 은 이 브라우저의 소리만 끄고 서버 상태는 바꾸지 않는다(결정 3). 새 알람이 오면 SIL 해제.
4. docs/interfaces.md 에 ack API 와 WS 구조 추가.
5. 단위 테스트(활성/해소 ACK, 권한 401)와 mock 으로 브라우저 2개 확인 절차. 완료 기준을 하나씩 실행해 결과 표로 보여줘. 실제 로봇 확인은 내가 한다.
```

## Step A3. 알람 소리와 소리 설정
### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA3-1 | 알람 레벨은 `HH` / `H` / `L` / `LL` / `info` 5단계로 구분한다. `info` 는 무음, 나머지는 레벨마다 다른 소리를 낸다 |
| FRA3-2 | 레벨별 소리는 사용자가 준비한 사운드 파일로 재생한다. 파일이 없거나 재생에 실패하면 Web Audio 합성 비프음으로 대체한다(소리 없이 넘어가지 않는다) |
| FRA3-3 | 사운드 파일은 사용자가 직접 업로드한다. 형식(mp3/wav/ogg), 파일 크기 상한, 보관 위치와 업로드 방식은 결정 10 |
| FRA3-4 | 알람 사운드 시험: 소리 설정 화면에 레벨별 시험 재생·정지 버튼, 반복 주기를 적용한 미리듣기를 둔다. 시험 재생은 실제 알람을 만들지 않는다 |
| FRA3-5 | 활성 알람이 남아 있는 동안 반복 재생(주기 설정), ACK/해소/SIL 시 중단 |
| FRA3-6 | 소리 설정: 전체 켜기/끄기, 음량, 레벨별 켜기/끄기, 레벨별 사운드 파일 선택, 반복 주기 |
| FRA3-7 | 설정은 이 브라우저의 localStorage(`pinky.alarmsound.v1`)에 저장하고 저장소를 못 써도 동작한다 |
| FRA3-8 | 브라우저 자동재생 제한 때문에 첫 클릭 전에는 소리가 막힌다. 막힌 동안 알람바에 "Click to enable alarm sound" 안내를 표시한다 |
| FRA3-9 | 비상정지 실패(`HH`)는 전체 음소거 상태에서도 시각 경고가 유지된다(소리는 설정을 따름) |
| FRA3-10 | 소리 설정 화면의 모든 문구는 한국어다 (알람 소리, 음량, 시험, 정지, 반복 주기, 사운드 파일, 합성 비프음 등, 8-4절) |

### 완료 기준
- [x] 소리 로직(반복·중단·SIL·파일 실패 시 합성음 대체)을 순수 함수로 분리해 테스트 (`frontend/alarmsound.js`, `tests/test_alarmsound_js.py`)
- [ ] 사운드 파일 업로드(또는 배치) 후 레벨별 시험 재생으로 재생 확인(사용자가 확인): 1차안이라 `config/sounds/` 에 배치. 자동 확인은 재생 호출을 세는 `verify_alarm_sound_ui.py` 까지, 실제 소리는 `docs/checklists/alarm_a3_real_robot_checklist.md`
- [x] 파일을 지운 상태에서 합성음으로 대체되는지 확인 (`verify_alarm_sound_ui.py`; 실제 소리는 사용자 확인)
- [x] 자동재생 차단 안내 문구 확인 (`verify_alarm_sound_ui.py`, 칩 줄에 표시, 첫 클릭 뒤 사라짐)

**구현 메모 (A3, 사용자 결정 10 의 1차안)**: 파일은 `config/sounds/` 에 직접 두고(GUI 업로드 없음) 서버가 `/sounds/<파일>` 로 서빙한다. 안내 문구는 소리가 켜져 있고 브라우저가 막은 동안 **알람이 없어도** 항상 보여, 첫 알람이 오기 전에 한 번 눌러 두게 한다(알람이 올 때 처음 알리면 그 알람의 소리는 이미 막힌 뒤다). 반복 주기는 레벨별이 아니라 하나다(8-4절 `Repeat interval (s)` 한 칸).

### Claude Code 프롬프트

```text
CLAUDE.md 와 이 PRD 의 Step A2 구현물(알람바, ACK/SIL)을 먼저 읽어줘.
이 PRD 의 "Step A3" 기능 요구사항 표(FRA3-1~10)와 완료 기준, 결정 9·10·11 을 따라 구현해줘. 결정 10 은 1차안(config/sounds/ 에 파일을 직접 두고 서버가 서빙)으로 하고 GUI 업로드는 만들지 마.
1. frontend/alarmsound.js: 반복·중단·SIL·파일 실패 시 합성음 대체 로직은 DOM 과 Audio 에 의존하지 않는 순수 함수로 분리하고, Web Audio 합성 비프음(레벨별 다른 음)과 파일 재생(mp3/wav/ogg)은 얇은 어댑터로 둔다.
2. GET /api/alarms/sounds(파일 목록)과 /sounds/<파일> 정적 서빙. 경로 탈출과 허용 확장자·크기 검증.
3. 소리 설정 화면(문구는 8-4절 한국어): 전체 on/off, 음량, 레벨별 on/off, 사운드 파일 선택, 반복 주기, 레벨별 시험/정지. 시험 재생은 알람을 만들지 않는다. localStorage 키 pinky.alarmsound.v1, try/catch.
4. 자동재생 차단 시 알람바에 "Click to enable alarm sound" 표시.
5. 순수 함수 단위 테스트(node 또는 pytest). 완료 기준을 하나씩 확인한 표를 보여줘. 실제 소리 확인은 내가 한다.
```

## Step A4. 알람 카탈로그 목록(읽기 전용)
### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA4-1 | 시스템이 낼 수 있는 모든 알람을 `config/alarms.yaml` 과 코드 기본값으로 정의하고 `GET /api/alarms/catalog` 로 제공 |
| FRA4-2 | Alarm 화면(Step A7)의 **Catalog 탭**에 목록: ID, 이름, 발생 조건, 메시지 문구, 레벨, 소리 여부, 사용 여부, 최근 발생 시각·횟수 |
| FRA4-3 | (Step A7 로 이관) ACK 되어 알람바에서 숨겨진 알람까지의 조회는 Alarm 화면의 History 탭이 맡는다 |
| FRA4-4 | 카탈로그에는 현재 코드가 내는 모든 알람이 빠짐없이 있어야 한다(누락 검사 테스트) |
| FRA4-5 | 이 단계에서는 수정 불가. operator 는 조회만 |
| FRA4-6 | 카탈로그 화면의 고정 문구(열 제목, 필터, 버튼)는 한국어다 (ID / 이름 / 발생 조건 / 메시지 / 레벨 / 소리 / 사용 / 최근 발생 / 횟수, 8-4절) |

### 초기 카탈로그(조사 기준, 구현 시 재확인)
레벨은 결정 9(확정)를 따른다. 이름과 메시지는 8절(한국어): 비상정지 실패·일부 전송(HH), 명령 전송 실패(H), 로봇 오프라인(H), 순찰 실패·재시도(H), 서버 연결 끊김(H, 브라우저 전용), 명령 거절(L), 명령 무응답(L), 순찰 정지(LL), 지도·API 불러오기 실패(LL), 구역 관련·프로세스 제어 오류(구현 때 확인 필요).

### 완료 기준
- [x] 누락 검사 테스트 통과, 화면 스크린샷 (`tests/test_alarmcatalog.py`, `docs/alarm_a4.png`, `docs/alarm_a4_narrow.png`)
- [x] `docs/interfaces.md` 갱신 (6-7절 "알람 카탈로그")

**구현 메모 (A4)**: Step A7(Alarm 화면)은 아직 없어서 A4 가 **화면 껍데기**(왼쪽 메뉴 `Alarm`, 탭 두 개, 화면 전환)와 Catalog 탭을 함께 만들었다. History 탭은 비활성으로 두었고 A7 이 `frontend/alarmview.js` 와 `#alarmView` 에 이어서 채운다. 조사 결과 카탈로그에 없던 알람 8개를 더했다: 서버 쪽은 없음(8-2절 13개가 `backend/alarms.py` 가 내는 전부), 브라우저 쪽은 `SERVER_RECONNECTED`, `ACK_REQUEST_FAILED`, `CMD_REQUEST_FAILED`, `ESTOP_REQUEST_FAILED`, `PROC_REQUEST_FAILED`, `ROUTE_INVALID`, `SCAN_PENDING`, `DRIVE_DENIED` (모두 `frontend/app.js` 의 `addAlarm`; 전에는 전부 코드 `LOCAL` 이라 구분이 안 됐다). 로드 실패 알람(`LOAD_FAILED`)은 PRD 대로 LL 로 내도록 바꿨다 (전에는 H/L).

### Claude Code 프롬프트

```text
CLAUDE.md 와 docs/interfaces.md, 이 PRD 의 Step A1~A3 구현물(backend/alarms.py)을 먼저 읽어줘.
이 PRD 의 "Step A4" 기능 요구사항 표(FRA4-1~6)와 완료 기준, 결정 7(왼쪽 메뉴 새 항목)을 따라 구현해줘. 이 단계에서는 수정 기능을 만들지 마(읽기 전용).
1. config/alarms.yaml 과 backend/config.py 의 기존 _load_xxx 패턴으로 카탈로그 로더. 8-2절 템플릿을 기본값으로 하되, 현재 코드(commands.py, main.py, ros_bridge.py, 프론트엔드 addAlarm)가 실제로 내는 알람을 grep 으로 전수 조사해서 빠진 것은 추가하고 근거 파일을 보고해줘.
2. GET /api/alarms/catalog, GET /api/alarms/history (operator 이상).
3. 프론트엔드: Step A7 의 Alarm 화면(왼쪽 메뉴 `Alarm`)의 Catalog 탭에 카탈로그 표(ID, Name, Condition, Message, Level, Sound, Enabled, Last raised, Count)를 채운다. 이력 조회는 A7 의 History 탭이 맡으므로 만들지 않는다.
4. 누락 검사 테스트(코드가 내는 모든 code 가 카탈로그에 있는지), docs/interfaces.md 갱신, headless Chrome 스크린샷.
```

## Step A5. 카탈로그 수정·Enable/Disable (mngr)
### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA5-1 | manager 만 알람별 Enable/Disable 가능. 끈 알람은 만들어지지 않고 알람바·소리에서 제외 |
| FRA5-2 | manager 는 메시지 문구, 레벨, 소리 여부, 반복 주기를 수정하고 저장(`PUT /api/alarms/catalog/{code}`) |
| FRA5-3 | 저장은 `config/alarms.yaml` 이 아니라 별도 override 파일(`config/alarms.local.yaml`)에 하여 기본값을 보존하고 "기본값으로 되돌리기" 제공 |
| FRA5-4 | `HH`(비상정지 계열)은 끌 수 없고 서버가 거부한다 |
| FRA5-5 | 비활성·수정 내역은 로그 화면(`LogStore`)에 누가 언제 바꿨는지 남긴다 |
| FRA5-6 | 입력 검증: 문구 길이 제한, 허용 레벨만, 문구 안의 치환 자리표시자만 허용 |
| FRA5-7 | 편집 화면의 입력 안내·검증 오류와 서버 오류 응답은 한국어다(예: "메시지는 줄바꿈 없이 1~120글자여야 합니다"). 알람 메시지 입력란은 한글·영문 모두 허용하고 길이는 글자 수로 센다 |

### 완료 기준
- [x] 권한·검증·되돌리기 테스트, 서버 재시작 후 유지 (`tests/test_alarmcatalog_edit.py`)
- [x] operator 로 수정 시도 시 403 (`test_operator_gets_403_in_english_and_nothing_changes`, `verify_alarm_edit_ui.py`)
- [x] 끈 알람이 mock 에서 발생해도 표시되지 않음 (`test_disabled_alarm_is_not_raised_shown_or_recorded`, 화면·소리는 `verify_alarm_edit_ui.py`)

**구현 메모 (A5)**: 파일은 3겹이다(코드 기본값 ← `alarms.yaml` ← `alarms.local.yaml`), GUI 는 마지막 파일만 쓰고 기본과 다른 항목만 저장한다. 사용자 결정: **브라우저 전용 알람도 Enable·레벨까지 편집**(문구는 호출부의 동적 문장이라 제외). 설계 결정 — (1) 비상정지 계열은 `ESTOP_REQUEST_FAILED`(브라우저의 비상정지 요청 실패)까지 보호하고 기본 레벨을 H 에서 HH 로 올렸으며, 끄기·레벨 변경에 더해 **소리 끄기도 거부**한다. (2) 끄는 순간 이미 떠 있는 같은 알람은 해소해 소리를 멈춘다. 레벨·문구 변경은 이후 알람부터. (3) 브라우저 알람 대부분은 `state=event`(한 번 알리고 끝, ACK 불가)라 소리 대상이 아니다(A3 규칙). 소리를 낼 수 있는 것은 서버 알람과 `SERVER_DISCONNECTED` 뿐이고 나머지는 카탈로그에서 소리 칸이 잠긴다(`soundable`). (4) 알람별 사운드 파일(결정 11 확장)은 알람 파일 > 레벨 파일 > 합성 비프, 반복 주기는 이기는 알람의 값. (5) 정책은 `snapshot.alarm_policy`·WS `alarm_policy` 로 모든 화면에 방송. (6) local 파일이 깨지면 서버는 기본값으로 시작하고 화면에 알린다. (7) 새 엔드포인트의 오류 응답은 한국어다(FRA5-7). 로그인 전 401 은 전역 인증 게이트가 먼저 막는다. (처음 구현은 영문이었으나 영문화 철회로 한국어로 되돌렸다.) **A5 이후 추가 변경**: (8) 소리 설정 버튼을 왼쪽 메뉴 `소리` 에서 `알람` 화면 탭 줄의 `알람소리`(카탈로그 탭 옆)로 옮겼다. (9) 카탈로그 표 머리글 경계를 끌어 열 너비를 조절한다(더블클릭: 자동, 브라우저 localStorage `pinky.avColWidths`). (10) 서버 알람 `PROC_FAILED`(H) 추가: 시스템 패널 프로세스가 `failed` 가 되면(시작·SSH 실패, 시작 직후/예기치 않은 종료, 정지 실패) 알람, 다시 시작하거나 정지·종료되면 해소. 이 실패에는 응답 `alarmed` 로 화면의 `PROC_REQUEST_FAILED` 중복을 막는다. 알람 종류는 22개(서버 12 + 브라우저 10).

### Claude Code 프롬프트

```text
CLAUDE.md, backend/auth.py, backend/alarms.py, backend/logstore 계열(로그 저장소)과 이 PRD 의 Step A4 구현물을 먼저 읽어줘.
이 PRD 의 "Step A5" 기능 요구사항 표(FRA5-1~7)와 완료 기준, 0-7 공통 원칙 3·4번을 따라 구현해줘.
1. 수정 내용은 config/alarms.local.yaml(override)에만 저장하고 config/alarms.yaml 은 건드리지 않는다. Reset to default 제공. 서버 재시작 후 유지.
2. PUT /api/alarms/catalog/{code}, POST .../reset: manager(mngr)만, operator 는 403. 문구 길이(글자 수), 허용 레벨, 허용 자리표시자({robot},{cmd},{reason} 등)만 통과. HH 비상정지 계열은 끄기·레벨 변경을 서버가 거부.
3. 비활성·수정 내역을 누가 언제 바꿨는지 로그 화면 저장소에 남긴다.
4. 프론트엔드: manager 에게만 편집 UI(사용 토글, 메시지, 레벨, 소리, 반복 주기, 저장/취소/기본값으로 되돌리기), 오류 문구는 한국어(FRA5-7). 알람별 사운드 지정(결정 11)도 이 단계에서 추가.
5. 끈 알람이 mock 에서 발생해도 표시·재생되지 않는 테스트, 권한·검증·되돌리기 테스트. 완료 기준 결과를 표로 보여줘.
```

## Step A6. (취소) GUI 전체 영문화
사용자 결정(2026-10-12)으로 **하지 않는다.** 처음에는 GUI 의 모든 문구를 영문으로 바꾸는 단계(FRA6-1~8)였으나, 화면은 기존과 같은 한국어를 기본으로 하기로 했다. 이미 영문으로 만든 알람바·소리 설정·카탈로그 화면의 문구와 알람 메시지·서버 오류 문구도 한국어로 되돌렸다 (8절이 현재 문구 목록이다).

## Step A7. Alarm 화면 (전체 알람 목록)
### 배경
알람바는 최근 100건만 보여 주고, 서버를 재시작하면 비워진다. 일어난 모든 알람 이벤트는 이미 `data/alarms/` 이력 파일에 저장되고(Step A1, `docs/reference/alarm-history-format.md`) `GET /api/alarms/history` 로 읽을 수 있지만, 이것을 사람이 보는 화면이 없다. 왼쪽 메뉴에 `Alarm` 버튼을 두어 지난 알람 전체를 날짜·로봇·레벨로 찾아보는 화면을 만든다.

### 범위
**포함**: 왼쪽 메뉴 `Alarm` 버튼과 화면 전환, History 탭(전체 알람 목록, 필터, 검색, 페이지, 상세), 이력 파일 목록 보기, 내려받기, 알람 집계 API. Catalog 탭의 자리(내용은 Step A4).
**제외**: 알람 삭제·수정(보관 기간 정리는 별도 결정), ACK/SIL 버튼의 동작(Step A2, 이 화면은 ACK 상태를 보여 주기만 한다), 카탈로그 편집(A5), 브라우저 전용 알람(서버가 모르므로 이 목록에 없다).

### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA7-1 | 왼쪽 메뉴바에 `Alarm` 버튼을 추가한다 (`Main`/`Log` 와 같은 메뉴, 화면 전환 방식은 로그 화면과 같다). 선택한 화면은 새로고침해도 유지한다. 레이아웃 편집 버튼은 로그 화면처럼 숨고, 위의 알람바(비상 정지 포함)는 그대로 보인다 |
| FRA7-2 | 메뉴의 `Alarm` 버튼에 **지금 active 인 알람 수**를 작은 배지로 보인다 (HH/H 가 있으면 빨강, 없으면 숨김). 다른 화면(Main, Log)에서도 알람을 놓치지 않게 하려는 것이다 |
| FRA7-3 | 화면은 탭 두 개: `History`(이 Step), `Catalog`(Step A4 가 채운다, 그 전에는 탭을 숨기거나 비활성) |
| FRA7-4 | History 탭은 이력 파일의 **알람 한 건당 한 행**으로 보여 준다. 파일은 이벤트(`raised`/`repeated`/`acked`/`cleared`)마다 한 줄이므로, 서버가 `(id, t_raised)` 로 묶어 마지막 상태를 한 행으로 만든다. 열: Raised(발생 시각), Robot, Level, Message, State(Active/Cleared/Acknowledged), Count, Acked(확인한 계정·시각), Cleared(해소 시각), Duration(지속 시간) |
| FRA7-5 | 알람바의 100건 제한과 무관하게 저장된 모든 알람을 보며, 최신이 위다. 한 번에 최대 200행을 가져오고 `Load more`(또는 스크롤 끝)로 더 가져온다. 이력 전체를 한꺼번에 읽지 않는다 |
| FRA7-6 | 필터: 기간(From ~ To, 기본은 오늘), Robot, Level(여러 개 선택), State, Code. 검색창은 Message·detail 문구에서 찾는다. 필터는 서버가 적용한다 |
| FRA7-7 | 지금 active 인 알람은 목록 맨 위에 고정해 보이고 WS `alarm` 으로 실시간 갱신된다. 그 아래는 조회한 이력이다 (같은 알람이 두 번 보이지 않게 `id` 로 합친다) |
| FRA7-8 | 행을 누르면 상세: 모든 이벤트 줄의 타임라인(`raised` → `repeated` N회 → `acked` → `cleared` 각 시각, 계정 포함), 전체 Message 와 detail 원문(긴 사유는 줄바꿈), Code, 저장된 파일 이름 |
| FRA7-9 | 화면 위에 이력 파일 요약(저장 폴더, 파일 수, 총 알람 수, 파일당 건수)을 한 줄로 보인다. 파일 목록 펼치기는 읽기 전용이다 (삭제는 GUI 에 두지 않는다) |
| FRA7-10 | `Export`: 지금 필터에 맞는 결과를 CSV 로 내려받는다 (파일 이름에 기간 포함, 열은 FRA7-4 와 같고 시각은 `YYYY-MM-DD HH:MM:SS`). 행 수 상한을 두고 넘으면 안내한다 |
| FRA7-11 | 서버가 저장 이력을 쓰지 못하는 설정(`alarm_history_dir` 없음)이면 이 화면은 "History is disabled" 안내만 보인다. 파일이 깨져 있어도(쓰다 끊긴 줄) 화면은 뜨고 그 줄만 건너뛴다 |
| FRA7-12 | **서버가 꺼져 해소되지 못한 알람은 해소되지 않은 상태 그대로 기록하고 그렇게 보여 준다** (결정 15). 이력 파일에 종료 이벤트나 가짜 `cleared` 를 쓰지 않고, 서버 실행 구분자(`run`)도 추가하지 않는다. History 탭은 마지막 이벤트가 `cleared` 가 아닌 알람을 State `Uncleared` 로 보이되, **지금 서버 메모리에 active 로 살아 있는 알람(FRA7-7)만 `Active`** 라고 한다. 둘을 가르는 것은 서버 메모리의 `(id, t_raised)` 일치 여부뿐이며 추정하지 않는다. 서버를 재시작하면 `id` 가 1부터 다시 시작하므로 알람을 묶고 알아볼 때 `id` 단독이 아니라 `(id, t_raised)` 쌍을 쓴다 |
| FRA7-13 | 서버 API: `GET /api/alarms/history?view=alarms&from=&to=&robot=&level=&state=&code=&q=&limit=&before=` (한 건당 한 행으로 묶은 결과와 다음 페이지 표지), `GET /api/alarms/history/{id}/events` 또는 같은 효과의 상세 조회, 내려받기용 `.../export`. 모두 operator 이상 (보기 전용), 입력 검증(날짜 형식, limit 상한, 알 수 없는 level 은 400). 기존 `GET /api/alarms/history` 의 이벤트 단위 응답은 그대로 유지한다 |
| FRA7-14 | 이 화면의 고정 문구는 한국어다 (8-5절). 알람 메시지와 detail 은 카탈로그가 만든 한국어 문장이다 |
| FRA7-15 | `docs/interfaces.md` 6-7절에 새 API 를 기록하고, `README.md` 의 화면 구성에 Alarm 화면을 추가한다 |

### 완료 기준
- [x] 단위 테스트: 이벤트 줄 묶기(raised/repeated/acked/cleared → 한 행, 시도마다 한 줄), 필터(기간·로봇·레벨·상태·코드·검색), 페이지 표지, 해소 줄이 없는 알람이 `Uncleared` 로 나오고 서버 메모리에 살아 있는 알람만 `Active` 로 나옴, 서버를 재시작해 `id` 가 겹쳐도 `(id, t_raised)` 로 서로 다른 알람으로 묶임, 깨진 줄 건너뛰기, 잘못된 파라미터 400
- [x] mock 으로 알람 수백 건을 만들어(100건 제한을 넘기고 이력 파일이 여러 개) Alarm 화면에서 오래된 알람까지 보이고 `Load more` 가 동작
- [x] 필터·검색 결과가 서버 응답과 일치, 새로고침 뒤에도 선택한 화면 유지
- [x] active 알람이 맨 위에 고정되고 실시간으로 Cleared 로 바뀜. 메뉴 배지가 active 수를 따라감
- [x] 상세에서 이벤트 타임라인이 이력 파일과 같음, CSV 가 스프레드시트로 열림(한글 깨짐 없음)
- [x] 메인 화면의 알람바와 비상 정지가 이 화면에서도 보이고 눌림, 좁은 화면(860px 이하)에서 가로 스크롤 없음 (headless Chrome 스크린샷)
- [x] 화면 고정 문구가 8-5절 (사용자 결정으로 버튼 이름은 영문, 결정 22)의 한국어 문구와 같음 (영문 잔존 없음)
- [ ] 실제 로봇 확인은 사용자가 수행 (체크리스트에 항목 추가)

**구현 메모 (A7)**: 서버는 `backend/alarmquery.py`(순수 함수: 조건 해석·검사, 이벤트 줄 묶기, 상태 판정, 필터, CSV)와 `AlarmHistory.alarms/export_rows/events_of/summary`(`backend/alarmlog.py`)로 이력을 알람 한 건당 한 행으로 묶는다. 파일을 **최신부터 한 개씩**(파일 하나는 1000줄 이하) 읽고 필요한 만큼에서 멈춘다. 알람의 뒤 이벤트가 더 최신 파일에 있어도 `(id, t_raised)` 로 이어 붙이고, 기간 시작일보다 이른 날짜의 파일은 열지 않는다. 다음 쪽(`before`)은 새 파일의 뒤 이벤트를 다시 읽는 비용이 든다(파일 수에 비례). 설계 결정 — (1) **State 4가지**: `active`(서버 메모리의 `(id, t_raised)` 와 일치하고 ACK 안 됨), `acknowledged`(일치하고 ACK 됨), `cleared`, `uncleared`(해소 기록 없음, 메모리에도 없음). 해소 여부는 마지막 줄의 `state`/`t_cleared` 로 본다(해소된 알람을 나중에 ACK 하면 마지막 줄의 `event` 가 `acked` 라서 `event` 로 보면 `uncleared` 로 잘못 읽힌다). (2) **API**: `GET /api/alarms/history?view=alarms&from=&to=&robot=&level=&state=&code=&q=&limit=&before=`(`from`/`to` 는 `YYYY-MM-DD`, 발생 날짜, 서버 PC 현지, 1~366일), `level`/`state` 는 쉼표로 여러 개, `limit` 1~200 기본 200, `q` 100글자까지, 잘못된 값은 400. `view` 가 없으면 기존 이벤트 단위 응답 그대로. `GET /api/alarms/history/{id}/events?t_raised=`(타임라인), `GET /api/alarms/history/export`(CSV, UTF-8 BOM, 최대 10000행, 넘으면 헤더 `X-Export-Truncated: 1`), `GET /api/alarms/history/summary`(파일별 이벤트·알람 수, 저장이 꺼져 있으면 `{enabled:false}`). 모두 operator 이상, 무거운 읽기는 스레드 풀에서 돈다. (3) **고정 알람**: 서버 메모리의 active 알람은 기간 조건과 무관하게 맨 위에 두고(며칠 전 알람도 살아 있으면 보인다) 로봇·레벨·상태·코드·검색 조건은 건다. 해소되어도 그 자리에서 `해소` 로 바뀌고, 다시 조회하면 이력으로 내려간다. 이력과 같은 알람은 `(id, t_raised)` 로 한 번만 보인다. (4) **배지**: 메뉴 `Alarm` 옆에 지금 `active` 인 서버 알람 수(ACK 했어도 해소 전이면 센다). HH/H 가 있으면 빨강, 그 밖에는 주황, 하나도 없으면 숨긴다. (5) **화면 유지**: 보던 화면과 탭을 `localStorage`(`pinky.view`, `pinky.alarmTab`)에 둔다(try/catch). 기본 탭은 History. (6) CSV 열은 FRA7-4 에서 `확인` 을 계정·시각 둘로 나누고 `코드` 를 더했다. 수식으로 읽힐 수 있는 칸(`=`, `+`, `-`, `@` 로 시작)은 앞에 `'` 를 붙인다. (7) 요약의 알람 수는 `raised` 줄 수다(닫힌 파일은 캐시). (8) 기간의 기본값(오늘)은 화면이 채우고 API 는 조건이 없으면 전체를 본다. 화면의 오늘은 브라우저 시계, 서버의 기간은 서버 PC 시계다. 검증: `tests/test_alarmquery.py`(55건), `tests/alarmview_cases.js`, `tests/verify_alarm_history_ui.py`(알람 330건 이상·파일 14개, 스크린샷 `docs/alarm_a7.png`, `docs/alarm_a7_narrow.png`).

### Claude Code 프롬프트

```text
CLAUDE.md, docs/interfaces.md 6-7절, docs/reference/alarm-history-format.md 를 먼저 읽고, backend/alarmlog.py(AlarmHistory), backend/alarms.py, backend/main.py 의 /api/alarms/history 라우트, frontend/app.js 의 setView·왼쪽 메뉴·알람바 렌더(renderAlarms)와 로그 화면(logview.js)을 읽어줘. 기존 ROS 패키지는 수정하지 마.
이 PRD 의 "Step A7" 기능 요구사항 표(FRA7-1~15)와 완료 기준, 0-7 공통 원칙 4·7번, 8-5절 문구를 그대로 따라 구현해줘. ACK/SIL 동작, 카탈로그 내용, 이력 삭제는 만들지 마.
1. backend: AlarmHistory 에 이벤트 줄을 (id, t_raised) 로 묶어 알람 단위로 돌려주는 조회(필터, 정렬, 페이지 표지)를 추가. 마지막 이벤트가 cleared 가 아닌 알람은 `Uncleared`, 서버 메모리에 (id, t_raised) 가 일치하는 active 알람만 `Active` 로 구분한다 (종료 이벤트나 `run` 필드는 추가하지 말고 이력 파일 형식은 바꾸지 마). 순수 함수로 분리해 단위 테스트. 이력 전체를 메모리에 올리지 말고 파일을 최신부터 필요한 만큼만 읽는다. 기존 이벤트 단위 API 의 응답은 바꾸지 않는다.
2. REST: FRA7-13 의 API 를 operator 이상으로, 입력 검증과 상한을 두고 추가. CSV 내보내기는 UTF-8 BOM 으로 한글이 스프레드시트에서 깨지지 않게.
3. frontend: 왼쪽 메뉴 Alarm 버튼(active 배지 포함)과 로그 화면과 같은 방식의 화면 전환, 새로고침 뒤 유지(localStorage, try/catch). History 탭: 필터, 검색, 목록(active 고정 + 이력), Load more, 상세 타임라인, 파일 요약, Export. 새 화면은 별도 파일(alarmview.js)로 두고 문구는 한 곳에 모은다.
4. docs/interfaces.md, README.md 갱신, docs/checklists/alarm_a1_real_robot_checklist.md 또는 새 체크리스트에 Alarm 화면 항목 추가.
5. 테스트: pytest, mock 서버에 알람 수백 건(/api/mock/* 훅과 반복 명령으로) 만든 뒤 headless Chrome 으로 검증(tests/verify_alarm_ui.py 확장 또는 새 스크립트), 1400px 과 860px 이하 스크린샷. ROS 검증이 필요하면 ROS_DOMAIN_ID=77, ROS_LOCALHOST_ONLY=1 로만. 완료 기준을 하나씩 실행한 결과 표를 보여줘. 실제 로봇 확인은 내가 한다.
```

## 4. 요구사항 요약
G1→A1, G2→A2, G3→A3, G4→A4·A5, G5→A1, G6→A6, G7→A7(알람 화면은 각 Step 의 FR 포함).

## 5. 위험과 대응
| 위험 | 대응 |
|---|---|
| 알람을 끄면 비상 상황을 놓침 | HH 비상정지 계열은 끌 수 없음, 끈 내역을 로그에 남김 |
| 서버가 재시작되면 화면의 알람 상태가 사라짐 | 알람 이력은 `data/alarms/` 파일에 남는다(결정 4). 알람바는 조건이 다시 생기면 새로 만들어진다. 해소 기록 없이 끝난 알람은 History 탭에 `Uncleared` 로 남는다(결정 15) |
| 이력 통합으로 일반 명령 행이 알람을 묻어 버림 | info 행은 접어서 표시, 필터 제공(결정 1) |
| 소리가 자동재생 정책에 막힘 | 안내 문구와 시험 재생 |
| 네트워크 진단 PRD N5 와 중복 | N5 의 알람 칩/목록은 이 PRD 의 `AlarmManager` 에 규칙으로 얹는다(결정 6) |
| (철회) GUI 전체 영문화 | 영문화는 하지 않는다 (결정 12~14 철회). 약어·식별자(ACK, SIL, HH, ID 등)는 그대로 둔다 |
| Alarm 화면이 큰 이력 파일을 읽느라 서버가 느려짐 | 파일을 최신부터 필요한 만큼만 읽고(최대 200행/요청), 필터·기간 상한을 서버가 적용 (Step A7) |
| 서버가 꺼져 `cleared` 가 없는 알람이 이력에 해소되지 않은 채 남음 | 의도된 동작(결정 15): History 탭에서 `Uncleared` 로 구분해 보이고, 지금 살아 있는 알람만 `Active`. 사실을 추정하지 않는다 |
| 레이아웃 키 변경으로 사용자 배치 초기화 | 알림 없이 기본 배치로 복귀, 문서에 명시 |

## 6. 결정 항목 (확정은 사용자 결정, 나머지는 권장안)
1. 정상 명령(전송 성공)도 알람바에 보일까? → **확정(사용자 결정): info 행으로 보이되 소리·ACK 대상 아님, 필터로 숨김 가능**
2. ACK 가 활성 알람에도 적용되나? → **확정(사용자 결정): 활성은 "확인됨" 표시만, 숨기는 것은 해소된 것만**
3. SIL 범위: 이 브라우저만 vs 모든 화면? → **확정(사용자 결정): 이 브라우저만**(소리가 브라우저에서 나므로)
4. 알람·ACK 이력을 디스크에 저장할까? → **따로 data 파일에 저장하기로 했음** (알람 이력은 구현됨: `data/alarms/`, `docs/reference/alarm-history-format.md`. ACK 는 A2 에서 같은 이력에 `acked` 줄로 구현됨, 시간 순서로 계속 추가만 한다)
5. 소리 설정 위치: 브라우저 vs 서버(계정별)? → **확정(사용자 결정): 브라우저(localStorage)**, 알람별 소리 정책만 서버 카탈로그
6. 네트워크 진단 PRD N5 와 순서? → **확정(사용자 결정): A1 먼저, N5 는 AlarmManager 규칙으로 구현**
7. 카탈로그 화면 위치: 시스템 패널 탭 vs 별도 메뉴? → **확정(사용자 결정): 왼쪽 메뉴의 `Alarm` 항목(Step A7)의 Catalog 탭 (manager 만 편집)**. 전체 알람 목록(History 탭)과 한 화면을 공유한다
8. operator 에게 ACK/SIL 허용? → **확정(사용자 결정): 허용**(비상 상황 대응), 카탈로그 수정만 manager

9. 레벨 5단계의 심각도 순서와 기존 표시색(`ok/warn/bad`) 대응은? → **확정(사용자 결정): HH > H > L > LL > info, HH 에서 LL 로 갈수록 심각도가 낮아진다** (LL 은 "아주 낮음" 같은 극단값이 아니라 가장 덜 심각한 알람 레벨이다). 색 대응은 HH·H=빨강, L=주황, LL=노랑, info=기본.
10. 사운드 파일 업로드 방식은? → **확정(사용자 결정): 1차: 파일을 `pinky_web_gui/config/sounds/` 에 직접 두고 서버가 서빙, 2차: manager 가 GUI 에서 업로드**(형식 mp3/wav/ogg, 파일당 1MB 이하 권장). GUI 업로드는 저장·검증 코드가 늘어나므로 단계 분리.
11. 사운드 파일 지정은 레벨별 하나인가, 알람(카탈로그 항목)별로도 지정하나? → **확정(사용자 결정): 1차 레벨별, A5 에서 알람별 지정 확장**

12. GUI 영문화의 범위 → **철회(사용자 결정 2026-10-12): 영문화하지 않는다. 기존 화면과 같은 한국어를 기본으로 한다.** (처음에는 GUI 전체 영문 전환이었다)
13. 영문 문구를 코드에 고정하나, 언어 전환(KO/EN)을 둘 것인가? → **철회: 영문화를 하지 않으므로 해당 없음.** 문구는 화면 코드(`alarmview.js` 의 `TEXT` 등) 한 곳에 모아 두어 나중에 언어를 추가할 수 있게 한 구조는 그대로 둔다
14. A6 를 언제 하나? → **철회: A6(GUI 전체 영문화)는 하지 않는다.**
15. 서버가 꺼져 `cleared` 가 기록되지 못한 알람은 이력에서 어떻게 보이나? → **확정(사용자 결정): 해소되지 않은 상태 그대로 기록한다.** 서버 종료 때 종료 이벤트·가짜 `cleared` 를 쓰지 않고 이력 줄에 `run` 필드도 넣지 않는다(A1 의 파일 형식은 그대로). History 탭은 `Uncleared`(이력에 해소 기록이 없음)와 `Active`(지금 서버가 들고 있는 알람)를 구분해 보여 준다
16. Alarm 화면이 읽는 데이터는? → **저장된 이력 파일(서버가 묶어서 제공)**. 화면은 파일을 직접 읽지 않고 API 로만 받는다. 알람바의 100건 목록과 별개로 항상 전체를 볼 수 있다
17. A7 을 언제 하나? → **A1 직후, A2 앞** 권장: 이력 파일이 이미 쌓이고 있어 화면만 있으면 되고, ACK(A2)·카탈로그(A4)가 같은 화면을 쓴다
18. 이력의 보관 기간·용량 정리는? → **A7 에 넣지 않는다** (화면은 읽기 전용, 파일 삭제는 사용자가 직접). 정책(예: 30일)은 정해지면 서버 쪽에서 별도로 추가
19. 반복되는 알람의 정의 → **확정(사용자 결정, A2 재작업): 묶되 ACK 이후는 새 행.** 같은 `code`+`robot` 이 active 이고 ACK 안 됐으면 새 행 없이 `×N`(`count`)으로 묶고, ACK 했거나 해소된 뒤에 또 생기면 새 행(새 `id`)이다. 이력 파일은 시간 순서로 줄을 추가만 한다. **이력에는 시도 하나하나를 시간 순서로 모두 남긴다(사용자 결정: 언제 어떤 조작을 했는지 트러블슈팅에 필요)**: 화면은 `×N` 으로 묶어도 반복마다 `repeated` 줄이 한 줄씩 쌓이며 조작·수신 이벤트 구분은 없다. 연타 때 파일이 빨리 차는 것은 감수한다
20. ACK 한 알람과 SIL 대상 → **A2 구현**: ACK 는 `state` 를 바꾸지 않고 `acked_by/acked_at` 만 채운다. 해소된 알람이 ACK 되어 있으면 알람바에서 숨기고(메모리·이력 파일에는 남음), 활성 알람은 `Acknowledged <계정> <시각>` 으로 계속 보인다. SIL 은 ACK 안 된 활성 알람(소리가 나는 알람)이 있을 때만 켜지고, 새 서버 알람 행이 생기거나 같은 알람이 또 일어나면(`count` 증가) 풀린다
21. 이력 파일 크기와 이름 → **확정(사용자 결정, A2 후속)**: 알람바는 100건 유지, 이력 파일은 **파일당 1000줄**, 가득 차면 **그 시점의 시각**으로 새 파일(`alarms_YYYY-MM-DD_HHMMSS.jsonl`). 기록은 이벤트마다 즉시 추가(넘칠 때 몰아 저장하지 않음). 예전 번호 이름 파일도 읽는다
22. **버튼 이름은 영문 (사용자 결정, A7)**: Alarm 화면과 그 메뉴·탭의 버튼 이름은 영문이다 — 메뉴 `Alarm`, 탭 `History`/`Catalog`, `Apply`, `Reset`, `Export CSV`, `Load more`, `Show files`/`Hide files`. 열 제목·상태·필터 라벨·안내 문구는 한국어(8-5절)다. **후속 지시로 범위 확대**: 왼쪽 메뉴 전체(`Main`, `Log`, `Alarm`, `Points`, `Edit Layout`(편집 중 `Done`/`Reset`/`Cancel`), `Add Robot`, `Dark Mode`/`Light Mode`, `Switch Account`, `Logout`)와 `알람소리` → `Alarm Sound` 도 영문이다. 카탈로그 탭의 `새로고침`/`초기화`/`편집`, 편집창·소리 설정창·로봇 카드 등 화면 안의 버튼은 지시가 없어 한국어 그대로다. 또 History·Catalog 표는 열 너비 조절(`pinky.ahColWidths`/`pinky.avColWidths`)과 단어 단위 줄바꿈(`word-break: keep-all`)을 갖는다 **(추가 확대)** 이후 화면 전체의 버튼·탭·상태 칩 이름도 영문으로 맞췄다 — 사이드바 탭 `Robots`/`System`/`Route`/`Control`/`Coordinates`, `Emergency Stop`, `Start Patrol`/`Stop Patrol`/`Go to Route`, `Start All`/`Stop All`, 알람바 `Hide Info`/`Show Info`, 칩 `Connected`/`Zone: Free` 등. 설명 문장·안내문·메시지는 계속 한국어다.

## 7. 권장 진행 순서
A1 → A7(Alarm 화면, 결정 17) → A2 → A3 → A4 → A5. (A6 영문화는 철회.) A1 은 데이터 구조가 이후 모두의 기반이므로 먼저 검토·확정.

## 8. GUI 표시 문구 (한국어)
영문화는 철회했다(결정 12~14). ACK, SIL, HH/H/L/LL/info, ID, LiDAR 같은 약어·식별자는 그대로 쓴다.

### 8-1 알람바
| 요소 | 문구 |
|---|---|
| 열 제목 | 시각 / 로봇 / 메시지 / 상태 |
| 상태 | 활성 / 해소 / 확인됨 |
| 버튼 | ACK / SIL (툴팁: "해소된 알람을 확인(ACK)해 목록에서 숨깁니다" / "지금 울리는 알람 소리를 끕니다") |
| 빈 목록 | 알람 없음 |
| 반복 횟수 | `×3` |
| 소리 차단 안내 | 클릭하면 알람 소리가 켜집니다 |
| 필터 | 기록 표시 / 기록 숨김 |
| 연결 끊김(브라우저 전용) | 서버 연결 끊김 / 서버에 다시 연결됨 |

### 8-2 알람 메시지 템플릿 (레벨은 결정 9 확정)
| 코드 | 레벨 | 메시지 |
|---|---|---|
| ESTOP_FAILED | HH | {robot} 비상정지 실패: {reason} |
| ESTOP_PARTIAL | HH | {robot} 비상정지가 일부만 전송됨: {reason} |
| CMD_SEND_FAILED | H | {robot} 에 명령 `{cmd}` 를 보내지 못함: {reason} |
| ROBOT_OFFLINE | H | {robot} 오프라인 |
| PATROL_FAILED | H | {robot} 순찰 실패: {reason} |
| PATROL_RETRY | H | {robot} 순찰 재시도 중: {reason} (A1 구현 때 추가) |
| SERVER_DISCONNECTED | H | 서버 연결 끊김 |
| CMD_REJECTED | L | {robot} 에 대한 명령 `{cmd}` 거절: {reason} |
| CMD_NO_RESPONSE | L | {robot} 이(가) `{cmd}` 에 응답 없음 |
| PATROL_STOPPED | LL | {robot} 순찰 정지됨 |
| LOAD_FAILED | LL | {resource} 을(를) 불러오지 못함 |
| CMD_SENT | info | {robot} 에 명령 `{cmd}` 전송 |
| ROBOT_ONLINE | info | {robot} 온라인 |

`{reason}` 에는 기존 한국어 원인 문구(`commands.py`, `main.py:do_estop`, `PatrolView.failReason`, 명령 거절 사유 등)가 그대로 들어간다.

### 8-3 (삭제)
영문 대응표는 영문화 철회로 필요 없어 삭제했다.

### 8-4 소리·카탈로그 화면
알람 소리 / 알람 소리 켜기 / 음량 / 반복 주기(초) / 레벨 / 켜짐 / 사운드 파일 / 시험 / 정지 / 합성 비프음 / 닫기 — 알람 카탈로그 / 이름 / 발생 조건 / 메시지 / 레벨 / 소리 / 사용 / 최근 발생 / 횟수 / 편집 / 사용 / 사운드 파일 / 반복 주기(초) / 저장 / 취소 / 기본값으로 되돌리기

### 8-5 Alarm 화면 (Step A7)
| 요소 | 문구 |
|---|---|
| 메뉴 버튼 | Alarm (결정 22: 버튼 이름은 영문) |
| 탭 | History / Catalog |
| 열 제목 | 발생 / 로봇 / 레벨 / 메시지 / 상태 / 횟수 / 해소 / 지속 시간 |
| 상태 | 활성 / 해소 / 확인됨 / 해소 기록 없음 |
| 필터 | 시작 / 끝 / 로봇 / 레벨 / 상태 / 코드 / 검색 (버튼은 Apply / Reset) |
| 버튼 | Load more / Export CSV / Show files / Hide files / Apply / Reset (결정 22) |
| 상세 | 타임라인 / 발생 / 반복 / 해소 / 상세 / 파일 |
| 안내 | 알람이 없습니다 / 이력 저장이 꺼져 있습니다 / 최신 N건 (전체 M건) / 내보내기는 N행까지입니다 |

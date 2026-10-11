# Pinky 알람바 개선 — 단계별 PRD
2026-10-11 · 초안 v4 (알람 레벨 5단계, 사운드 파일 업로드·시험 재생, GUI 전체 영문(EN) 표시, 알람 문구는 한영 혼용 허용. 결정이 필요한 항목은 6절)

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
| G6 | GUI 전체의 표시 언어를 영문(EN)으로 바꾼다. 단, 알람 메시지 문구는 한글·영문 혼용을 허용한다 (문구 목록은 8절, 전체 전환은 Step A6) |

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
7. **표시 언어 규칙**: GUI 의 고정 문구(메뉴, 버튼, 열 제목, 상태, 안내, 팝업, 오류 응답)는 영문(EN)으로 쓴다. **알람 메시지 문구(알람바에 찍히는 사건 설명)만 한글·영문 혼용을 허용**한다. 이 PRD 문서와 코드 주석은 한국어를 유지한다. 문구 목록은 8절.

### 0-8 로드맵
- Step A1. 서버 알람 모델과 통합(이력 패널 삭제)
- Step A2. ACK / SIL
- Step A3. 알람 소리와 소리 설정
- Step A4. 알람 카탈로그 목록(읽기 전용)
- Step A5. 카탈로그 수정·Enable/Disable (mngr)
- Step A6. GUI 전체 영문화 (A1~A5 와 독립, 병행 가능)

## Step A1. 서버 알람 모델과 통합
### 범위
**포함**: `AlarmManager`, 알람 데이터 모델, WS `alarm`/`snapshot.alarms`, 알람바가 옛 이력 내용을 흡수, 하단 `panelHist` 삭제와 레이아웃 키 v3 이전, 알람바 높이·스크롤.
**제외**: ACK/SIL, 소리, 카탈로그 편집.

### 알람 데이터 구조(안)
`id, code(카탈로그 ID), level(HH|H|L|LL|info), robot, text, detail, state(active|cleared|acked), t_raised, t_cleared, count`

### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA1-1 | 서버가 명령 결과(rejected/failed/no_response/비상정지 실패)·로봇 online 변화·patrol FAILED/RETRY/STOPPED 를 알람으로 만든다 |
| FRA1-2 | 같은 `code`+`robot` 의 반복 알람은 새 행이 아니라 `count` 증가로 묶는다 |
| FRA1-3 | 조건이 사라지면(온라인 복귀, 명령 성공 등) 알람이 `해소` 로 바뀌고 해소 시각을 표시한다 |
| FRA1-4 | 새로 접속한 화면도 `snapshot.alarms` 로 현재 알람을 똑같이 본다 |
| FRA1-5 | 알람바에 시각·로봇·메시지·상태 열을 두고 목록 영역은 스크롤된다(기존 4건 제한 폐지) |
| FRA1-6 | 하단 명령 이력 패널과 `renderHistory`/`upsertHistory`/`historyBody` 를 제거하고 레이아웃 저장 키를 `pinky.layout.v3` 로 올린다(구버전 값은 무시) |
| FRA1-7 | 서버 시각(epoch)만 쓴다(브라우저 시각 혼용 제거) |
| FRA1-8 | `docs/interfaces.md` 에 알람 WS/REST 와 `command` 이벤트 구조를 기록한다 |
| FRA1-9 | 알람바의 고정 문구(열 제목 Time / Robot / Message / State, 상태 Active / Cleared / Acknowledged, 버튼, 안내)는 영문이다. 시각은 24시간제 `HH:MM:SS` 로 쓴다 |
| FRA1-10 | 알람 메시지(`Message` 열)는 카탈로그 템플릿으로 만들며 한글·영문 혼용을 허용한다. 기존 한국어 오류 원인 문구(`commands.py`, `main.py:do_estop`, `PatrolView.failReason`)는 번역 없이 `{reason}` 에 그대로 실어도 된다. 영문 대응(8절)은 있으면 쓰는 선택 사항이다 |

### 완료 기준
- [ ] `AlarmManager` 단위 테스트(생성, 묶기, 해소, 비상정지 비활성 불가)
- [ ] mock 으로 구독자 없음·오프라인·FAILED 를 일으켜 알람바에 표시되고 새 탭에서도 동일
- [ ] 하단 이력 패널이 없고 지도·사이드 패널 레이아웃이 깨지지 않음(headless Chrome 스크린샷)
- [ ] 기존 `test_commands.py` 통과, `verify_step4_ui.py` 의 `#historyBody` 검사를 알람바 기준으로 수정
- [ ] 알람바의 고정 문구(열 제목·상태·버튼)에 한글이 없음(알람 메시지 문구는 제외, mock 시나리오로 확인)
- [ ] 알람 메시지 문구를 검사하는 기존 테스트(`test_commands.py` 등)가 새 알람 구조에서도 통과
- [ ] 실제 로봇 확인은 사용자가 수행

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
| FRA2-5 | 숨긴 알람은 사라지지 않고 알람 카탈로그 화면의 "이력" 보기에서 조회 가능(Step A4) |
| FRA2-6 | operator 도 ACK/SIL 가능, ACK 한 계정이 기록된다 |

### 완료 기준
- [ ] 해소 알람 ACK → 두 브라우저 모두에서 사라짐, 활성 알람은 유지
- [ ] SIL 후 새 알람이 오면 다시 소리가 남(수동 확인)
- [ ] 권한 테스트(로그인 안 함 401)

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
| FRA3-10 | 소리 설정 화면의 모든 문구는 영문이다(Alarm sound, Volume, Test, Stop, Repeat interval, Sound file, Fallback beep 등) |

### 완료 기준
- [ ] 소리 로직(반복·중단·SIL·파일 실패 시 합성음 대체)을 순수 함수로 분리해 테스트
- [ ] 사운드 파일 업로드(또는 배치) 후 레벨별 시험 재생으로 재생 확인(사용자가 확인)
- [ ] 파일을 지운 상태에서 합성음으로 대체되는지 확인
- [ ] 자동재생 차단 안내 문구 확인

### Claude Code 프롬프트

```text
CLAUDE.md 와 이 PRD 의 Step A2 구현물(알람바, ACK/SIL)을 먼저 읽어줘.
이 PRD 의 "Step A3" 기능 요구사항 표(FRA3-1~10)와 완료 기준, 결정 9·10·11 을 따라 구현해줘. 결정 10 은 1차안(config/sounds/ 에 파일을 직접 두고 서버가 서빙)으로 하고 GUI 업로드는 만들지 마.
1. frontend/alarmsound.js: 반복·중단·SIL·파일 실패 시 합성음 대체 로직은 DOM 과 Audio 에 의존하지 않는 순수 함수로 분리하고, Web Audio 합성 비프음(레벨별 다른 음)과 파일 재생(mp3/wav/ogg)은 얇은 어댑터로 둔다.
2. GET /api/alarms/sounds(파일 목록)과 /sounds/<파일> 정적 서빙. 경로 탈출과 허용 확장자·크기 검증.
3. 소리 설정 화면(문구는 8-4절 영문): 전체 on/off, 음량, 레벨별 on/off, 사운드 파일 선택, 반복 주기, 레벨별 Test/Stop. 시험 재생은 알람을 만들지 않는다. localStorage 키 pinky.alarmsound.v1, try/catch.
4. 자동재생 차단 시 알람바에 "Click to enable alarm sound" 표시.
5. 순수 함수 단위 테스트(node 또는 pytest). 완료 기준을 하나씩 확인한 표를 보여줘. 실제 소리 확인은 내가 한다.
```

## Step A4. 알람 카탈로그 목록(읽기 전용)
### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA4-1 | 시스템이 낼 수 있는 모든 알람을 `config/alarms.yaml` 과 코드 기본값으로 정의하고 `GET /api/alarms/catalog` 로 제공 |
| FRA4-2 | 화면(시스템 패널 또는 별도 탭)에 목록: ID, 이름, 발생 조건, 메시지 문구, 레벨, 소리 여부, 사용 여부, 최근 발생 시각·횟수 |
| FRA4-3 | "알람 이력" 보기: ACK 되어 알람바에서 숨겨진 알람까지 조회(서버 메모리 보관 개수는 설정) |
| FRA4-4 | 카탈로그에는 현재 코드가 내는 모든 알람이 빠짐없이 있어야 한다(누락 검사 테스트) |
| FRA4-5 | 이 단계에서는 수정 불가. operator 는 조회만 |
| FRA4-6 | 카탈로그 화면의 고정 문구(열 제목, 필터, 버튼)는 영문이다(Name / Condition / Message / Level / Sound / Enabled / Last raised / Count). 알람 이름·조건·메시지 문구는 한영 혼용 가능 |

### 초기 카탈로그(조사 기준, 구현 시 재확인)
레벨은 결정 9 확정 전의 잠정 배정이다. 영문 이름과 메시지는 8절: E-STOP failed / partially failed(HH), Command send failed(H), Robot offline(H), Patrol failed·retrying(H), Server disconnected(H, 브라우저 전용), Command rejected(L), No response to command(L), Patrol stopped(LL), Map / API load failed(LL), 구역 관련·프로세스 제어 오류(구현 때 확인 필요).

### 완료 기준
- [ ] 누락 검사 테스트 통과, 화면 스크린샷
- [ ] `docs/interfaces.md` 갱신

### Claude Code 프롬프트

```text
CLAUDE.md 와 docs/interfaces.md, 이 PRD 의 Step A1~A3 구현물(backend/alarms.py)을 먼저 읽어줘.
이 PRD 의 "Step A4" 기능 요구사항 표(FRA4-1~6)와 완료 기준, 결정 7(왼쪽 메뉴 새 항목)을 따라 구현해줘. 이 단계에서는 수정 기능을 만들지 마(읽기 전용).
1. config/alarms.yaml 과 backend/config.py 의 기존 _load_xxx 패턴으로 카탈로그 로더. 8-2절 템플릿을 기본값으로 하되, 현재 코드(commands.py, main.py, ros_bridge.py, 프론트엔드 addAlarm)가 실제로 내는 알람을 grep 으로 전수 조사해서 빠진 것은 추가하고 근거 파일을 보고해줘.
2. GET /api/alarms/catalog, GET /api/alarms/history (operator 이상).
3. 프론트엔드: 왼쪽 메뉴 'Alarms' 항목, 카탈로그 표(ID, Name, Condition, Message, Level, Sound, Enabled, Last raised, Count)와 Alarm history 보기. 로그 화면과 같은 방식으로 전환.
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
| FRA5-7 | 편집 화면의 입력 안내·검증 오류와 서버 오류 응답은 영문이다(예: "Message must be 1–120 characters"). 알람 메시지 입력란은 한글·영문 모두 허용하고 길이는 글자 수로 센다 |

### 완료 기준
- [ ] 권한·검증·되돌리기 테스트, 서버 재시작 후 유지
- [ ] operator 로 수정 시도 시 403
- [ ] 끈 알람이 mock 에서 발생해도 표시되지 않음

### Claude Code 프롬프트

```text
CLAUDE.md, backend/auth.py, backend/alarms.py, backend/logstore 계열(로그 저장소)과 이 PRD 의 Step A4 구현물을 먼저 읽어줘.
이 PRD 의 "Step A5" 기능 요구사항 표(FRA5-1~7)와 완료 기준, 0-7 공통 원칙 3·4번을 따라 구현해줘.
1. 수정 내용은 config/alarms.local.yaml(override)에만 저장하고 config/alarms.yaml 은 건드리지 않는다. Reset to default 제공. 서버 재시작 후 유지.
2. PUT /api/alarms/catalog/{code}, POST .../reset: manager(mngr)만, operator 는 403. 문구 길이(글자 수), 허용 레벨, 허용 자리표시자({robot},{cmd},{reason} 등)만 통과. HH 비상정지 계열은 끄기·레벨 변경을 서버가 거부.
3. 비활성·수정 내역을 누가 언제 바꿨는지 로그 화면 저장소에 남긴다.
4. 프론트엔드: manager 에게만 편집 UI(Enable 토글, 메시지, 레벨, 소리, 반복 주기, Save/Cancel/Reset), 오류 문구는 영문(FRA5-7). 알람별 사운드 지정(결정 11)도 이 단계에서 추가.
5. 끈 알람이 mock 에서 발생해도 표시·재생되지 않는 테스트, 권한·검증·되돌리기 테스트. 완료 기준 결과를 표로 보여줘.
```

## Step A6. GUI 전체 영문화
### 범위
**포함**: `frontend/` 의 모든 화면(메인, 로그, 레이아웃 편집, 포인트 등 메뉴, 로봇 추가 팝업, 로그인, 시스템·프로세스 패널, 구역·순찰 표시, 명령 버튼, 지도 정보줄)과 서버가 브라우저에 돌려주는 사용자 대면 오류 문구(`main.py`, `commands.py`, `auth.py`, `procs.py` 등의 HTTP/WS 응답).
**제외**: 코드 주석·docstring(한국어 유지, 코딩 규칙), 서버 로그 파일 내용, 알람 메시지 문구(한영 혼용 허용), 로봇·ROS 쪽 문자열.

### 기능 요구사항
| ID | 요구사항 |
|---|---|
| FRA6-1 | 사용자에게 보이는 고정 문구를 전부 영문으로 바꾼다. 조사 기준: `frontend/` 의 한글 문자열(`app.js`, `index.html`, `patrolview.js`, `procview.js`, `addrobot.js`, `zoneview.js`, `logview.js`) |
| FRA6-2 | 문구를 `frontend/strings.js` 한 곳에 모으고 화면 코드는 키로 참조한다(후속 언어 추가 대비, 결정 13) |
| FRA6-3 | 서버가 브라우저에 돌려주는 오류·상태 문구(`detail`, `error`)를 영문으로 바꾼다. 알람 메시지로 쓰이는 `{reason}` 은 FRA1-10 에 따라 한국어 유지 가능 |
| FRA6-4 | `<html lang>` 을 `en` 으로 하고 날짜·시각 표기를 24시간제로 통일한다 |
| FRA6-5 | 영문으로 길어진 문구가 메뉴·버튼·표에서 줄바꿈·잘림 없이 보인다(왼쪽 메뉴 폭, 로봇 카드 버튼, 시스템 패널 버튼) |
| FRA6-6 | 한글 폰트가 필요 없는 화면에서도 알람 메시지의 한글이 깨지지 않도록 폰트 스택에 한글 폰트를 유지한다 |
| FRA6-7 | 한국어 문구를 검사하는 기존 테스트·검증 스크립트(`tests/verify_*_ui.py`, `test_commands.py`, `test_auth.py` 등)를 영문 기준으로 갱신한다 |
| FRA6-8 | `README.md`, `docs/interfaces.md` 의 화면 이름 설명을 새 영문 이름에 맞춘다(문서 본문은 한국어 유지) |

### 완료 기준
- [ ] 한글이 남은 사용자 대면 문구 전수 목록과 영문 대응표를 `docs/` 에 남기고, 화면별 headless Chrome 스크린샷으로 한글 잔존 확인(알람 메시지 제외)
- [ ] 모든 화면에서 문구 잘림·줄바꿈 깨짐이 없음(스크린샷, 폭 860px 이하 포함)
- [ ] 갱신한 기존 테스트 전부 통과
- [ ] 실제 로봇 확인은 사용자가 수행

### Claude Code 프롬프트

```text
CLAUDE.md 와 docs/interfaces.md 를 먼저 읽고, frontend/ 의 app.js, index.html, patrolview.js, procview.js, addrobot.js, zoneview.js, logview.js 와 backend 의 main.py, commands.py, auth.py, procs.py 를 읽어줘. 기존 ROS 패키지는 수정하지 마.
이 PRD 의 "Step A6" 범위·기능 요구사항 표(FRA6-1~8)와 완료 기준, 0-7 공통 원칙 7번, 결정 12~14 를 따라 GUI 전체를 영문으로 바꿔줘. 코드 주석·docstring 은 한국어 유지, 알람 메시지 문구와 {reason} 은 한영 혼용 허용.
1. 먼저 한글이 남은 사용자 대면 문구 전수 목록과 영문 대응표를 docs/ 에 만들어 보여줘(구현 전에 내가 검토한다).
2. 승인 후 frontend/strings.js 에 문구를 모으고 화면 코드는 키로 참조. 서버가 돌려주는 detail/error 도 영문으로.
3. <html lang="en">, 시각은 24시간제. 한글 폰트 스택은 유지(알람 메시지용).
4. 영문으로 길어진 문구가 왼쪽 메뉴·로봇 카드 버튼·시스템 패널에서 잘리거나 줄바꿈이 깨지지 않게 조정.
5. 한국어를 검사하는 기존 테스트·verify_*_ui.py 를 영문 기준으로 갱신하고 전부 통과시킨다. README.md 와 docs/interfaces.md 의 화면 이름 설명도 갱신(본문은 한국어).
6. 화면별 headless Chrome 스크린샷(폭 860px 이하 포함)으로 한글 잔존·잘림 확인. 완료 기준 결과를 표로 보여줘.
```

## 4. 요구사항 요약
G1→A1, G2→A2, G3→A3, G4→A4·A5, G5→A1, G6→A6(알람 화면은 각 Step 의 FR 포함).

## 5. 위험과 대응
| 위험 | 대응 |
|---|---|
| 알람을 끄면 비상 상황을 놓침 | HH 비상정지 계열은 끌 수 없음, 끈 내역을 로그에 남김 |
| 서버가 재시작되면 알람 상태가 사라짐 | 알람 상태는 조건 재평가로 복구, ACK 이력은 휘발(결정 4) |
| 이력 통합으로 일반 명령 행이 알람을 묻어 버림 | info 행은 접어서 표시, 필터 제공(결정 1) |
| 소리가 자동재생 정책에 막힘 | 안내 문구와 시험 재생 |
| 네트워크 진단 PRD N5 와 중복 | N5 의 알람 칩/목록은 이 PRD 의 `AlarmManager` 에 규칙으로 얹는다(결정 6) |
| GUI 전체 영문화 중 번역 누락·문구 길이로 레이아웃 깨짐 | 문구 표(`strings.js`)로 일원화하고 A6 완료 기준에 한글 잔존·잘림 스크린샷 검증 포함 |
| 레이아웃 키 변경으로 사용자 배치 초기화 | 알림 없이 기본 배치로 복귀, 문서에 명시 |

## 6. 결정이 필요한 항목 (권장안)
1. 정상 명령(전송 성공)도 알람바에 보일까? → **info 행으로 보이되 소리·ACK 대상 아님, 필터로 숨김 가능**
2. ACK 가 활성 알람에도 적용되나? → **활성은 "확인됨" 표시만, 숨기는 것은 해소된 것만**
3. SIL 범위: 이 브라우저만 vs 모든 화면? → **이 브라우저만**(소리가 브라우저에서 나므로)
4. 알람·ACK 이력을 디스크에 저장할까? → **1차 메모리, 필요 시 파일 추가**
5. 소리 설정 위치: 브라우저 vs 서버(계정별)? → **브라우저(localStorage)**, 알람별 소리 정책만 서버 카탈로그
6. 네트워크 진단 PRD N5 와 순서? → **A1 먼저, N5 는 AlarmManager 규칙으로 구현**
7. 카탈로그 화면 위치: 시스템 패널 탭 vs 별도 메뉴? → **왼쪽 메뉴의 새 항목(manager 만 편집)**
8. operator 에게 ACK/SIL 허용? → **허용**(비상 상황 대응), 카탈로그 수정만 manager

9. 레벨 5단계의 심각도 순서와 기존 표시색(`ok/warn/bad`) 대응은? → **HH > H > L > LL > info (높을수록 심각)로 해석**. 공정 알람 관례(HH/LL 모두 극단값)와 다를 수 있으니 의도를 확인해 주세요. 색 대응은 HH·H=빨강, L=주황, LL=노랑, info=기본.
10. 사운드 파일 업로드 방식은? → **1차: 파일을 `pinky_web_gui/config/sounds/` 에 직접 두고 서버가 서빙, 2차: manager 가 GUI 에서 업로드**(형식 mp3/wav/ogg, 파일당 1MB 이하 권장). GUI 업로드는 저장·검증 코드가 늘어나므로 단계 분리.
11. 사운드 파일 지정은 레벨별 하나인가, 알람(카탈로그 항목)별로도 지정하나? → **1차 레벨별, A5 에서 알람별 지정 확장**

12. GUI 영문화의 범위 → **확정: GUI 전체를 영문으로 전환(Step A6). 알람 메시지 문구만 한영 혼용 허용**
13. 영문 문구는 코드에 고정하나, 언어 전환(KO/EN)을 둘 것인가? → **EN 고정. 문구는 한 곳에 모아(`frontend/strings.js` 성격의 문구 표) 나중에 언어를 추가할 수 있게 한다.** 서버가 내는 사용자 대면 오류 문구도 같은 원칙
14. A6 를 언제 하나? → **A1 직후 권장**: A1 이 알람바·이력 화면을 새로 쓰므로 그 뒤에 하면 번역 대상이 줄고, A2~A5 는 처음부터 영문 문구 표를 쓴다

## 7. 권장 진행 순서
A1 → A6(영문화, 결정 14) → A2 → A3 → A4 → A5. A1 은 데이터 구조가 이후 모두의 기반이므로 먼저 검토·확정.

## 8. GUI 표시 문구 (EN)
### 8-1 알람바
| 요소 | 문구 |
|---|---|
| 열 제목 | Time / Robot / Message / State |
| 상태 | Active / Cleared / Acknowledged |
| 버튼 | ACK / SIL (툴팁: "Acknowledge cleared alarms" / "Silence current alarm sound") |
| 빈 목록 | No alarms |
| 반복 횟수 | `×3` |
| 소리 차단 안내 | Click to enable alarm sound |
| 필터 | Show info / Hide info |
| 연결 끊김(브라우저 전용) | Server disconnected / Server reconnected |

### 8-2 알람 메시지 템플릿 (잠정, 레벨은 결정 9 후 확정)
| 코드 | 레벨 | 메시지 |
|---|---|---|
| ESTOP_FAILED | HH | E-STOP failed on {robot}: {reason} |
| ESTOP_PARTIAL | HH | E-STOP partially sent on {robot}: {reason} |
| CMD_SEND_FAILED | H | Command `{cmd}` could not be sent to {robot}: {reason} |
| ROBOT_OFFLINE | H | {robot} is offline |
| PATROL_FAILED | H | Patrol failed on {robot}: {reason} |
| PATROL_RETRY | H | Patrol retrying on {robot}: {reason} (A1 구현 때 추가) |
| SERVER_DISCONNECTED | H | Server disconnected |
| CMD_REJECTED | L | Command `{cmd}` rejected for {robot}: {reason} |
| CMD_NO_RESPONSE | L | No response from {robot} to `{cmd}` |
| PATROL_STOPPED | LL | Patrol stopped on {robot} |
| LOAD_FAILED | LL | Failed to load {resource} |
| CMD_SENT | info | Command `{cmd}` sent to {robot} |
| ROBOT_ONLINE | info | {robot} is online |

### 8-3 오류 원인 문구 (`{reason}`) 영문 대응 (선택 사항)
| 기존 한국어 원인 | 영문 |
|---|---|
| cmd_vel 0 속도를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다) | No subscriber for cmd_vel zero-velocity (domain_bridge may be down) |
| patrol_cmd stop 전송 실패 | Failed to send patrol_cmd stop |
| 로봇이 이미 멈춰 있었거나 stop 이 반영되지 않았을 수 있음 | Robot may already be stopped or stop may not have taken effect (zero velocity was sent) |

알람 메시지는 한영 혼용을 허용하므로(공통 원칙 7) 이 대응은 필수가 아니다. 대응이 없는 원인은 한국어 원문을 그대로 보인다. 나머지 원인 문구(`PatrolView.failReason`, 명령 거절 사유 등)는 필요할 때 구현 중 추가한다.

### 8-4 소리·카탈로그 화면
Alarm sound / Master on-off / Volume / Repeat interval (s) / Level / Sound file / Upload / Test / Stop / Fallback beep / Alarm catalog / Alarm history / Name / Condition / Message / Enabled / Last raised / Count / Reset to default / Save / Cancel

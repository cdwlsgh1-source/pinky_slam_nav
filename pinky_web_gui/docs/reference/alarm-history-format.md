# 알람 이력 파일 형식

> 역할: 서버가 `data/alarms/` 에 저장하는 알람 이력 파일 한 줄의 구조(필드, 타입, 값 범위)와 읽는 법. 저장 규칙(분할, 날짜, 쓰기 시점)은 `../interfaces.md` 6-7절 "알람 이력 파일", 알람 코드와 레벨은 같은 절의 표를 본다.
> 갱신: 2026-10-12 (Step A2: `acked` 이벤트와 `acked_by`/`acked_at` 추가, 반복은 화면에서 ACK 전까지만 묶고 이력에는 시도마다 기록, 파일은 1000줄·생성 시각 이름) · 근거 코드: `backend/alarmlog.py` 의 `AlarmHistory.record`, `backend/alarms.py` 의 `AlarmManager`

## 1. 파일

| 항목 | 내용 |
|---|---|
| 형식 | JSON Lines (JSONL): UTF-8 텍스트, **한 줄 = JSON 객체 하나 = 알람 이벤트 하나**. 한글은 `\uXXXX` 로 바꾸지 않고 그대로 쓴다 |
| 이름 | `alarms_YYYY-MM-DD_HHMMSS.jsonl` (그 파일을 처음 만든 서버 PC 현지 날짜·시각. 첫 줄의 `saved_at` 과 같다. 같은 초에 둘이면 `-2`). Step A2 이전 파일은 `alarms_YYYY-MM-DD_NNN.jsonl`(번호) 이름이며 같이 읽는다 |
| 분량 | 파일 하나에 1000줄까지(`alarm_history_chunk`), 차면 다음 이벤트의 시각으로 새 파일. 날짜가 바뀌어도 새 파일 |
| 위치 | `data/alarms/` (`--mock` 은 `data/alarms-mock/`). `data/` 는 `.gitignore` 대상이라 저장소에 올라가지 않는다. 형식 설명이 이 문서에 있는 이유다 |

## 2. 필드

모든 줄에 아래 16개 키가 **항상 같은 순서로** 들어 있다 (없는 값은 `null` 또는 빈 문자열).

| 필드 | 타입 | 값 | 의미 |
|---|---|---|---|
| `saved_at` | 문자열 | `"2026-10-11 19:01:17"` | 이 줄을 파일에 쓴 시각 (서버 PC 현지 시각, 사람이 읽는 용) |
| `t` | 실수 | epoch 초 | `saved_at` 과 같은 시각 (계산용) |
| `event` | 문자열 | `raised` \| `repeated` \| `acked` \| `cleared` | 이 줄이 기록하는 사건: 생김 / ACK 전의 같은 알람이 또 생김(`count` 증가, 시도마다 한 줄) / 확인(ACK)됨 / 해소됨 |
| `id` | 정수 | 1, 2, … | 알람 한 건의 번호. 같은 알람의 `raised`/`repeated`/`acked`/`cleared` 줄은 `id` 가 같다 |
| `code` | 문자열 | `ESTOP_FAILED` 등 | 알람 종류 (목록: `../interfaces.md` 6-7절) |
| `level` | 문자열 | `HH` \| `H` \| `L` \| `LL` \| `info` | 심각도 (높을수록 앞) |
| `robot` | 문자열 \| `null` | `"pinky2"` | 대상 로봇. 로봇과 무관하면 `null` |
| `text` | 문자열 | | 알람바에 보인 문구 (한영 혼용, `detail` 이 안에 들어 있다) |
| `detail` | 문자열 | | 문구의 `{reason}` 원문 (한국어 오류 원인 그대로). 없으면 `""` |
| `state` | 문자열 | `active` \| `cleared` | 이 줄을 쓴 시점의 알람 상태. ACK 해도 바뀌지 않는다 (ACK 는 `acked_by` 로 본다) |
| `t_raised` | 실수 | epoch 초 | 알람이 처음 생긴 시각 |
| `t_last` | 실수 | epoch 초 | 마지막으로 발생한 시각 (반복되면 갱신) |
| `t_cleared` | 실수 \| `null` | epoch 초 | 해소된 시각. 해소 전에는 `null` |
| `count` | 정수 | 1 이상 | ACK 전에 같은 알람이 반복된 누적 횟수 (새 행은 1) |
| `acked_by` | 문자열 \| `null` | `"oper"` | ACK 한 계정 ID (`mngr`, `oper`, 인증이 꺼진 서버는 `no-auth`). ACK 전에는 `null`. `info` 는 ACK 대상이 아니라 항상 `null` |
| `acked_at` | 실수 \| `null` | epoch 초 | ACK 한 시각. ACK 전에는 `null` |

## 3. 이벤트 순서

한 알람은 보통 이렇게 줄이 쌓인다 (`id` 가 같다). 파일은 **일어난 순서대로 추가만 하고 기존 줄은 고치지 않는다.** 같은 알람이 ACK 전에 또 일어나면 한 행(`id`)으로 묶고 `count` 를 올린다. **이력에는 화면과 달리 시도 하나하나가 시간순으로 한 줄씩 남는다** (조작이든 로봇에서 받은 이벤트든 같다). ACK 하거나 해소된 뒤에 또 일어나면 새 `id` 의 `raised` 줄이 생긴다.

| 줄 | `event` | `state` | `count` | `acked_by` | `t_cleared` |
|---|---|---|---|---|---|
| 1 | `raised` | `active` | 1 | `null` | `null` |
| 2, 3, … (ACK 전에 반복될 때마다) | `repeated` | `active` | 2, 3, … | `null` | `null` |
| ACK 했을 때만 | `acked` | 그 시점의 상태 | 그 시점의 값 | 계정 ID | 그 시점의 값 |
| 마지막 (해소됐을 때) | `cleared` | `cleared` | 마지막 값 | ACK 했다면 계정 ID | 해소 시각 |

**예: 비상정지를 두 번 누르는 사이에 다른 알람이 낀 경우** (모든 줄이 일어난 순서대로 쌓인다):

| 시각 | 일어난 일 | 파일에 추가되는 줄 |
|---|---|---|
| 22:00:00 | pinky1 비상정지 실패 | `raised` (id 1, `ESTOP_FAILED`, count 1) |
| 22:00:05 | pinky2 오프라인 | `raised` (id 2, `ROBOT_OFFLINE`) |
| 22:00:10 | pinky1 비상정지 또 실패 | `repeated` (id 1, count 2, 이 시도의 시각 `t`) |
| 22:00:20 | `mngr` 가 ACK | `acked` (id 1, count 2, `acked_by` `mngr`) |
| 22:00:30 | pinky1 비상정지 또 실패 | `raised` (id 3, count 1: ACK 한 뒤라 새 알람) |

알람바에서는 22:00:10 에 id 1 이 한 행에서 `×2` 가 될 뿐이지만, 파일에는 시도마다 줄이 남아 언제 무슨 조작을 했는지 추적할 수 있다.

- ACK 와 해소의 순서는 어느 쪽이 먼저여도 된다 (활성 알람을 먼저 ACK 하면 `acked` 줄의 `state` 가 `active`, 해소 뒤에 ACK 하면 `cleared`). 해소되고 ACK 된 알람은 알람바에서 숨기지만 파일에는 남는다.
- 묶인 알람이라도 시도마다 `repeated` 줄이 있어, 각 시도의 시각(`t`, `t_last`)과 문구를 알 수 있다. ACK 를 한 줄은 `acked_by` 로 누가 언제 확인했는지 남긴다.
- `info` 레벨(명령 전송 기록, 로봇 온라인)은 해소 개념이 없어서 **`raised` 한 줄뿐**이고 `state` 가 처음부터 `cleared` 이며 `t_cleared` 는 `t_raised` 와 같은 값이다.
- **해소되지 않은 채 서버가 꺼지면 `cleared` 줄은 없다. 이것은 의도된 동작이다** (PRD 결정 15: 종료 이벤트나 가짜 `cleared` 를 쓰지 않고 해소되지 않은 상태 그대로 둔다). 파일에서는 마지막 줄이 `active` 인 채 끝난다. 그 알람이 지금도 살아 있는지는 파일로 알 수 없고, 서버 메모리의 알람과 `(id, t_raised)` 가 같을 때만 지금 active 이다 (Alarm 화면은 나머지를 `Uncleared` 로 보인다). 마지막 줄의 `t` 는 서버가 꺼진 시각의 상한으로 볼 수 있다.

## 4. 예시

```json
{"saved_at":"2026-10-11 19:01:17","t":1791712877.6030862,"event":"repeated","id":2,"code":"ESTOP_FAILED","level":"HH","robot":"pinky2","text":"pinky2 비상정지 실패: patrol_cmd stop 전송 실패: /pinky2/patrol_cmd 를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다) · cmd_vel 0 속도를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다)","detail":"patrol_cmd stop 전송 실패: /pinky2/patrol_cmd 를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다) · cmd_vel 0 속도를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다)","state":"active","t_raised":1791712750.9648645,"t_last":1791712877.603082,"t_cleared":null,"count":32,"acked_by":null,"acked_at":null}
```

읽는 법: pinky2 의 비상정지 실패(`HH`)가 처음 생긴 뒤(`t_raised`) 약 127초 동안 반복되어 이 줄에서 `count` 가 32 가 되었고, 아직 해소되지 않았으며(`t_cleared` 가 `null`) 아무도 확인(ACK)하지 않았다(`acked_by` 가 `null`). 원인은 domain_bridge 가 꺼져 있어 `patrol_cmd` 와 `cmd_vel` 구독자가 없는 것이다. 이 알람을 ACK 한 뒤에 또 실패하면 같은 `id` 가 아니라 새 `id` 의 `raised` 줄이 생긴다.

> Step A1 에서 저장한 옛 파일(14개 키)에는 `acked_by`/`acked_at` 이 없다. 함께 읽는 스크립트는 `row.get('acked_by')` 처럼 없어도 되게 쓴다.

## 5. 읽을 때 주의할 점

- **시각이 두 종류다.** `saved_at`/`t` 는 줄을 쓴 시각이고, `t_raised`/`t_last`/`t_cleared` 는 알람의 시각이다. `t` 와 `t_last` 가 마이크로초 단위로 조금 다른 것은 서로 다른 시점에 시계를 읽기 때문이며 정상이다.
- **`id` 는 서버를 다시 켜면 1부터 다시 시작한다.** 여러 날이나 여러 번의 실행을 합쳐 분석할 때 `id` 만으로는 같은 알람을 구분할 수 없다. `(id, t_raised)` 쌍을 키로 쓴다.
- **`repeated` 는 시도할 때마다 한 줄씩 쌓인다.** 한 알람의 반복 횟수는 마지막 줄의 `count` 로 알 수 있다. 비상정지를 연타하면 1000줄짜리 파일도 찰 수 있다.
- **`text` 와 `detail` 이 겹친다.** 검색은 `code` 와 `robot` 으로 하는 편이 간단하다.
- **브라우저 전용 알람은 저장되지 않는다.** 서버 끊김, 지도 불러오기 실패 같은 알람은 서버가 모른다 (화면에서는 `localStorage` 에 남는다).
- 서버가 쓰는 도중 꺼지면 마지막 줄이 깨질 수 있다. 서버의 조회 API 는 깨진 줄을 건너뛰지만, 직접 읽는 스크립트는 `json.loads` 실패를 처리해야 한다.

## 6. 읽는 방법

```bash
tail -f data/alarms/alarms_2026-10-11_193012.jsonl                 # 실시간 (가장 최근 파일: ls -t data/alarms | head -1)
grep '"code":"ESTOP_FAILED"' data/alarms/*.jsonl                   # 코드로 검색
grep -h '"robot":"pinky2"' data/alarms/*.jsonl | grep '"event":"cleared"'
curl localhost:8000/api/alarms/history/files                       # 파일 목록
curl 'localhost:8000/api/alarms/history?date=2026-10-11&limit=50'  # 최신이 앞
```

```python
import json
rows = []
for path in sorted(Path('data/alarms').glob('alarms_*.jsonl')):
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue          # 쓰다 끊긴 줄
# pandas: pd.read_json(path, lines=True)
```

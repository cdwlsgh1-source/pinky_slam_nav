# 알람 이력 파일 형식

> 역할: 서버가 `data/alarms/` 에 저장하는 알람 이력 파일 한 줄의 구조(필드, 타입, 값 범위)와 읽는 법. 저장 규칙(분할, 날짜, 쓰기 시점)은 `../interfaces.md` 6-7절 "알람 이력 파일", 알람 코드와 레벨은 같은 절의 표를 본다.
> 갱신: 2026-10-11 · 근거 코드: `backend/alarmlog.py` 의 `AlarmHistory.record`, `backend/alarms.py` 의 `AlarmManager`

## 1. 파일

| 항목 | 내용 |
|---|---|
| 형식 | JSON Lines (JSONL): UTF-8 텍스트, **한 줄 = JSON 객체 하나 = 알람 이벤트 하나**. 한글은 `\uXXXX` 로 바꾸지 않고 그대로 쓴다 |
| 이름 | `alarms_YYYY-MM-DD_NNN.jsonl` (날짜는 저장한 날의 서버 PC 현지 날짜, `NNN` 은 그날의 파일 번호) |
| 분량 | 파일 하나에 100줄까지, 차면 다음 번호 |
| 위치 | `data/alarms/` (`--mock` 은 `data/alarms-mock/`). `data/` 는 `.gitignore` 대상이라 저장소에 올라가지 않는다. 형식 설명이 이 문서에 있는 이유다 |

## 2. 필드

모든 줄에 아래 14개 키가 **항상 같은 순서로** 들어 있다 (없는 값은 `null` 또는 빈 문자열).

| 필드 | 타입 | 값 | 의미 |
|---|---|---|---|
| `saved_at` | 문자열 | `"2026-10-11 19:01:17"` | 이 줄을 파일에 쓴 시각 (서버 PC 현지 시각, 사람이 읽는 용) |
| `t` | 실수 | epoch 초 | `saved_at` 과 같은 시각 (계산용) |
| `event` | 문자열 | `raised` \| `repeated` \| `cleared` | 이 줄이 기록하는 사건: 처음 생김 / 같은 알람이 반복됨(`count` 증가) / 해소됨 |
| `id` | 정수 | 1, 2, … | 알람 한 건의 번호. 같은 알람의 `raised`/`repeated`/`cleared` 줄은 `id` 가 같다 |
| `code` | 문자열 | `ESTOP_FAILED` 등 | 알람 종류 (목록: `../interfaces.md` 6-7절) |
| `level` | 문자열 | `HH` \| `H` \| `L` \| `LL` \| `info` | 심각도 (높을수록 앞) |
| `robot` | 문자열 \| `null` | `"pinky2"` | 대상 로봇. 로봇과 무관하면 `null` |
| `text` | 문자열 | | 알람바에 보인 문구 (한영 혼용, `detail` 이 안에 들어 있다) |
| `detail` | 문자열 | | 문구의 `{reason}` 원문 (한국어 오류 원인 그대로). 없으면 `""` |
| `state` | 문자열 | `active` \| `cleared` (`acked` 는 Step A2 예정) | 이 줄을 쓴 시점의 알람 상태 |
| `t_raised` | 실수 | epoch 초 | 알람이 처음 생긴 시각 |
| `t_last` | 실수 | epoch 초 | 마지막으로 발생한 시각 (반복되면 갱신) |
| `t_cleared` | 실수 \| `null` | epoch 초 | 해소된 시각. 해소 전에는 `null` |
| `count` | 정수 | 1 이상 | 같은 `code`+`robot` 의 active 알람이 반복된 누적 횟수 |

## 3. 이벤트 순서

한 알람은 보통 이렇게 줄이 쌓인다 (`id` 가 같다).

| 줄 | `event` | `state` | `count` | `t_cleared` |
|---|---|---|---|---|
| 1 | `raised` | `active` | 1 | `null` |
| 2, 3, … | `repeated` | `active` | 2, 3, … | `null` |
| 마지막 | `cleared` | `cleared` | 마지막 값 그대로 | 해소 시각 |

- `info` 레벨(명령 전송 기록, 로봇 온라인)은 해소 개념이 없어서 **`raised` 한 줄뿐**이고 `state` 가 처음부터 `cleared` 이며 `t_cleared` 는 `t_raised` 와 같은 값이다.
- **해소되지 않은 채 서버가 꺼지면 `cleared` 줄은 없다. 이것은 의도된 동작이다** (PRD 결정 15: 종료 이벤트나 가짜 `cleared` 를 쓰지 않고 해소되지 않은 상태 그대로 둔다). 파일에서는 마지막 줄이 `active` 인 채 끝난다. 그 알람이 지금도 살아 있는지는 파일로 알 수 없고, 서버 메모리의 알람과 `(id, t_raised)` 가 같을 때만 지금 active 이다 (Alarm 화면은 나머지를 `Uncleared` 로 보인다). 마지막 줄의 `t` 는 서버가 꺼진 시각의 상한으로 볼 수 있다.

## 4. 예시

```json
{"saved_at":"2026-10-11 19:01:17","t":1791712877.6030862,"event":"repeated","id":2,"code":"ESTOP_FAILED","level":"HH","robot":"pinky2","text":"E-STOP failed on pinky2: patrol_cmd stop 전송 실패: /pinky2/patrol_cmd 를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다) · cmd_vel 0 속도를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다)","detail":"patrol_cmd stop 전송 실패: /pinky2/patrol_cmd 를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다) · cmd_vel 0 속도를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다)","state":"active","t_raised":1791712750.9648645,"t_last":1791712877.603082,"t_cleared":null,"count":32}
```

읽는 법: pinky2 의 비상정지 실패(`HH`)가 처음 생긴 뒤(`t_raised`) 약 127초 동안 계속 반복되어 이 줄에서 `count` 가 32 가 되었고, 아직 해소되지 않았다(`t_cleared` 가 `null`). 원인은 domain_bridge 가 꺼져 있어 `patrol_cmd` 와 `cmd_vel` 구독자가 없는 것이다.

## 5. 읽을 때 주의할 점

- **시각이 두 종류다.** `saved_at`/`t` 는 줄을 쓴 시각이고, `t_raised`/`t_last`/`t_cleared` 는 알람의 시각이다. `t` 와 `t_last` 가 마이크로초 단위로 조금 다른 것은 서로 다른 시점에 시계를 읽기 때문이며 정상이다.
- **`id` 는 서버를 다시 켜면 1부터 다시 시작한다.** 여러 날이나 여러 번의 실행을 합쳐 분석할 때 `id` 만으로는 같은 알람을 구분할 수 없다. `(id, t_raised)` 쌍을 키로 쓴다.
- **`repeated` 는 반복할 때마다 한 줄씩 쌓인다.** 비상정지를 연타하면 100줄짜리 파일이 금방 찬다. 한 알람의 반복 횟수는 마지막 줄의 `count` 로 알 수 있다.
- **`text` 와 `detail` 이 겹친다.** 검색은 `code` 와 `robot` 으로 하는 편이 간단하다.
- **브라우저 전용 알람은 저장되지 않는다.** 서버 끊김, 지도 불러오기 실패 같은 알람은 서버가 모른다 (화면에서는 `localStorage` 에 남는다).
- 서버가 쓰는 도중 꺼지면 마지막 줄이 깨질 수 있다. 서버의 조회 API 는 깨진 줄을 건너뛰지만, 직접 읽는 스크립트는 `json.loads` 실패를 처리해야 한다.

## 6. 읽는 방법

```bash
tail -f data/alarms/alarms_2026-10-11_001.jsonl                    # 실시간
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

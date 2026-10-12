"""알람 카탈로그: 시스템이 낼 수 있는 모든 알람의 정의와 그 수정.

세 겹이다 (뒤가 앞을 덮는다):
  1. 코드 기본값 (이 파일의 DEFAULTS)
  2. config/alarms.yaml        — 사람이 편집기로 고치는 파일. GUI 는 절대 쓰지 않는다 (Step A4)
  3. config/alarms.local.yaml  — manager 가 GUI 에서 저장한 변경분만 (Step A5). 기본과 다른 필드만 담고, 되돌리기는 그 항목을 지운다.
AlarmManager(alarms.py)는 합친 결과의 레벨·메시지·사용 여부로 알람을 만들고, GET /api/alarms/catalog 는 같은 표를 화면에 보여 준다.
rclpy 와 무관한 순수 모듈이다 (시험은 tests/test_alarmcatalog*.py).

origin: server 는 서버(AlarmManager)가 만들어 모든 화면에 보내는 알람, browser 는 브라우저가 이 화면에서만 만드는 알람이다 (서버가 모르므로 발생 횟수를 셀 수 없다).
  브라우저 알람의 Enable·레벨·소리는 서버가 정책(policy())으로 모든 화면에 알려 브라우저가 적용한다. 문구는 호출부의 동적 문장이라 바꿀 수 없다.
source: 그 알람을 내는 코드 위치 (파일과 함수 이름). 줄 번호는 낡기 쉬워 적지 않는다. 누락 검사 테스트(tests/test_alarmcatalog.py)가 코드와 이 표를 맞춰 본다.
오류 문구는 한국어다 (PRD FRA5-7): 브라우저에 그대로 보이기 때문이다. 화면의 검사(frontend/alarmview.js)와 같은 문구를 쓴다.
"""
import logging
import os
import string
import tempfile
from pathlib import Path

import yaml

from .sounds import ALLOWED_EXT

log = logging.getLogger(__name__)

HH, H, L, LL, INFO = 'HH', 'H', 'L', 'LL', 'info'
LEVELS = (HH, H, L, LL, INFO)   # 심각도 순서 (높을수록 앞). PRD 결정 9

# 비상정지 계열은 어떤 설정으로도 끌 수 없고 레벨(HH)과 소리도 바꿀 수 없다 (PRD 0-7 원칙 3, FRA5-4)
PROTECTED = ('ESTOP_FAILED', 'ESTOP_PARTIAL', 'ESTOP_REQUEST_FAILED')
YAML_FIELDS = ('name', 'condition', 'message', 'level', 'enabled', 'sound', 'sound_file', 'repeat')   # alarms.yaml 에 적을 수 있는 항목
API_FIELDS = ('message', 'level', 'enabled', 'sound', 'sound_file', 'repeat')                          # GUI(PUT)로 바꿀 수 있는 항목. 이름·조건은 yaml 에서만
SERVER_ONLY_EDIT = ('message',)   # 브라우저 알람의 문구는 호출부가 정하는 동적 문장이라 바꿀 수 없다
MESSAGE_MAX = 120
REPEAT_MIN, REPEAT_MAX = 1, 60    # 초. alarmsound.js 의 INTERVAL_MIN/MAX 와 같다
SOUND_FILE_MAX = 200
# 브라우저 알람은 대부분 '한 번 알리고 끝나는 기록'(state=event)이라 ACK 로 끌 수 없어 소리 대상이 아니다 (소리는 ACK 안 된 active 알람만, Step A3). 연결 끊김만 active 상태를 가진다
SOUNDABLE_BROWSER = ('SERVER_DISCONNECTED',)


class CatalogError(ValueError):
    """입력 검증 실패. status 는 REST 응답 코드 (기본 400)."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _e(name, condition, level, message, origin, source):
    return {'name': name, 'condition': condition, 'level': level, 'message': message, 'origin': origin, 'source': source}


# 순서는 화면에 보이는 순서다 (심각한 것부터).
DEFAULTS = {
    'ESTOP_FAILED': _e('비상정지 실패', 'GUI 비상정지에서 patrol_cmd stop 도 cmd_vel 0 도 나가지 못함', HH,
                       '{robot} 비상정지 실패: {reason}', 'server', 'backend/main.py estop → alarms.estop'),
    'ESTOP_PARTIAL': _e('비상정지 일부만 전송', '비상정지에서 patrol_cmd stop 과 cmd_vel 0 중 한쪽만 나감', HH,
                        '{robot} 비상정지가 일부만 전송됨: {reason}', 'server', 'backend/main.py estop → alarms.estop'),
    'ESTOP_REQUEST_FAILED': _e('비상정지 요청 실패', '비상정지 요청을 서버가 처리하지 못함 (401/403 등) 또는 전송 오류', HH,
                               '{who} 비상정지 요청이 처리되지 않았습니다 (HTTP {status})', 'browser', 'frontend/app.js estop'),
    'CMD_SEND_FAILED': _e('명령 전송 실패', '명령(start/stop/goto)을 토픽으로 내보내지 못함 (브리지 꺼짐 등)', H,
                          '{robot} 에 명령 `{cmd}` 를 보내지 못함: {reason}', 'server', 'backend/main.py api_command → alarms.command_send_failed'),
    'CMD_REQUEST_FAILED': _e('명령 요청 실패', '명령 요청을 서버가 처리하지 못함(400/503 이외) 또는 전송 오류', H,
                             '{robot} {cmd} 실패: {reason}', 'browser', 'frontend/app.js sendCommand'),
    'ACK_REQUEST_FAILED': _e('ACK 요청 실패', 'ACK 요청을 서버가 받아들이지 않음 (HTTP 오류) 또는 전송 오류', H,
                            'ACK 요청이 거절되었습니다 (HTTP {status})', 'browser', 'frontend/app.js ackAlarms'),
    'ROBOT_OFFLINE': _e('로봇 오프라인', '마지막 수신 후 online_timeout_sec 이 지나 로봇 online 이 false 가 됨', H,
                        '{robot} 오프라인', 'server', 'backend/main.py state 변화 → alarms.robot_online'),
    'PROC_FAILED': _e('프로세스 실패', '시스템 패널의 프로세스가 FAILED 가 됨 (시작 실패, SSH 연결 실패, 시작 직후 종료, 예기치 않은 종료, 정지 실패)', H,
                      '{resource} 실패: {reason}', 'server', 'backend/main.py procs_changed → alarms.proc_state'),
    'PATROL_FAILED': _e('순찰 실패', 'patrol_status 가 FAILED', H,
                        '{robot} 순찰 실패: {reason}', 'server', 'backend/main.py patrol_status → alarms.patrol'),
    'PATROL_RETRY': _e('순찰 재시도', 'patrol_status 가 RETRY', H,
                       '{robot} 순찰 재시도 중: {reason}', 'server', 'backend/main.py patrol_status → alarms.patrol'),
    'SERVER_DISCONNECTED': _e('서버 연결 끊김', '브라우저의 WebSocket 연결이 끊김', H,
                              '서버 연결 끊김', 'browser', 'frontend/app.js connect'),
    'PROC_REQUEST_FAILED': _e('프로세스 제어 실패', '프로세스 제어(시작/중지) 요청이 거절되거나 전송 오류', H,
                              '{reason}', 'browser', 'frontend/app.js procPost'),
    'CMD_REJECTED': _e('명령 거절', '명령이 허용 목록·검증에서 거절됨 (HTTP 400)', L,
                       '{robot} 에 대한 명령 `{cmd}` 거절: {reason}', 'server', 'backend/main.py api_command → alarms.command_rejected'),
    'CMD_NO_RESPONSE': _e('명령 무응답', '명령 후 no_response_sec 안에 patrol_status 변화가 없음', L,
                          '{robot} 이(가) `{cmd}` 에 응답 없음', 'server', 'backend/main.py 명령 추적기 만료 → alarms.command_no_response'),
    'ROUTE_INVALID': _e('경로 거부', '경로 편집에서 허용되지 않은 지점이거나 최대 지점 수를 넘김', L,
                        '{robot} 는 {point} 로 이동할 수 없습니다 / 경로는 최대 {max}개 지점까지입니다', 'browser', 'frontend/app.js addRoutePoint'),
    'SCAN_PENDING': _e('LiDAR 서버 연결 대기', '서버에 연결되지 않은 상태에서 LiDAR 를 켬', L,
                       '서버에 연결되면 LiDAR 가 켜집니다', 'browser', 'frontend/app.js 스캔 토글'),
    'DRIVE_DENIED': _e('수동 조작 거절', '수동 조작이 서버에서 거절됨', L,
                       '{robot} 수동 조작 거절: {reason}', 'browser', 'frontend/app.js driveDenied'),
    'PATROL_STOPPED': _e('순찰 정지', 'patrol_status 가 STOPPED', LL,
                         '{robot} 순찰 정지됨', 'server', 'backend/main.py patrol_status → alarms.patrol'),
    'LOAD_FAILED': _e('불러오기 실패', '화면이 서버에서 설정·지도·로그인 상태·경로 추정을 받지 못함', LL,
                      '{resource} 을(를) 불러오지 못함', 'browser', 'frontend/app.js boot, ensurePlans, loadMap'),
    'CMD_SENT': _e('명령 전송', '명령(또는 비상정지)이 나감', INFO,
                   '{robot} 에 명령 `{cmd}` 전송', 'server', 'backend/main.py api_command, estop → alarms.command_sent, alarms.estop'),
    'ROBOT_ONLINE': _e('로봇 온라인', '오프라인이던 로봇이 다시 신호를 보냄', INFO,
                       '{robot} 온라인', 'server', 'backend/main.py state 변화 → alarms.robot_online'),
    'SERVER_RECONNECTED': _e('서버 재연결', '끊겼던 WebSocket 연결이 다시 이어짐', INFO,
                             '서버에 다시 연결됨', 'browser', 'frontend/app.js connect'),
}


def _fields_of(template):
    """메시지 템플릿의 자리표시자 이름 목록. 문법이 틀리면 CatalogError."""
    names = []
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        raise CatalogError('메시지의 { } 짝이 맞지 않습니다')
    for _lit, field, spec, conv in parsed:
        if field is None:
            continue
        if field == '' or '.' in field or '[' in field or '!' in field or spec or conv:
            raise CatalogError('{이름} 형태의 자리표시자만 쓸 수 있습니다')
        names.append(field)
    return names


for _code, _e_ in DEFAULTS.items():   # 허용 자리표시자: 그 알람의 기본 문구가 쓰는 것 + {robot}
    _e_['placeholders'] = sorted(set(_fields_of(_e_['message'])) | {'robot'})
    _e_['soundable'] = _e_['origin'] == 'server' or _code in SOUNDABLE_BROWSER   # 소리를 낼 수 있는 종류의 알람인가


def has_sound(level):
    """레벨이 소리를 내는지 (info 는 기록 행이라 소리·ACK 대상이 아니다)."""
    return level != INFO


def default_sound(e):
    """소리 기본값: 소리를 낼 수 있는 종류이고 info 가 아니면 켜짐."""
    return e['soundable'] and has_sound(e['level'])


def check_message(text, allowed):
    if not isinstance(text, str) or not 1 <= len(text) <= MESSAGE_MAX or not text.strip() or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise CatalogError(f'메시지는 줄바꿈 없이 1~{MESSAGE_MAX}글자여야 합니다')
    for name in _fields_of(text):
        if name not in allowed:
            raise CatalogError('알 수 없는 자리표시자 {' + name + '}. 허용: ' + ', '.join('{' + n + '}' for n in allowed))
    return text


def check_sound_file(name, sound_ok=None):
    if not isinstance(name, str) or len(name) > SOUND_FILE_MAX:
        raise CatalogError('알 수 없는 사운드 파일입니다')
    if name == '':
        return ''
    if name != Path(name).name or name.startswith('.') or Path(name).suffix.lower() not in ALLOWED_EXT or (sound_ok is not None and not sound_ok(name)):
        raise CatalogError('알 수 없는 사운드 파일입니다')
    return name


def check_repeat(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not REPEAT_MIN <= v <= REPEAT_MAX:
        raise CatalogError(f'반복 주기는 {REPEAT_MIN}~{REPEAT_MAX}초여야 합니다')
    return v


def check_field(code, key, val, sound_ok=None):
    """항목 하나의 값 검사. 통과한 값을 돌려준다. 알람 간 관계(보호, info 와 소리)는 _check_entry 가 본다."""
    e = DEFAULTS[code]
    if key not in YAML_FIELDS:
        raise CatalogError('알 수 없는 항목: ' + str(key))
    if key in SERVER_ONLY_EDIT and e['origin'] != 'server':
        raise CatalogError('이 알람의 메시지는 바꿀 수 없습니다')
    if key in ('name', 'condition'):
        if not isinstance(val, str) or not val.strip():
            raise CatalogError(f'{key} 는 비어 있지 않은 문자열이어야 합니다')
        return val
    if key == 'message':
        return check_message(val, e['placeholders'])
    if key == 'level':
        if val not in LEVELS:
            raise CatalogError('레벨은 ' + ', '.join(LEVELS) + ' 중 하나여야 합니다')
        return val
    if key in ('enabled', 'sound'):
        if not isinstance(val, bool):
            raise CatalogError(f"{'사용' if key == 'enabled' else '소리'} 값은 true 또는 false 여야 합니다")
        return val
    if key == 'sound_file':
        return check_sound_file(val, sound_ok)
    return check_repeat(val)


def _check_entry(code, e):
    if code in PROTECTED:
        if not e['enabled']:
            raise CatalogError('이 알람은 끌 수 없습니다')
        if e['level'] != DEFAULTS[code]['level']:
            raise CatalogError('이 알람의 레벨은 바꿀 수 없습니다')
        if e['soundable'] and e['sound'] is False:
            raise CatalogError('이 알람의 소리는 끌 수 없습니다')
    if not e['soundable']:
        if e['sound'] is True or e['sound_file'] or e['repeat'] is not None:
            raise CatalogError('이 알람은 한 번 알리고 끝나는 안내라 소리가 없습니다')
        return
    if e['level'] == INFO and e['sound'] is True:
        raise CatalogError('info 알람에는 소리를 켤 수 없습니다')


def effective(overrides=None, sound_ok=None, prefix=True):
    """기본값에 overrides({code: {항목: 값}})를 덮어쓴 카탈로그 {code: 항목}. 잘못된 덮어쓰기는 ValueError(CatalogError, 코드 이름 포함).
    항목: name, condition, level, message, origin, source, placeholders, soundable, enabled, protected, sound(해소된 bool), sound_file, repeat.
    prefix=False 이면 오류 문구 앞에 코드 이름을 붙이지 않는다 (REST 응답용)."""
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise CatalogError('alarms 는 {알람 코드: 설정} 형식이어야 합니다')
    out = {code: dict(e, enabled=True, protected=code in PROTECTED, sound=None, sound_file='', repeat=None) for code, e in DEFAULTS.items()}
    for code, ov in overrides.items():
        if code not in out:
            raise CatalogError(f'알 수 없는 알람 코드: {code}', 404)
        if not isinstance(ov, dict):
            raise CatalogError(f'{code}: 설정은 {{항목: 값}} 형식이어야 합니다')
        for key, val in ov.items():
            try:
                out[code][key] = check_field(code, key, val, sound_ok)
            except CatalogError as e:
                raise CatalogError(f'{code}: {e}' if prefix else str(e), e.status)
    for code, e in out.items():
        try:
            _check_entry(code, e)
        except CatalogError as err:
            raise CatalogError(f'{code}: {err}' if prefix else str(err), err.status)
        e['sound_raw'] = e['sound']
        e['sound'] = e['sound'] if e['sound'] is not None else default_sound(e)
    return out


def _raw_default(code, key):
    return {'message': DEFAULTS[code]['message'], 'level': DEFAULTS[code]['level'], 'enabled': True, 'sound': None, 'sound_file': '', 'repeat': None}[key]


def rows(catalog, stats=None, base=None):
    """화면/API 용 목록. stats({code: {count, last}}) 는 서버가 이번 실행 중 센 값이다. 브라우저 전용 알람은 서버가 모르므로 null.
    base(기본 카탈로그, alarms.yaml 까지만 반영)를 주면 항목마다 defaults 와 modified(GUI 로 바꾼 적 있음)를 붙인다."""
    stats = stats or {}
    out = []
    for code, e in catalog.items():
        s = stats.get(code) if e['origin'] == 'server' else None
        row = {'code': code, 'name': e['name'], 'condition': e['condition'], 'message': e['message'], 'level': e['level'],
               'sound': e['sound'], 'sound_file': e['sound_file'], 'repeat': e['repeat'], 'enabled': e['enabled'],
               'protected': e['protected'], 'origin': e['origin'], 'source': e['source'], 'placeholders': e['placeholders'], 'soundable': e['soundable'],
               'last_raised': s['last'] if s else None,
               'count': (s['count'] if s else 0) if e['origin'] == 'server' else None}
        if base is not None:
            row['defaults'] = {k: base[code][k] for k in API_FIELDS}
            row['modified'] = any(row[k] != base[code][k] for k in API_FIELDS)
        out.append(row)
    return out


def policy_of(catalog):
    """브라우저가 적용할 정책 {code: {enabled, level, sound, sound_file, repeat}}."""
    return {c: {'enabled': e['enabled'], 'level': e['level'], 'sound': e['sound'], 'sound_file': e['sound_file'], 'repeat': e['repeat']} for c, e in catalog.items()}


def _merge_raw(base_raw, local_raw):
    out = {c: dict(v) for c, v in (base_raw or {}).items()}
    for c, v in (local_raw or {}).items():
        out.setdefault(c, {}).update(v)
    return out


class CatalogStore:
    """기본 변경분(alarms.yaml)과 GUI 변경분(alarms.local.yaml)을 합쳐 지금 카탈로그를 들고 있다. 쓰기는 이 클래스만 한다.
    local 파일이 깨졌으면 기본값으로 시작하고 local_error 에 사유를 둔다 (서버를 멈추지 않는다, 파일은 건드리지 않는다)."""

    def __init__(self, base_overrides=None, local_path=None, sound_ok=None):
        self.base_raw = {c: dict(v) for c, v in (base_overrides or {}).items()}
        self.path = Path(local_path) if local_path else None
        self.sound_ok = sound_ok
        self.local_error = ''
        self.base = effective(self.base_raw)
        self.local = self._load()
        self.current = effective(_merge_raw(self.base_raw, self.local))

    def _load(self):
        if self.path is None or not self.path.exists():
            return {}
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = yaml.safe_load(f) or {}
            if not isinstance(raw, dict):
                raise CatalogError('최상위는 매핑이어야 합니다')
            local = raw.get('alarms') or {}
            effective(_merge_raw(self.base_raw, local))   # 검증만 한다 (사운드 파일은 지워졌을 수 있어 존재 확인은 안 한다)
            return {c: dict(v) for c, v in local.items()}
        except (OSError, yaml.YAMLError, CatalogError, AttributeError, TypeError) as e:
            self.local_error = f'{self.path.name}: {e}'
            log.error('알람 카탈로그 변경 파일을 읽지 못해 기본값으로 시작한다: %s', self.local_error)
            return {}

    def _save(self, local):
        if self.path is None:
            return
        text = ('# GUI 가 쓰는 알람 카탈로그 변경분 (Step A5). 직접 고치지 않는다: config/alarms.yaml 의 값과 다른 항목만 들어 있고, 화면의 Reset to default 가 항목을 지운다.\n'
                + yaml.safe_dump({'alarms': local}, allow_unicode=True, sort_keys=True))
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix='.alarms.local.', suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.replace(tmp, self.path)   # 쓰다 끊겨도 옛 파일이 남는다
        except OSError as e:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise CatalogError(f'알람 카탈로그를 저장하지 못했습니다: {e.strerror or e}', 500)

    def _commit(self, code, new_local_entry):
        """code 의 local 항목을 바꿔 저장하고 새 카탈로그를 적용한다. 실패하면 아무것도 바꾸지 않는다. 바뀐 내역(문자열 목록)을 돌려준다."""
        local = {c: dict(v) for c, v in self.local.items()}
        if new_local_entry:
            local[code] = new_local_entry
        else:
            local.pop(code, None)
        cur = effective(_merge_raw(self.base_raw, local), prefix=False)
        self._save(local)
        old = self.current
        self.local, self.current = local, cur
        return [f'{k} {old[code][k]!r}\u2192{cur[code][k]!r}' for k in API_FIELDS if old[code][k] != cur[code][k]]

    def update(self, code, patch):
        """변경분(patch)을 검증해 저장한다. 기본값과 같아진 항목은 local 파일에서 빠진다. CatalogError 는 아무것도 바꾸지 않는다."""
        if code not in DEFAULTS:
            raise CatalogError('알 수 없는 알람 코드입니다', 404)
        if not isinstance(patch, dict) or not patch:
            raise CatalogError('요청 본문은 항목이 하나 이상인 JSON 객체여야 합니다')
        for k in patch:
            if k not in API_FIELDS:
                raise CatalogError('알 수 없는 항목: ' + str(k))
        for k, v in patch.items():
            check_field(code, k, v, self.sound_ok)   # 사운드 파일이 실제로 있는지는 이번에 바꾸는 값만 본다 (다른 알람의 지워진 파일 때문에 막히지 않게)
        merged = dict(self.local.get(code, {}))
        merged.update(patch)
        cand = effective(_merge_raw(self.base_raw, {**self.local, code: merged}), prefix=False)[code]   # 보호 규칙과 항목 간 관계를 한 번에
        keep = {}
        for k in API_FIELDS:
            if k not in merged:
                continue
            v = merged[k]
            base_raw = self.base_raw.get(code, {}).get(k, _raw_default(code, k))
            if k == 'sound' and base_raw is None and v == default_sound(cand):
                continue   # 레벨에서 정해지는 기본값과 같으면 저장하지 않는다 (레벨을 바꾸면 소리 기본값도 따라간다)
            if v != base_raw:
                keep[k] = v
        return self._commit(code, keep)

    def reset(self, code):
        if code not in DEFAULTS:
            raise CatalogError('알 수 없는 알람 코드입니다', 404)
        return self._commit(code, {})

    def rows(self, stats=None):
        return rows(self.current, stats, self.base)

    def policy(self):
        return policy_of(self.current)

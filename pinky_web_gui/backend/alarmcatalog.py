"""알람 카탈로그: 시스템이 낼 수 있는 모든 알람의 정의 (코드 기본값 + config/alarms.yaml 덮어쓰기).

AlarmManager(alarms.py)는 여기의 레벨·메시지·사용 여부로 알람을 만들고, GET /api/alarms/catalog 는 같은 표를 화면에 보여 준다.
이 단계(Step A4)는 읽기 전용이다: GUI 에서 고치는 기능은 없고, 고치려면 config/alarms.yaml 을 편집해 서버를 다시 켠다.
rclpy 와 무관한 순수 모듈이다.

origin: server 는 서버(AlarmManager)가 만들어 모든 화면에 보내는 알람, browser 는 브라우저가 이 화면에서만 만드는 알람이다 (서버가 모르므로 발생 횟수를 셀 수 없다).
source: 그 알람을 내는 코드 위치 (파일과 함수 이름). 줄 번호는 낡기 쉬워 적지 않는다. 누락 검사 테스트(tests/test_alarmcatalog.py)가 코드와 이 표를 맞춰 본다.
"""
HH, H, L, LL, INFO = 'HH', 'H', 'L', 'LL', 'info'
LEVELS = (HH, H, L, LL, INFO)   # 심각도 순서 (높을수록 앞). PRD 결정 9

# 비상정지 계열은 어떤 설정으로도 끌 수 없다
PROTECTED = ('ESTOP_FAILED', 'ESTOP_PARTIAL')
# yaml 로 바꿀 수 있는 항목. 소리 여부(sound)는 레벨에서 정해지며(info 만 소리 없음) 알람별 지정은 Step A5 에서 한다
EDITABLE = ('name', 'condition', 'message', 'level', 'enabled')
# 서버가 만드는 알람만 level / message / enabled 를 따른다 (브라우저 전용 알람은 코드가 직접 레벨과 문구를 정한다)
SERVER_ONLY_EDIT = ('message', 'level', 'enabled')


def _e(name, condition, level, message, origin, source):
    return {'name': name, 'condition': condition, 'level': level, 'message': message, 'origin': origin, 'source': source}


# 순서는 화면에 보이는 순서다 (심각한 것부터).
DEFAULTS = {
    'ESTOP_FAILED': _e('E-STOP failed', 'GUI 비상정지에서 patrol_cmd stop 도 cmd_vel 0 도 나가지 못함', HH,
                       'E-STOP failed on {robot}: {reason}', 'server', 'backend/main.py estop → alarms.estop'),
    'ESTOP_PARTIAL': _e('E-STOP partially sent', '비상정지에서 patrol_cmd stop 과 cmd_vel 0 중 한쪽만 나감', HH,
                        'E-STOP partially sent on {robot}: {reason}', 'server', 'backend/main.py estop → alarms.estop'),
    'ESTOP_REQUEST_FAILED': _e('E-STOP request failed', '비상정지 요청을 서버가 처리하지 못함 (401/403 등) 또는 전송 오류', H,
                               '{who} 비상정지 요청이 처리되지 않았습니다 (HTTP {status})', 'browser', 'frontend/app.js estop'),
    'CMD_SEND_FAILED': _e('Command send failed', '명령(start/stop/goto)을 토픽으로 내보내지 못함 (브리지 꺼짐 등)', H,
                          'Command `{cmd}` could not be sent to {robot}: {reason}', 'server', 'backend/main.py api_command → alarms.command_send_failed'),
    'CMD_REQUEST_FAILED': _e('Command request failed', '명령 요청을 서버가 처리하지 못함(400/503 이외) 또는 전송 오류', H,
                             '{robot} {cmd} 실패: {reason}', 'browser', 'frontend/app.js sendCommand'),
    'ACK_REQUEST_FAILED': _e('ACK request failed', 'ACK 요청을 서버가 받아들이지 않음 (HTTP 오류) 또는 전송 오류', H,
                            'ACK was not accepted (HTTP {status})', 'browser', 'frontend/app.js ackAlarms'),
    'ROBOT_OFFLINE': _e('Robot offline', '마지막 수신 후 online_timeout_sec 이 지나 로봇 online 이 false 가 됨', H,
                        '{robot} is offline', 'server', 'backend/main.py state 변화 → alarms.robot_online'),
    'PATROL_FAILED': _e('Patrol failed', 'patrol_status 가 FAILED', H,
                        'Patrol failed on {robot}: {reason}', 'server', 'backend/main.py patrol_status → alarms.patrol'),
    'PATROL_RETRY': _e('Patrol retrying', 'patrol_status 가 RETRY', H,
                       'Patrol retrying on {robot}: {reason}', 'server', 'backend/main.py patrol_status → alarms.patrol'),
    'SERVER_DISCONNECTED': _e('Server disconnected', '브라우저의 WebSocket 연결이 끊김', H,
                              'Server disconnected', 'browser', 'frontend/app.js connect'),
    'PROC_REQUEST_FAILED': _e('Process control failed', '프로세스 제어(시작/중지) 요청이 거절되거나 전송 오류', H,
                              '{reason}', 'browser', 'frontend/app.js procPost'),
    'CMD_REJECTED': _e('Command rejected', '명령이 허용 목록·검증에서 거절됨 (HTTP 400)', L,
                       'Command `{cmd}` rejected for {robot}: {reason}', 'server', 'backend/main.py api_command → alarms.command_rejected'),
    'CMD_NO_RESPONSE': _e('No response to command', '명령 후 no_response_sec 안에 patrol_status 변화가 없음', L,
                          'No response from {robot} to `{cmd}`', 'server', 'backend/main.py 명령 추적기 만료 → alarms.command_no_response'),
    'ROUTE_INVALID': _e('Route not accepted', '경로 편집에서 허용되지 않은 지점이거나 최대 지점 수를 넘김', L,
                        '{robot} 는 {point} 로 이동할 수 없습니다 / 경로는 최대 {max}개 지점까지입니다', 'browser', 'frontend/app.js addRoutePoint'),
    'SCAN_PENDING': _e('LiDAR waits for server', '서버에 연결되지 않은 상태에서 LiDAR 를 켬', L,
                       '서버에 연결되면 LiDAR 가 켜집니다', 'browser', 'frontend/app.js 스캔 토글'),
    'DRIVE_DENIED': _e('Manual drive denied', '수동 조작이 서버에서 거절됨', L,
                       '{robot} 수동 조작 거절: {reason}', 'browser', 'frontend/app.js driveDenied'),
    'PATROL_STOPPED': _e('Patrol stopped', 'patrol_status 가 STOPPED', LL,
                         'Patrol stopped on {robot}', 'server', 'backend/main.py patrol_status → alarms.patrol'),
    'LOAD_FAILED': _e('Load failed', '화면이 서버에서 설정·지도·로그인 상태·경로 추정을 받지 못함', LL,
                      'Failed to load {resource}', 'browser', 'frontend/app.js boot, ensurePlans, loadMap'),
    'CMD_SENT': _e('Command sent', '명령(또는 비상정지)이 나감', INFO,
                   'Command `{cmd}` sent to {robot}', 'server', 'backend/main.py api_command, estop → alarms.command_sent, alarms.estop'),
    'ROBOT_ONLINE': _e('Robot online', '오프라인이던 로봇이 다시 신호를 보냄', INFO,
                       '{robot} is online', 'server', 'backend/main.py state 변화 → alarms.robot_online'),
    'SERVER_RECONNECTED': _e('Server reconnected', '끊겼던 WebSocket 연결이 다시 이어짐', INFO,
                             'Server reconnected', 'browser', 'frontend/app.js connect'),
}


def has_sound(level):
    """레벨이 소리를 내는지 (info 는 기록 행이라 소리·ACK 대상이 아니다)."""
    return level != INFO


def effective(overrides=None):
    """기본값에 overrides({code: {name|condition|message|level|enabled: 값}})를 덮어쓴 카탈로그 {code: 항목}.
    항목: name, condition, level, message, origin, source, enabled, protected. 잘못된 덮어쓰기는 ValueError."""
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError('alarms 는 {코드: {항목: 값}} 형식이어야 한다')
    out = {code: dict(e, enabled=True, protected=code in PROTECTED) for code, e in DEFAULTS.items()}
    for code, ov in overrides.items():
        if code not in out:
            raise ValueError(f'알 수 없는 알람 코드: {code} (카탈로그에 있는 코드만 적을 수 있다)')
        if not isinstance(ov, dict):
            raise ValueError(f'{code}: 항목은 {{name, condition, message, level, enabled}} 형식이어야 한다')
        e = out[code]
        for key, val in ov.items():
            if key not in EDITABLE:
                hint = ' (소리는 Step A5 에서 알람별로 지정한다)' if key == 'sound' else ''
                raise ValueError(f'{code}: 바꿀 수 없는 항목 {key}{hint}. 가능: {", ".join(EDITABLE)}')
            if key in SERVER_ONLY_EDIT and e['origin'] != 'server':
                raise ValueError(f'{code}: 브라우저 전용 알람의 {key} 는 아직 바꿀 수 없다 (이름과 조건만 가능)')
            if key == 'level' and val not in LEVELS:
                raise ValueError(f'{code}: level 은 {"/".join(LEVELS)} 중 하나여야 한다')
            if key == 'enabled':
                if not isinstance(val, bool):
                    raise ValueError(f'{code}: enabled 는 true/false 여야 한다')
                if val is False and code in PROTECTED:
                    raise ValueError(f'{code}: 비상정지 알람은 끌 수 없다')
            if key in ('name', 'condition', 'message') and (not isinstance(val, str) or not val.strip()):
                raise ValueError(f'{code}: {key} 는 비어 있지 않은 문자열이어야 한다')
            e[key] = val
    return out


def rows(catalog, stats=None):
    """화면/API 용 목록. stats({code: {count, last}}) 는 서버가 이번 실행 중 센 값이다. 브라우저 전용 알람은 서버가 모르므로 null."""
    stats = stats or {}
    out = []
    for code, e in catalog.items():
        s = stats.get(code) if e['origin'] == 'server' else None
        out.append({'code': code, 'name': e['name'], 'condition': e['condition'], 'message': e['message'], 'level': e['level'],
                    'sound': has_sound(e['level']), 'enabled': e['enabled'], 'protected': e['protected'], 'origin': e['origin'],
                    'source': e['source'],
                    'last_raised': s['last'] if s else None,
                    'count': (s['count'] if s else 0) if e['origin'] == 'server' else None})
    return out

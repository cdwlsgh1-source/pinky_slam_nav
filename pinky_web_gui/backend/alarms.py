"""서버 알람 모델. 알람바가 보여 주는 모든 사건(명령 결과, 비상정지, 로봇 온라인 변화, 순찰 상태)을 서버가 만든다.

브라우저마다 따로 만들던 알람을 서버로 옮겨서, 접속한 모든 화면이 같은 알람을 보게 한다 (snapshot.alarms + WS `alarm`).
rclpy 와 무관한 순수 클래스이며 시계는 주입한다 (CommandTracker, StateStore 와 같은 `now=` 관례).
쓰기 함수는 모두 브로드캐스트할 메시지 목록을 돌려준다 (호출자가 `hub.broadcast`).

알람 상태: active(조건 지속) -> cleared(조건 사라짐). acked 는 Step A2 에서 쓰며 여기서는 값만 정의한다.
같은 (code, robot) 의 active 알람이 있으면 새 행을 만들지 않고 count 만 올린다. 해소된 뒤 다시 생기면 새 행이다.
"""
import time

MONITOR_SIZE = 100   # 알람바(모니터링 화면)에 두는 최대 알람 수. 잘려 나간 것도 이력 파일(alarmlog.py)에는 남아 있다

HH, H, L, LL, INFO = 'HH', 'H', 'L', 'LL', 'info'
LEVELS = (HH, H, L, LL, INFO)   # 심각도 순서 (높을수록 앞). PRD 결정 9 로 확정

ACTIVE, CLEARED, ACKED = 'active', 'cleared', 'acked'

# code -> (레벨, 메시지 템플릿). 메시지는 한영 혼용을 허용한다 (PRD 8-2절). 치환 가능한 키는 TEMPLATE_KEYS 뿐이다.
CATALOG = {
    'ESTOP_FAILED': (HH, 'E-STOP failed on {robot}: {reason}'),
    'ESTOP_PARTIAL': (HH, 'E-STOP partially sent on {robot}: {reason}'),
    'CMD_SEND_FAILED': (H, 'Command `{cmd}` could not be sent to {robot}: {reason}'),
    'ROBOT_OFFLINE': (H, '{robot} is offline'),
    'PATROL_FAILED': (H, 'Patrol failed on {robot}: {reason}'),
    'PATROL_RETRY': (H, 'Patrol retrying on {robot}: {reason}'),
    'SERVER_DISCONNECTED': (H, 'Server disconnected'),
    'CMD_REJECTED': (L, 'Command `{cmd}` rejected for {robot}: {reason}'),
    'CMD_NO_RESPONSE': (L, 'No response from {robot} to `{cmd}`'),
    'PATROL_STOPPED': (LL, 'Patrol stopped on {robot}'),
    'LOAD_FAILED': (LL, 'Failed to load {resource}'),
    'CMD_SENT': (INFO, 'Command `{cmd}` sent to {robot}'),
    'ROBOT_ONLINE': (INFO, '{robot} is online'),
}
TEMPLATE_KEYS = ('robot', 'cmd', 'reason', 'resource')

# 서버가 알 수 없어 브라우저가 직접 만드는 알람 (서버는 발생시키지 않는다)
BROWSER_ONLY = ('SERVER_DISCONNECTED', 'LOAD_FAILED')
# 비상정지 계열은 어떤 설정으로도 끌 수 없다 (Step A5 의 Enable/Disable 이 이 값을 보고 거부한다)
PROTECTED = ('ESTOP_FAILED', 'ESTOP_PARTIAL')

PATROL_CODES = {'FAILED': 'PATROL_FAILED', 'RETRY': 'PATROL_RETRY', 'STOPPED': 'PATROL_STOPPED'}
COMMAND_FAIL_CODES = ('CMD_SEND_FAILED', 'CMD_REJECTED', 'CMD_NO_RESPONSE')   # 로봇이 응답하거나 새 명령이 나가면 해소
ESTOP_CODES = ('ESTOP_FAILED', 'ESTOP_PARTIAL')


class _Fmt(dict):
    def __missing__(self, key):
        return ''


def is_protected(code):
    return code in PROTECTED


def command_text(cmd, points=None):
    """알람 문구에 쓰는 명령 표기. goto 는 지점을 붙인다 (예: 'goto P2 P3')."""
    cmd = str(cmd) if cmd is not None else '?'
    if points:
        return f'{cmd} ' + ' '.join(str(p) for p in points)
    return cmd


class AlarmManager:
    def __init__(self, now=time.time, max_alarms=MONITOR_SIZE, sink=None):
        """sink(event, alarm): 알람 이벤트(raised | repeated | cleared)마다 부른다 (이력 파일 저장용). 예외는 알람 동작에 영향을 주지 않는다."""
        self._now = now
        self._sink = sink
        self._max = max(1, int(max_alarms))
        self._alarms = []     # 오래된 것이 앞. 새 알람은 뒤에 붙는다
        self._next_id = 1

    # ---- 기본 동작 ----
    def raise_(self, code, robot=None, **fmt):
        """알람을 만들거나(없으면) count 를 올린다(active 가 이미 있으면). 메시지 목록을 돌려준다."""
        if code not in CATALOG:
            raise ValueError(f'알 수 없는 알람 코드: {code}')
        level, template = CATALOG[code]
        keys = _Fmt({k: ('' if v is None else str(v)) for k, v in fmt.items() if k in TEMPLATE_KEYS})
        keys['robot'] = robot or ''
        text = template.format_map(keys).rstrip().rstrip(':').rstrip()
        reason = keys['reason']
        t = self._now()
        if level != INFO:
            for a in self._alarms:
                if a['code'] == code and a['robot'] == robot and a['state'] == ACTIVE:
                    a['count'] += 1
                    a['t_last'] = t
                    a['text'], a['detail'] = text, reason
                    self._emit('repeated', a)
                    return [self._msg(a)]
        a = {'id': self._next_id, 'code': code, 'level': level, 'robot': robot, 'text': text, 'detail': reason,
             'state': CLEARED if level == INFO else ACTIVE,   # info 는 해소 개념이 없는 기록 행이다
             't_raised': t, 't_last': t, 't_cleared': t if level == INFO else None, 'count': 1}
        self._next_id += 1
        self._alarms.append(a)
        self._emit('raised', a)
        self._trim()
        return [self._msg(a)]

    def clear(self, code, robot=None):
        """(code, robot) 의 active 알람을 해소한다. 없으면 빈 목록."""
        t = self._now()
        msgs = []
        for a in self._alarms:
            if a['code'] == code and a['robot'] == robot and a['state'] == ACTIVE:
                a['state'], a['t_cleared'] = CLEARED, t
                self._emit('cleared', a)
                msgs.append(self._msg(a))
        return msgs

    def clear_many(self, codes, robot=None):
        msgs = []
        for code in codes:
            msgs += self.clear(code, robot)
        return msgs

    def snapshot(self):
        """새 화면에 보낼 전체 알람 (최신이 앞)."""
        return [dict(a) for a in reversed(self._alarms)]

    def _emit(self, event, a):
        if self._sink is None:
            return
        try:
            self._sink(event, dict(a))
        except Exception:   # 이력 저장이 알람 표시를 막으면 안 된다
            pass

    def _msg(self, a):
        return {'type': 'alarm', 'alarm': dict(a)}

    def _trim(self):
        """상한을 넘으면 해소된 것 중 가장 오래된 것부터, 그래도 넘으면 가장 오래된 것부터 버린다 (active 는 최대한 남긴다)."""
        while len(self._alarms) > self._max:
            idx = next((i for i, a in enumerate(self._alarms) if a['state'] != ACTIVE), 0)
            del self._alarms[idx]

    # ---- 이벤트 -> 알람 (main.py 가 부른다) ----
    def robot_online(self, robot, online):
        if online:
            return self.clear('ROBOT_OFFLINE', robot) + self.raise_('ROBOT_ONLINE', robot)
        return self.raise_('ROBOT_OFFLINE', robot)

    def patrol(self, robot, state, detail=''):
        """patrol_status 의 상태. FAILED/RETRY/STOPPED 만 알람이고, 그 밖의 상태로 바뀌면 순찰 알람을 해소한다."""
        code = PATROL_CODES.get(state)
        msgs = self.clear_many([c for c in PATROL_CODES.values() if c != code], robot)
        if code:
            msgs += self.raise_(code, robot, reason=detail)
        return msgs

    def command_sent(self, robot, cmd, points=None):
        """명령이 나갔다. 이전 전송 실패/거절/무응답 알람은 새 명령이 나갔으므로 해소한다."""
        return self.clear_many(COMMAND_FAIL_CODES, robot) + self.raise_('CMD_SENT', robot, cmd=command_text(cmd, points))

    def command_rejected(self, robot, cmd, points, reason):
        return self.raise_('CMD_REJECTED', robot, cmd=command_text(cmd, points), reason=reason)

    def command_send_failed(self, robot, cmd, points, reason):
        return self.raise_('CMD_SEND_FAILED', robot, cmd=command_text(cmd, points), reason=reason)

    def command_no_response(self, robot, cmd, points=None):
        return self.raise_('CMD_NO_RESPONSE', robot, cmd=command_text(cmd, points))

    def command_acknowledged(self, robot):
        """로봇이 명령에 맞는 patrol 상태를 냈다 (살아서 응답한다)."""
        return self.clear_many(('CMD_NO_RESPONSE', 'CMD_REJECTED'), robot)

    def estop(self, robot, patrol_ok, vel_ok, reason=''):
        """비상정지 결과. 둘 다 나갔으면 기록(info) 만, 한쪽만 나갔으면 PARTIAL, 둘 다 못 나갔으면 FAILED."""
        if patrol_ok and vel_ok:
            return self.clear_many(ESTOP_CODES, robot) + self.raise_('CMD_SENT', robot, cmd='estop')
        if patrol_ok or vel_ok:
            return self.clear('ESTOP_FAILED', robot) + self.raise_('ESTOP_PARTIAL', robot, reason=reason)
        return self.clear('ESTOP_PARTIAL', robot) + self.raise_('ESTOP_FAILED', robot, reason=reason)

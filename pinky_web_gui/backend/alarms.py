"""서버 알람 모델. 알람바가 보여 주는 모든 사건(명령 결과, 비상정지, 로봇 온라인 변화, 순찰 상태)을 서버가 만든다.

브라우저마다 따로 만들던 알람을 서버로 옮겨서, 접속한 모든 화면이 같은 알람을 보게 한다 (snapshot.alarms + WS `alarm`).
rclpy 와 무관한 순수 클래스이며 시계는 주입한다 (CommandTracker, StateStore 와 같은 `now=` 관례).
쓰기 함수는 모두 브로드캐스트할 메시지 목록을 돌려준다 (호출자가 `hub.broadcast`).

알람 상태: active(조건 지속) -> cleared(조건 사라짐). ACK 는 상태를 바꾸지 않고 acked_by/acked_at 만 채운다.
  해소된 알람을 ACK 하거나, ACK 한 활성 알람이 해소되면 알람바에서 숨긴다 (메모리와 이력 파일에는 남는다).

반복의 정의 (PRD 결정 19): 같은 (code, robot) 의 알람이 아직 active 이고 ACK 되지 않았으면 새 행을 만들지 않고 count 만 올린다(×N).
  ACK 했거나 해소된 뒤에 또 생기면 새 행이다. 화면에서는 묶지만 **이력 파일에는 발생 하나하나를 시간 순서로 모두 남긴다**
  (raised / repeated 줄마다 그 시도의 시각·문구·count). 운영자가 언제 어떤 조작을 했는지로 문제를 추적하려면 중간 시도가 빠지면 안 된다.
"""
import time

from .alarmcatalog import DEFAULTS, HH, H, INFO, L, LEVELS, LL, PROTECTED, effective

MONITOR_SIZE = 100   # 알람바(모니터링 화면)에 두는 최대 알람 수. 잘려 나간 것도 이력 파일(alarmlog.py)에는 남아 있다

ACTIVE, CLEARED = 'active', 'cleared'

# code -> (레벨, 메시지 템플릿): 코드 기본값. 정의 전체(이름, 발생 조건, 근거 코드 …)와 config/alarms.yaml 덮어쓰기는 alarmcatalog.py.
# 메시지는 한영 혼용을 허용한다 (PRD 8-2절). 치환 가능한 키는 TEMPLATE_KEYS 뿐이다.
CATALOG = {code: (e['level'], e['message']) for code, e in DEFAULTS.items()}
TEMPLATE_KEYS = ('robot', 'cmd', 'reason', 'resource')

# 서버가 알 수 없어 브라우저가 직접 만드는 알람 (서버는 발생시키지 않는다)
BROWSER_ONLY = tuple(code for code, e in DEFAULTS.items() if e['origin'] == 'browser')


PATROL_CODES = {'FAILED': 'PATROL_FAILED', 'RETRY': 'PATROL_RETRY', 'STOPPED': 'PATROL_STOPPED'}
COMMAND_FAIL_CODES = ('CMD_SEND_FAILED', 'CMD_REJECTED', 'CMD_NO_RESPONSE')   # 로봇이 응답하거나 새 명령이 나가면 해소
ESTOP_CODES = ('ESTOP_FAILED', 'ESTOP_PARTIAL')


class _Fmt(dict):
    def __missing__(self, key):
        return ''


def is_protected(code):
    return code in PROTECTED


def is_hidden(a):
    """알람바에서 숨기는 알람: ACK 를 받은 해소 알람. info 행은 ACK 대상이 아니라 숨기지 않는다."""
    return a['state'] == CLEARED and a['acked_by'] is not None and a['level'] != INFO


def command_text(cmd, points=None):
    """알람 문구에 쓰는 명령 표기. goto 는 지점을 붙인다 (예: 'goto P2 P3')."""
    cmd = str(cmd) if cmd is not None else '?'
    if points:
        return f'{cmd} ' + ' '.join(str(p) for p in points)
    return cmd


class AlarmManager:
    def __init__(self, now=time.time, max_alarms=MONITOR_SIZE, sink=None, catalog=None):
        """sink(event, alarm): 알람 이벤트(raised | repeated | acked | cleared)마다 부른다 (이력 파일 저장용). 예외는 알람 동작에 영향을 주지 않는다.
        catalog: alarmcatalog.effective() 결과 (config/alarms.yaml 을 반영한 레벨·메시지·사용 여부). 없으면 코드 기본값."""
        self._now = now
        self.catalog = catalog if catalog is not None else effective()
        self._stats = {}      # code -> {'count': 이번 서버 실행 중 발생 횟수(반복 포함), 'last': 마지막 발생 시각}. 카탈로그 화면용

        self._sink = sink
        self._max = max(1, int(max_alarms))
        self._alarms = []     # 오래된 것이 앞. 새 알람은 뒤에 붙는다
        self._next_id = 1

    # ---- 기본 동작 ----
    def raise_(self, code, robot=None, **fmt):
        """알람 행을 만들거나, ACK 전의 같은 알람이 active 이면 count 를 올린다. 반복도 이력에는 매번 한 줄 남긴다. 메시지 목록을 돌려준다."""
        entry = self.catalog.get(code)
        if entry is None:
            raise ValueError(f'알 수 없는 알람 코드: {code}')
        if not entry['enabled']:   # 카탈로그에서 끈 알람은 만들지 않는다 (config/alarms.yaml enabled: false)
            return []
        level, template = entry['level'], entry['message']
        keys = _Fmt({k: ('' if v is None else str(v)) for k, v in fmt.items() if k in TEMPLATE_KEYS})
        keys['robot'] = robot or ''
        text = template.format_map(keys).rstrip().rstrip(':').rstrip()
        reason = keys['reason']
        t = self._now()
        st = self._stats.setdefault(code, {'count': 0, 'last': None})
        st['count'] += 1
        st['last'] = t
        for a in self._alarms:
            if a['code'] == code and a['robot'] == robot and a['state'] == ACTIVE:
                if a['acked_by'] is None:   # ACK 전의 같은 알람은 묶는다 (ACK 했다면 새 확인이 필요하므로 아래에서 새 행)
                    a['count'] += 1
                    a['t_last'] = t
                    a['text'], a['detail'] = text, reason
                    self._emit('repeated', a)
                    return [self._msg(a)]
        a = {'id': self._next_id, 'code': code, 'level': level, 'robot': robot, 'text': text, 'detail': reason,
             'state': CLEARED if level == INFO else ACTIVE,   # info 는 해소 개념이 없는 기록 행이다
             't_raised': t, 't_last': t, 't_cleared': t if level == INFO else None, 'count': 1, 'acked_by': None, 'acked_at': None}
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

    def ack(self, ids=None, user='', t=None):
        """ACK: ids(None 이면 전체)에서 info 가 아니고 아직 ACK 안 된 알람에 acked_by/acked_at 을 기록한다.
        활성 알람은 그대로 보이고 해소 알람은 숨겨진다. 바뀐 알람의 메시지 목록을 돌려준다 (이미 ACK 했거나 없는 id 는 건너뛴다)."""
        t = self._now() if t is None else t
        want = None if ids is None else set(ids)
        msgs = []
        for a in self._alarms:
            if (want is None or a['id'] in want) and a['level'] != INFO and a['acked_by'] is None:
                a['acked_by'], a['acked_at'] = user, t
                self._emit('acked', a)
                msgs.append(self._msg(a))
        return msgs

    def set_catalog(self, catalog):
        """카탈로그를 바꾼다 (Step A5, manager 가 저장하거나 되돌렸을 때). 이미 떠 있는 알람의 레벨·문구는 그대로이고 이후에 생기는 알람부터 새 값을 쓴다.
        새로 꺼진 코드의 active 알람은 해소해서 소리를 멈춘다. 해소된 알람의 메시지 목록을 돌려준다."""
        newly_off = [c for c, e in catalog.items() if not e['enabled'] and self.catalog.get(c, {}).get('enabled', True)]
        self.catalog = catalog
        msgs = []
        for code in newly_off:
            for a in self._alarms:
                if a['code'] == code and a['state'] == ACTIVE:
                    a['state'], a['t_cleared'] = CLEARED, self._now()
                    self._emit('cleared', a)
                    msgs.append(self._msg(a))
        return msgs

    def stats(self):
        """코드별 {count, last} (이번 서버 실행 중). 서버를 다시 켜면 0 부터 센다 (이력 파일에는 모든 발생이 남아 있다)."""
        return {c: dict(v) for c, v in self._stats.items()}

    def snapshot(self):
        """새 화면에 보낼 알람 (최신이 앞). ACK 로 숨긴 알람은 뺀다."""
        return [dict(a) for a in reversed(self._alarms) if not is_hidden(a)]

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
        """상한을 넘으면 숨긴 것, 해소된 것 순으로 가장 오래된 것부터, 그래도 넘으면 가장 오래된 것부터 버린다 (active 는 최대한 남긴다)."""
        while len(self._alarms) > self._max:
            idx = next((i for i, a in enumerate(self._alarms) if is_hidden(a)), None)
            if idx is None:
                idx = next((i for i, a in enumerate(self._alarms) if a['state'] != ACTIVE), 0)
            del self._alarms[idx]

    # ---- 이벤트 -> 알람 (main.py 가 부른다) ----
    def robot_online(self, robot, online):
        if online:
            return self.clear('ROBOT_OFFLINE', robot) + self.raise_('ROBOT_ONLINE', robot)
        return self.raise_('ROBOT_OFFLINE', robot)

    def proc_state(self, pid, label, state, message=''):
        """시스템 패널 프로세스의 상태 (알람 키는 프로세스 id). FAILED 가 되면 알람이고, 다시 시작하거나 정지·종료되면 해소한다."""
        if state == 'failed':
            return self.raise_('PROC_FAILED', pid, resource=label, reason=message)
        return self.clear('PROC_FAILED', pid)

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

"""로봇으로 나가는 명령의 검증과 이력 관리. rclpy 에 의존하지 않고, 시계는 주입받는다 (테스트용).

보안/안전 경계는 여기다. 로봇 노드도 goto 를 검증하지만 노드의 거절은 FAILED 상태로만 알려지고, 작업 중에
보내면 진행 중인 작업의 상태처럼 보이는 FAILED 가 발행된다 (CLAUDE.md 7절). 그래서 허용 목록 밖의 명령은 아예 발행하지 않는다.
"""
import time
from collections import deque

from .config import is_door

ALLOWED_CMDS = ('start', 'stop', 'goto')

# 명령마다 "받아들였다" 고 볼 수 있는 patrol_status 상태. 노드는 start/goto 를 받으면 곧바로 STARTING 을 발행하고,
# stop 은 작업을 끊으면서 STOPPED 를 발행한다. 작업 중인 로봇은 무시한 명령과 상관없이 MOVING 등으로 계속 상태가 바뀌므로
# "아무 상태 변화" 를 응답으로 보면 무시된 start 가 응답한 것으로 잘못 표시된다.
ACK_STATES = {'start': ('STARTING',), 'goto': ('STARTING',), 'stop': ('STOPPED',), 'estop': ('STOPPED',)}

# 비상정지 이력의 detail (patrol_cmd stop 에 로봇이 응답하지 않은 경우). 작업 중이 아니던 로봇은 stop 에 응답하지 않는 것이 정상이라
# '응답 없음' 오류로 표시하지 않고, 0 속도는 따로 보냈다는 사실을 알려 준다.
ESTOP_NO_STOPPED = '로봇이 이미 멈춰 있었거나 stop 이 반영되지 않았을 수 있음 (cmd_vel 0 속도는 전송됨)'

# 이력의 결과 값
SENT = 'sent'                    # 토픽에 발행함 (로봇이 받았다는 뜻은 아니다)
ACKNOWLEDGED = 'acknowledged'    # 발행 뒤 해당 로봇의 patrol_status 가 바뀜
NO_RESPONSE = 'no_response'      # 제한 시간 안에 patrol_status 가 바뀌지 않음 (무시됐거나 반영 전)
REJECTED = 'rejected'            # 백엔드 검증에서 거절 (발행 안 함)
FAILED = 'failed'                # 검증은 통과했지만 발행하지 못함 (예: 브리지가 꺼져 구독자 없음)


class CommandRejected(Exception):
    """허용 목록 검증 실패. 메시지는 사용자에게 그대로 보여 준다."""


class CommandUnavailable(Exception):
    """발행할 수 없는 상태 (구독자 없음, ROS 미초기화 등)."""


def validate(settings, max_points, body):
    """body 를 검증해서 (cmd, points, 발행 문자열) 을 돌려준다. settings 는 로봇별 RobotSettings.

    goto 의 각 지점은 strip + 대문자로 바꾼 뒤 로봇별 허용 집합과 **정확히 일치**해야 한다.
    그래서 "P2,RED1IN", "P2 P3" 처럼 한 항목에 구분자를 섞어 노드의 파서를 통과시키려는 입력은 전부 거절된다.
    """
    if not isinstance(body, dict):
        raise CommandRejected('요청 본문은 JSON 객체여야 합니다')
    cmd = body.get('cmd')
    if not isinstance(cmd, str) or cmd not in ALLOWED_CMDS:
        raise CommandRejected(f'알 수 없는 명령입니다 (허용: {", ".join(ALLOWED_CMDS)})')
    points = body.get('points')

    if cmd in ('start', 'stop'):
        if points not in (None, []):
            raise CommandRejected(f'{cmd} 명령에는 points 를 줄 수 없습니다')
        return cmd, [], cmd

    if not isinstance(points, list) or not points:
        raise CommandRejected('goto 에는 지점이 1개 이상 필요합니다')
    if len(points) > max_points:
        raise CommandRejected(f'지점은 최대 {max_points}개까지입니다 ({len(points)}개 받음)')
    allowed = set(settings.goto_allowed)
    names = []
    for p in points:
        if not isinstance(p, str):
            raise CommandRejected('지점 이름은 문자열이어야 합니다')
        name = p.strip().upper()
        if is_door(name):  # 설정이 잘못돼도 구역 문은 보내지 않는다
            raise CommandRejected(f'{name} 은 구역 문이라 직접 이동할 수 없습니다 (문 경유는 로봇이 자동으로 처리합니다)')
        if name not in allowed:
            raise CommandRejected(f'허용되지 않은 지점입니다: {p!r} (허용: {", ".join(settings.goto_allowed)})')
        names.append(name)
    return 'goto', names, 'goto:' + ','.join(names)


class CommandTracker:
    """명령 이력(최근 N건)과 로봇별 마지막 명령, 응답 여부 추적.

    StateStore 와 같은 스레드 모델이다: 이벤트 루프 스레드에서만 호출하므로 락이 없다.
    반환값은 WebSocket 으로 내보낼 메시지 목록이다.
    """

    def __init__(self, robots, history_size, no_response_sec, now=time.time):
        self._robots = tuple(robots)
        self._no_response = no_response_sec
        self._now = now
        self._history = deque(maxlen=history_size)
        self._next_id = 1
        self._last = {rid: None for rid in self._robots}      # 로봇별 마지막으로 '발행한' 명령 entry
        self._last_task = {rid: None for rid in self._robots}  # 그중 start/goto 만 (진행 상황을 해석하는 기준, stop 은 덮어쓰지 않는다)
        self._pending = {rid: None for rid in self._robots}   # 응답 대기 중인 entry (로봇당 1개)

    # ---- 기록 ----
    def _new_entry(self, robot, cmd, points, sent, result, detail):
        e = {'id': self._next_id, 'time': self._now(), 'robot': robot, 'cmd': cmd,
             'points': list(points), 'sent': sent, 'result': result, 'detail': detail}
        self._next_id += 1
        self._history.append(e)
        return e

    def sent(self, robot, cmd, points, sent_str):
        """발행에 성공했다. 응답 대기를 시작하고 이 명령을 로봇의 마지막 명령으로 둔다."""
        e = self._new_entry(robot, cmd, points, sent_str, SENT, '')
        self._pending[robot] = e  # 이전 명령은 더 이상 추적하지 않는다
        self._last[robot] = e
        if cmd in ('start', 'goto'):
            self._last_task[robot] = e
        return e, self._entry_msgs(e)

    def rejected(self, robot, cmd, points, reason, result=REJECTED):
        """검증에서 거절됐거나(REJECTED) 발행하지 못했다(FAILED). 로봇의 마지막 명령은 바뀌지 않는다."""
        safe_cmd = cmd if isinstance(cmd, str) and len(cmd) <= 16 else '?'
        safe_points = [p if isinstance(p, str) and len(p) <= 16 else '?' for p in points][:20] \
            if isinstance(points, list) else []
        e = self._new_entry(robot, safe_cmd, safe_points, '', result, reason)
        return e, [{'type': 'command', 'entry': dict(e)}]

    def estop(self, robot, sent_str, patrol_ok, vel_ok, detail=''):
        """비상정지를 기록한다 (patrol_cmd stop 과 cmd_vel 0 속도 burst). 둘 다 못 보냈으면 FAILED, 하나라도 나갔으면 SENT.

        patrol_ok 면 로봇의 STOPPED 를 기다려 '응답 확인' 으로 바꾼다. 응답이 없어도 NO_RESPONSE 로 바꾸지 않는다.
        """
        result = SENT if (patrol_ok or vel_ok) else FAILED
        e = self._new_entry(robot, 'estop', [], sent_str, result, detail)
        self._pending[robot] = e if patrol_ok else None
        self._last[robot] = e
        return e, self._entry_msgs(e)

    def _entry_msgs(self, e):
        out = [{'type': 'command', 'entry': dict(e)}]
        for field, table in (('last_command', self._last), ('last_task', self._last_task)):
            if table.get(e['robot']) is e:
                out.append({'type': 'robot_update', 'robot': e['robot'], 'field': field, 'data': dict(e)})
        return out

    # ---- 응답 추적 ----
    def on_patrol(self, robot, state):
        """해당 로봇의 patrol_status 가 바뀌었다. 대기 중인 명령이 기대하는 상태(ACK_STATES)면 응답한 것으로 본다."""
        e = self._pending.get(robot)
        if e is None or state not in ACK_STATES.get(e['cmd'], ()):
            return []
        self._pending[robot] = None
        e['result'] = ACKNOWLEDGED
        return self._entry_msgs(e)

    def tick(self):
        out = []
        now = self._now()
        for rid, e in self._pending.items():
            if e is not None and now - e['time'] >= self._no_response:
                self._pending[rid] = None
                if e['cmd'] == 'estop':
                    e['detail'] = (e['detail'] + ' · ' if e['detail'] else '') + ESTOP_NO_STOPPED
                else:
                    e['result'] = NO_RESPONSE
                out += self._entry_msgs(e)
        return out

    # ---- 조회 ----
    def history(self):
        return [dict(e) for e in self._history]

    def last_command(self, robot):
        e = self._last.get(robot)
        return dict(e) if e else None

    def last_task(self, robot):
        e = self._last_task.get(robot)
        return dict(e) if e else None

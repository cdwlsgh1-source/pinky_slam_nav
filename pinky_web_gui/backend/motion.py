"""/{id}/cmd_vel 발행을 한곳에서 관리한다: 비상정지 0 속도 burst와 수동 조작(데드맨). rclpy 에 의존하지 않는다.

이벤트 루프 스레드에서만 호출한다 (StateStore 와 같은 모델이라 락이 없다). 시계와 발행 함수는 주입받는다.

원칙: 이 파일의 어떤 경로든 '입력이 끊기면 0 속도' 로 끝난다.
  - 비상정지: 즉시 0 속도 1회, 이어서 estop_burst_hz 로 estop_burst_sec 동안 반복 (FR4-8).
  - 수동 조작: 입력이 manual_timeout_sec 동안 없으면, 클라이언트 연결이 끊기면, 순찰이 시작되면, 오프라인이 되면 즉시 0 속도.
  - 비상정지 중에는 수동 조작 입력을 무시한다 (정지가 이긴다).
"""
import logging
import math
import time

from .config import ANGULAR_HARD_MAX, LINEAR_HARD_MAX

log = logging.getLogger('backend.motion')

# 작업 중 상태 (순찰 노드의 _running 이 True 인 동안). 이때는 수동 조작을 받지 않는다 (FR4-9).
WORKING = ('STARTING', 'MOVING', 'WAITING_ZONE', 'ARRIVED', 'LEAVING_ZONE', 'RETURNING', 'RETRY')
TAIL_SEC = 0.3  # 수동 조작이 끝난 뒤 0 속도를 이만큼 더 반복한다 (패킷 하나가 유실돼도 멈추도록)


class Motion:
    def __init__(self, robots, settings, publish, patrol_state, is_online, now=time.monotonic):
        """publish(rid, linear, angular) -> bool. 실패하면 예외 또는 False. patrol_state(rid) 는 마지막 state 문자열 또는 None."""
        self._robots = tuple(robots)
        self._s = settings
        self._publish = publish
        self._patrol_state = patrol_state
        self._is_online = is_online
        self._now = now
        self._lin_max = min(settings.manual_max_linear, LINEAR_HARD_MAX)
        self._ang_max = min(settings.manual_max_angular, ANGULAR_HARD_MAX)
        self._burst = {}   # rid -> {'next', 'end'}: 0 속도 반복 (비상정지 burst 또는 수동 조작 종료 꼬리)
        self._estop = {}   # rid -> 비상정지 burst 가 끝나는 시각
        self._drive = {}   # rid -> {'client', 'lin', 'ang', 'last', 'next'}

    # ---- 발행 ----
    def _send(self, rid, lin, ang):
        try:
            return bool(self._publish(rid, lin, ang))
        except Exception as e:  # 발행 실패가 tick 을 죽이면 안 된다 (다음 주기에 다시 시도한다)
            log.error('%s cmd_vel 발행 실패: %s', rid, e)
            return False

    # ---- 비상정지 ----
    def estop(self, rid):
        """0 속도를 즉시 보내고 burst 를 시작(이미 진행 중이면 연장)한다. 반환: 첫 0 속도를 보냈는지."""
        now = self._now()
        self._drive.pop(rid, None)  # 수동 조작은 즉시 끊는다
        ok = self._send(rid, 0.0, 0.0)
        end = now + self._s.estop_burst_sec
        self._estop[rid] = end
        self._burst[rid] = {'next': now + 1.0 / self._s.estop_burst_hz, 'end': end}
        log.warning('%s 비상정지: cmd_vel 0 속도 %.1f초 burst 시작', rid, self._s.estop_burst_sec)
        return ok

    def estop_active(self, rid):
        return self._estop.get(rid, 0) > self._now()

    # ---- 수동 조작 ----
    def drive(self, client, rid, linear, angular):
        """수동 조작 입력 하나. 반환 (받아들였는지, 거절 사유). 입력은 manual_rate_hz 로 계속 와야 유지된다."""
        s = self._s
        if not s.manual_enabled:
            return False, '수동 조작이 설정에서 꺼져 있습니다'
        if rid not in self._robots:
            return False, '알 수 없는 로봇입니다'
        try:
            lin, ang = float(linear), float(angular)
        except (TypeError, ValueError):
            return False, '속도 값이 숫자가 아닙니다'
        if not (math.isfinite(lin) and math.isfinite(ang)):
            return False, '속도 값이 유한하지 않습니다'
        now = self._now()
        if self.estop_active(rid):
            return False, '비상정지 중입니다'
        if not self._is_online(rid):
            return False, '로봇이 오프라인입니다'
        if self._patrol_state(rid) in WORKING:
            return False, '순찰 중에는 수동 조작을 할 수 없습니다'
        cur = self._drive.get(rid)
        if cur and cur['client'] != client and now - cur['last'] <= s.manual_timeout_sec:
            return False, '다른 화면에서 조작 중입니다'
        lin = max(-self._lin_max, min(self._lin_max, lin))
        ang = max(-self._ang_max, min(self._ang_max, ang))
        if lin == 0.0 and ang == 0.0:  # 0 입력은 조작을 끝내는 것과 같다
            self._end_drive(rid)
            return True, None
        first = cur is None or cur['client'] != client
        self._drive[rid] = {'client': client, 'lin': lin, 'ang': ang, 'last': now,
                            'next': now if first else cur['next']}
        if first:  # 첫 입력은 다음 tick 을 기다리지 않고 바로 보낸다
            self._send(rid, lin, ang)
            self._drive[rid]['next'] = now + 1.0 / s.manual_rate_hz
        return True, None

    def _end_drive(self, rid):
        """수동 조작을 끝낸다: 즉시 0 속도, 그리고 짧게 더 반복."""
        if self._drive.pop(rid, None) is None:
            return
        now = self._now()
        self._send(rid, 0.0, 0.0)
        self._burst[rid] = {'next': now + 0.1, 'end': now + TAIL_SEC}

    def release(self, client):
        """클라이언트가 조작을 놓았거나 연결이 끊겼다. 그 클라이언트가 조작 중이던 로봇을 즉시 멈춘다."""
        for rid in [r for r, d in self._drive.items() if d['client'] == client]:
            log.info('%s 수동 조작 종료 (클라이언트 해제)', rid)
            self._end_drive(rid)

    # ---- 주기 작업 (약 20 Hz 로 호출) ----
    def tick(self):
        now = self._now()
        s = self._s
        # 수동 조작: 입력 없음 / 순찰 시작 / 오프라인 -> 즉시 0 속도. 아니면 마지막 입력을 manual_rate_hz 로 반복
        for rid, d in list(self._drive.items()):
            if now - d['last'] > s.manual_timeout_sec:
                log.warning('%s 수동 조작 입력이 %.1f초 없어 정지', rid, s.manual_timeout_sec)
                self._end_drive(rid)
            elif self._patrol_state(rid) in WORKING or not self._is_online(rid):
                log.warning('%s 순찰 시작 또는 오프라인이라 수동 조작 정지', rid)
                self._end_drive(rid)
            elif now >= d['next']:
                self._send(rid, d['lin'], d['ang'])
                d['next'] += 1.0 / s.manual_rate_hz
                if d['next'] < now:  # tick 이 밀렸으면 따라잡지 않고 지금부터 센다
                    d['next'] = now + 1.0 / s.manual_rate_hz
        # 0 속도 반복 (비상정지 burst, 수동 조작 꼬리)
        for rid, b in list(self._burst.items()):
            period = 1.0 / s.estop_burst_hz if self.estop_active(rid) else 0.1
            while b['next'] <= now and b['next'] <= b['end'] + 1e-9:
                self._send(rid, 0.0, 0.0)
                b['next'] += period
            if b['next'] > b['end'] + 1e-9:
                del self._burst[rid]
        for rid in [r for r, t in self._estop.items() if t <= now]:
            del self._estop[rid]

    def shutdown(self):
        """서버 종료: 움직이고 있던(수동 조작, 비상정지 중) 로봇에 마지막 0 속도를 보낸다."""
        for rid in set(self._drive) | set(self._burst) | set(self._estop):
            self._send(rid, 0.0, 0.0)
        self._drive.clear()
        self._burst.clear()
        self._estop.clear()

    def status(self):
        now = self._now()
        return {'estop': sorted(r for r, t in self._estop.items() if t > now),
                'driving': {r: d['client'] for r, d in self._drive.items()}}

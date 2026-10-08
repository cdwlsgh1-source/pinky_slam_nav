"""비상정지 burst, 수동 조작 데드맨, 연결 끊김, 상한. 가짜 시계와 가짜 발행 함수로 시간 흐름을 결정적으로 시험한다."""
import pytest

from backend.config import ANGULAR_HARD_MAX, LINEAR_HARD_MAX, MotionConfig
from backend.motion import Motion


class Rig:
    def __init__(self, **cfg):
        self.t = 100.0
        self.sent = []                 # (시각, rid, lin, ang)
        self.state = {'a': None, 'b': None}
        self.online = {'a': True, 'b': True}
        self.fail = False
        self.m = Motion(['a', 'b'], MotionConfig(**cfg), self._pub, lambda r: self.state[r], lambda r: self.online[r],
                        now=lambda: self.t)

    def _pub(self, rid, lin, ang):
        if self.fail:
            raise RuntimeError('publish 실패')
        self.sent.append((round(self.t, 3), rid, lin, ang))
        return True

    def run(self, sec, step=0.05):
        end = self.t + sec
        while self.t < end - 1e-9:
            self.t += step
            self.m.tick()

    def of(self, rid):
        return [(t, lin, ang) for t, r, lin, ang in self.sent if r == rid]


# ---- 비상정지 ----
def test_estop_sends_zero_now_then_burst_for_2s_at_10hz():
    r = Rig()
    assert r.m.estop('a') is True
    assert r.of('a') == [(100.0, 0.0, 0.0)]               # 즉시 1회 (tick 을 기다리지 않는다)
    r.run(3.0)
    zeros = r.of('a')
    assert all(lin == 0.0 and ang == 0.0 for _, lin, ang in zeros)
    assert 20 <= len(zeros) <= 22                          # 즉시 1회 + 10Hz x 2초
    assert max(t for t, _, _ in zeros) <= 102.0 + 1e-6     # 2초가 지나면 멈춘다
    assert r.of('b') == []                                 # 다른 로봇에는 보내지 않는다
    assert r.m.status()['estop'] == []


def test_estop_again_extends_burst():
    r = Rig()
    r.m.estop('a')
    r.run(1.5)
    r.m.estop('a')
    r.run(1.0)
    assert max(t for t, _, _ in r.of('a')) > 102.0         # 첫 burst 가 끝났을 시각 이후에도 0 속도가 나간다
    r.run(3.0)
    assert r.m.status()['estop'] == []


def test_estop_survives_publish_failure_and_reports_it():
    r = Rig()
    r.fail = True
    assert r.m.estop('a') is False                          # 실패를 알려 주되 예외로 죽지 않는다
    r.run(0.5)                                              # tick 도 죽지 않는다
    r.fail = False
    r.run(0.5)
    assert r.of('a')                                        # 복구되면 계속 보낸다


# ---- 수동 조작 ----
def test_drive_publishes_at_rate_and_stops_after_input_timeout():
    r = Rig()
    assert r.m.drive('c1', 'a', 0.05, 0.2) == (True, None)
    assert r.of('a')[0] == (100.0, 0.05, 0.2)               # 첫 입력은 즉시
    r.run(0.3)
    moving = [x for x in r.of('a') if x[1] != 0.0]
    assert len(moving) >= 3                                 # 약 10Hz 로 반복
    n = len(r.of('a'))
    r.run(0.7)                                              # 입력이 더 오지 않음 -> 0.5초 뒤 0 속도
    after = r.of('a')[n:]
    assert any(lin == 0.0 and ang == 0.0 for _, lin, ang in after)
    assert after[-1][1:] == (0.0, 0.0)
    last_move = max(t for t, lin, _ in r.of('a') if lin != 0.0)
    first_zero = min(t for t, lin, ang in r.of('a') if t > last_move and lin == 0.0 and ang == 0.0)
    assert first_zero - 100.0 <= 0.5 + 0.1                  # PRD: 0.5초 안에 0 속도
    r.run(1.0)
    assert r.of('a')[-1][1:] == (0.0, 0.0)
    assert r.m.status()['driving'] == {}


def test_drive_input_keeps_it_alive_and_updates_values():
    r = Rig()
    for i in range(10):
        r.m.drive('c1', 'a', 0.05 if i < 5 else -0.05, 0.0)
        r.run(0.1)
    assert r.m.status()['driving'] == {'a': 'c1'}
    vals = {lin for _, lin, _ in r.of('a')}
    assert 0.05 in vals and -0.05 in vals


def test_release_stops_immediately_and_repeats_zero():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    r.run(0.2)
    n = len(r.of('a'))
    r.m.release('c1')                                       # 연결 끊김/버튼 뗌
    zero_now = r.of('a')[n]
    assert zero_now[1:] == (0.0, 0.0) and zero_now[0] == pytest.approx(r.t)   # 같은 순간에 0 속도
    r.run(0.6)
    tail = r.of('a')[n:]
    assert 2 <= len(tail) <= 5 and all(x[1:] == (0.0, 0.0) for x in tail)
    assert r.m.status()['driving'] == {}


def test_release_of_other_client_does_nothing():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    n = len(r.of('a'))
    r.m.release('c2')
    assert len(r.of('a')) == n and r.m.status()['driving'] == {'a': 'c1'}


def test_zero_input_ends_drive():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    assert r.m.drive('c1', 'a', 0.0, 0.0) == (True, None)
    assert r.of('a')[-1][1:] == (0.0, 0.0) and r.m.status()['driving'] == {}


def test_clamp_to_config_and_hard_limits():
    r = Rig(manual_max_linear=0.1, manual_max_angular=0.5)
    r.m.drive('c1', 'a', 9.0, -9.0)
    assert r.of('a')[0][1:] == (0.1, -0.5)
    # 설정이 하드 상한보다 커도(검증을 우회해 만든 경우) 코드의 상한이 이긴다
    r2 = Rig(manual_max_linear=5.0, manual_max_angular=5.0)
    r2.m.drive('c1', 'a', 9.0, 9.0)
    assert r2.of('a')[0][1:] == (LINEAR_HARD_MAX, ANGULAR_HARD_MAX)


@pytest.mark.parametrize('lin,ang', [(float('nan'), 0), (0, float('inf')), ('x', 0), (None, 0)])
def test_rejects_non_finite_input(lin, ang):
    r = Rig()
    ok, why = r.m.drive('c1', 'a', lin, ang)
    assert not ok and why and r.sent == []


@pytest.mark.parametrize('state', ['STARTING', 'MOVING', 'WAITING_ZONE', 'ARRIVED', 'LEAVING_ZONE', 'RETURNING', 'RETRY'])
def test_rejected_while_patrolling(state):
    r = Rig()
    r.state['a'] = state
    ok, why = r.m.drive('c1', 'a', 0.05, 0.0)
    assert not ok and '순찰' in why and r.sent == []


@pytest.mark.parametrize('state', [None, 'IDLE', 'DONE', 'STOPPED', 'FAILED'])
def test_allowed_when_not_working(state):
    r = Rig()
    r.state['a'] = state
    assert r.m.drive('c1', 'a', 0.05, 0.0)[0]


def test_rejected_when_offline_or_unknown_robot_or_disabled():
    r = Rig()
    r.online['a'] = False
    assert not r.m.drive('c1', 'a', 0.05, 0)[0]
    assert not r.m.drive('c1', 'zzz', 0.05, 0)[0]
    assert not Rig(manual_enabled=False).m.drive('c1', 'a', 0.05, 0)[0]


def test_patrol_start_or_offline_during_drive_stops_it():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    r.run(0.2)
    r.state['a'] = 'STARTING'                               # 다른 경로(CLI)로 순찰이 시작됨
    r.run(0.1)
    assert r.of('a')[-1][1:] == (0.0, 0.0) and r.m.status()['driving'] == {}
    r2 = Rig()
    r2.m.drive('c1', 'a', 0.05, 0.0)
    r2.online['a'] = False
    r2.run(0.1)
    assert r2.of('a')[-1][1:] == (0.0, 0.0)


def test_estop_overrides_drive_and_blocks_new_input():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    r.run(0.2)
    r.m.estop('a')
    assert r.of('a')[-1][1:] == (0.0, 0.0) and r.m.status()['driving'] == {}
    ok, why = r.m.drive('c1', 'a', 0.05, 0.0)               # 비상정지 burst 중에는 받지 않는다
    assert not ok and '비상정지' in why
    r.run(0.3)
    assert all(lin == 0.0 for _, lin, _ in r.of('a')[-4:])
    r.run(2.5)
    assert r.m.drive('c1', 'a', 0.05, 0.0)[0]               # burst 가 끝나면 다시 가능


def test_only_one_client_drives_a_robot():
    r = Rig()
    assert r.m.drive('c1', 'a', 0.05, 0.0)[0]
    ok, why = r.m.drive('c2', 'a', 0.05, 0.0)
    assert not ok and '다른 화면' in why
    assert r.m.drive('c2', 'b', 0.05, 0.0)[0]               # 다른 로봇은 다른 클라이언트가 가능
    r.run(0.7)                                              # c1 이 입력을 멈춰 만료되면 c2 가 넘겨받을 수 있다
    assert r.m.drive('c2', 'a', 0.05, 0.0)[0]


def test_shutdown_sends_final_zero_to_active_robots_only():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    r.m.estop('b')
    n_a, n_b = len(r.of('a')), len(r.of('b'))
    r.m.shutdown()
    assert r.of('a')[n_a:] == [(r.t, 0.0, 0.0)] and r.of('b')[n_b:] == [(r.t, 0.0, 0.0)]
    r.run(1.0)
    assert len(r.of('a')) == n_a + 1                        # 이후에는 더 보내지 않는다


def test_tick_survives_publish_failure_during_drive():
    r = Rig()
    r.m.drive('c1', 'a', 0.05, 0.0)
    r.fail = True
    r.run(0.8)                                              # 발행이 계속 실패해도 예외 없이 입력 만료까지 진행
    assert r.m.status()['driving'] == {}

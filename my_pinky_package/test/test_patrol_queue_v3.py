"""pinky_patrol_node_pinky{1,2}_v3 의 대기열 진입 흐름 테스트.

- Nav2 / 실로봇 없이 가짜 navigator 를 쓰고, 매니저는 실제 zone_manager_node_v3 를 띄운다.
- ROS_DOMAIN_ID=77 + ROS_LOCALHOST_ONLY=1 이 아니면 통합 테스트는 건너뜀 (실제 관제 도메인 보호).
- 이 PC 에는 pinky_interfaces(로봇 전용)가 없으므로 SetLed 를 스텁으로 대체한다.
"""
import importlib
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parent                                   # my_pinky_package/
ZONE_PKG = PKG.parent / 'zone_traffic_control'      # zone_traffic_control/
for p in (str(PKG), str(ZONE_PKG)):
    if p not in sys.path:
        sys.path.insert(0, p)

# pinky_interfaces 스텁 (로봇에만 설치되어 있음)
if 'pinky_interfaces' not in sys.modules:
    _pi = types.ModuleType('pinky_interfaces')
    _srv = types.ModuleType('pinky_interfaces.srv')
    _srv.SetLed = type('SetLed', (), {})
    _pi.srv = _srv
    sys.modules['pinky_interfaces'] = _pi
    sys.modules['pinky_interfaces.srv'] = _srv

MODS = {1: 'my_pinky_package.pinky_patrol_node_pinky1_v3',
        2: 'my_pinky_package.pinky_patrol_node_pinky2_v3'}
V2_PINKY2 = 'my_pinky_package.pinky_patrol_node_pinky2_v2'

ISOLATED = (os.environ.get('ROS_DOMAIN_ID') == '77'
            and os.environ.get('ROS_LOCALHOST_ONLY') == '1')


# ==============================================================================
# 정적 검증 (ROS 불필요)
# ==============================================================================
@pytest.mark.parametrize('n', [1, 2])
def test_waypoint_indexes_match_door_names(n):
    """WAYPOINTS 순번(ZONE_*_INDEX)이 문 좌표(ZONE_*_NAME)와 일치해야 start 경로가 맞게 동작한다."""
    cls = importlib.import_module(MODS[n]).PinkyPatrolNode
    assert cls.WAYPOINTS[cls.ZONE_ENTRY_INDEX] == cls.POINTS[cls.ZONE_ENTRY_NAME]
    assert cls.WAYPOINTS[cls.ZONE_EXIT_INDEX] == cls.POINTS[cls.ZONE_EXIT_NAME]


def test_v2_pinky2_index_mismatch_is_documented():
    """v2 의 불일치(RED2IN 이 0번인데 ENTRY_INDEX=1)를 v3 가 정정했음을 고정해 둔다."""
    v2 = importlib.import_module(V2_PINKY2).PinkyPatrolNode
    assert v2.WAYPOINTS[v2.ZONE_ENTRY_INDEX] != v2.POINTS[v2.ZONE_ENTRY_NAME]


@pytest.mark.parametrize('n', [1, 2])
def test_no_hardcoded_queue_coordinates(n):
    """로봇 코드에는 슬롯 좌표가 없다: POINTS 에 RED*/WAIT*/QUEUE* 항목이 없어야 한다 (슬롯은 매니저가 줌)."""
    pts = importlib.import_module(MODS[n]).PinkyPatrolNode.POINTS
    extra = [k for k in pts if k.startswith(('RED', 'WAIT', 'QUEUE'))]
    assert extra == []
    src = Path(importlib.import_module(MODS[n]).__file__).read_text(encoding='utf-8')
    assert 'zone_gate_client import' not in src.replace('zone_gate_client_v3', '')


@pytest.mark.parametrize('n', [1, 2])
def test_zone_entry_matches_yaml_anchor(n):
    """로봇의 ZONE_ENTRY 는 매니저 yaml 의 queue_anchor 와 같아야 한다 (앵커를 바꾸면 여기서 실패)."""
    import yaml
    prm = yaml.safe_load((ZONE_PKG / 'config/zone_params_v3.yaml').read_text(encoding='utf-8'))
    anchor = tuple(prm['zone_manager_node_v3']['ros__parameters']['queue_anchor'])
    entry = importlib.import_module(MODS[n]).PinkyPatrolNode.POINTS['ZONE_ENTRY']
    assert entry == pytest.approx(anchor)


@pytest.mark.parametrize('n', [1, 2])
def test_zone_exit_is_shared_and_red_points_removed(n):
    """출구 좌표는 두 로봇이 같고(yaw 3.14 = 뒤 방향), 기존 RED* 좌표는 POINTS 에 없다."""
    cls = importlib.import_module(MODS[n]).PinkyPatrolNode
    other = importlib.import_module(MODS[3 - n]).PinkyPatrolNode
    assert cls.POINTS['ZONE_EXIT'] == other.POINTS['ZONE_EXIT']
    assert cls.POINTS['ZONE_ENTRY'] == other.POINTS['ZONE_ENTRY']
    assert cls.POINTS['ZONE_EXIT'][2] == pytest.approx(3.14, abs=0.01)
    assert not [k for k in cls.POINTS if k.startswith('RED')]
    assert cls.ZONE_ENTRY_NAME == 'ZONE_ENTRY' and cls.ZONE_EXIT_NAME == 'ZONE_EXIT'
    assert cls.POINTS['ZONE_EXIT'][:2] == (0.5, -0.5)


def test_stop_exit_points_line_up_behind_zone_exit():
    """stop 퇴장 위치: pinky1 = ZONE_EXIT, pinky2 = ZONE_EXIT 에서 +x 로 0.30 m (겹치지 않는 고정 오프셋)."""
    c1 = importlib.import_module(MODS[1]).PinkyPatrolNode
    c2 = importlib.import_module(MODS[2]).PinkyPatrolNode
    ex = c1.POINTS['ZONE_EXIT']
    assert c1.POINTS['ZONE_STOP_EXIT'] == ex
    assert c2.POINTS['ZONE_STOP_EXIT'][0] == pytest.approx(ex[0] + 0.30)
    assert c2.POINTS['ZONE_STOP_EXIT'][1:] == ex[1:]
    for c in (c1, c2):
        assert c.STOP_EXIT_NAME == 'ZONE_STOP_EXIT'
        assert 'ZONE_STOP_EXIT' not in c.GOTO_ALLOWED      # 직접 goto 금지


# ==============================================================================
# 통합: 실제 매니저 + 가짜 navigator
# ==============================================================================
@pytest.fixture(scope='module')
def env():
    if not ISOLATED:
        pytest.skip('ROS_DOMAIN_ID=77 + ROS_LOCALHOST_ONLY=1 에서만 실행')
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from std_msgs.msg import String
    from nav2_simple_commander.robot_navigator import TaskResult
    from zone_traffic_control.zone_gate_client_v3 import ZoneGateClient

    mgr = subprocess.Popen(
        [sys.executable, '-m', 'zone_traffic_control.zone_manager_node_v3',
         '--ros-args', '--params-file', str(ZONE_PKG / 'config/zone_params_v3.yaml')],
        cwd=str(ZONE_PKG), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rclpy.init()

    class FakeNavigator(Node):
        """Nav2 대신: goal 을 기록하고 travel 초 뒤에 성공 처리. cancelTask 는 즉시 CANCELED."""
        _n = 0

        def __init__(self, travel=0.3):
            FakeNavigator._n += 1
            super().__init__(f'fake_nav_{FakeNavigator._n}')
            self.travel, self.goals = travel, []
            self.fail_all = False
            self._active, self._cancelled, self._done_at = False, False, 0.0

        def goToPose(self, pose):
            p = pose.pose
            yaw = 2.0 * __import__('math').atan2(p.orientation.z, p.orientation.w)
            self.goals.append((round(p.position.x, 3), round(p.position.y, 3), round(yaw, 2)))
            self._active, self._cancelled = True, False
            self._done_at = time.time() + self.travel

        def isTaskComplete(self):
            rclpy.spin_once(self, timeout_sec=0.05)     # 실제 BasicNavigator 처럼 자신을 spin
            if not self._active:
                return True
            if self._cancelled or time.time() >= self._done_at:
                self._active = False
                return True
            return False

        def cancelTask(self):
            self._cancelled = True

        def getResult(self):
            if self._cancelled:
                return TaskResult.CANCELED
            return TaskResult.FAILED if self.fail_all else TaskResult.SUCCEEDED

        def getFeedback(self):
            return None

        def waitUntilNav2Active(self):
            pass

    other = Node('other_clients')                        # 점유자/다른 대기자 역할 + 상태 감시
    ex = SingleThreadedExecutor()
    ex.add_node(other)
    status = {'v': None}
    other.create_subscription(String, '/zone_manager_v3/status',
                              lambda m: status.__setitem__('v', m.data), 10)

    def pump(cond, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            ex.spin_once(timeout_sec=0.05)
            if cond():
                return True
        return False

    # 매니저 준비 대기 (빈 구역이면 request 에 grant 가 온다)
    warm = ZoneGateClient(other, 'warm')
    warm.reset()
    tok = warm.send_request()
    ok, end = False, time.time() + 10
    while time.time() < end and not ok:
        warm.resend_if_due()
        ex.spin_once(timeout_sec=0.05)
        ok = warm.granted(tok)
    assert ok, 'zone_manager_node_v3 가 응답하지 않음'
    warm.notify_exit()
    pump(lambda: status['v'] == 'free', 3.0)

    ns = types.SimpleNamespace(rclpy=rclpy, other=other, pump=pump, status=status,
                               Gate=ZoneGateClient, Nav=FakeNavigator)
    yield ns
    mgr.terminate()
    mgr.wait(timeout=5)
    other.destroy_node()
    rclpy.shutdown()


def make_robot(env, n, robot_id, travel=0.3):
    """v3 순찰 노드 클래스에서 필요한 메서드/상수만 가져와 Node 생성 없이 쓰는 로봇 객체."""
    cls = importlib.import_module(MODS[n]).PinkyPatrolNode
    nav = env.Nav(travel=travel)
    r = types.SimpleNamespace(navigator=nav, robot_id=robot_id, statuses=[], _stop_requested=False,
                              _emergency_requested=False, _running=True, notified=[])
    r.gate = env.Gate(nav, robot_id)
    for name in ('POINTS', 'WAYPOINTS', 'ZONE_ENTRY_NAME', 'ZONE_EXIT_NAME', 'ZONE_ENTRY_INDEX',
                 'ZONE_EXIT_INDEX', 'NAV_RETRIES', 'STOP_EXIT_NAME'):
        setattr(r, name, getattr(cls, name))
    for name in ('_queue_and_enter', '_start_goal', '_cancel_goal', '_record_pose', '_abort_entry',
                 'make_pose', 'get_current_pose', '_run_patrol', '_retreat_after_stop'):
        setattr(r, name, types.MethodType(cls.__dict__[name], r))
    r.get_logger = nav.get_logger
    r.publish_status = lambda state, idx, detail='': r.statuses.append((state, detail))
    r.start_blink = lambda *a, **k: None
    r.stop_blink = lambda: None
    r.set_drive_led = lambda: None
    r.set_idle_led = lambda: None
    r.save_last_pose = lambda *a: None
    r._init_pose_once = lambda: None
    return r


def run_in_thread(fn, *args):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault('r', fn(*args)), daemon=True)
    t.start()
    return t, out


def occupy(env, rid='pinky1'):
    """다른 로봇이 구역을 점유한 상황을 만든다 (반환: 게이트)."""
    g = env.Gate(env.other, rid)
    g.reset()
    tok = g.send_request()
    assert env.pump(lambda: g.granted(tok))
    return g


def states(r):
    return [s for s, _ in r.statuses]


_POINTS = importlib.import_module(MODS[1]).PinkyPatrolNode.POINTS
DOOR1 = tuple(_POINTS['ZONE_ENTRY'])   # 앵커 (순찰 노드 상수, yaml queue_anchor 와 같은지는 별도 테스트가 검사)
EXIT = tuple(_POINTS['ZONE_EXIT'])     # 락 반납 출구
def _slot(k):
    """zone_params_v3.yaml 의 앵커/후방 벡터/간격으로 슬롯 k 를 계산 (매니저가 발행하는 값과 같은 규칙)."""
    import yaml
    from zone_traffic_control.zone_manager_node_v3 import normalize_dir, slot_pose
    prm = yaml.safe_load((ZONE_PKG / 'config/zone_params_v3.yaml').read_text(encoding='utf-8'))
    prm = prm['zone_manager_node_v3']['ros__parameters']
    x, y, yaw = slot_pose(k, prm['queue_anchor'], normalize_dir(prm['queue_back_dir']),
                          prm['queue_spacing'])
    return (round(x, 3), round(y, 3), round(yaw, 2))   # FakeNavigator 가 기록하는 형식과 동일


SLOT0 = _slot(0)
SLOT1 = _slot(1)


def test_empty_zone_grant_does_not_wait_at_anchor(env):
    r = make_robot(env, 1, 'pinky2')
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: not t.is_alive())
    assert out['r'] is True
    assert len(r.navigator.goals) <= 1                   # 슬롯 이동 없음, 앵커 도착을 기다리지도 않음
    assert r.navigator.goals in ([], [DOOR1])
    assert 'QUEUEING' not in states(r)
    r.gate.notify_exit()
    assert env.pump(lambda: env.status['v'] == 'free')


def test_waits_in_slot_then_enters(env):
    occ = occupy(env)
    r = make_robot(env, 1, 'pinky2')
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: 'QUEUEING' in states(r))
    assert env.pump(lambda: SLOT0 in r.navigator.goals)  # 슬롯 좌표는 매니저가 준 값
    time.sleep(0.8)
    assert t.is_alive()                                  # 허가 전에는 문으로 들어가지 않음
    n_before = len(r.navigator.goals)
    occ.notify_exit()
    assert env.pump(lambda: not t.is_alive(), 10)
    assert out['r'] is True
    assert r.navigator.goals[-1] == SLOT0                # 허가 후 앵커 이동 없음 (이미 슬롯 0 에 있음)
    assert len(r.navigator.goals) == n_before            # 허가 후 새 goal 없음
    r.gate.notify_exit()
    assert env.pump(lambda: env.status['v'] == 'free')


def test_slot_reassign_cancels_and_moves_forward(env):
    occ = occupy(env)
    front = env.Gate(env.other, 'pinky4')
    front.reset()
    front.send_request()
    assert env.pump(lambda: front.current_slot is not None)      # k=0
    r = make_robot(env, 1, 'pinky2')
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: SLOT1 in r.navigator.goals)          # 뒤 로봇은 k=1
    front.cancel_entry()                                         # 앞 대기자 포기 -> 한 칸 전진
    assert env.pump(lambda: r.navigator.goals[-1] == SLOT0, 8)
    r._stop_requested = True
    assert env.pump(lambda: not t.is_alive())
    assert out['r'] is False
    occ.notify_exit()
    assert env.pump(lambda: env.status['v'] == 'free')


def test_stop_while_queued_cancels_entry(env):
    occ = occupy(env)
    r = make_robot(env, 1, 'pinky2')
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: SLOT0 in r.navigator.goals)
    time.sleep(0.5)
    r._stop_requested = True
    assert env.pump(lambda: not t.is_alive())
    assert out['r'] is False and states(r)[-1] == 'STOPPED'
    occ.notify_exit()                                    # 취소했으므로 아무에게도 허가가 가면 안 됨
    assert env.pump(lambda: env.status['v'] == 'free', 4)


@pytest.mark.parametrize('n', [1, 2])
def test_stop_command_callback_does_not_cancel_from_callback_thread(env, n):
    """stop 명령 콜백은 플래그만 세운다. 콜백에서 navigator.cancelTask() 를 부르면 실기에서
    'Executor is already spinning' 으로 노드가 죽었다 (T4, 2026-10-14)."""
    cls = importlib.import_module(MODS[n]).PinkyPatrolNode
    r = make_robot(env, n, f'pinky{n}')
    r._cmd_cb = types.MethodType(cls.__dict__['_cmd_cb'], r)
    calls = []
    r.navigator.cancelTask = lambda: calls.append('cancel')
    r._running = True
    r._cmd_cb(types.SimpleNamespace(data='stop'))
    assert r._stop_requested is True
    assert calls == []


def test_grant_received_while_traveling_to_slot(env):
    occ = occupy(env)
    r = make_robot(env, 1, 'pinky2', travel=2.0)         # 느린 이동: 도착 전에 허가가 온다
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: SLOT0 in r.navigator.goals)
    occ.notify_exit()                                    # 슬롯에 도착하기 전에 점유자 이탈
    assert env.pump(lambda: not t.is_alive(), 12)
    assert out['r'] is True
    assert r.navigator.goals[0] == DOOR1 and SLOT0 in r.navigator.goals
    assert r.navigator.goals[-1] == SLOT0                # 허가 후 슬롯 goal 만 취소, 앵커로 가지 않음
    r.gate.notify_exit()
    assert env.pump(lambda: env.status['v'] == 'free')


def test_queue_full_gives_up_without_holding(env):
    occ = occupy(env)
    fillers = [env.Gate(env.other, f'pinky{i}') for i in (4, 5, 6)]   # queue_max=3
    for g in fillers:
        g.reset()
        g.send_request()
    assert env.pump(lambda: all(g.current_slot is not None for g in fillers))
    r = make_robot(env, 1, 'pinky2')
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: not t.is_alive())
    assert out['r'] is False
    assert r.statuses[-1] == ('FAILED', 'queue_full')
    for g in fillers:
        g.cancel_entry()
    env.pump(lambda: False, 0.5)                         # 취소가 먼저 반영돼야 함 (게시자가 달라 순서 비보장)
    occ.notify_exit()
    assert env.pump(lambda: env.status['v'] == 'free', 4)


def test_grant_returns_immediately_without_going_to_anchor(env):
    """슬롯 1 에서 기다리다 허가를 받으면 앵커/슬롯 0 을 거치지 않고 바로 반환한다 (호출자가 목적지로 직행)."""
    occ = occupy(env)
    front = env.Gate(env.other, 'pinky4')
    front.reset()
    front.send_request()
    assert env.pump(lambda: front.current_slot is not None)      # 앞 대기자 k=0
    r = make_robot(env, 1, 'pinky2', travel=0.3)
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: SLOT1 in r.navigator.goals)          # 뒤 로봇 k=1
    occ.notify_exit()                                            # 허가는 앞 대기자(pinky4)에게
    front.notify_exit()                                          # 앞 대기자도 통과 -> pinky2 에게 허가
    assert env.pump(lambda: not t.is_alive(), 12)
    assert out['r'] is True
    n_end = len(r.navigator.goals)
    env.pump(lambda: False, 0.8)
    assert len(r.navigator.goals) == n_end                       # 반환 뒤 앵커 등 새 goal 이 나가지 않음
    r.gate.notify_exit()
    assert env.pump(lambda: env.status['v'] == 'free', 4)


def test_pinky2_start_path_uses_corrected_indexes(env):
    """pinky2_v3 start 경로: ZONE_ENTRY(0번) 에서 진입 절차 -> P3 -> P6 -> ZONE_EXIT 에서 락 반납 -> P7."""
    r = make_robot(env, 2, 'pinky2')
    t, out = run_in_thread(r._run_patrol)
    assert env.pump(lambda: not t.is_alive(), 15)
    g = r.navigator.goals
    assert len(g) == 5, g
    assert g[0] == DOOR1 and g[3] == EXIT and g[3][2] == pytest.approx(3.14, abs=0.01)
    assert states(r)[-1] == 'DONE'
    assert env.pump(lambda: env.status['v'] == 'free', 4)   # ZONE_EXIT(3번) 도착 때 notify_exit


def test_pinky1_start_path_waits_in_queue(env):
    occ = occupy(env)
    r = make_robot(env, 1, 'pinky1x')
    t, out = run_in_thread(r._run_patrol)
    assert env.pump(lambda: 'QUEUEING' in states(r), 10)
    assert SLOT0 in r.navigator.goals
    occ.notify_exit()
    assert env.pump(lambda: not t.is_alive(), 20)
    assert states(r)[-1] == 'DONE'
    assert env.pump(lambda: env.status['v'] == 'free', 4)


# ------------------------------------------------------------------------------
# stop / emergency_stop (T4 후속, 2026-10-14)
# ------------------------------------------------------------------------------
def _goal(wp):
    """POINTS 튜플 -> FakeNavigator.goals 에 기록되는 (x, y, yaw) 형태."""
    return (round(wp[0], 3), round(wp[1], 3), round(wp[2] if len(wp) > 2 else 0.0, 2))


def _stop_in_zone(env, emergency):
    r = make_robot(env, 2, 'pinky2', travel=1.0)
    t, out = run_in_thread(r._run_patrol)
    p3 = _goal(r.POINTS['P3'])
    assert env.pump(lambda: p3 in r.navigator.goals)       # 허가받고 구역 안(P3)으로 가는 중
    assert env.status['v'] == 'occupied_by:pinky2'
    r._emergency_requested = emergency
    r._stop_requested = True
    return r, t


def test_stop_in_zone_retreats_to_stop_exit_and_returns_token(env):
    r, t = _stop_in_zone(env, emergency=False)
    assert env.pump(lambda: not t.is_alive(), 12)
    assert r.navigator.goals[-1] == _goal(r.POINTS['ZONE_STOP_EXIT'])
    assert 'RETREATING' in states(r) and states(r)[-1] == 'STOPPED'
    assert env.pump(lambda: env.status['v'] == 'free', 4)    # 토큰 반납됨


def test_emergency_stop_in_zone_keeps_token_and_position(env):
    r, t = _stop_in_zone(env, emergency=True)
    assert env.pump(lambda: not t.is_alive(), 12)
    assert _goal(r.POINTS['ZONE_STOP_EXIT']) not in r.navigator.goals
    assert 'RETREATING' not in states(r)
    assert not env.pump(lambda: env.status['v'] == 'free', 2.0)   # 락 유지 (max_hold_sec 뒤에만 해제)
    r.gate.notify_exit()                                   # 정리
    assert env.pump(lambda: env.status['v'] == 'free', 4)


@pytest.mark.parametrize('n', [1, 2])
def test_cmd_cb_emergency_stop_sets_both_flags(env, n):
    cls = importlib.import_module(MODS[n]).PinkyPatrolNode
    r = make_robot(env, n, f'pinky{n}')
    r._cmd_cb = types.MethodType(cls.__dict__['_cmd_cb'], r)
    r._running = True
    r._cmd_cb(types.SimpleNamespace(data='emergency_stop'))
    assert r._stop_requested and r._emergency_requested
    r._stop_requested = r._emergency_requested = False
    r._cmd_cb(types.SimpleNamespace(data='stop'))
    assert r._stop_requested and not r._emergency_requested

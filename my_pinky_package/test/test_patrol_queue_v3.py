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
    """로봇 코드에는 대기 좌표가 없다: POINTS 에 RED*IN/OUT 외의 RED/WAIT/QUEUE 항목이 없어야 한다."""
    pts = importlib.import_module(MODS[n]).PinkyPatrolNode.POINTS
    extra = [k for k in pts if k.startswith(('RED', 'WAIT', 'QUEUE')) and not k.endswith(('IN', 'OUT'))]
    assert extra == []
    src = Path(importlib.import_module(MODS[n]).__file__).read_text(encoding='utf-8')
    assert 'zone_gate_client import' not in src.replace('zone_gate_client_v3', '')


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
                              _running=True, notified=[])
    r.gate = env.Gate(nav, robot_id)
    for name in ('POINTS', 'WAYPOINTS', 'ZONE_ENTRY_NAME', 'ZONE_EXIT_NAME', 'ZONE_ENTRY_INDEX',
                 'ZONE_EXIT_INDEX', 'NAV_RETRIES'):
        setattr(r, name, getattr(cls, name))
    for name in ('_queue_and_enter', '_start_goal', '_cancel_goal', '_record_pose', '_abort_entry',
                 'make_pose', 'get_current_pose', '_run_patrol'):
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


DOOR1 = (0.6, -0.5, 0.0)
SLOT0 = (0.3, -0.45, 0.0)
SLOT1 = (-0.05, -0.45, 0.0)


def test_empty_zone_goes_straight_to_door(env):
    r = make_robot(env, 1, 'pinky2')
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: not t.is_alive())
    assert out['r'] is True
    assert r.navigator.goals == [DOOR1]                  # 슬롯 이동 없음
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
    occ.notify_exit()
    assert env.pump(lambda: not t.is_alive(), 10)
    assert out['r'] is True
    assert r.navigator.goals[-1] == DOOR1                # 허가 후 문으로 이동
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


def test_grant_received_while_traveling_to_slot(env):
    occ = occupy(env)
    r = make_robot(env, 1, 'pinky2', travel=2.0)         # 느린 이동: 도착 전에 허가가 온다
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: SLOT0 in r.navigator.goals)
    occ.notify_exit()                                    # 슬롯에 도착하기 전에 점유자 이탈
    assert env.pump(lambda: not t.is_alive(), 12)
    assert out['r'] is True
    assert r.navigator.goals[0] == DOOR1 and SLOT0 in r.navigator.goals
    assert r.navigator.goals[-1] == DOOR1
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


def test_door_unreachable_after_grant_releases_lock(env):
    r = make_robot(env, 1, 'pinky2')
    r.navigator.fail_all = True                          # 문까지 이동이 계속 실패
    t, out = run_in_thread(r._queue_and_enter, 0, 'P3')
    assert env.pump(lambda: not t.is_alive())
    assert out['r'] is False and states(r)[-1] == 'FAILED'
    assert len(r.navigator.goals) == 3                   # NAV_RETRIES 만큼만 시도
    assert env.pump(lambda: env.status['v'] == 'free', 4)    # 구역 밖이므로 락 반납


def test_pinky2_start_path_uses_corrected_indexes(env):
    """pinky2_v3 start 경로: RED2IN(0번) 에서 진입 절차 -> P3 -> P6 -> RED2OUT 에서 락 반납 -> P7."""
    r = make_robot(env, 2, 'pinky2')
    t, out = run_in_thread(r._run_patrol)
    assert env.pump(lambda: not t.is_alive(), 15)
    g = r.navigator.goals
    assert len(g) == 5, g
    assert g[0] == (0.55, -0.6, 0.0) and g[3][2] == pytest.approx(3.14, abs=0.01)
    assert states(r)[-1] == 'DONE'
    assert env.pump(lambda: env.status['v'] == 'free', 4)   # RED2OUT(3번) 도착 때 notify_exit


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

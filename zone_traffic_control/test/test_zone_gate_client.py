"""zone_gate_client 테스트.

1) 순수 로직(슬롯/허가 파싱, 재전송 타이밍): ROS 없이 실행.
2) 통합: zone_manager_node 를 실제로 띄워 슬롯 변경·거부·허가 수신을 확인.
   ROS_DOMAIN_ID=77 + ROS_LOCALHOST_ONLY=1 이 아니면 건너뜀 (실제 관제 도메인 보호).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from zone_traffic_control.zone_gate_client import (RetryTimer,
                                                      choose_entry_target,
                                                      parse_grant_msg,
                                                      parse_slot_msg)

PKG_ROOT = Path(__file__).resolve().parents[1]


def _params():
    import yaml
    prm = yaml.safe_load((PKG_ROOT / 'config/zone_params.yaml').read_text(encoding='utf-8'))
    return prm['zone_manager_node']['ros__parameters']


# 슬롯 전진은 queue_advance_delay_sec 뒤에 일어나므로 대기 시간을 yaml 값에서 계산 (지연을 바꿔도 테스트 유지)
ADVANCE_WAIT = 2 * _params()['queue_advance_delay_sec'] + 3.0


def expected_slot(k):
    """zone_params.yaml 의 앵커/후방 벡터/간격으로 슬롯 k 의 (x, y) 를 계산 (앵커가 바뀌어도 테스트 유지)."""
    import yaml
    from zone_traffic_control.zone_manager_node import normalize_dir, slot_pose
    prm = yaml.safe_load((PKG_ROOT / 'config/zone_params.yaml').read_text(encoding='utf-8'))
    prm = prm['zone_manager_node']['ros__parameters']
    x, y, _ = slot_pose(k, prm['queue_anchor'], normalize_dir(prm['queue_back_dir']),
                        prm['queue_spacing'])
    return x, y


# ---------------- 순수 로직 ----------------
def test_parse_slot_ok():
    assert parse_slot_msg('pinky2:1:-0.050,-0.450,0.000') == ('pinky2', 1, -0.05, -0.45, 0.0)


def test_parse_slot_queue_full():
    assert parse_slot_msg('pinky3:-1:0.0,0.0,0.0')[1] == -1


@pytest.mark.parametrize('bad', ['', 'pinky1', 'pinky1:1', 'pinky1:x:1,2,3',
                                 'pinky1:1:1,2', 'pinky1:1:a,b,c', None])
def test_parse_slot_bad(bad):
    assert parse_slot_msg(bad) is None


def test_parse_grant():
    assert parse_grant_msg('pinky1:abc123') == ('pinky1', 'abc123')
    assert parse_grant_msg('pinky1:a:b') == ('pinky1', 'a:b')
    assert parse_grant_msg('noseparator') is None


def test_retry_timer_due_boundaries():
    t = RetryTimer(1.0)
    assert t.due(0.0)                 # 한 번도 안 보냈으면 즉시 due
    t.mark(10.0)
    assert not t.due(10.5)
    assert t.due(11.0)                # 경계: 정확히 period 경과 -> due
    t.mark(11.0)
    assert not t.due(11.9)
    t.reset()
    assert t.due(11.9)


# ---------------- choose_entry_target (patrol 노드 공용 판단) ----------------
DOOR = (0.6, -0.5, 0.0)


def test_choose_target_no_response_goes_to_door():
    assert choose_entry_target(False, None, DOOR) == ('door', DOOR)


def test_choose_target_slot_assigned():
    assert choose_entry_target(False, (1, -0.05, -0.45, 0.0), DOOR) == ('slot', (-0.05, -0.45, 0.0))


def test_choose_target_queue_full():
    assert choose_entry_target(False, (-1, 0.0, 0.0, 0.0), DOOR) == ('full', None)


def test_choose_target_grant_beats_stale_slot_and_full():
    assert choose_entry_target(True, (0, 0.3, -0.45, 0.0), DOOR) == ('door', DOOR)
    assert choose_entry_target(True, (-1, 0.0, 0.0, 0.0), DOOR) == ('door', DOOR)


# ---------------- 통합 (실제 zone_manager_node) ----------------
ISOLATED = (os.environ.get('ROS_DOMAIN_ID') == '77'
            and os.environ.get('ROS_LOCALHOST_ONLY') == '1')


@pytest.fixture(scope='module')
def ros_env():
    if not ISOLATED:
        pytest.skip('ROS_DOMAIN_ID=77 + ROS_LOCALHOST_ONLY=1 에서만 실행')
    rclpy = pytest.importorskip('rclpy')
    from rclpy.node import Node
    from zone_traffic_control.zone_gate_client import ZoneGateClient

    mgr = subprocess.Popen(
        [sys.executable, '-m', 'zone_traffic_control.zone_manager_node',
         '--ros-args', '--params-file', str(PKG_ROOT / 'config/zone_params.yaml')],
        cwd=str(PKG_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rclpy.init()
    node = Node('gate_client_test')
    clients = {rid: ZoneGateClient(node, rid) for rid in ('pinky1', 'pinky2', 'pinky3', 'pinky4')}

    def spin_until(cond, timeout=ADVANCE_WAIT):
        end = time.time() + timeout
        while time.time() < end:
            # 실제 순찰 노드처럼 대기 중(미허가)인 클라이언트는 1초마다 재전송한다.
            # 안 하면 queue_stale_sec(5초) 안에 전진 지연(queue_advance_delay_sec)을 기다리다 큐에서 제거됨
            for c in clients.values():
                if c._token is not None and not c.granted():
                    c.resend_if_due()
            rclpy.spin_once(node, timeout_sec=0.05)
            if cond():
                return True
        return False

    # 매니저 discovery 대기: 점유자가 없으면 request 에 곧 grant 가 온다 (워밍업 후 해제)
    warm = clients['pinky4']
    warm.reset()
    tok = warm.send_request()
    ok = False
    end = time.time() + 10.0
    while time.time() < end and not ok:
        warm.resend_if_due()
        rclpy.spin_once(node, timeout_sec=0.05)
        ok = warm.granted(tok)
    assert ok, 'zone_manager_node 가 응답하지 않음'
    warm.notify_exit()
    spin_until(lambda: False, 0.5)
    yield node, clients, spin_until
    mgr.terminate()
    mgr.wait(timeout=5)
    node.destroy_node()
    rclpy.shutdown()


def test_integration_grant_slots_full_and_cancel(ros_env):
    node, c, spin_until = ros_env
    p1, p2, p3, p4 = c['pinky1'], c['pinky2'], c['pinky3'], c['pinky4']
    for x in (p1, p2, p3, p4):
        x.reset()

    # 1) 빈 구역: 즉시 허가, 슬롯 없음
    t1 = p1.send_request()
    assert spin_until(lambda: p1.granted(t1))
    assert p1.current_slot is None

    # 2) 점유 중: p2, p3 는 슬롯 k=0, 1 (yaml 의 앵커 / 앵커 + d·후방 벡터)
    t2 = p2.send_request()
    assert spin_until(lambda: p2.current_slot is not None)
    k, x, y, _ = p2.current_slot
    assert k == 0 and (x, y) == pytest.approx(expected_slot(0))
    assert not p2.granted(t2)

    p3.send_request()
    assert spin_until(lambda: p3.current_slot is not None)
    k, x, y, _ = p3.current_slot
    assert k == 1 and (x, y) == pytest.approx(expected_slot(1))

    # 3) queue_max=3 이므로 p4 까지는 들어간다(k=2). 넘치는 경우는 pinky5 로 확인
    p4.send_request()
    assert spin_until(lambda: p4.current_slot is not None and p4.current_slot[0] == 2)

    # 4) 취소: p3 가 cancel 하면 p4 가 k=1 로 전진 (advance delay 1.5s 이후)
    p3.cancel_entry()
    assert p3.current_slot is None
    assert spin_until(lambda: p4.current_slot[0] == 1, timeout=ADVANCE_WAIT)

    # 5) 출입: p1 이 exit -> p2 허가, p4 는 k=0
    p1.notify_exit()
    assert spin_until(lambda: p2.granted(t2))
    assert spin_until(lambda: p4.current_slot[0] == 0, timeout=ADVANCE_WAIT)

    # 정리: p2 exit -> p4 허가 -> p4 exit
    p2.notify_exit()
    assert spin_until(lambda: p4.granted())
    p4.notify_exit()
    spin_until(lambda: False, 0.5)


def test_integration_queue_full_rejection(ros_env):
    node, c, spin_until = ros_env
    from rclpy.node import Node
    from zone_traffic_control.zone_gate_client import ZoneGateClient
    extra = {rid: ZoneGateClient(node, rid) for rid in ('pinky5', 'pinky6')}
    p1, p2, p3, p4 = (c[r] for r in ('pinky1', 'pinky2', 'pinky3', 'pinky4'))
    for x in (p1, p2, p3, p4, *extra.values()):
        x.reset()

    t1 = p1.send_request()
    assert spin_until(lambda: p1.granted(t1))
    for p in (p2, p3, p4):                       # 대기자 3명 = queue_max
        p.send_request()
    assert spin_until(lambda: all(p.current_slot is not None for p in (p2, p3, p4)))

    full = extra['pinky5']
    full.send_request()
    assert spin_until(lambda: full.queue_full)
    assert full.current_slot[0] == -1
    assert not full.granted()

    # 거부된 로봇이 cancel 해도 큐/점유에 영향 없음
    full.cancel_entry()
    assert not full.queue_full

    # 정리
    for p in (p2, p3, p4):
        p.cancel_entry()
    p1.notify_exit()
    spin_until(lambda: False, 1.0)

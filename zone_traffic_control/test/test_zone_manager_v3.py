"""zone_manager_node_v3 의 순수 로직 테스트 (slot_pose, normalize_dir, ZoneQueue). ROS 노드는 띄우지 않는다."""
import math

import pytest

from zone_traffic_control.zone_manager_node_v3 import (ZoneQueue, normalize_dir,
                                                       slot_pose)

ANCHOR = (0.30, -0.45, 0.0)
BACK = (-1.0, 0.0)


# ---------------- slot_pose ----------------
def test_slot0_is_anchor():
    assert slot_pose(0, ANCHOR, BACK, 0.35) == ANCHOR


def test_slot_k_moves_back_by_spacing():
    x, y, yaw = slot_pose(2, ANCHOR, BACK, 0.35)
    assert x == pytest.approx(0.30 - 0.70)
    assert y == pytest.approx(-0.45)
    assert yaw == 0.0


def test_slot_diagonal_distance_is_k_times_spacing():
    d = normalize_dir((3.0, 4.0))
    x, y, _ = slot_pose(3, (0.0, 0.0, 0.0), d, 0.35)
    assert math.hypot(x, y) == pytest.approx(3 * 0.35)


def test_normalize_dir():
    assert normalize_dir((0.0, -5.0)) == pytest.approx((0.0, -1.0))
    assert normalize_dir((0.0, 0.0)) == (-1.0, 0.0)          # 길이 0 -> 기본값
    assert normalize_dir(['a', 1]) == (-1.0, 0.0)            # 형식 오류 -> 기본값
    assert normalize_dir([1.0]) == (-1.0, 0.0)               # 길이 부족 -> 기본값
    assert normalize_dir((0.0, 0.0), default=None) is None


# ---------------- ZoneQueue ----------------
def make():
    return ZoneQueue(queue_max=2, stale_sec=5.0)


def test_empty_zone_grants_immediately():
    q = make()
    assert q.request('pinky1', 'a', 0.0) == ('grant', 'pinky1', 'a')
    assert q.occupant == 'pinky1'
    assert q.order() == []


def test_occupied_zone_queues_in_fifo_order():
    q = make()
    q.request('pinky1', 'a', 0.0)
    assert q.request('pinky2', 'b', 1.0) == ('queued',)
    assert q.request('pinky3', 'c', 2.0) == ('queued',)
    assert q.order() == ['pinky2', 'pinky3']        # 인덱스 = 슬롯 번호 k


def test_occupant_rerequest_regrants():
    q = make()
    q.request('pinky1', 'a', 0.0)
    assert q.request('pinky1', 'a2', 1.0) == ('grant', 'pinky1', 'a2')
    assert q.order() == []


def test_waiter_rerequest_updates_token_not_position():
    q = make()
    q.request('pinky1', 'a', 0.0)
    q.request('pinky2', 'b', 1.0)
    q.request('pinky3', 'c', 2.0)
    assert q.request('pinky2', 'b2', 3.0) == ('queued',)
    assert q.order() == ['pinky2', 'pinky3']
    assert q.queue[0][1] == 'b2'


def test_queue_full_rejects_without_enqueue():
    q = make()                                       # queue_max=2
    q.request('pinky1', 'a', 0.0)
    q.request('pinky2', 'b', 0.0)
    q.request('pinky3', 'c', 0.0)
    assert q.request('pinky4', 'd', 0.0) == ('full',)
    assert q.order() == ['pinky2', 'pinky3']
    assert q.request('pinky2', 'b2', 1.0) == ('queued',)   # 이미 있는 대기자는 거부되지 않음


def test_exit_promotes_head():
    q = make()
    q.request('pinky1', 'a', 0.0)
    q.request('pinky2', 'b', 1.0)
    q.request('pinky3', 'c', 2.0)
    ok, nxt = q.exit('pinky1', 10.0)
    assert ok and nxt == ('grant', 'pinky2', 'b')
    assert q.occupant == 'pinky2'
    assert q.order() == ['pinky3']                   # 나머지는 k-1


def test_exit_by_non_occupant_ignored():
    q = make()
    q.request('pinky1', 'a', 0.0)
    q.request('pinky2', 'b', 1.0)
    assert q.exit('pinky2', 2.0) == (False, None)
    assert q.occupant == 'pinky1'


def test_exit_with_empty_queue_frees_zone():
    q = make()
    q.request('pinky1', 'a', 0.0)
    assert q.exit('pinky1', 1.0) == (True, None)
    assert q.occupant is None


def test_cancel_removes_waiter_only():
    q = make()
    q.request('pinky1', 'a', 0.0)
    q.request('pinky2', 'b', 1.0)
    q.request('pinky3', 'c', 2.0)
    assert q.cancel('pinky2') is True
    assert q.order() == ['pinky3']
    assert q.cancel('pinky1') is False               # 점유자 락은 건드리지 않음
    assert q.occupant == 'pinky1'
    assert q.cancel('nobody') is False


def test_stale_waiter_removed_but_fresh_kept():
    q = make()
    q.request('pinky1', 'a', 0.0)
    q.request('pinky2', 'b', 0.0)
    q.request('pinky3', 'c', 4.0)
    assert q.expire_stale(5.0) == []                 # 경계: 5.0 - 0.0 > 5.0 아님
    assert q.expire_stale(5.1) == ['pinky2']
    assert q.order() == ['pinky3']
    assert q.occupant == 'pinky1'                    # 점유자는 stale 대상 아님


def test_hold_timeout():
    q = make()
    assert q.hold_exceeded(100.0, 120.0) is None     # 점유자 없음
    q.request('pinky1', 'a', 0.0)
    assert q.hold_exceeded(120.0, 120.0) is None
    assert q.hold_exceeded(121.0, 120.0) == pytest.approx(121.0)
    nxt = q.release_and_advance(121.0)
    assert nxt is None and q.occupant is None

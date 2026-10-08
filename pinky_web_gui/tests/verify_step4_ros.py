"""RosBridge 의 Step 4 경로(LiDAR 구독/해제, cmd_vel, 비상정지)를 '로봇과 분리된' ROS 도메인에서 확인한다.

이 스크립트는 가짜 로봇 노드(스캔 발행, cmd_vel/patrol_cmd 수신)와 실제 백엔드(--mock 없음)를 같은 격리 도메인에 띄운다.
실제 로봇 도메인(20, 22, 50)에는 절대 연결하지 않는다: ROS_DOMAIN_ID=77 과 ROS_LOCALHOST_ONLY=1 이 아니면 실행을 거부한다.

실행:
  bash -c 'source /opt/ros/jazzy/setup.bash && ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1 .venv/bin/python tests/verify_step4_ros.py'
"""
import asyncio
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

if os.environ.get('ROS_DOMAIN_ID') != '77' or os.environ.get('ROS_LOCALHOST_ONLY') != '1':
    sys.exit('거부: ROS_DOMAIN_ID=77 과 ROS_LOCALHOST_ONLY=1 일 때만 실행한다 (실제 로봇 도메인과 섞이면 안 된다)')

import rclpy  # noqa: E402
import websockets  # noqa: E402
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data  # noqa: E402
from sensor_msgs.msg import LaserScan  # noqa: E402
from std_msgs.msg import String  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT = 8017
BASE = f'http://127.0.0.1:{PORT}'
WS = f'ws://127.0.0.1:{PORT}/ws'
FAILS = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def http(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


class FakeRobot(Node):
    """pinky1 역할: 스캔과 amcl_pose 를 발행하고, patrol_cmd 와 cmd_vel 을 받아 기록한다. pinky2 는 아무 구독자도 없다."""

    def __init__(self, scan_reliable):
        super().__init__('fake_pinky1')
        self.vel, self.cmd = [], []
        self.create_subscription(Twist, '/pinky1/cmd_vel', lambda m: self.vel.append((time.time(), m.linear.x, m.angular.z)), 10)
        self.create_subscription(String, '/pinky1/patrol_cmd', lambda m: self.cmd.append((time.time(), m.data)), 10)
        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE if scan_reliable else ReliabilityPolicy.BEST_EFFORT)
        self.scan_pub = self.create_publisher(LaserScan, '/pinky1/scan', qos)
        pose_pub = self.create_publisher(PoseWithCovarianceStamped, '/pinky1/amcl_pose',
                                         QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        m = PoseWithCovarianceStamped()
        m.pose.pose.position.x, m.pose.pose.position.y = 1.0, 2.0
        m.pose.pose.orientation.w = 1.0  # yaw = 0
        pose_pub.publish(m)
        self._pose_pub = pose_pub
        self._pose_msg = m
        self.online = True
        self.create_timer(0.05, self._scan)  # 20Hz 로 발행해서 백엔드의 5Hz 제한을 시험한다
        self.create_timer(1.0, self._alive)  # 로봇이 살아 있다는 신호 (아무 토픽이든 오면 online, 5초 없으면 offline)

    def _alive(self):
        if self.online:
            self._pose_pub.publish(self._pose_msg)

    def _scan(self):
        m = LaserScan()
        m.angle_min, m.angle_increment = 0.0, 2 * math.pi / 360
        m.range_min, m.range_max = 0.05, 12.0
        m.ranges = [1.0] * 360
        self.scan_pub.publish(m)


async def until(ws, pred, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.01, end - time.monotonic())))
        except asyncio.TimeoutError:
            return None
        if pred(m):
            return m
    return None


async def wait_for(cond, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def scenario(scan_reliable):
    label = 'RELIABLE' if scan_reliable else 'BEST_EFFORT'
    rclpy.init()
    robot = FakeRobot(scan_reliable)
    spin = threading.Thread(target=lambda: rclpy.spin(robot), daemon=True)
    spin.start()
    log_out = open(os.environ['STEP4_SERVER_LOG'], 'a') if os.environ.get('STEP4_SERVER_LOG') else subprocess.DEVNULL  # 디버깅용
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--port', str(PORT)], cwd=ROOT,
                           stdout=log_out, stderr=log_out)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(BASE + '/api/health')
                break
            except Exception:
                time.sleep(0.1)
        n_scan = lambda: robot.count_subscribers('/pinky1/scan')  # noqa: E731
        await asyncio.sleep(1.0)
        check(f'[{label}] 처음에는 /pinky1/scan 구독자가 없다', n_scan() == 0, n_scan())

        async with websockets.connect(WS) as ws:
            snap = json.loads(await ws.recv())
            await asyncio.sleep(0.5)
            await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
            check(f'[{label}] 켜면 /pinky1/scan 구독자가 생긴다', await wait_for(lambda: n_scan() == 1, 3), n_scan())
            got = []
            t_end = time.monotonic() + 2.0
            while time.monotonic() < t_end:
                m = await until(ws, lambda x: x['type'] == 'scan', 0.5)
                if m:
                    got.append(m)
            check(f'[{label}] 스캔이 5Hz 안팎으로 온다 (발행은 20Hz)', 6 <= len(got) <= 12, len(got))
            if got:
                m = got[-1]
                r = [math.hypot(x - 1.0, y - 2.0) for x, y in m['points']]
                check(f'[{label}] 점이 amcl_pose(1,2) 를 중심으로 반지름 1 m 에 있다 (3개당 1개)',
                      len(m['points']) == 120 and all(abs(v - 1.0) < 0.002 for v in r), (len(m['points']), r[:3]))
            await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': False}))
            check(f'[{label}] 끄면 구독이 해제된다 (구독자 0)', await wait_for(lambda: n_scan() == 0, 3), n_scan())

            # 구독/해제를 빠르게 반복해도 (rclpy 가 spin 중인 상태에서) 죽거나 구독이 남지 않는다
            for _ in range(40):
                await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
                await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': False}))
            await asyncio.sleep(0.8)
            check(f'[{label}] 반복 토글 뒤에도 서버가 살아 있고 구독이 남지 않는다', http('GET', '/api/health')[1]['ok'] and n_scan() == 0, n_scan())
            await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
            check(f'[{label}] 다시 켜면 구독자 1', await wait_for(lambda: n_scan() == 1, 3), n_scan())

        # 화면(WS)이 닫히면 구독이 해제된다
        check(f'[{label}] 화면이 닫히면 구독 해제', await wait_for(lambda: n_scan() == 0, 3), n_scan())

        if scan_reliable:
            return
        # ---- 비상정지: patrol_cmd stop + cmd_vel 0 속도 burst ----
        robot.vel.clear(); robot.cmd.clear()
        t0 = time.time()
        s, body = http('POST', '/api/robots/pinky1/estop', {})
        check('비상정지 200 (stop, cmd_vel 모두 전송)', s == 200 and body['patrol_stop'] and body['cmd_vel'], (s, body))
        await asyncio.sleep(2.6)
        zeros = [v for v in robot.vel if v[0] >= t0 - 0.01]
        check('가짜 로봇이 받은 0 속도 약 20회 (10Hz x 2초)', 19 <= len(zeros) <= 23 and all(v[1] == 0 and v[2] == 0 for v in zeros), len(zeros))
        check('burst 길이 약 2초', 1.7 <= zeros[-1][0] - zeros[0][0] <= 2.3, zeros[-1][0] - zeros[0][0])
        check('patrol_cmd 로 stop 이 도착', any(d == 'stop' for _, d in robot.cmd), robot.cmd)

        # ---- 구독자가 없는 로봇(pinky2): 막지 않고 시도하되 실패를 알린다 ----
        s, body = http('POST', '/api/robots/pinky2/estop', {})
        check('구독자 없는 로봇의 비상정지: 503 + 실패 사유', s == 503 and not body['patrol_stop'] and not body['cmd_vel'] and len(body['errors']) == 2, (s, body))
        s, body = http('POST', '/api/estop', {})
        check('모두 정지: 한 대가 실패해도 다른 로봇은 전송', s == 200 and {r['robot']: r['cmd_vel'] for r in body['results']} == {'pinky1': True, 'pinky2': False}, body)
        hist = http('GET', '/api/state')[1]['commands']
        failed = [c for c in hist if c['cmd'] == 'estop' and c['result'] == 'failed']
        check('실패한 비상정지가 이력에 failed 로 남음', len(failed) >= 1 and failed[0]['robot'] == 'pinky2', failed[:1])
        await asyncio.sleep(2.4)

        # ---- 오프라인 로봇은 수동 조작을 받지 않는다 ----
        robot.online = False
        await asyncio.sleep(6.0)                       # 5초 넘게 아무 토픽이 없다 -> offline
        robot.vel.clear()
        async with websockets.connect(WS) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 0.05, 'angular': 0}))
            den = await until(ws, lambda x: x['type'] == 'drive_denied', 2)
            check('오프라인 로봇의 수동 조작 거절', den and '오프라인' in den['reason'] and not robot.vel, den)
        robot.online = True
        await asyncio.sleep(1.5)                       # 다시 신호가 오면 online

        # ---- 수동 조작 -> 갑작스러운 연결 끊김 ----
        robot.vel.clear()
        async with websockets.connect(WS) as ws:
            await ws.recv()
            for _ in range(5):
                await ws.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 9.0, 'angular': -9.0}))
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.05)
            mv = [v for v in robot.vel if v[1] != 0 or v[2] != 0]
            check('조작 입력이 cmd_vel 로 도착, 상한으로 잘림', mv and all(abs(v[1]) <= 0.1 + 1e-6 and abs(v[2]) <= 0.5 + 1e-6 for v in mv) and abs(mv[0][1] - 0.1) < 1e-6, mv[:2])
            t_cut = time.time()
            ws.transport.abort()
        await asyncio.sleep(0.5)
        z = [v for v in robot.vel if v[0] >= t_cut - 0.01 and v[1] == 0 and v[2] == 0]
        check('연결이 끊기면 0.5초 안에 0 속도가 도착', z and z[0][0] - t_cut <= 0.5, z[:1])
        await asyncio.sleep(0.6)
        check('끊긴 뒤 0 속도 외의 속도는 오지 않음', all(v[1] == 0 and v[2] == 0 for v in robot.vel if v[0] >= t_cut))

        # ---- 수동 조작 중에 서버를 종료해도 로봇은 0 속도로 끝난다 ----
        async with websockets.connect(WS) as ws:
            await ws.recv()
            for _ in range(3):
                await ws.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 0.05, 'angular': 0}))
                await asyncio.sleep(0.1)
            check('(종료 전) 로봇이 움직이는 중', any(v[1] != 0 for v in robot.vel[-4:]))
            t_term = time.time()
            srv.terminate()
            await asyncio.to_thread(srv.wait, 8)   # 이벤트 루프를 막지 않는다 (서버가 보낸 close 프레임을 클라이언트가 처리해야 한다)
        await asyncio.sleep(0.5)
        print('  종료 신호 이후 받은 속도:', [(round(t - t_term, 2), lin) for t, lin, _ in robot.vel if t >= t_term - 0.15])
        check('서버를 종료해도 로봇이 받은 마지막 속도는 0', robot.vel and robot.vel[-1][1] == 0 and robot.vel[-1][2] == 0, robot.vel[-3:])
    finally:
        if srv.poll() is None:
            srv.terminate()
            try:
                srv.wait(timeout=8)
            except subprocess.TimeoutExpired:
                srv.kill()
        rclpy.shutdown()
        spin.join(timeout=3)


async def main():
    print(f'격리 도메인 ROS_DOMAIN_ID={os.environ["ROS_DOMAIN_ID"]}, localhost 전용')
    await scenario(scan_reliable=False)   # 센서 데이터의 일반적인 QoS
    time.sleep(1.5)
    await scenario(scan_reliable=True)    # 발행자가 RELIABLE 이어도 (BEST_EFFORT 구독과) 연결되는지
    print('ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))

# pinky_multi_patrol.py

import rclpy
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
import json, math, os, time


class PinkyPatrol:
    """
    [방법 A] 로봇이 마지막 위치를 기억했다가 재시작 시 그 위치로 초기화하고,
    정해진 웨이포인트를 순찰하는 클래스.

    [수정] 관제 PC에서 domain_bridge를 통해 여러 로봇을 제어할 수 있도록
    로봇 이름(robot_name)을 인자로 받아, 각 로봇마다
      - BasicNavigator의 네임스페이스 (예: /pinky1/navigate_to_pose)
      - 마지막 위치 저장 파일 (로봇별로 분리)
    를 서로 다르게 사용하도록 변경함.
    """

    def __init__(self, robot_name: str):
        # 로봇 이름 저장 (예: 'pinky1', 'pinky2')
        # -> 브릿지 config.yaml에서 remap 해둔 네임스페이스와 반드시 동일해야 함
        self.robot_name = robot_name

        # 로봇별로 마지막 위치를 따로 저장 (파일명이 겹치지 않도록 robot_name 포함)
        self.POSE_FILE = os.path.expanduser(f'~/.pinky_last_pose_{robot_name}.json')

        # ROS2 통신 초기화는 메인(main)에서 한 번만 수행하고,
        # 여기서는 navigator만 생성 (rclpy.init을 여러 번 부르면 에러 발생하므로)
        self.navigator = BasicNavigator(namespace=robot_name)

    # ------------------------------------------------------------------
    # 쿼터니언(orientation) -> yaw(라디안) 각도로 변환
    # ------------------------------------------------------------------
    def quat_to_yaw(self, q):
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.atan2(siny_cosp, cosy_cosp)

    # ------------------------------------------------------------------
    # 목표 지점(x, y, yaw)을 Nav2가 이해하는 PoseStamped 메시지로 만들어줌
    # ------------------------------------------------------------------
    def make_pose(self, x, y, yaw=0.0):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.navigator.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    # ------------------------------------------------------------------
    # 로봇의 "실제(AMCL로 보정된) 현재 위치"를 가져옴
    # ------------------------------------------------------------------
    def get_current_pose(self):
        feedback = self.navigator.getFeedback()
        if feedback is None:
            return None
        cp = feedback.current_pose.pose
        return (cp.position.x, cp.position.y, self.quat_to_yaw(cp.orientation))

    # ------------------------------------------------------------------
    # AMCL에게 "로봇이 지금 대략 여기 있다"고 초기 위치를 알려줌
    # ------------------------------------------------------------------
    def publish_initial_pose(self, x, y, yaw):
        pub = self.navigator.create_publisher(PoseWithCovarianceStamped, 'initialpose', 10)

        deadline = time.time() + 5.0
        while pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.1)

        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.navigator.get_clock().now().to_msg()
        msg.pose.pose.position.x = x
        msg.pose.pose.position.y = y
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.3
        pub.publish(msg)

    # ------------------------------------------------------------------
    # 초기 위치 publish 직후, 로봇을 제자리에서 회전시켜
    # AMCL이 실제 방향으로 스스로 수렴하도록 도와주는 메서드
    # ------------------------------------------------------------------
    def spin_in_place(self, duration=4.0, angular_speed=0.5):
        cmd_pub = self.navigator.create_publisher(Twist, 'cmd_vel', 10)

        deadline = time.time() + 5.0
        while cmd_pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.1)

        twist = Twist()
        twist.angular.z = angular_speed

        end_time = time.time() + duration
        while time.time() < end_time:
            cmd_pub.publish(twist)
            time.sleep(0.1)

        cmd_pub.publish(Twist())
        time.sleep(0.5)

    # ------------------------------------------------------------------
    # 마지막 위치(x, y, yaw)를 JSON 파일로 저장
    # ------------------------------------------------------------------
    def save_last_pose(self, x, y, yaw=0.0):
        with open(self.POSE_FILE, 'w') as f:
            json.dump({'x': x, 'y': y, 'yaw': yaw}, f)

    # ------------------------------------------------------------------
    # 저장해둔 마지막 위치를 불러옴 (파일 없으면 None)
    # ------------------------------------------------------------------
    def load_last_pose(self):
        if os.path.exists(self.POSE_FILE):
            with open(self.POSE_FILE) as f:
                data = json.load(f)
                data.setdefault('yaw', 0.0)
                return data
        return None

    # ------------------------------------------------------------------
    # 순찰 실행 메서드
    # [수정] 기존에는 여기서 rclpy.init()/rclpy.shutdown()까지 같이 했지만,
    #   두 로봇을 한 프로세스(같은 rclpy context)에서 순차 실행하려면
    #   init/shutdown은 메인에서 한 번만 호출해야 하므로 run()에서는 제거함.
    # ------------------------------------------------------------------
    def run(self):
        print(f'\n===== [{self.robot_name}] 순찰 시작 =====')

        last = self.load_last_pose()

        if last is None:
            start_x, start_y, start_yaw = 0.0, 0.0, 0.0
        else:
            start_x, start_y, start_yaw = last['x'], last['y'], last['yaw']
            self.publish_initial_pose(start_x, start_y, start_yaw)

            print(f'[INFO][{self.robot_name}] 초기 방향 보정을 위해 제자리 회전을 시작합니다...')
            self.spin_in_place(duration=4.0, angular_speed=0.5)
            print(f'[INFO][{self.robot_name}] 초기 방향 보정 완료.')

        self.navigator.waitUntilNav2Active()

        # 순서대로 방문할 웨이포인트 목록
        # 로봇마다 다른 맵/경로를 쓴다면 robot_name 별로 waypoints를 분기해도 됨
        waypoints = [
            self.make_pose(0.08, 0.02, 0),
            self.make_pose(0.59, 0.09, -1.57),
            self.make_pose(1.49, -0.45, 3.14),
            self.make_pose(1.00, -0.45, 1.57),
            self.make_pose(1.49, -0.03, 3.14),
            self.make_pose(0.07, -0.68, 1.57),
            self.make_pose(0.08, 0.02, 0),
        ]

        for i, wp in enumerate(waypoints):
            for attempt in range(3):
                self.navigator.goToPose(wp)
                while not self.navigator.isTaskComplete():
                    time.sleep(0.1)
                result = self.navigator.getResult()

                current = self.get_current_pose()
                if current is None:
                    print(f'[WARN][{self.robot_name}] {i+1}번째 목표: 실제 위치를 못 읽어 명령 좌표로 저장합니다.')
                    current = (wp.pose.position.x, wp.pose.position.y, 0.0)
                self.save_last_pose(*current)

                if result == TaskResult.SUCCEEDED:
                    print(f'[{self.robot_name}] {i+1}번째 목표 도착 성공 ({attempt+1}번째 시도)')
                    break
                else:
                    print(f'[{self.robot_name}] {i+1}번째 목표 {attempt+1}번째 시도 실패: {result}, 재시도합니다')
            else:
                print(f'[{self.robot_name}] {i+1}번째 목표 최종 실패 (3회 모두 실패)')

        print(f'===== [{self.robot_name}] 순찰 종료 =====\n')

    def destroy(self):
        # 다음 로봇으로 넘어가기 전에 이 로봇의 노드를 정리
        self.navigator.lifecycleShutdown()
        self.navigator.destroy_node()


def main(args=None):
    # rclpy는 프로세스 전체에서 딱 한 번만 init
    rclpy.init()

    # 관제 PC(도메인 100)에서 domain_bridge로 넘어온 네임스페이스와
    # 이름이 반드시 일치해야 함 (예: /pinky1/..., /pinky2/...)
    robot_names = ['pinky1', 'pinky2', 'pinky3']

    for name in robot_names:
        patrol = PinkyPatrol(name)
        patrol.run()
        patrol.destroy()

    rclpy.shutdown()


if __name__ == '__main__':
    main()
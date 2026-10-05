import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import sys
import time


class PatrolClient(Node):
    """
    관제 PC에서 실행하는 노드.
    - 로봇에게 명령(start / stop / goto)을 보낸다.   (publisher)
    - 로봇의 상태(status)를 구독해서 화면에 출력한다. (subscriber)
    """

    def __init__(self, namespace):
        # 노드 이름 = 'patrol_client_' + 로봇 이름
        # 예) namespace가 'pinky1' → 'patrol_client_pinky1'
        # (로봇마다 이름이 달라야 여러 개를 동시에 실행해도 충돌하지 않음)
        super().__init__('patrol_client_' + namespace)

        # [Publisher] 로봇에게 명령을 보내는 통로
        # 예) '/pinky1/patrol_cmd'  (String 타입, 큐 크기 10)
        topic_cmd = '/' + namespace + '/patrol_cmd'
        self.cmd_pub = self.create_publisher(String, topic_cmd, 10)

        # [Subscriber] 로봇이 보내는 상태를 받는 통로
        # 예) '/pinky1/patrol_status'
        # 메시지가 도착하면 self.status_callback 이 자동 호출됨
        topic_status = '/' + namespace + '/patrol_status'
        self.create_subscription(String, topic_status, self.status_callback, 10)

    def status_callback(self, msg):
        # 로봇이 상태를 보낼 때마다 자동 실행 (우리가 직접 호출하지 않음)
        print('상태 수신:', msg.data)

    def send_command(self, cmd):
        # 노드가 켜진 직후에는 로봇과 연결(discovery)이 안 됐을 수 있음
        # → 1초 기다린 뒤 보내야 메시지가 유실되지 않음
        time.sleep(1.0)

        msg = String()             # 1) 빈 메시지 상자 생성
        msg.data = cmd             # 2) 상자(.data)에 명령 문자열 담기
        self.cmd_pub.publish(msg)  # 3) 토픽으로 전송

        # [설계 이유]
        # start / stop / goto 마다 함수를 따로 만들지 않고,
        # 문자열만 바꿔 넣는 하나의 함수로 통합 → 명령이 늘어나도 코드 수정이 적음


def main():
    # [인자 개수 확인]
    # 실행 형식: python3 patrol_client.py <pinky1|pinky2> <start|stop|monitor|goto> [포인트...]
    # sys.argv[0]=파일명, [1]=로봇 이름, [2]=동작, [3:]=포인트들
    # → 최소 3개(파일명, 로봇, 동작)가 없으면 사용법 출력 후 종료
    if len(sys.argv) < 3:
        print('사용법: python3 patrol_client.py <pinky1|pinky2> <start|stop|monitor|goto> [포인트]')
        print('예시  : python3 patrol_client.py pinky1 goto P2')
        return

    namespace = sys.argv[1]   # 예) 'pinky1'
    action = sys.argv[2]      # 예) 'start'

    rclpy.init()                    # ROS2 통신 시작 (노드 만들기 전에 필수)
    node = PatrolClient(namespace)

    # ── start / stop : 문자열 그대로 전송 ──
    if action == 'start' or action == 'stop':
        node.send_command(action)
        print(namespace, '에게', action, '명령 전송 완료')

    # ── goto : 포인트 목록을 하나의 문자열로 묶어 전송 ──
    # 예) 입력 'goto P2 P3 P6' → 로봇에는 'goto:P2,P3,P6' 로 전송
    elif action == 'goto':
        if len(sys.argv) < 4:
            # 포인트가 하나도 없는 경우
            print('포인트가 필요합니다. 예: python3 patrol_client.py pinky1 goto P2 P3 P6')
        else:
            # sys.argv[3:] = ['p2', 'P3', 'p6'] (포인트 목록)
            # p.upper()    = 대문자로 통일 ('p2' → 'P2')
            # ','.join()   = 쉼표로 이어 붙이기 → 'P2,P3,P6'
            points = ','.join(p.upper() for p in sys.argv[3:])
            node.send_command('goto:' + points)
            print(namespace, '에게 goto:' + points, '명령 전송 완료')

    # ── monitor : 종료 전까지 계속 상태 수신 ──
    elif action == 'monitor':
        print(namespace, '상태 모니터링 시작 (Ctrl+C로 종료)')
        rclpy.spin(node)   # 계속 대기하며 status_callback 실행

    # ── 그 외 : 잘못된 입력 ──
    else:
        print('알 수 없는 명령:', action)

    # 뒷정리 (노드 삭제 → ROS2 종료)
    node.destroy_node()
    rclpy.shutdown()


# 이 파일을 직접 실행했을 때만 main() 호출
# (다른 파일에서 import 할 때는 실행되지 않음)
if __name__ == '__main__':
    main()
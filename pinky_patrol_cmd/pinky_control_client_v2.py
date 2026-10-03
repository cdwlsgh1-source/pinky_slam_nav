import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import sys
import time


class PatrolClient(Node):
    """
    관제 PC에서 실행하는 노드.
    로봇에게 명령(start/stop)을 보내거나, 로봇 상태(status)를 구독해서 화면에 출력한다.
    """

    def __init__(self, namespace):
        # 노드 이름을 'patrol_client_pinky1' 같은 식으로 짓는다
        super().__init__('patrol_client_' + namespace)        # 결과 = patrol_client_pinky1(2)

        # 명령을 보낼 토픽 (publisher)
        topic_cmd = '/' + namespace + '/patrol_cmd'           #  결과 = /pinky1(2)/patrol_cmd
        self.cmd_pub = self.create_publisher(String, topic_cmd, 10)

        # 상태를 받을 토픽 (subscriber)
        topic_status = '/' + namespace + '/patrol_status'     # 결과 topic_status = /pinky1(2)/patrol_status
        self.create_subscription(String, topic_status, self.status_callback, 10)

    def status_callback(self, msg):
        # 로봇이 상태를 보내올 때마다 이 함수가 자동으로 실행됨
        print('상태 수신:', msg.data)

    def send_command(self, cmd):
        # 노드가 막 켜진 직후라 로봇과 연결이 안 됐을 수도 있으니
        # 1초 정도 잠깐 기다렸다가 명령을 보낸다
        time.sleep(1.0)
        msg = String()             # 빈 메시지 상자 만듬
        msg.data = cmd             # String().data 값에 cmd 값을 복사
        self.cmd_pub.publish(msg)  # create_publisher.publisher(msg) / 복사된 cmd 값을 publish 함 
        """이렇게 사용하는 이유는: 
        명령어 종류마다 Start, Stop, monitor 등 함수를 따로 만들지 않고 하나의 함수로 통합하기 위함"""

def main():
    # === GOTO-RETURN MODIFIED: 사용법 안내에 goto 추가 ================================
    # 기존: python3 patrol_client.py <pinky1|pinky2> <start|stop|monitor>
    if len(sys.argv) < 3:
        print('사용법: python3 patrol_client.py <pinky1|pinky2> <start|stop|monitor|goto> [포인트]')
        print('예시  : python3 patrol_client.py pinky1 goto P2')
        return
    # ============================================================================

    namespace = sys.argv[1]
    action = sys.argv[2]

    rclpy.init()
    node = PatrolClient(namespace)

    if action == 'start' or action == 'stop':
        node.send_command(action)
        print(namespace, '에게', action, '명령 전송 완료')
    
    # === GOTO-RETURN ADDED: goto 명령 ==============================================
    # 'goto P2' 로 입력하면 로봇에는 'goto:P2' 문자열로 전송됩니다.
    elif action == 'goto':
        if len(sys.argv) < 4:
            print('포인트 이름이 필요합니다. 예: python3 patrol_client.py pinky1 goto P2')
        else:
            point = sys.argv[3].upper()    # p2 -> P2 (로봇 쪽에서도 upper() 처리하지만 이중 안전)
            node.send_command('goto:' + point)
            print(namespace, '에게 goto:' + point, '명령 전송 완료')
    # ============================================================================

    elif action == 'monitor':
        print(namespace, '상태 모니터링 시작 (Ctrl+C로 종료)')
        rclpy.spin(node)

    else:
        print('알 수 없는 명령:', action)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()

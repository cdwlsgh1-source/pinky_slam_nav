# 알려진 함정

> 역할: CLAUDE.md 에서 옮겨 온 참고 문서. 작업에 필요할 때만 읽는다. 내용은 옮기기 전과 같고, 규칙은 `CLAUDE.md` 에 남아 있다.
> 옮긴 날: 2026-10-11

- **goto 검증 FAILED**: 순찰 노드는 작업 중인지 확인하기 *전에* goto 입력을 검증한다 (`pinky1_v2.py:265-279`). 작업 중에 빈/알 수 없는/금지 포인트를 보내면 `FAILED`가 발행되어 진행 중인 작업의 상태처럼 보일 수 있다. 백엔드에서 사전 검증한다.
- **`start`/`goto` 무시 시 무응답**: 이미 작업 중이면 경고 로그만 남고 `patrol_status`는 발행되지 않는다 (`:253`, `:280`). `stop`도 작업 중이 아니면 무응답 (`:292`).
- **보정 중 stop**: 첫 작업의 초기 위치 보정 회전(4초)은 `stop`을 확인하지 않는다 (`:376-378`, `:398`).
- **늦게 접속한 구독자**: `patrol_status`의 `IDLE`은 시작 시 한 번만 발행(`:155`), `/zone_manager/status`는 상태가 바뀔 때만 발행(`zone_manager_node.py:134,140`). 처음 접속한 GUI는 현재 값을 모를 수 있다. 실제 QoS 동작은 **확인 필요**.
- **`waypoint`/`detail` 의미**: `start`와 `goto`에서 다르다. `docs/interfaces.md` 4절 참고.
- **pinky2 경로**: `start`는 5개 `RED2IN(0) → P3(1) → P6(2) → RED2OUT(3) → P7(4)`, `DONE.waypoint`=5, `goto` 홈도 P7이다 (`pinky2_v2.py:84-90,109`). pinky1은 6개(`P2` 경유)이고 진입/이탈 인덱스 1/4, `DONE.waypoint`=6이다. 로봇별 경로를 설정으로 가져야 한다.
- **pinky2 인덱스 불일치 (미해결, 2026-10-06 확인)**: 현재 파일의 `ZONE_ENTRY_INDEX = 1`, `ZONE_EXIT_INDEX = 4` (`pinky2_v2.py:98-99`)는 5개 경로의 `RED2IN(0)`/`RED2OUT(3)`과 맞지 않는다. 이 값이면 P3 도착 시 진입 허가를 요청하고 P7 도착 시 이탈을 알린다. 올바른 값은 0/3이다. 사용자가 이전에 고쳤으나 GitHub 동기화로 되돌아갔다. 코드는 수정하지 않았고 사용자가 고쳐야 한다. 같은 파일 83행 주석과 `WAYPOINTS` 줄 주석(85-89)의 경로·좌표도 옛 값이다 (`POINTS`가 정답).
- **하드코딩 절대 경로**: `pinky_camera/config/lane_detector_params.yaml:4`(`/home/jinho/dev_ws/best.pt`, 이 PC에 파일 없음), `pinky_camera/pinky_camera/lane_detector_node.py:235-236`(`/home/jinho/dev_ws/pinky_slam_nav/pinky_camera/...pt`). 홈 상대 경로 `~/.pinky_last_pose.json`은 순찰 노드 6개 파일에 있다 (`pinky1_v2.py:64`, `pinky2_v2.py:64`, `pinky_patrol_node_pinky1.py:29`, `pinky2.py:29`, `my_pinky_patrol.py:15`, `pinky_patrol.py:15`). 수정은 하지 않는다.
- **setup.py**: `my_pinky_package/setup.py:30`의 `pinky_patrol_node_zone_pinky1`은 가리키는 모듈이 없지만 빌드에 영향이 없다 (사용자 확인). 수정하지 않는다.
- **위험 구역 = P3, P4, P5, P6** (사용자 확인). 영역 좌표는 코드에 없고 `ZONE_POINTS`로만 정의된다 (`docs/interfaces.md` 5-1절).
- **GUI 서버 `kill -9`**: 서버가 켠 프로세스를 정리하지 못해 관제 PC 쪽 자식(`domain_bridge`)이 남고, 다음 시작이 "이미 실행 중"으로 거절된다. 정상 종료(Ctrl-C, SIGHUP, SIGTERM)는 켠 프로세스를 모두 정지한다 (`pinky_web_gui/README.md` 인증·프로세스 제어 절).
- **SSH 비대화형 셸**: `~/.bashrc` 를 읽지 않아 `RMW_IMPLEMENTATION`, `CYCLONEDDS_URI` 가 빠진다. 로봇 프로세스는 `processes.yaml` 의 `env`/`source` 에 명시한다.
- **bringup 만 켠 로봇의 online 판정**: `amcl_pose`/`patrol_status` 가 없어서 `/odom`(브리지에 있음)을 생존 확인용으로 받는다 (`backend/ros_bridge.py`).
- **GUI 프로세스 제어는 실제 로봇에서 한 번도 실행하지 않았다** (mock 검증만). 실물 절차는 `docs/checklists/step6_process_control_real_checklist.md`.

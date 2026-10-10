# 프로세스 제어 실물 확인 절차 (사용자 수행)

GUI 의 시스템 패널로 도메인 브릿지, zone_manager, 로봇의 bringup/map 을 켜고 끄는 기능이다. mock 에서만 검증했고 **실제 ros2/ssh 는 한 번도 실행하지 않았다.** 아래 순서대로, 로봇이 움직이지 않는 단계부터 확인한다.

## 0. 준비 (한 번만)

1. 서버를 실행하고 `mngr` 계정(비밀번호 mngr, manager 역할)으로 로그인한다 (계정은 `backend/auth.py` 에 고정: mngr/mngr, oper/oper). `--no-auth` 로 실행하면 프로세스 제어는 꺼진다.
   ```bash
   cd pinky_web_gui && .venv/bin/python -m backend.main
   ```
2. 로봇 SSH 키 등록 (비밀번호는 이때 터미널에 직접 입력):
   ```bash
   ssh-copy-id <user>@<pinky1 주소>
   ssh <user>@<pinky1 주소> true        # 처음 접속 확인(known_hosts 등록). 이후 서버는 BatchMode 로만 접속한다
   ```
   키를 못 쓰면 `processes.yaml` 의 `ssh.password_env` 에 환경 변수 이름을 적고 `sshpass` 를 설치한다.
3. `config/processes.yaml` 의 pinky1 항목은 아래 값으로 채워져 있다. **로봇에서 직접 확인한 값이 아니므로** 먼저 맞는지 본다 (pinky2 는 비어 있다).
   ```yaml
   ssh: {host: "192.168.45.20", user: "pinky"}
   cwd: /home/pinky                         # 터미널의 기본 위치(~). 지도 my_pinky_map10.yaml 이 여기서 찾아진다
   source: ["/opt/ros/jazzy/setup.bash", "/home/pinky/pinky_slam_nav/install/setup.bash"]    # ROS 배포판 확인
   command: "ros2 launch pinky_bringup bringup_robot.launch.xml"           # map 은 ros2 launch pinky_navigation bringup_launch.xml map:=my_pinky_map10.yaml
   ```
   로봇에서 확인할 것 (터미널에서 직접):
   ```bash
   ssh pinky@192.168.45.20 'ls -d ~/pinky_slam_nav ~/pinky_slam_nav /opt/ros/*/setup.bash 2>&1'
   ```
   - SSH 는 `~/.bashrc` 를 읽지 않으므로 `/opt/ros/<배포판>/setup.bash` 가 `source` 에 있어야 한다.
   - `map:=my_pinky_map10.yaml` 을 로봇이 `cwd` 에서 찾는지 (못 찾으면 로그에 map_server 오류. 절대 경로로 바꾼다).
   로봇의 ROS 도메인은 `domain: 20` 으로 export 한다. 서버를 다시 시작해야 반영된다.

## A. 로봇이 움직이지 않는 확인

| # | 할 일 | 기대 | 실패하면 |
|---|---|---|---|
| A1 | 로그인 | operator 는 시스템 패널 버튼이 모두 꺼짐. manager 는 켜짐 | |
| A2 | 터미널에서 브릿지를 미리 켜 둔 채 GUI 로 `도메인 브릿지` 시작 | "이미 실행 중인 같은 프로세스가 있습니다 (pid …)" 로 거절 | `ps aux \| grep domain_bridge` 로 패턴 확인 (`detect`) |
| A3 | 터미널 브릿지를 끄고 GUI 로 시작 | 실행 중 → 로봇 토픽이 오면 초록, 아니면 "토픽 없음". `ros2 topic list` 에 `/pinky1/...` | 로그 보기 확인. `source` 경로 문제면 로그에 나온다 |
| A4 | 로그 버튼 | 시작 줄(`$ local: …`)과 브릿지 출력 | |
| A5 | 정지 | 정지. 브릿지 프로세스가 남지 않음 (`pgrep -fa domain_bridge`) | 자식이 남으면 알려 주세요 |
| A6 | zone_manager 시작/정지 | 구역 배지가 "구역: 비어 있음" 으로 바뀜 (`ros2 topic echo /zone_manager/status`) | |
| A7 | 로봇 SSH 항목에 host 가 틀린 상태로 시작 | 실패 표시 + 사유(접속 불가). 로봇 쪽에 아무것도 시작되지 않음 | |

## B. 로봇 프로세스 (SSH)

> 로봇이 움직일 수 있는 단계다. 바퀴를 띄우거나 주변을 비우고 한다.

| # | 할 일 | 기대 |
|---|---|---|
| B1 | `pinky1 bringup` 시작 | 실행 중 → 곧 `pinky1` online (health 초록). 로봇에서 `ros2 node list` 로 노드 확인 |
| B2 | 같은 명령을 로봇에서 터미널로 미리 켠 뒤 GUI 시작 | "이미 실행 중" 거절 (`detect` 가 로봇에서 동작하는지) |
| B3 | `pinky1 map (Nav2)` 시작 | 실행 중. `/amcl_pose` 가 오고 지도 위에 로봇이 보임 |
| B4 | `pinky1 map` 정지 (확인 팝업) | 팝업이 뜨고 승인하면 정지. 로봇에서 `ros2 node list` 에 Nav2 노드가 사라지고 `pgrep -fa <detect>` 가 비어 있음 |
| B5 | 정지했는데 로봇에 남은 경우 | 패널이 "SSH 는 종료했지만 로봇에서 아직 실행 중입니다 (pid …)" 로 **실패** 표시. 로봇에서 직접 종료 |
| B6 | 전체 시작 | 도메인 브릿지 → bringup → map → zone_manager → 순찰 노드 순서. bringup 뒤에 로봇 토픽이 올 때까지 기다리고, 안 오면 브릿지를 한 번 자동 재시작. 그래도 안 오면 거기서 멈춤. 중간에 실패하면 거기서 멈추고 사유가 표시됨 |
| B7 | 전체 정지 | 반대 순서로 정지. 확인 팝업에 로봇 경고가 나옴 |

## C. 서버 재시작

| # | 할 일 | 기대 / 확인 |
|---|---|---|
| C1 | 브릿지·zone_manager 를 켠 채 서버를 Ctrl-C (터미널 창 닫기도 같다) | 관제 PC 쪽 프로세스는 같이 정지. `pgrep -fa domain_bridge` 비어 있음 |
| C2 | 로봇 bringup/map 을 켠 채 서버를 Ctrl-C (종료에 몇 초 걸린다) | 로봇의 프로세스도 같이 정지. 로봇에서 `pgrep -fa "ros2 launch pinky_"` 가 비어 있음. 남아 있으면 알려 주세요 |
| C3 | 서버를 다시 켠 뒤 시작 | 바로 시작된다 (C2 가 잘 정리됐다면 "이미 실행 중" 이 나오지 않는다) |
| C4 | `kill -9` 로 서버를 죽이거나 전원이 꺼진 뒤 다시 시작 | 정리하지 못하므로 "이미 실행 중 (pid …)" 으로 거절된다. 로봇에서 직접 종료: `ssh pinky@<주소> 'pkill -INT -f "[r]os2 launch pinky_bringup"'` |

## 알려진 한계

- GUI 는 자기가 띄운 프로세스만 정지할 수 있다. 터미널에서 직접 켠 것은 감지만 한다.
- SSH 로 원격 프로세스를 끄는 것은 네트워크가 끊기면 보장되지 않는다. 로봇에서 `ros2 node list` 로 확인한다.
- 소프트웨어 버튼이다. 주행 중 비상 상황은 기존 비상정지(하드웨어 정지 포함)로 대응한다.
- 평문 HTTP 로 비밀번호가 오간다. 신뢰하는 내부망/VPN 에서만 쓴다.
- `kill -9` 로 서버를 죽이면 관제 PC 쪽 자식 프로세스가 남을 수 있다 (`pgrep -fa domain_bridge`).

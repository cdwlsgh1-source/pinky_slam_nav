# processes.yaml 정리 (GUI 시스템 패널 프로세스 설정)

## 1. 한 줄 요약
GUI "시스템" 패널에서 켜고 끌 프로세스를 정의하는 설정 파일. 터미널에서 손으로 치던 실행 명령(로컬 실행, 로봇 SSH 실행)을 GUI 서버가 이 파일을 읽어 대신 실행한다.

- 파일 자체가 실행되는 것이 아니라, 이 파일을 읽는 GUI 서버가 실행을 담당한다.
- 이 목록에 있는 프로세스만 GUI에서 실행할 수 있다.
- 로그인(manager 계정)이 필요하다. 서버를 `--no-auth`로 띄우면 이 기능은 꺼진다.
- 비밀번호는 이 파일에 적지 않는다.

## 2. 동작 방식
1. 서버 시작 시 파일을 읽어 "시스템" 패널에 프로세스 목록을 만든다.
2. [시작] 클릭 시 항목 설정대로 명령을 실행한다.
   - `kind: local` → 관제 PC에서 직접 실행
   - `kind: ssh` → 로봇에 SSH 접속 후 실행
3. [전체 시작]은 목록 순서대로, [전체 정지]는 반대 순서로 실행한다.

**실행 순서 (전체 시작 기준)**
도메인 브릿지 → bringup → map → zone_manager → 순찰 노드

## 3. 파일 구조

| 영역 | 역할 |
|---|---|
| `defaults.local_source` | 로컬(관제 PC) 프로세스에서 공통으로 source 할 파일 |
| `defaults.pinky1_env`, `pinky2_env` | 로봇별 DDS 환경 변수 (YAML 앵커 `&`로 정의, `*`로 재사용) |
| `processes` | 실행할 프로세스 목록 (순서 = 시작 순서) |

### 치환 변수
- `{repo}` = pinky_slam_nav 폴더의 절대 경로

## 4. 프로세스 항목 필드

| 필드 | 설명 |
|---|---|
| `id` | 프로세스 고유 이름 |
| `group` | 패널에서 같은 이름끼리 묶어 표시 (묶음 순서 = 처음 나온 순서, 비우면 '기타') |
| `label` | 화면에 표시되는 이름 |
| `kind` | `local`(관제 PC) 또는 `ssh`(로봇) |
| `robot` | 대상 로봇 이름 (ssh 항목) |
| `domain` | ROS 도메인 ID |
| `ssh` | 접속 정보 `{host, user, port}` |
| `cwd` | 명령을 실행할 위치. 로봇 쪽은 절대 경로만 가능 (`~` 불가) |
| `source` | 실행 전에 source 할 setup.bash 목록 |
| `env` | 실행 전에 export 할 환경 변수 |
| `command` | 실제 실행 명령. 비어 있으면 "명령 미설정"으로 표시되고 시작할 수 없음 |
| `detect` | `pgrep -f` 패턴(정규식). 같은 프로세스가 이미 도는지 확인 (터미널에서 직접 켠 것과 중복 방지). 영문/숫자로 시작해야 함 |
| `health` | 상태 확인 대상 여부 (bringup 항목에 사용) |
| `restart_if_stalled` | 로봇 토픽이 안 오면 [전체 시작]이 이 프로세스를 한 번 재시작 |

## 5. 터미널 명령과의 대응
`bringup_pinky1` 항목은 터미널로 풀면 다음과 같다.

```bash
ssh pinky@192.168.45.20                      # ssh
cd /home/pinky                               # cwd
source /opt/ros/jazzy/setup.bash             # source
source /home/pinky/pinky_slam_nav/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp # env
export CYCLONEDDS_URI=file:///home/pinky/.ros/cyclonedds_robot.xml
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
ros2 launch pinky_bringup bringup_robot.launch.xml   # command
```

## 6. 현재 구성된 프로세스

| 분류 | id | 실행 위치 | 도메인 | 명령 |
|---|---|---|---|---|
| 1. 도메인 브릿지 | bridge | 관제 PC | 100 | `ros2 launch domain_bridge bridges.launch.xml` |
| 2. bringup | bringup_pinky1 / 2 | 로봇 SSH | 10 / 20 | `ros2 launch pinky_bringup bringup_robot.launch.xml` |
| 3. map (Nav2) | map_pinky1 / 2 | 로봇 SSH | 10 / 20 | `ros2 launch pinky_navigation bringup_launch.xml map:=my_pinky_map10.yaml` |
| 4. zone_manager | zone_manager | 관제 PC | 100 | `ros2 launch zone_traffic_control zone_manager.launch.xml` |
| 5. 순찰 노드 | patrol_pinky1 / 2 | 로봇 SSH | 10 / 20 | `ros2 run my_pinky_package pinky_patrol_node_pinky{1,2}` |

- 로봇 IP: pinky1 = 192.168.45.20, pinky2 = 192.168.45.22 (user: pinky, port 22)
- 도메인 브릿지: pinky1(10↔100), pinky2(20↔100), pinky3(30↔100)를 하나의 launch로 같이 띄움

## 7. 핵심 개념과 주의점

### SSH로 실행하면 ~/.bashrc가 안 읽힌다
로봇 터미널에서는 자동으로 되던 설정이 SSH 실행에서는 빠진다. 그래서 `source`(ROS 배포판 setup.bash 포함)와 `env`를 직접 적어야 한다.

### DDS 환경 변수가 빠지면 데이터가 안 온다
`RMW_IMPLEMENTATION`, `CYCLONEDDS_URI`가 빠지면 로봇이 기본 DDS(FastDDS)로 떠서, PC의 CycloneDDS 브릿지에 토픽 이름만 보이고 데이터가 안 온다. 값은 로봇 터미널에서 `env | grep -E "RMW|CYCLONE|ROS_"`로 확인해 같게 둔다.

### 브릿지 시작 순서와 QoS
브릿지를 먼저 켜면 로봇 토픽이 없을 때 구독이 기본 QoS(reliable)로 만들어져, best_effort 센서 토픽(`/odom`, `/scan`)이 안 넘어올 수 있다. 그래서 [전체 시작]은 bringup 이후 로봇 토픽이 오는지 확인하고, 안 오면 `restart_if_stalled: true`인 브릿지를 한 번 재시작한다.

### 순찰 노드는 map(Nav2) 이후에 켠다
순찰 노드가 Nav2를 쓰기 때문이다. 목록 순서가 곧 시작 순서이므로 순서를 바꾸지 않는다.

### detect 패턴 규칙
서버가 패턴 첫 글자를 `[x]`로 감싸 자기 자신(pgrep 프로세스)을 찾지 않게 하므로, 패턴은 영문/숫자로 시작해야 한다.

## 8. SSH 사전 준비 (한 번만, 터미널에서 직접)
```bash
ssh-copy-id <user>@<host>      # 비밀번호는 터미널에만 입력. 이후 서버는 키로만 접속
ssh <user>@<host> true         # 처음 접속 확인 (known_hosts 등록)
```
- pinky2도 키 등록 필요: `ssh-copy-id pinky@192.168.45.22`
- 키 대신 비밀번호를 써야 하면 `ssh.password_env`에 환경 변수 이름을 적고 서버 실행 환경에 그 변수를 둔다 (sshpass 설치 필요).

## 9. 새 로봇(pinky3 등) 추가
GUI 왼쪽 메뉴바의 [로봇 추가] 팝업에서 이름, 도메인 ID, IP를 입력하면 이 파일에 붙여 넣을 내용과 GUI 밖에서 할 일(브릿지, zone_manager, 순찰 노드, SSH 키)이 나온다.

- 이 파일에 미리 실제로 써 두지 않는다. 로봇이 아직 없거나 지점(RED*)이 없으면 서버가 시작하지 못하고, [전체 시작]이 그 로봇에서 멈추기 때문이다.

## 10. 작성 규칙
- 항목 순서 = [전체 시작] 순서, 역순 = [전체 정지] 순서
- `command`를 비우면 시작 불가 (미설정 상태 표시)
- 비밀번호는 파일에 적지 않는다
- 로컬 항목의 `cwd`는 PC 경로 (`{repo}`), 로봇 항목의 `cwd`는 로봇 경로 (`/home/pinky/...`)
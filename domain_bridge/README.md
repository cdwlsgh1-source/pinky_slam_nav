# domain_bridge

관제 PC(도메인 100)와 핑키 로봇(도메인 10 / 20 / 30) 사이의 ROS 2 통신을 이어 주는 도메인 브릿지.
[ros2/domain_bridge](https://github.com/ros2/domain_bridge) 를 기반으로 하며, 이 저장소에는 핑키 3대용 설정과 런치 파일이 추가되어 있다.

동작 원리는 [design document](doc/design.md) 참고.

## 구성

| 로봇 | 로봇 도메인 | 관제 PC 도메인 | 설정 파일 |
|---|---|---|---|
| pinky1 | 10 | 100 | `config/pinky_bridge_pinky1.yaml` |
| pinky2 | 20 | 100 | `config/pinky_bridge_pinky2.yaml` |
| pinky3 | 30 | 100 | `config/pinky_bridge_pinky3.yaml` |

브릿지는 **관제 PC에서** 실행한다. 로봇마다 프로세스 하나씩, 총 3개가 뜬다.

### 토픽 이름 규칙

로봇 쪽(10/20/30)은 네임스페이스 없이 원래 이름을 쓰고, 관제 PC 쪽(100)은 `/pinkyN/...` 으로 remap 한다.

| 방향 | 로봇 쪽 토픽 | 관제 PC 쪽 토픽 |
|---|---|---|
| 관제 → 로봇 | `/patrol_cmd`, `/cmd_vel`, `/initialpose`, `/goal_pose` | `/pinkyN/patrol_cmd`, `/pinkyN/cmd_vel`, `/pinkyN/initialpose`, `/pinkyN/goal_pose` |
| 로봇 → 관제 | `/patrol_status`, `/scan`, `/odom`, `/tf`, `/tf_static`, `/amcl_pose`, `/battery_state`, `/battery/percent`, `/battery/voltage` | `/pinkyN/patrol_status`, `/pinkyN/scan`, `/pinkyN/odom`, `/pinkyN/tf`, `/pinkyN/tf_static`, `/pinkyN/amcl_pose`, `/pinkyN/battery_state`, `/pinkyN/battery/percent`, `/pinkyN/battery/voltage` |

`/zone_manager/*` (진입 요청·허가, 대기 슬롯 등)은 메시지 안에 `robot_id` 가 들어 있어 remap 없이 이름 그대로 건넌다.

**pinky1 만 추가로 넘기는 토픽:** `/pinky1/lane/center_offset`, `/pinky1/lane/detected`, `/pinky1/crossline/detected`.
차선 주행 노드(`lane_follower_node`)가 관제 PC에서 돌기 때문이다. 로봇이 이미 `pinky1` 네임스페이스로 발행하므로 remap 하지 않는다.

## 설치

```sh
cd <워크스페이스>
colcon build --packages-select domain_bridge
source install/setup.bash
```

## 실행 (bridges 런치)

`launch/bridges.launch.xml` 하나로 3대 브릿지를 한 번에 띄운다.

```sh
ros2 launch domain_bridge bridges.launch.xml
```

일부 로봇만 띄우려면 로봇 이름을 `false` 로 지정한다.

```sh
# pinky2 제외
ros2 launch domain_bridge bridges.launch.xml pinky2:=false

# pinky1 만
ros2 launch domain_bridge bridges.launch.xml pinky2:=false pinky3:=false
```

| 인자 | 기본값 | 설명 |
|---|---|---|
| `pinky1` | `true` | pinky1 브릿지(10 ↔ 100) 실행 여부 |
| `pinky2` | `true` | pinky2 브릿지(20 ↔ 100) 실행 여부 |
| `pinky3` | `true` | pinky3 브릿지(30 ↔ 100) 실행 여부 |

내부적으로는 로봇마다 아래 명령을 하나씩 실행한다.

```sh
ros2 run domain_bridge domain_bridge $(ros2 pkg prefix --share domain_bridge)/config/pinky_bridge_pinky1.yaml
```

### 다른 런치 파일에서 포함하기

```xml
<include file="$(find-pkg-share domain_bridge)/launch/bridges.launch.xml">
  <arg name="pinky3" value="false" />
</include>
```

## 토픽 추가·수정

`config/pinky_bridge_pinky{1,2,3}.yaml` 의 `topics:` 항목을 고친다. 한 항목의 형식은 다음과 같다.

```yaml
  /scan:                       # 출발 도메인에서의 토픽 이름
    type: sensor_msgs/msg/LaserScan
    from_domain: 10
    to_domain: 100
    remap: /pinky1/scan        # 도착 도메인에서 쓸 이름 (생략하면 그대로)
```

- 로봇 3대는 설정 파일이 따로이므로 **세 파일을 같이 고쳐야** 한다. (도메인 번호와 `pinkyN` 만 다르다.)
- 설정 파일은 `install/` 로 복사되므로 수정 후에는 `colcon build` 를 다시 해야 반영된다. `--symlink-install` 로 빌드했다면 재빌드 없이 런치만 다시 하면 된다.

## 확인

브릿지를 띄운 뒤 관제 PC 터미널에서 도메인 100 의 토픽이 보이는지 확인한다.

```sh
ROS_DOMAIN_ID=100 ros2 topic list | grep pinky1
ROS_DOMAIN_ID=100 ros2 topic echo /pinky1/amcl_pose --once
```

`/pinkyN/amcl_pose` 는 순찰 도착 판정에 쓰이므로 가장 먼저 확인할 것.

## 기타 사용법 (업스트림)

### 단일 yaml 로 실행

`launch/domain_bridge.launch.xml` 은 yaml 하나를 받아 브릿지 하나를 띄우는 업스트림 런치 파일이다.

```sh
ros2 launch domain_bridge domain_bridge.launch.xml config:=examples/example_bridge_config.yaml
```

선택 인자 `from_domain`, `to_domain` 으로 yaml 안의 도메인 ID 를 덮어쓸 수 있다.

### CLI

```sh
ros2 run domain_bridge domain_bridge <yaml>
ros2 run domain_bridge domain_bridge --from 7 --to 8 <yaml>
ros2 run domain_bridge domain_bridge --help
```

### C++ 라이브러리

프로세스에 직접 넣어 쓰는 C++ API 도 있다. [API 문서](https://docs.ros.org/en/ros2_packages/rolling/api/domain_bridge/index.html) 참고.

## 라이선스

Apache 2.0 (`LICENSE` 참고)

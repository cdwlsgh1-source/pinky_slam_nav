# pinky_camera

Pinky 로봇의 CSI 카메라 영상을 ROS 2 토픽으로 발행하고, YOLO 세그멘테이션으로 차선·가로선(Crossline)을 검출해 `cmd_vel`로 차선을 따라가는 패키지입니다.

## 주요 기능

- **`camera_node`**: Picamera2로 캡처한 영상을 JPEG(`sensor_msgs/CompressedImage`)로 발행합니다. 카메라 오픈 실패 시 3회 재시도하고, 프레임 하나가 실패해도 노드는 죽지 않습니다.
- **`lane_detector_node`**: YOLO 모델(`lane_seg_best.pt`)로 차선(클래스 1)과 Crossline(클래스 0)을 검출해 차선 중심 오프셋, 좌/우 검출 여부, 코너 신호 등을 발행합니다. 디버그 영상도 발행합니다.
- **`lane_follower_node`**: 오프셋에 대한 PD 제어로 `cmd_vel`을 발행합니다. 한쪽 차선만 보일 때 감속·보정, 코너 회전, 차선 소실 시 단계별 감속·탐색·정지, 오프셋 토픽이 0.5초 끊기면 무조건 정지(워치독)하는 안전장치가 있습니다.
- 모든 노드가 파라미터를 `ros2 param set`으로 실행 중에 바꿀 수 있습니다 (`model_path`, `device`, `control_rate` 제외).

## 데이터 흐름

토픽 이름은 launch의 `namespace`(기본 `pinky1`)가 앞에 붙습니다. 예: `/pinky1/lane/center_offset`

```
camera_node ──camera/image_raw/compressed──▶ lane_detector_node ──lane/center_offset ...
──▶ lane_follower_node ──cmd_vel──▶ 모터
```
![데이터 흐름도](docs/pinky_camera_pipeline.svg)

| 노드 | 구독 | 발행 |
|---|---|---|
| `camera_node` | - | `camera/image_raw/compressed` |
| `lane_detector_node` | `camera/image_raw/compressed` | `lane/center_offset`(Float32), `lane/detected`, `lane/left_detected`, `lane/right_detected`, `lane/corner_ahead`(Bool), `lane/offset_trend`(Float32), `crossline/detected`(Bool), `lane/debug/compressed` |
| `lane_follower_node` | 위 `lane/*`, `crossline/detected`, `odom` | `cmd_vel`(Twist) |

## 설치 방법

환경: Ubuntu 24.04, ROS 2 `jazzy`. 저장소 루트에서 빌드합니다.

```bash
git clone https://github.com/cdwlsgh1-source/pinky_slam_nav.git
cd pinky_slam_nav
source /opt/ros/jazzy/setup.bash
colcon build --packages-select pinky_camera
source install/setup.bash
```

| 노드 | 필요한 것 |
|---|---|
| `camera_node` | 로봇(라즈베리파이)의 `python3-picamera2`, `python3-opencv`, CSI 카메라 |
| `lane_detector_node` | `python3-opencv`, pip 패키지 `ultralytics` (`package.xml`에 rosdep 키 없음) |
| `lane_follower_node` | `nav_msgs`, `geometry_msgs` (ROS 기본 제공) |

```bash
# lane_detector_node를 돌리는 장비에서 (torch 버전·GPU 사용 여부 [TODO])
pip install --break-system-packages ultralytics
```

> `config/*.yaml`은 설치 폴더로 복사되므로 설정을 고친 뒤에는 `colcon build --packages-select pinky_camera`를 다시 실행하세요.

## 실행 방법

`namespace`는 `pinky1` 또는 `pinky2`입니다. 각 터미널에서 `source /opt/ros/jazzy/setup.bash && source install/setup.bash`를 먼저 하고, 로봇의 `ROS_DOMAIN_ID`(pinky1 `20`, pinky2 `22`)를 맞춥니다.

**1. 카메라** (로봇)

```bash
ros2 launch pinky_camera camera.launch.xml namespace:=pinky1
```

**2. 차선 검출** (로봇)

모델 경로는 `config/lane_detector_params.yaml`의 `model_path`로 정합니다. 이 값이 코드 기본값보다 우선하므로, 실행 전에 실제 경로로 고치세요 (아래 [주의](#주의-todo)).

```bash
ros2 launch pinky_camera lane_detector.launch.xml namespace:=pinky1
```

**3. 차선 추종** (모터를 구동하는 쪽, 실행 위치는 [TODO])

```bash
ros2 launch pinky_camera lane_follower.launch.xml namespace:=pinky1
```

로봇이 바로 움직이므로 처음에는 바퀴를 띄우거나 넓은 곳에서 `linear_speed`를 낮춰 시험하세요. 종료는 `Ctrl+C`입니다.

## 사용 예시

```bash
# 영상이 나오는지, 검출 결과가 나오는지 확인
ros2 topic hz /pinky1/camera/image_raw/compressed
ros2 topic echo /pinky1/lane/center_offset
ros2 topic echo /pinky1/lane/detected

# 디버그 영상 보기 (rqt_image_view 설치 필요)
ros2 run rqt_image_view rqt_image_view /pinky1/lane/debug/compressed

# 실행 중 속도·게인 조정
ros2 param set /pinky1/lane_follower_node linear_speed 0.1
ros2 param set /pinky1/lane_follower_node kp 1.0
ros2 param set /pinky1/lane_detector_node conf 0.5
```

주요 파라미터 (기본값은 `config/*.yaml`)

| 노드 | 파라미터 | yaml 값 | 설명 |
|---|---|---|---|
| `camera_node` | `width` × `height`, `fps` | 640 × 480, 30 | 캡처 해상도·프레임레이트 |
| `camera_node` | `jpeg_quality`, `hflip`/`vflip` | 80, 코드 기본 `True`/`True` | 압축 품질, 좌우·상하 반전 (yaml에는 없음) |
| `lane_detector_node` | `model_path`, `conf`, `device` | `/home/jinho/dev_ws/best.pt`, 0.5, `cpu` | 모델 경로, 신뢰도 하한, 추론 장치 |
| `lane_follower_node` | `linear_speed`, `kp`, `kd` | 0.15, 1.2, 0.3 | 직진 속도(m/s), PD 게인 |
| `lane_follower_node` | `watchdog_timeout`, `stop_on_crossline` | 0.5, `false` | 오프셋 끊김 시 정지 시간(s), Crossline에서 정지 여부 |

## 환경변수

| 변수 | 값 | 설명 |
|---|---|---|
| `ROS_DOMAIN_ID` | 관제 PC `50`, pinky1 `20`, pinky2 `22` | 노드를 실행하는 장비의 도메인. 이 패키지가 직접 읽는 다른 환경변수는 없음 |

launch 인자 `namespace`(기본 `pinky1`)는 환경변수가 아닙니다. 나머지 설정은 `config/*.yaml`과 `ros2 param set`으로 바꿉니다.

## 프로젝트 구조

```
pinky_camera/
├── README.md
├── best.pt                 # 이전 모델 (코드 주석에 "이전 모델"로 표기)
├── lane_seg_best.pt        # 현재 코드 기본 모델 (클래스 0=Crossline, 1=Lane)
├── package.xml, setup.py, setup.cfg
├── config/
│   ├── camera_params.yaml
│   ├── lane_detector_params.yaml
│   └── lane_follower_params.yaml
├── launch/
│   ├── camera.launch.xml
│   ├── lane_detector.launch.xml
│   └── lane_follower.launch.xml
└── pinky_camera/
    ├── camera_node.py
    ├── lane_detector_node.py
    └── lane_follower_node.py
```

## 주의 / TODO

- **모델 경로**: `lane_detector_params.yaml`의 `model_path`가 이전 모델 `/home/jinho/dev_ws/best.pt`로 되어 있어, launch로 실행하면 코드 기본값(`.../pinky_camera/lane_seg_best.pt`)을 덮어씁니다. 두 경로 모두 `/home/jinho/...` 하드코딩이므로 실행 장비의 실제 경로로 고쳐야 합니다. 최종 사용 모델은 **[TODO]**.
- **노드 실행 위치**: 문서마다 다릅니다 **[TODO]**.
  - `lane_detector_node`: 도메인 브릿지 yaml 주석과 `pinky_web_gui/CLAUDE.md`는 로봇, `package.xml` 주석은 PC입니다. 카메라 토픽은 브릿지에 없으므로 카메라와 같은 도메인(로봇)에서 실행해야 이미지를 받습니다.
  - `lane_follower_node`: 브릿지 yaml 주석은 관제 PC(50)에서 실행한다고 합니다. 그런데 launch는 `cmd_vel`을 절대 경로 `/cmd_vel`로 리맵하고(모터 드라이버가 네임스페이스 없는 `/cmd_vel`을 구독), 브릿지는 `/pinky1/cmd_vel`만 로봇 `/cmd_vel`로 중계합니다. 어느 도메인에서 실행하는지에 따라 `cmd_vel` 또는 `odom` 경로를 확인해야 합니다.
- **브릿지 범위**: `pinky_bridge_pinky1.yaml`은 `lane/center_offset`, `lane/detected`, `crossline/detected`만 중계합니다. `lane_follower_node`가 구독하는 `lane/left_detected`, `lane/right_detected`, `lane/offset_trend`, `lane/corner_ahead`는 없어서, 도메인이 나뉜 구성에서는 한쪽 차선 처리와 코너 대응이 동작하지 않을 수 있습니다. `pinky_bridge_pinky2.yaml`에는 차선 관련 토픽이 하나도 없습니다.
- 웹 GUI는 카메라 영상을 쓰지 않습니다 (`pinky_web_gui/docs/interfaces.md`).
- 학습 데이터·모델 학습 방법, 패키지 라이선스·maintainer(`package.xml`은 `your_name` 자리표시자)는 **[TODO]**.

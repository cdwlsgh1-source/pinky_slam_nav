# 환경 (이 PC에서 확인한 값)

> 역할: CLAUDE.md 에서 옮겨 온 참고 문서. 작업에 필요할 때만 읽는다. 내용은 옮기기 전과 같고, 규칙은 `CLAUDE.md` 에 남아 있다.
> 옮긴 날: 2026-10-11

- Ubuntu 24.04.5 LTS, ROS 2 `jazzy` (`$ROS_DISTRO`), Python 3.12.3
- `fastapi`, `uvicorn`: 시스템 Python 에는 없고 `pinky_web_gui/.venv`(`--system-site-packages` venv)에 설치돼 있다 (fastapi 0.142.2, uvicorn 0.54.0). 설치 방법은 `pinky_web_gui/README.md`
- `rclpy` import 가능 여부, 로봇 쪽 ROS 배포판: **확인 필요**

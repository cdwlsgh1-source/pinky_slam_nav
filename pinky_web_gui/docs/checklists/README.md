# 실물 확인 체크리스트

> 역할: mock 과 격리 도메인(77)에서 자동 검증이 끝난 기능을 **실제 로봇에서 사용자가 직접 확인**하는 절차 모음. 이 폴더의 문서는 코드가 아니라 사람이 하는 시험 순서다.
> 갱신: 2026-10-12

| 파일 | 대상 기능 | 확인하는 내용 |
|---|---|---|
| `step3_real_robot_checklist.md` | PRD Step 3, 명령 패널 | `start`/`stop`/`goto` 버튼이 실제 로봇에서 동작하는지, 명령 응답과 상태 표시, 배터리 토픽 |
| `step4_real_robot_checklist.md` | PRD Step 4, 구역·비상정지·LiDAR·수동 조작 | 구역 점유 표시, 비상정지가 실제로 먹히는지(소프트웨어 정지), LiDAR 방향, 수동 조작 데드맨 |
| `alarm_a1_real_robot_checklist.md` | 알람바 PRD Step A1, 서버 알람과 이력 파일 | 오프라인·전송 실패·비상정지 실패·무응답 알람의 발생/해소, 두 화면 동일, 새로고침 유지, `data/alarms/` 이력 파일 1000건 분할(시각 이름) |
| `alarm_a2_real_robot_checklist.md` | 알람바 PRD Step A2, ACK / SIL | 활성·해소 알람 ACK 가 두 브라우저에 반영, `oper`/`mngr` 계정 기록, 로그인 없이 401, SIL 해제, 이력 파일 `acked` 줄 |
| `alarm_a3_real_robot_checklist.md` | 알람바 PRD Step A3, 알람 소리와 소리 설정 | 레벨별 실제 소리와 반복, SIL/ACK 로 멈춤, `config/sounds/` 파일 재생, 파일 삭제 시 합성음, 첫 클릭 전 안내 문구, 설정 저장 |
| `alarm_a4_real_robot_checklist.md` | 알람바 PRD Step A4, 알람 카탈로그(읽기 전용) | Alarm 메뉴와 Catalog 탭, 실제 알람 발생 뒤 횟수·시각, 권한, `alarms.yaml` 덮어쓰기 |
| `alarm_a5_real_robot_checklist.md` | 알람바 PRD Step A5, 알람 카탈로그 수정·Enable/Disable (manager) | 계정별 권한, 실제 알람을 끄고 켜기, 알람별 소리 파일·반복, 비상정지 알람 보호, 재시작 후 유지, 로그 화면 기록 |
| `alarm_a7_real_robot_checklist.md` | 알람바 PRD Step A7, Alarm 화면 History 탭 | 실제 알람이 이력 목록에 한 건씩 나오는지, 필터·검색·Load more, 활성 고정과 실시간 해소, 서버 재시작 뒤 `해소 기록 없음`, 상세 타임라인, CSV 를 스프레드시트로 열기, 메뉴 배지 |
| `step6_process_control_real_checklist.md` | PRD Step 6, 인증과 프로세스 제어 (시스템 패널) | 버튼으로 브릿지, zone_manager, 로봇 bringup/map/순찰 노드를 켜고 끄기, SSH, 서버 종료 시 정리 |

## 이름 규칙

- `stepN_…`: 계획서(`../prd/Pinky 관제주행 웹 GUI — 단계별 PRD.md`)의 Step N 에 해당하는 기능. 개발 중에 추가된 기능도 PRD 에 Step 을 부여해 같은 규칙을 따른다 (예: Step 6).
- 네트워크 진단처럼 별도 PRD 를 쓰는 기능은 `netN_…` 처럼 그 PRD 의 단계 이름을 쓴다 (**확인 필요**: 만들 때 정한다). 알람바 PRD 는 `alarm_aN_…` 을 쓴다 (예: `alarm_a1_real_robot_checklist.md`).
- 새 체크리스트는 `<단계>_<기능>_real_checklist.md` 로 만들고 이 표에 한 줄 추가한다.

## 공통 원칙

- 로봇 쪽 명령과 토픽 확인은 사용자가 직접 한다 (`CLAUDE.md` 6절). 이 문서의 어떤 항목도 아직 실제 로봇에서 실행되지 않았다.
- 로봇이 움직일 수 있는 단계는 주변을 비우고 전원 스위치에 손이 닿는 곳에서 한다. GUI 비상정지는 소프트웨어 정지이며 하드웨어 비상정지를 대체하지 않는다.
- 결과(통과/실패, 이상한 화면 캡처, 백엔드 로그)를 알려 주면 해당 문서와 `../reference/known-pitfalls.md` 에 반영한다.

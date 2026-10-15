# map_view_pc

Pinky 로봇으로 만든 지도(`my_pinky_map10`)와, 지도 위에서 map 좌표(m)를 확인하고 순찰 waypoint 좌표를 정할 때 쓰는 웹 뷰어입니다.

## 주요 기능

- **실시간 좌표 표시**: 마우스를 올리면 이미지 픽셀, map 좌표 `(x, y)`(m), occupancy(free / occupied / unknown)가 표시됩니다.
- **클릭으로 좌표 기록**: 지도를 클릭하면 `P1`, `P2`, … 순서로 점이 찍히고 map 좌표와 픽셀 좌표가 목록에 쌓입니다. 점별 `삭제`, 전체 `모든 점 지우기` 버튼이 있습니다.
- **그리드**: 0.05 m(파랑)·0.2 m(빨강, 좌표 라벨 포함) 그리드와 map 원점 `(0, 0)` 표시. `그리드 on/off` 버튼으로 끌 수 있습니다.
- **지도 정보 표시**: 이미지 크기, 해상도, origin, x/y 범위를 상단에 보여 줍니다.
- **서버·설치 불필요**: 지도 이미지가 HTML 안에 내장된 단일 파일이고 외부 요청이 없어 오프라인에서도 열립니다.
- **라이트/다크 모드**: 브라우저/OS 설정을 따라 자동 전환됩니다.

## 설치 방법

설치할 것이 없습니다. 최신 웹 브라우저만 있으면 됩니다. 저장소를 받아 둡니다.

```bash
git clone https://github.com/cdwlsgh1-source/pinky_slam_nav.git
cd pinky_slam_nav/map_view_pc
```

## 실행 방법

**웹에서 바로 보기**: [🗺️ Pinky Map Viewer 실행하기](https://htmlpreview.github.io/?https://raw.githubusercontent.com/cdwlsgh1-source/pinky_slam_nav/main/map_view_pc/pinky_map_viewer.html)

**로컬에서 열기** (`map_view_pc/` 폴더에서)

```bash
xdg-open pinky_map_viewer.html      # Linux
open pinky_map_viewer.html          # macOS
start pinky_map_viewer.html         # Windows (cmd)
```

파일을 더블클릭해서 브라우저로 열어도 됩니다.

## 사용 예시

1. 뷰어를 열고 지도 위에서 마우스를 움직여 원하는 위치의 map 좌표를 확인합니다.
2. 순찰 지점으로 쓸 위치를 클릭합니다. 목록에 아래처럼 기록됩니다.

   ```
   P1 — map: (0.650, 0.150) m   px: (82, 14)
   ```

3. 기록된 `map` 좌표(m)를 순찰 노드의 waypoint나 웹 GUI 설정의 지점 좌표로 옮겨 적습니다.
   - 웹 GUI는 `pinky_web_gui/config/robots.yaml`의 `map_yaml`로 이 폴더의 `my_pinky_map10.yaml`을 읽어 지도를 표시합니다.
   - 지점 좌표(P1~P7 등)는 `pinky_web_gui/config/points.yaml`에 있습니다.
4. 지도에 표시된 `P1`, `P2` 같은 번호는 뷰어에서 클릭한 순서일 뿐이고, 순찰 노드의 지점 이름(P1~P7)과는 관계없습니다.

## 지도 정보

`my_pinky_map10.yaml`의 값입니다.

| 항목 | 값 | 설명 |
|---|---|---|
| `image` | `my_pinky_map10.pgm` | 지도 이미지 (189 × 120 px) |
| `mode` | `trinary` | free / occupied / unknown 3값 |
| `resolution` | `0.010` | 1 px = 0.01 m |
| `origin` | `[-0.167, -0.907, 0]` | 이미지 **왼쪽 아래** 모서리의 map 좌표 `[x, y, yaw]` |
| `negate` | `0` | 색상 반전 없음 |
| `occupied_thresh` | `0.65` | 이 값보다 어두우면 occupied |
| `free_thresh` | `0.196` | 이 값보다 밝으면 free |

- 지도 범위: x `-0.167 ~ 1.723` m, y `-0.907 ~ 0.293` m (해상도와 이미지 크기에서 계산한 값).
- 뷰어의 좌표 변환식은 `x = -0.167 + px × 0.01`, `y = 0.293 − py × 0.01`입니다. 이미지의 맨 윗줄이 `py = 0`입니다.
- 뷰어는 픽셀 밝기만 보고 occupancy를 표시합니다(255 근처는 free, 0 근처는 occupied, 그 외는 unknown). yaml의 `occupied_thresh`/`free_thresh`로 판정하지는 않습니다.
- 뷰어 안의 지도 값(`META`)은 yaml에서 읽는 것이 아니라 HTML에 직접 적혀 있습니다. 지도를 새로 만들면 `pinky_map_viewer.html`의 `META`와 내장 이미지를 함께 바꿔야 합니다. 자동 변환 도구는 이 저장소에 없습니다 **[TODO]**.

## 프로젝트 구조

```
map_view_pc/
├── README.md                # 이 문서
├── pinky_map_viewer.html    # 좌표 뷰어 (지도 이미지 내장, 단일 파일)
├── my_pinky_map10.pgm       # 지도 이미지 (189 × 120 px)
└── my_pinky_map10.yaml      # 지도 메타데이터 (resolution, origin, threshold)
```

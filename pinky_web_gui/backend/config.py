"""config/robots.yaml 로더.

경로는 이 파일 위치 기준으로 계산한다. 실행 위치(cwd)가 어디든 같은 파일을 읽게 하기 위해서다.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'robots.yaml'


@dataclass(frozen=True)
class Config:
    robots: tuple
    zone_status_topic: str
    online_timeout_sec: float
    pose_max_hz: float


def load_config(path=CONFIG_PATH):
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}

    robots = raw.get('robots')
    if not isinstance(robots, list) or not robots or not all(isinstance(r, str) and r for r in robots):
        raise ValueError(f'{path}: robots 는 로봇 ID 문자열의 비어 있지 않은 목록이어야 한다')
    if len(set(robots)) != len(robots):
        raise ValueError(f'{path}: robots 에 중복된 ID가 있다')

    cfg = Config(
        robots=tuple(robots),
        zone_status_topic=str(raw.get('zone_status_topic', '/zone_manager/status')),
        online_timeout_sec=float(raw.get('online_timeout_sec', 5)),
        pose_max_hz=float(raw.get('pose_max_hz', 10)),
    )
    if cfg.online_timeout_sec <= 0 or cfg.pose_max_hz <= 0:
        raise ValueError(f'{path}: online_timeout_sec, pose_max_hz 는 0보다 커야 한다')
    return cfg

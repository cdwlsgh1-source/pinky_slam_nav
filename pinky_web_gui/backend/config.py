"""config/robots.yaml 로더. 로봇 이름, 토픽 이름, 타임아웃은 전부 이 파일을 거쳐서만 읽는다."""
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config' / 'robots.yaml'


@dataclass(frozen=True)
class Config:
    robots: tuple
    zone_status_topic: str
    online_timeout_sec: float
    pose_max_hz: float
    map_yaml: str = None  # 지도 yaml 의 절대 경로 (robots.yaml 위치 기준 상대 경로로 적는다)


def load_config(path=None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}

    robots = raw.get('robots')
    if not isinstance(robots, list) or not robots or not all(isinstance(r, str) and r for r in robots):
        raise ValueError(f'{path}: robots 는 비어 있지 않은 문자열 목록이어야 한다')
    if len(set(robots)) != len(robots):
        raise ValueError(f'{path}: robots 에 중복이 있다')
    zone_topic = raw.get('zone_status_topic')
    if not isinstance(zone_topic, str) or not zone_topic.startswith('/'):
        raise ValueError(f'{path}: zone_status_topic 은 "/"로 시작하는 토픽 이름이어야 한다')

    timeout = float(raw.get('online_timeout_sec', 5.0))
    hz = float(raw.get('pose_max_hz', 10))
    if timeout <= 0 or hz <= 0:
        raise ValueError(f'{path}: online_timeout_sec, pose_max_hz 는 0보다 커야 한다')

    map_yaml = raw.get('map_yaml')
    if map_yaml is not None:
        if not isinstance(map_yaml, str) or not map_yaml:
            raise ValueError(f'{path}: map_yaml 은 문자열 경로여야 한다')
        map_yaml = str((path.resolve().parent / map_yaml).resolve())

    return Config(tuple(robots), zone_topic, timeout, hz, map_yaml)

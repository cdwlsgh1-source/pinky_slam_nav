"""지도 yaml + pgm 을 읽어 메타데이터(JSON)와 PNG 로 만든다. 외부 라이브러리 없이 표준 라이브러리만 쓴다.

해상도, origin, 임계값은 yaml 에서 읽고 코드에 박지 않는다. pgm 의 픽셀 값은 PNG(8비트 그레이)에
그대로 옮기므로 프런트엔드가 같은 값으로 occupancy 를 판정할 수 있다.
"""
import struct
import zlib
from pathlib import Path

import yaml


def read_pgm(path):
    """P5(바이너리) PGM -> (width, height, 픽셀 bytes). 주석(#)과 공백을 처리한다."""
    data = Path(path).read_bytes()
    if data[:2] != b'P5':
        raise ValueError(f'{path}: P5(바이너리) PGM 이 아니다')
    pos, tokens = 2, []
    while len(tokens) < 3:
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b'#':
            while pos < len(data) and data[pos:pos + 1] != b'\n':
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        tokens.append(int(data[start:pos]))
    pos += 1  # maxval 뒤의 공백 1칸
    width, height, maxval = tokens
    if maxval > 255:
        raise ValueError(f'{path}: 16비트 PGM 은 지원하지 않는다 (maxval={maxval})')
    pixels = data[pos:pos + width * height]
    if len(pixels) != width * height:
        raise ValueError(f'{path}: 픽셀 데이터가 부족하다')
    return width, height, pixels


def encode_png_gray(width, height, pixels):
    def chunk(tag, body):
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body) & 0xFFFFFFFF)

    raw = b''.join(b'\x00' + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


def load_map(yaml_path):
    """반환: (메타데이터 dict, PNG bytes)"""
    yaml_path = Path(yaml_path)
    raw = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    width, height, pixels = read_pgm(yaml_path.parent / raw['image'])
    origin = [float(v) for v in raw['origin']]
    meta = {
        'width': width,
        'height': height,
        'resolution': float(raw['resolution']),
        'origin': origin,
        'negate': int(raw.get('negate', 0)),
        'occupied_thresh': float(raw.get('occupied_thresh', 0.65)),
        'free_thresh': float(raw.get('free_thresh', 0.196)),
        'image_url': '/api/map/image',
    }
    return meta, encode_png_gray(width, height, pixels)

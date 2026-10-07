"""지도 로더 테스트: 실제 지도(yaml/pgm) -> 메타데이터와 PNG. 실행: .venv/bin/python -m pytest tests -q"""
import io
import struct
import zlib

import pytest

from backend.config import load_config
from backend.map_loader import encode_png_gray, load_map, read_pgm


def test_pgm_with_comment(tmp_path):
    f = tmp_path / 'a.pgm'
    f.write_bytes(b'P5\n# comment\n3 2\n255\n' + bytes([0, 128, 255, 1, 2, 3]))
    assert read_pgm(f) == (3, 2, bytes([0, 128, 255, 1, 2, 3]))


def test_png_is_valid_and_lossless():
    png = encode_png_gray(3, 2, bytes([0, 128, 255, 1, 2, 3]))
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    w, h = struct.unpack('>II', png[16:24])
    assert (w, h) == (3, 2)
    idat = png[png.index(b'IDAT') + 4:png.index(b'IEND') - 4]
    raw = zlib.decompress(idat)
    assert raw == b'\x00' + bytes([0, 128, 255]) + b'\x00' + bytes([1, 2, 3])


def test_real_map_matches_yaml_and_pgm():
    cfg = load_config()
    meta, png = load_map(cfg.map_yaml)
    assert (meta['width'], meta['height']) == (189, 120)
    assert meta['resolution'] == pytest.approx(0.01)
    assert meta['origin'][:2] == pytest.approx([-0.167, -0.907])
    Image = pytest.importorskip('PIL.Image')
    im = Image.open(io.BytesIO(png))
    src = Image.open(cfg.map_yaml.replace('.yaml', '.pgm'))
    assert im.size == src.size and list(im.getdata()) == list(src.getdata())

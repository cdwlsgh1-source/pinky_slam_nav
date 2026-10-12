"""알람 사운드 파일 보관소(backend/sounds.py)와 API(GET /api/alarms/sounds, GET /sounds/<파일>) 테스트.
경로 탈출, 허용 확장자, 크기 상한, 인증을 확인한다. mock 서버를 TestClient 로 띄운다 (ROS 불필요)."""
import os

import pytest
from fastapi.testclient import TestClient

from backend.auth import Auth
from backend.config import load_config
from backend.main import create_app
from backend.sounds import MAX_BYTES, SoundLibrary

CFG = load_config()


def make_client(tmp_path, sound_dir, auth=None):
    app = create_app(CFG, mock=True, auth=auth if auth is not None else Auth.disabled(), alarm_dir=str(tmp_path / 'alarms'), sound_dir=str(sound_dir))
    return TestClient(app)


@pytest.fixture
def sdir(tmp_path):
    d = tmp_path / 'sounds'
    d.mkdir()
    (d / 'b_siren.wav').write_bytes(b'RIFF....WAVE')
    (d / 'a_beep.MP3').write_bytes(b'ID3\x00')                 # 확장자 대소문자는 구분하지 않는다
    (d / 'c.ogg').write_bytes(b'OggS')
    (d / 'notes.txt').write_text('x')                          # 허용 안 된 확장자
    (d / 'big.wav').write_bytes(b'0' * (MAX_BYTES + 1))        # 크기 초과
    (d / '.hidden.wav').write_bytes(b'RIFF')                   # 숨김 파일
    (d / 'sub').mkdir()
    (d / 'sub' / 'x.wav').write_bytes(b'RIFF')                 # 하위 폴더 안은 서빙하지 않는다
    (tmp_path / 'secret.wav').write_bytes(b'SECRET')           # 폴더 밖
    return d


def test_list_has_only_allowed_small_files_sorted(sdir):
    names = [f['name'] for f in SoundLibrary(sdir).files()]
    assert names == ['a_beep.MP3', 'b_siren.wav', 'c.ogg']


def test_list_types_and_sizes(sdir):
    files = {f['name']: f for f in SoundLibrary(sdir).files()}
    assert files['a_beep.MP3']['type'] == 'audio/mpeg'
    assert files['b_siren.wav']['type'] == 'audio/wav'
    assert files['c.ogg']['type'] == 'audio/ogg'
    assert files['c.ogg']['size'] == 4


def test_missing_folder_gives_empty_list(tmp_path):
    assert SoundLibrary(tmp_path / 'nope').files() == []


@pytest.mark.parametrize('name', ['../secret.wav', '..', '.', '', 'sub/x.wav', 'sub\\x.wav', '/etc/passwd', '.hidden.wav', 'notes.txt',
                                  'big.wav', 'missing.wav', 'a\x00.wav', 'secret.wav/..'])
def test_resolve_rejects(sdir, name):
    assert SoundLibrary(sdir).resolve(name) is None


def test_resolve_rejects_symlink_out_of_folder(sdir, tmp_path):
    os.symlink(tmp_path / 'secret.wav', sdir / 'link.wav')
    assert SoundLibrary(sdir).resolve('link.wav') is None
    assert 'link.wav' not in [f['name'] for f in SoundLibrary(sdir).files()]


def test_resolve_rejects_non_string(sdir):
    assert SoundLibrary(sdir).resolve(None) is None
    assert SoundLibrary(sdir).resolve(7) is None


def test_api_lists_files(sdir, tmp_path):
    with make_client(tmp_path, sdir) as c:
        j = c.get('/api/alarms/sounds').json()
        assert [f['name'] for f in j['files']] == ['a_beep.MP3', 'b_siren.wav', 'c.ogg']
        assert j['max_bytes'] == MAX_BYTES
        assert j['extensions'] == ['.mp3', '.ogg', '.wav']


def test_api_serves_file_with_media_type(sdir, tmp_path):
    with make_client(tmp_path, sdir) as c:
        r = c.get('/sounds/b_siren.wav')
        assert r.status_code == 200 and r.content == b'RIFF....WAVE'
        assert r.headers['content-type'].startswith('audio/wav')
        assert c.get('/sounds/a_beep.MP3').headers['content-type'].startswith('audio/mpeg')


@pytest.mark.parametrize('path', ['/sounds/notes.txt', '/sounds/big.wav', '/sounds/nope.wav', '/sounds/.hidden.wav', '/sounds/sub/x.wav',
                                  '/sounds/..%2Fsecret.wav', '/sounds/%2e%2e/secret.wav', '/sounds/..%5Csecret.wav', '/sounds/%2Fetc%2Fpasswd'])
def test_api_never_serves_anything_else(sdir, tmp_path, path):
    with make_client(tmp_path, sdir) as c:
        r = c.get(path)
        assert r.status_code == 404
        assert b'SECRET' not in r.content


def test_default_folder_is_config_sounds_and_readme_is_not_listed():
    lib = SoundLibrary(CFG.alarm_sound_dir)
    assert str(lib.dir).endswith('config/sounds')
    assert all(f['name'].lower().endswith(('.mp3', '.wav', '.ogg')) for f in lib.files())


def test_sound_list_needs_login_when_auth_is_on(sdir, tmp_path):
    with make_client(tmp_path, sdir, auth=Auth()) as c:
        assert c.get('/api/alarms/sounds').status_code == 401
        assert c.post('/api/login', json={'id': 'oper', 'password': 'oper'}).status_code == 200
        assert c.get('/api/alarms/sounds').status_code == 200

"""알람 사운드 파일 보관소 (config/sounds/). 파일은 사용자가 이 폴더에 직접 둔다 (PRD 결정 10 의 1차안, GUI 업로드는 없다).

브라우저가 보낸 이름은 '이 폴더 안의 평범한 파일 이름' 인지 검사한 뒤에만 파일로 바꾼다 (경로 탈출 방지).
허용 확장자와 크기 상한을 넘는 파일은 목록에도 서빙에도 나오지 않는다.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

ALLOWED_EXT = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg'}
MAX_BYTES = 1024 * 1024   # 파일당 1MB (PRD 결정 10). 알람 소리는 짧은 클립이면 충분하고, 큰 파일은 재생 시작이 늦어진다


class SoundLibrary:
    def __init__(self, directory, max_bytes=MAX_BYTES):
        self.dir = Path(directory).resolve()
        self.max_bytes = int(max_bytes)

    def resolve(self, name):
        """이 이름의 서빙 가능한 파일 경로. 허용되지 않으면 None (없음, 확장자, 크기, 폴더 밖 모두 같은 응답)."""
        if not isinstance(name, str) or not name or name != Path(name).name or name.startswith('.') or '\\' in name or '\x00' in name:
            return None
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            return None
        try:
            p = (self.dir / name).resolve()      # 심볼릭 링크는 따라간 뒤 폴더 안인지 다시 본다
            if p.parent != self.dir or not p.is_file() or p.stat().st_size > self.max_bytes:
                return None
        except OSError:
            return None
        return p

    def media_type(self, name):
        return ALLOWED_EXT[Path(name).suffix.lower()]

    def files(self):
        """[{name, size, type}] 이름 순. 폴더가 없으면 빈 목록 (그 경우 화면은 합성음을 쓴다)."""
        try:
            names = sorted(p.name for p in self.dir.iterdir())
        except OSError:
            return []
        out = []
        for n in names:
            p = self.resolve(n)
            if p is not None:
                out.append({'name': n, 'size': p.stat().st_size, 'type': self.media_type(n)})
        return out

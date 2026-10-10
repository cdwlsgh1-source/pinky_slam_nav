"""인증: 환경 변수로 지정한 비밀번호 두 개(operator, viewer)와 서버 메모리의 세션. 표준 라이브러리만 쓴다.

- PINKY_OPERATOR_PASSWORD: 명령 가능 (start/goto/수동 조작/프로세스 제어)
- PINKY_VIEWER_PASSWORD  : 보기 전용 (비상정지는 허용 - 정지는 누구나 눌러야 한다)
- 둘 다 없으면 인증이 꺼진다 (기존처럼 누구나 사용). 이때 프로세스 제어(원격 실행)는 강제로 꺼진다.
- 비밀번호는 코드·설정·로그에 남기지 않는다. 비교는 hmac.compare_digest (상수 시간).
- 세션은 서버 메모리에만 있어서 서버를 재시작하면 모두 로그아웃된다.
"""
import hmac
import secrets
import time
from collections import defaultdict, deque

OPERATOR, VIEWER = 'operator', 'viewer'
RANK = {VIEWER: 1, OPERATOR: 2}
COOKIE = 'pinky_session'
SESSION_SEC = 12 * 3600
MAX_FAILS = 5        # FAIL_WINDOW_SEC 안에 이만큼 틀리면 잠깐 막는다
FAIL_WINDOW_SEC = 60.0


class Auth:
    def __init__(self, operator_pw=None, viewer_pw=None, now=time.monotonic):
        self._pw = {OPERATOR: operator_pw or None, VIEWER: viewer_pw or None}
        self._now = now
        self._sessions = {}                 # 토큰 -> (역할, 만료 시각)
        self._fails = defaultdict(deque)    # 클라이언트 주소 -> 실패 시각들

    @classmethod
    def from_env(cls, env):
        return cls(env.get('PINKY_OPERATOR_PASSWORD'), env.get('PINKY_VIEWER_PASSWORD'))

    @property
    def enabled(self):
        """비밀번호가 하나라도 설정돼 있으면 인증을 한다."""
        return any(self._pw.values())

    @property
    def procs_allowed(self):
        """프로세스 제어는 operator 비밀번호가 있을 때만 켠다 (인증 없이 원격 실행을 열어 두지 않는다)."""
        return bool(self._pw[OPERATOR])

    def locked(self, who):
        q = self._fails[who]
        t = self._now()
        while q and t - q[0] > FAIL_WINDOW_SEC:
            q.popleft()
        return len(q) >= MAX_FAILS

    def login(self, password, who='-'):
        """반환: (토큰, 역할) 또는 (None, 사유). 사유는 'locked' | 'invalid'."""
        if self.locked(who):
            return None, 'locked'
        pw = password if isinstance(password, str) else ''
        # 두 역할 모두 항상 비교한다 (어느 쪽이 맞았는지가 응답 시간으로 드러나지 않게)
        role = None
        for r in (OPERATOR, VIEWER):
            expected = self._pw[r]
            if expected and hmac.compare_digest(pw.encode(), expected.encode()) and role is None:
                role = r
        if role is None:
            self._fails[who].append(self._now())
            return None, 'invalid'
        self._fails.pop(who, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (role, self._now() + SESSION_SEC)
        return token, role

    def logout(self, token):
        self._sessions.pop(token, None)

    def role_of(self, token):
        """세션 토큰의 역할. 인증이 꺼져 있으면 모두 operator(기존 동작). 없거나 만료면 None."""
        if not self.enabled:
            return OPERATOR
        s = self._sessions.get(token)
        if s is None:
            return None
        if s[1] <= self._now():
            del self._sessions[token]
            return None
        return s[0]

    @staticmethod
    def allows(role, need):
        return role is not None and RANK[role] >= RANK[need]

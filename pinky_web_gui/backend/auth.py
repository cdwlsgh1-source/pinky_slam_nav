"""인증: 프로그램에 고정된 계정(ID/비밀번호)과 서버 메모리의 세션. 표준 라이브러리만 쓴다.

- ID mngr / 비밀번호 mngr : manager 역할, 명령 가능 (start/goto/수동 조작/프로세스 제어)
- ID oper / 비밀번호 oper : operator 역할, 보기 전용 (비상정지는 허용 - 정지는 누구나 눌러야 한다)
- 계정은 아래 DEFAULT_ACCOUNTS 에 고정돼 있다 (사용자 결정). 환경 변수나 설정 파일로 바꾸지 않는다.
  바꾸려면 이 파일을 고친다. 소스를 볼 수 있는 사람은 비밀번호도 볼 수 있다는 뜻이므로, 신뢰하는 내부망에서만 쓴다.
- 서버를 --no-auth 로 실행하면 인증이 꺼진다 (개발/시험용). 이때 프로세스 제어(원격 실행)는 강제로 꺼진다.
- 비교는 hmac.compare_digest (상수 시간). 비밀번호는 로그에 남기지 않는다.
- 세션은 서버 메모리에만 있어서 서버를 재시작하면 모두 로그아웃된다.
"""
import hmac
import secrets
import time
from collections import defaultdict, deque

MANAGER, OPERATOR = 'manager', 'operator'
RANK = {OPERATOR: 1, MANAGER: 2}
COOKIE = 'pinky_session'
SESSION_SEC = 12 * 3600
MAX_FAILS = 5        # FAIL_WINDOW_SEC 안에 이만큼 틀리면 잠깐 막는다
FAIL_WINDOW_SEC = 60.0

# ID -> (비밀번호, 역할)
DEFAULT_ACCOUNTS = {
    'mngr': ('mngr', MANAGER),
    'oper': ('oper', OPERATOR),
}


class Auth:
    def __init__(self, accounts=None, now=time.monotonic):
        """accounts: {ID: (비밀번호, 역할)}. None 이면 고정 계정(DEFAULT_ACCOUNTS), 빈 dict 면 인증 꺼짐."""
        self._acc = dict(DEFAULT_ACCOUNTS if accounts is None else accounts)
        self._now = now
        self._sessions = {}                 # 토큰 -> (역할, ID, 만료 시각)
        self._fails = defaultdict(deque)    # 클라이언트 주소 -> 실패 시각들

    @classmethod
    def disabled(cls):
        return cls({})

    @property
    def enabled(self):
        return bool(self._acc)

    @property
    def procs_allowed(self):
        """프로세스 제어(원격 실행)는 인증이 켜져 있고 manager 계정이 있을 때만 켠다."""
        return any(role == MANAGER for _, role in self._acc.values())

    def locked(self, who):
        q = self._fails[who]
        t = self._now()
        while q and t - q[0] > FAIL_WINDOW_SEC:
            q.popleft()
        return len(q) >= MAX_FAILS

    def login(self, user, password, who='-'):
        """반환: (토큰, 역할) 또는 (None, 사유). 사유는 'locked' | 'invalid'."""
        if self.locked(who):
            return None, 'locked'
        user = user if isinstance(user, str) else ''
        pw = password if isinstance(password, str) else ''
        # ID 가 없어도 같은 비교를 한다 (ID 존재 여부가 응답 시간으로 드러나지 않게)
        expected, role = self._acc.get(user, ('\0', None))
        ok = hmac.compare_digest(pw.encode(), expected.encode())
        if not ok or role is None:
            self._fails[who].append(self._now())
            return None, 'invalid'
        self._fails.pop(who, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (role, user, self._now() + SESSION_SEC)
        return token, role

    def logout(self, token):
        self._sessions.pop(token, None)

    def _session(self, token):
        s = self._sessions.get(token)
        if s is None:
            return None
        if s[2] <= self._now():
            del self._sessions[token]
            return None
        return s

    def role_of(self, token):
        """세션 토큰의 역할. 인증이 꺼져 있으면 모두 manager(기존 동작). 없거나 만료면 None."""
        if not self.enabled:
            return MANAGER
        s = self._session(token)
        return s[0] if s else None

    def user_of(self, token):
        s = self._session(token) if self.enabled else None
        return s[1] if s else None

    @staticmethod
    def allows(role, need):
        return role is not None and RANK[role] >= RANK[need]
